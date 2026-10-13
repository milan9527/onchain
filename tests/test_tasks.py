import asyncio
import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from onchain.catalog import PRODUCTS, SKILLS, skill_by_id
from onchain.config import Settings
from onchain.domain import AgentSpec, Control
from onchain.extensions import EXTRA_SCHEMAS, LIVE_TOOLS
from onchain.oauth import TOOL_SCOPES
from onchain.security import Denied, Principal, TokenService, digest
from onchain.services import execute_tool
from onchain.store import MemoryStore
from onchain.tasks import TASKS, task_by_id, task_plan


@pytest.fixture
def env(tmp_path):
    config = Settings(local_key_path=str(tmp_path / "key.pem"))
    control = Control(MemoryStore())
    p = Principal(sub="template-user", tenant="tenant-a")
    return config, control, p


def agent_claims(env, template, enabled=True):
    config, control, p = env
    agent = control.create_agent(p, AgentSpec(name=template, template_id=template, enable_template_products=enabled))
    session = control.create_conversation(p, agent["id"])
    return agent, session, control.capability(p, session["id"], "task-run")


def test_catalog_size_and_legacy_skill_versions_do_not_expand():
    assert len(PRODUCTS) == 8
    assert len(SKILLS) == 10
    assert len(TASKS) == 15
    assert len(EXTRA_SCHEMAS) == 12
    original = {
        "id": "controlled-trader", "name": "受控交易", "version": "1.0.0",
        "description": "准备交易意图，只有有效审批或持续授权才能执行。",
        "tools": ["get_quote", "get_portfolio", "get_history", "prepare_trade", "execute_trade", "get_operation"],
    }
    assert skill_by_id("controlled-trader")["digest"] == digest(original)
    assert not set(EXTRA_SCHEMAS) & set(skill_by_id("controlled-trader")["tools"])
    for task in TASKS:
        assert set(task["tools"]) <= set(skill_by_id(task["skill_id"])["tools"])
        if task["mode"] == "read":
            assert not set(task["tools"]) & {"prepare_trade", "execute_trade"}


def test_template_requires_explicit_product_enablement_and_scope(env):
    _, control, p = env
    agent, session, claims = agent_claims(env, "portfolio-health", enabled=False)
    with pytest.raises(Denied) as result:
        control.authorize_tool(claims, "analyze_portfolio", {})
    assert result.value.code == "NOT_SUBSCRIBED"
    control.subscribe(p, "account-insights", True)
    control.authorize_tool(claims, "analyze_portfolio", {})
    agent, session, claims = agent_claims(env, "token-risk")
    args = {"chain": "8453", "token_address": "0x" + "a"*40}
    claims["oauth_scopes"] = ["onchain/portfolio.read"]
    with pytest.raises(Denied) as result:
        control.authorize_tool(claims, "scan_token_risk", args)
    assert result.value.code == "INSUFFICIENT_SCOPE"
    claims["oauth_scopes"].append("onchain/market.read")
    control.authorize_tool(claims, "scan_token_risk", args)
    with pytest.raises(Denied):
        control.authorize_tool(claims, "get_smart_money_signals", {"chain": "8453", "limit": 2})
    assert TOOL_SCOPES["scan_token_risk"] == "onchain/market.read"


def test_insights_are_owner_bound_and_preflight_never_approves(env):
    config, control, p = env
    agent, _, claims = agent_claims(env, "trade-preflight")
    result = execute_tool(control, config, claims, "preview_trade", {"amount": "101"})
    assert result["within_limits"] is False
    assert result["approved"] is False
    assert control.snapshot(p)["operations"] == []
    with pytest.raises(Denied):
        execute_tool(control, config, claims, "preview_trade", {"amount": "1", "owner": "victim"})
    _, _, auditor = agent_claims(env, "portfolio-health")
    result = execute_tool(control, config, auditor, "analyze_portfolio", {})
    assert result["total_value_usdc"] == "10000"
    assert "参考价" in result["source"]
    forged = {**auditor, "sub": "someone-else"}
    with pytest.raises(Denied):
        execute_tool(control, config, forged, "analyze_portfolio", {})


def test_task_arguments_digest_and_stop_after_failure(env):
    from runtimes.task_runner import run_template
    task = task_by_id("token-research")
    spec = {"template_id": task["id"], "template_digest": task["digest"],
            "parameters": {"chain": "8453", "token_address": "0x" + "a"*40}}
    calls = []
    async def call(tool, args):
        calls.append(tool)
        return {"error": {"code": "OKX_NOT_CONFIGURED"}} if len(calls) == 2 else {"data": []}
    async def progress(**event):
        pass
    result = asyncio.run(run_template(spec, {"task_hash": digest(spec)}, call, progress))
    assert calls == ["get_token_info", "get_token_price"]
    assert result["tool_result"]["error"]["code"] == "OKX_NOT_CONFIGURED"
    with pytest.raises(Denied):
        asyncio.run(run_template(spec, {"task_hash": "forged"}, call, progress))
    with pytest.raises(Denied):
        task_plan(task["id"], {**spec["parameters"], "tool": "execute_trade"})
    with pytest.raises(Denied):
        task_plan("eth-dca", {})
    with pytest.raises(Denied):
        task_plan(task["id"], spec["parameters"], "old-version")


def test_live_tools_never_fabricate_data_when_unconfigured(env):
    from onchain.onchain_os import invoke
    with pytest.raises(Denied) as result:
        invoke(env[0].model_copy(update={"onchain_data_mode": "live"}), "get_token_price", {"chain": "8453", "token_address": "0x" + "a"*40})
    assert result.value.code == "OKX_NOT_CONFIGURED"
    assert len(LIVE_TOOLS) == 10


def test_okx_adapter_exact_signature_endpoint_parameters_and_bounded_data(env, monkeypatch):
    from onchain import onchain_os
    config = env[0].model_copy(update={"okx_secret_arn": "test-secret", "onchain_data_mode": "live"})
    secrets = {"api_key": "test-key", "secret_key": "test-secret", "passphrase": "test-passphrase"}
    monkeypatch.setattr(onchain_os, "credentials", lambda *_: secrets)
    actual_client = httpx.Client
    captured = []
    def handle(request):
        captured.append(request)
        relative = request.url.raw_path.decode()
        signature = base64.b64encode(hmac.new(
            secrets["secret_key"].encode(),
            (request.headers["OK-ACCESS-TIMESTAMP"] + request.method + relative + request.content.decode()).encode(),
            hashlib.sha256,
        ).digest()).decode()
        assert request.headers["OK-ACCESS-SIGN"] == signature
        assert request.url.host == "web3.okx.com"
        return httpx.Response(200, json={"code": "0", "data": [{"symbol": "ETH"}]})
    monkeypatch.setattr(onchain_os.httpx, "Client", lambda **kwargs: actual_client(transport=httpx.MockTransport(handle), **kwargs))
    result = onchain_os.invoke(config, "search_tokens", {"chain": "8453", "query": "A B&?"})
    assert captured[0].url.params["search"] == "A B&?"
    assert result["simulated"] is False
    assert result["provider"] == "OKX Onchain OS"
    onchain_os.invoke(config, "scan_token_risk", {"chain": "8453", "token_address": "0x" + "a"*40})
    body = json.loads(captured[1].content)
    assert body == {"source": "onchain_os_cli", "tokenList": [{"chainId": "8453", "contractAddress": "0x"+"a"*40}]}
    with pytest.raises(Denied):
        onchain_os.invoke(config, "get_token_info", {"chain": "501", "token_address": "0x"+"a"*40})
    assert len(captured) == 2
    data, truncated = onchain_os.bounded_data(["x" * 7000])
    assert truncated and len(json.dumps(data).encode()) < 6000


@pytest.mark.parametrize("status,code", [(402, "PROVIDER_PAYMENT_REQUIRED"), (429, "PROVIDER_RATE_LIMIT"),
                                        (302, "PROVIDER_UNAVAILABLE"), (500, "PROVIDER_UNAVAILABLE")])
def test_provider_challenges_do_not_pay_redirect_or_leak_errors(env, monkeypatch, status, code):
    from onchain import onchain_os
    monkeypatch.setattr(onchain_os, "credentials", lambda *_: {"api_key": "secret", "secret_key": "secret", "passphrase": "secret"})
    client = httpx.Client
    calls = []
    def handle(req):
        calls.append(req)
        return httpx.Response(status, text="secret-error-body", headers={"location": "https://untrusted.invalid"})
    monkeypatch.setattr(onchain_os.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(Denied) as result:
        onchain_os.invoke(env[0].model_copy(update={"okx_secret_arn": "test", "onchain_data_mode": "live"}), "get_gas_price", {"chain": "8453"})
    assert result.value.code == code
    assert "secret" not in result.value.message
    assert len(calls) == 1


def test_task_api_preserves_login_and_owner_isolation(env, monkeypatch):
    from onchain.api import app
    import onchain.api
    config, control, _ = env
    tokens = TokenService(config)
    monkeypatch.setattr(onchain.api, "dependencies", lambda: (config, control.store, control, tokens))
    queued = []
    async def enqueue(*args, **kwargs):
        queued.append((args, kwargs))
        return {"status": "QUEUED"}
    monkeypatch.setattr(onchain.api, "enqueue", enqueue)
    with TestClient(app) as buyer, TestClient(app) as stranger:
        for client, email in [(buyer, "alice@example.test"), (stranger, "bob@example.test")]:
            response = client.post("/api/auth/login", json={"email": email, "password": "local-only-change-me"}).json()
            client.headers["X-CSRF-Token"] = response["csrf"]
        agent = buyer.post("/api/agents", json={"name": "Audit", "template_id": "portfolio-health",
                                              "enable_template_products": True}).json()
        session = buyer.post(f"/api/agents/{agent['id']}/conversations").json()
        task = task_by_id("portfolio-health")
        body = {"template_id": task["id"], "template_digest": task["digest"], "parameters": {}}
        url = f"/api/conversations/{session['id']}/tasks"
        assert stranger.post(url, json=body).status_code == 404
        assert buyer.post(url, json=body).status_code == 202
        assert len(queued) == 1
        buyer.post("/api/auth/logout")
        login = buyer.post("/api/auth/login", json={"email": "alice@example.test", "password": "local-only-change-me"}).json()
        buyer.headers["X-CSRF-Token"] = login["csrf"]
        assert buyer.post(url, json=body).status_code in {401, 409}
        assert len(queued) == 1


def test_mcp_publishes_every_reviewed_extension():
    from runtimes.mcp_server import mcp
    tools = asyncio.run(mcp.list_tools())
    assert set(EXTRA_SCHEMAS) <= {t.name for t in tools}
