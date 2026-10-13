import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from functools import lru_cache

import boto3
import httpx

from .config import settings
from .domain import Control, TradeIntent
from .security import Denied, Principal, TokenService, digest
from .store import make_store
from .history import trace_claims


@lru_cache
def dependencies():
    s = settings()
    store = make_store()
    return s, store, Control(store), TokenService(s)


def quote_eth(config):
    """No invented live prices. Local paper reference is conspicuously labeled."""
    timestamp = int(time.time())
    if config.onchain_data_mode == "live":
        if not config.data_provider_secret_arn:
            raise Denied("PROVIDER_NOT_CONFIGURED", "尚未配置实时数据凭据，无法查询实时链上数据。", 503)
        secret = boto3.client("secretsmanager", region_name=config.region).get_secret_value(
            SecretId=config.data_provider_secret_arn
        )
        creds = json.loads(secret["SecretString"])
        path = "/api/v6/dex/market/price"
        body = json.dumps(
            [{"chainIndex": "8453", "tokenContractAddress": "0x4200000000000000000000000000000000000006"}],
            separators=(",", ":"),
        )
        iso = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        signature = base64.b64encode(hmac.new(
            creds["secret_key"].encode(), (iso+"POST"+path+body).encode(), hashlib.sha256,
        ).digest()).decode()
        response = httpx.post(
            "https://web3.okx.com"+path, content=body, timeout=10,
            headers={"Content-Type": "application/json", "OK-ACCESS-KEY": creds["api_key"],
                     "OK-ACCESS-PASSPHRASE": creds["passphrase"],
                     "OK-ACCESS-TIMESTAMP": iso, "OK-ACCESS-SIGN": signature},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "0" or not payload.get("data"):
            raise Denied("QUOTE_UNAVAILABLE", "Data provider did not return a usable quote.", 503)
        price = float(payload["data"][0]["price"])
        return {"symbol": "ETH", "price": price, "source": "链上数据服务",
                "as_of": timestamp, "expires_at": timestamp+30, "simulated": False}
    return {"symbol": "ETH", "price": 3000, "source": "Paper reference • not a live market price",
            "as_of": timestamp, "expires_at": timestamp+30, "simulated": True}


def execute_tool(control, config, claims, tool, args):
    p = control.authorize_tool(claims, tool, args, count=False)
    from .extensions import LIVE_TOOLS
    if tool in LIVE_TOOLS:
        from .onchain_os import invoke
        return invoke(config, tool, args)
    if tool == "analyze_portfolio":
        from .insights import analyze
        return analyze(control, p)
    if tool == "preview_trade":
        from .insights import preflight
        return preflight(control, p, claims["agent_id"], args["amount"])
    if tool == "get_quote":
        return quote_eth(config)
    if tool == "get_portfolio":
        return {"wallet": control.snapshot(p)["wallet"], "mode": "paper"}
    if tool == "get_history":
        return {"operations": control.snapshot(p)["operations"][:40]}
    if tool == "prepare_trade":
        operation = control.prepare_trade(
            p, claims["agent_id"], TradeIntent.model_validate(args),
            source_id=claims["run_id"]+digest(args),
            conversation_id=claims["sid"],
            quote=quote_eth(config),
        )
        return {"status": "approval_required", "operation": operation,
                "approval_url": config.public_url + "/?review_operation=" + operation["id"]}
    if tool == "get_operation":
        return control.operation(p, args["operation_id"])
    if tool == "execute_trade":
        return control.execute(p, args["operation_id"], quote_eth(config))
    raise Denied("TOOL_DENIED", "Unknown tool.")


def executor_handler(event, _context):
    """Invoked only by the MCP Runtime role; independently validates the invocation capability."""
    config, _store, control, tokens = dependencies()
    claims = None
    started = time.monotonic()
    try:
        claims = tokens.verify(event.get("security_context", ""), "onchain-tools")
        tool, args = event["tool"], event["arguments"]
        if claims.get("tool") != tool or claims.get("arguments_hash") != digest(args):
            raise Denied("ARGUMENTS_CHANGED", "Signed tool request does not match.")
        trace_claims(control, claims, "EXECUTOR_STARTED", "executor", "工具执行服务开始处理",
                     {"tool": tool, "arguments": args})
        result = execute_tool(control, config, claims, tool, args)
        trace_claims(control, claims, "EXECUTOR_RESULT", "executor", "工具执行服务返回结果",
                     {"tool": tool, "result": result}, duration_ms=int((time.monotonic()-started)*1000))
        if claims.get("external"):
            p = principal_from_claims(claims)
            control.run_status(p, claims["sid"], claims["run_id"], "SUCCEEDED")
        return {"ok": True, "result": result}
    except Denied as e:
        if claims:
            trace_claims(control, claims, "EXECUTOR_BLOCKED", "executor", "执行被权限规则阻止",
                         {"code": e.code, "message": e.message}, "BLOCKED")
            if claims.get("external"):
                control.run_status(principal_from_claims(claims), claims["sid"], claims["run_id"], "BLOCKED")
        return {"ok": False, "error": {"code": e.code, "message": e.message}}


def principal_from_claims(claims):
    return Principal(sub=claims["sub"], tenant=claims["tenant"])
