import asyncio
import json

import pytest
from pydantic import ValidationError

from onchain import onchain_os
from onchain.config import Settings
from onchain.demo_data import DATASET, example_parameters, tokens
from onchain.domain import AgentSpec, Control
from onchain.extensions import CHAINS, LIVE_TOOLS
from onchain.security import Denied, Principal, digest
from onchain.services import execute_tool, quote_eth
from onchain.store import MemoryStore
from onchain.tasks import TASKS, task_catalog, task_plan
from runtimes.task_runner import run_template


def test_explicit_mode_and_catalog_do_not_change_audited_versions():
    demo = task_catalog(Settings())
    live = task_catalog(Settings(onchain_data_mode="live"))
    assert all(t["availability"] == "READY" for t in demo)
    assert sum(t["availability"] == "REQUIRES_DATA_PROVIDER" for t in live) == 12
    assert [t["digest"] for t in demo] == [t["digest"] for t in live] == [t["digest"] for t in TASKS]
    assert not any("examples" in t for t in live)
    with pytest.raises(ValidationError):
        Settings(onchain_data_mode="auto")


@pytest.mark.parametrize("chain", list(CHAINS))
def test_every_demo_template_runs_signed_plan_with_normal_authorization_without_network(chain, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Demo must not access credentials or external HTTP")
    monkeypatch.setattr(onchain_os, "credentials", forbidden)
    monkeypatch.setattr(onchain_os.httpx, "Client", forbidden)
    config = Settings(onchain_data_mode="demo", data_provider_secret_arn="ignored-in-demo")
    control = Control(MemoryStore())
    owner = Principal(sub="demo-user", tenant="demo-user", auth_key="demo-login")
    seen = set()
    for task in task_catalog(config):
        if task["data_mode"] != "demo":
            continue
        agent = control.create_agent(owner, AgentSpec(
            name=task["id"], template_id=task["id"], enable_template_products=True,
        ))
        session = control.create_conversation(owner, agent["id"])
        claims = control.capability(owner, session["id"], "demo-task")
        spec = {"template_id": task["id"], "template_digest": task["digest"], "parameters": task["examples"][chain]}
        task_plan(task["id"], spec["parameters"], task["digest"])
        async def call(tool, args, claims=claims):
            seen.add(tool)
            result = execute_tool(control, config, claims, tool, args)
            assert result["simulated"] is True
            assert result["dataset"] == DATASET
            assert result["provider"] != "链上数据服务"
            assert result["data"]  # Every provided example actually returns useful data.
            return result
        async def progress(**_event):
            pass
        report = asyncio.run(run_template(spec, {**claims, "task_hash": digest(spec)}, call, progress))
        assert all(s["status"] == "OK" and s["result"]["simulated"] for s in report["tool_result"]["steps"])
        # Labels must remain even when the bounded task report truncates provider data.
        assert all("非实时行情" in s["result"]["notice"] for s in report["tool_result"]["steps"])
    assert seen == LIVE_TOOLS
    assert control.snapshot(owner)["operations"] == []


def test_quotes_prices_candles_risk_and_unknown_contracts_are_consistent():
    config = Settings()
    native, stable, risk = tokens("8453")
    result = onchain_os.invoke(config, "get_swap_quote", example_parameters("swap-route"))
    assert result["data"]["fromTokenAmount"] == "20000000"
    assert result["data"]["toTokenAmount"] == "6666666666666666"
    assert result["data"]["executable"] is False
    assert "tx" not in result["data"]
    candles = onchain_os.invoke(config, "get_candles", example_parameters("candle-analysis"))["data"]
    price = onchain_os.invoke(config, "get_token_price", example_parameters("token-research"))["data"][0]["price"]
    assert candles[0][4] == price
    for asset, level in [(native, "UNKNOWN"), (risk, "HIGH"),
                         ({"tokenContractAddress": "0x" + "a" * 40}, "UNKNOWN")]:
        args = {"chain": "8453", "token_address": asset["tokenContractAddress"]}
        response = onchain_os.invoke(config, "scan_token_risk", args)["data"][0]
        assert response["riskLevel"] == level
        assert response["safeToTrade"] is False
    unknown = {"chain": "8453", "token_address": "0x" + "a" * 40}
    assert onchain_os.invoke(config, "get_token_price", unknown)["data"] == []
    assert onchain_os.invoke(config, "get_token_info", unknown)["data"][0]["status"] == "UNKNOWN"
    missing_route = {**example_parameters("swap-route"), "to_token": risk["tokenContractAddress"]}
    assert onchain_os.invoke(config, "get_swap_quote", missing_route)["data"]["status"] == "NO_ROUTE"
    assert stable["price"] == "1"


def test_demo_cannot_bypass_owner_scope_subscription_or_skill():
    control, config = Control(MemoryStore()), Settings()
    owner = Principal(sub="owner", tenant="owner")
    agent = control.create_agent(owner, AgentSpec(name="Research", template_id="token-research"))
    session = control.create_conversation(owner, agent["id"])
    claims = control.capability(owner, session["id"], "demo-run")
    args = example_parameters("token-research")
    with pytest.raises(Denied) as e:
        execute_tool(control, config, claims, "get_token_info", args)
    assert e.value.code == "NOT_SUBSCRIBED"
    control.subscribe(owner, "token-data", True)
    for invalid in [{**claims, "sub": "stranger"}, {**claims, "oauth_scopes": ["onchain/portfolio.read"]}]:
        with pytest.raises(Denied):
            execute_tool(control, config, invalid, "get_token_info", args)
    with pytest.raises(Denied):
        execute_tool(control, config, claims, "execute_trade", {"operation_id": "anything"})
    with pytest.raises(Denied):
        execute_tool(control, config, claims, "get_token_info", {**args, "data_mode": "live"})
    with pytest.raises(Denied):
        onchain_os.invoke(config, "get_token_info", {"chain": "501", "token_address": args["token_address"]})


def test_task_truncation_preserves_demo_disclosure():
    task = next(t for t in task_catalog(Settings()) if t["id"] == "token-research")
    spec = {"template_id": task["id"], "template_digest": task["digest"], "parameters": task["examples"]["8453"]}
    async def call(_tool, _args):
        return {"simulated": True, "source": "平台演示数据", "notice": "非实时行情",
                "data_mode": "demo", "data": ["x" * 8000]}
    async def progress(**_event):
        pass
    report = asyncio.run(run_template(spec, {"task_hash": digest(spec)}, call, progress))
    for step in report["tool_result"]["steps"]:
        assert step["result"]["simulated"] is True
        assert step["result"]["truncated"] is True
    assert len(json.dumps(report).encode()) < 8000


def test_trading_quote_respects_explicit_demo_mode_even_if_a_secret_is_retained(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("A retained live secret must not be used in demo mode")
    monkeypatch.setattr("onchain.services.boto3.client", forbidden)
    monkeypatch.setattr("onchain.services.httpx.post", forbidden)
    quote = quote_eth(Settings(data_provider_secret_arn="retained-secret"))
    assert quote["simulated"] is True and quote["price"] == 3000
    with pytest.raises(Denied) as error:
        quote_eth(Settings(onchain_data_mode="live"))
    assert error.value.code == "PROVIDER_NOT_CONFIGURED"


def test_provider_configuration_preserves_legacy_deployments_and_prefers_new_variable(monkeypatch):
    from onchain.config import settings
    assert Settings(okx_secret_arn="legacy-secret").data_provider_secret_arn == "legacy-secret"
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("OKX_SECRET_ARN", "legacy-secret")
    monkeypatch.delenv("DATA_PROVIDER_SECRET_ARN", raising=False)
    try:
        settings.cache_clear()
        assert settings().data_provider_secret_arn == "legacy-secret"
        monkeypatch.setenv("DATA_PROVIDER_SECRET_ARN", "new-secret")
        settings.cache_clear()
        assert settings().data_provider_secret_arn == "new-secret"
        monkeypatch.setenv("DATA_PROVIDER_SECRET_ARN", "")
        settings.cache_clear()
        assert settings().data_provider_secret_arn == ""
    finally:
        settings.cache_clear()
