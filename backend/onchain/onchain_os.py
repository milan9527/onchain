"""Fixed OKX API adapters. No CLI, arbitrary URL, wallet signature or automatic x402 payment."""
import base64
import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urlencode

import boto3
import httpx

from .security import Denied

REFERENCE = "https://github.com/okx/onchainos-skills/tree/9d9c66f5c296461b8687a71cdab188acedcb781d"
BASE_URL = "https://web3.okx.com"


@lru_cache(maxsize=4)
def credentials(arn, region, rotation_window):
    value = boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=arn)
    secret = json.loads(value["SecretString"])
    if not all(isinstance(secret.get(k), str) and secret[k] for k in ("api_key", "secret_key", "passphrase")):
        raise Denied("PROVIDER_CONFIGURATION", "Data provider credentials are not configured correctly.", 503)
    return secret


def address(chain, value):
    pattern = r"[1-9A-HJ-NP-Za-km-z]{32,44}" if chain == "501" else r"0x[a-fA-F0-9]{40}"
    if not re.fullmatch(pattern, value):
        raise Denied("INVALID_ADDRESS", "Token contract format does not match the selected network.", 422)
    return value


def request_spec(tool, args):
    chain = args.get("chain")
    token = address(chain, args["token_address"]) if "token_address" in args else None
    pair = [{"chainIndex": chain, "tokenContractAddress": token}]
    query = {"chainIndex": chain, "tokenContractAddress": token}
    if tool == "search_tokens":
        return "GET", "/api/v6/dex/market/token/search", {"chains": chain, "search": args["query"], "limit": "20"}
    if tool in {"get_token_info", "get_token_price"}:
        return "POST", "/api/v6/dex/market/" + ("token/basic-info" if tool == "get_token_info" else "price"), pair
    if tool == "get_candles":
        return "GET", "/api/v6/dex/market/candles", {**query, "bar": args["bar"], "limit": str(args["limit"])}
    if tool == "get_token_holders":
        return "GET", "/api/v6/dex/market/token/holder", {**query, "limit": str(args["limit"])}
    if tool == "scan_token_risk":
        return "POST", "/api/v6/security/token-scan", {
            "source": "onchain_os_cli", "tokenList": [{"chainId": chain, "contractAddress": token}],
        }
    if tool == "get_smart_money_signals":
        return "POST", "/api/v6/dex/market/signal/list", {"chainIndex": chain, "limit": str(args["limit"])}
    if tool == "get_swap_quote":
        source, target = address(chain, args["from_token"]), address(chain, args["to_token"])
        if source == target:
            raise Denied("INVALID_PAIR", "Select different source and destination tokens.", 422)
        return "GET", "/api/v6/dex/aggregator/quote", {
            "chainIndex": chain, "fromTokenAddress": source, "toTokenAddress": target,
            "amount": args["amount_raw"], "swapMode": "exactIn",
        }
    if tool == "get_gas_price":
        return "GET", "/api/v6/dex/pre-transaction/gas-price", {"chainIndex": chain}
    if tool == "get_supported_chains":
        return "GET", "/api/v6/dex/pre-transaction/supported/chain", {}
    raise Denied("TOOL_DENIED", "No reviewed data provider endpoint for this tool.")


def bounded_data(value):
    """Bound persisted provider output; never reinterpret a partial risk response as 'safe'."""
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded.encode()) <= 5500:
        return value, False
    return {"preview": encoded.encode()[:5000].decode(errors="ignore")}, True


def invoke(config, tool, args):
    method, path, parameters = request_spec(tool, args)
    if config.onchain_data_mode == "demo":
        from .demo_data import invoke as demo_invoke
        return demo_invoke(tool, args)
    if not config.data_provider_secret_arn:
        raise Denied("PROVIDER_NOT_CONFIGURED", "尚未配置实时数据凭据，无法查询实时链上数据。", 503)
    try:
        secret = credentials(config.data_provider_secret_arn, config.region, int(time.time()) // 300)
        body = "" if method == "GET" else json.dumps(parameters, separators=(",", ":"), ensure_ascii=False)
        relative = path + ("?" + urlencode(parameters) if method == "GET" and parameters else "")
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        signature = base64.b64encode(hmac.new(
            secret["secret_key"].encode(), (timestamp + method + relative + body).encode(), hashlib.sha256,
        ).digest()).decode()
        headers = {
            "Content-Type": "application/json", "OK-ACCESS-KEY": secret["api_key"],
            "OK-ACCESS-SIGN": signature, "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": secret["passphrase"],
        }
        if secret.get("project_id"):
            headers["OK-ACCESS-PROJECT"] = secret["project_id"]
        # Redirects and provider payment challenges never broaden destination or spending authority.
        with httpx.Client(timeout=12, follow_redirects=False) as client:
            with client.stream(method, BASE_URL + relative, content=body or None, headers=headers) as response:
                if response.status_code == 402:
                    raise Denied("PROVIDER_PAYMENT_REQUIRED", "数据源要求付费；平台未授权自动支付。", 503)
                if response.status_code == 429:
                    raise Denied("PROVIDER_RATE_LIMIT", "实时数据源限流，请稍后重试。", 503)
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 256_000:
                        raise Denied("PROVIDER_RESPONSE_LIMIT", "Provider response exceeds the bounded tool limit.", 503)
                    chunks.append(chunk)
                payload = json.loads(b"".join(chunks))
        if not isinstance(payload, dict) or str(payload.get("code")) != "0" or not isinstance(payload.get("data"), (list, dict)):
            raise Denied("PROVIDER_UNAVAILABLE", "数据源未返回有效数据，无法形成结论。", 503)
        from .interceptors import redact
        data, truncated = bounded_data(redact(payload["data"]))
        return {"provider": "链上数据服务", "source": path, "observed_at": int(time.time()),
                "data_mode": "live", "simulated": False, "read_only": True, "data": data, "truncated": truncated,
                "notice": "数据仅供研究；风险扫描不构成安全保证。"}
    except Denied:
        raise
    except Exception as e:
        # Never return provider bodies, credential values or signed URLs in exceptions.
        raise Denied("PROVIDER_UNAVAILABLE", "实时数据服务暂不可用；请检查凭据、接口权限和服务状态。", 503) from e
