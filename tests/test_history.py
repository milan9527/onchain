import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from onchain.auth import Auth
from onchain.config import Settings
from onchain.domain import AgentSpec, Control
from onchain.history import safe_details
from onchain.security import Denied, Principal, TokenService
from onchain.store import MemoryStore


@pytest.fixture
def history_env(tmp_path, monkeypatch):
    config = Settings(local_key_path=str(tmp_path / "key.pem"))
    store = MemoryStore()
    control = Control(store)
    tokens = TokenService(config)
    dependencies = lambda: (config, store, control, tokens)  # noqa: E731
    for module in ["api", "worker", "services", "interceptors"]:
        monkeypatch.setattr(f"onchain.{module}.dependencies", dependencies)
    alice = Principal(sub="alice", tenant="a")
    bob = Principal(sub="bob", tenant="b")
    agent = control.create_agent(alice, AgentSpec(name="history"))
    return config, store, control, tokens, alice, bob, agent


def test_history_scoped_to_user_and_agent_and_safe_after_logout(history_env):
    config, store, control, _, alice, bob, agent = history_env
    auth = Auth(config, store)
    cookie, _ = auth.new_session(alice)
    key, _ = auth.record(cookie)
    session = control.create_conversation(alice, agent["id"], key)
    control.append_message(alice, session["id"], "user", "查看我的持仓")
    control.start_run(alice, session["id"], "r1", "chat")
    control.trace(alice, session["id"], "r1", "TOOL_CALL", "mcp", "get_portfolio", {"tool": "get_portfolio"})
    auth.logout(cookie)
    history = control.public_conversation(alice, session["id"], "new-login")
    assert not history["can_resume"]
    assert history["title"] == "查看我的持仓"
    assert len(history["messages"]) == 1
    assert not {"auth_key", "owner", "runtime_session_id", "lease"} & history.keys()
    assert control.run_record(alice, session["id"], "r1")["events"][-1]["kind"] == "TOOL_CALL"
    with pytest.raises(Denied):
        control.capability(alice, session["id"], "new-run")
    with pytest.raises(Denied):
        control.list_conversations(bob, agent["id"])
    with pytest.raises(Denied):
        control.run_record(bob, session["id"], "r1")
    second_agent = control.create_agent(alice, AgentSpec(name="separate"))
    assert control.list_conversations(alice, second_agent["id"]) == []


def test_events_preserve_concurrent_producers_and_redact_nested_credentials(history_env):
    _, _, control, _, alice, _, agent = history_env
    session = control.create_conversation(alice, agent["id"])
    sid = session["id"]
    control.start_run(alice, sid, "r1", "chat")
    def write(index):
        control.trace(alice, sid, "r1", "TOOL_RESULT", "executor", str(index), {
            "password": "secret-password", "arguments": {"amount": "20", "security_context": "secret-capability"},
            "text": '{"access_token":"secret-token","ok":true}',
        })
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(20)))
    control.run_status(alice, sid, "r1", "SUCCEEDED")
    run = control.run_record(alice, sid, "r1")
    assert len(run["events"]) == 21
    serialized = json.dumps(run)
    assert "secret-password" not in serialized and "secret-token" not in serialized
    assert "secret-capability" not in serialized and "amount" in serialized
    assert run["duration_ms"] >= 0 and run["status"] == "SUCCEEDED"
    assert len(json.dumps(safe_details({"data": ["中"*10000]*20})).encode()) < 25000


def test_history_api_does_not_restore_login_bound_execution(history_env):
    from onchain.api import app
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"email": "alice@example.test", "password": "local-only-change-me"}).json()
        headers = {"X-CSRF-Token": login["csrf"]}
        agent = client.post("/api/agents", headers=headers, json={"name": "api-history"}).json()
        session = client.post(f"/api/agents/{agent['id']}/conversations", headers=headers).json()
        assert session["can_resume"] and "auth_key" not in session
        client.post("/api/auth/logout", headers=headers)
        login = client.post("/api/auth/login", json={"email": "alice@example.test", "password": "local-only-change-me"}).json()
        headers = {"X-CSRF-Token": login["csrf"]}
        assert not client.get(f"/api/conversations/{session['id']}").json()["can_resume"]
        assert len(client.get(f"/api/agents/{agent['id']}/conversations").json()["conversations"]) == 1
        assert client.post(f"/api/conversations/{session['id']}/messages", headers=headers,
                           json={"message": "reuse old session"}).status_code == 401


def test_streamed_runtime_emits_real_steps_and_worker_persists_tools(history_env, monkeypatch):
    from runtimes.agent import app, binding
    from onchain import worker
    config, _, control, tokens, alice, _, agent = history_env
    session = control.create_conversation(alice, agent["id"])
    sid, run_id = session["id"], "run-history"
    claims = control.capability(alice, sid, run_id)
    intent = {"intent": {"action": "quote", "explanation": "查询 ETH 报价", "trade": None}}
    monkeypatch.setattr("runtimes.agent.settings", lambda: config)
    monkeypatch.setattr("runtimes.agent.recognize", lambda *_: intent.copy())
    monkeypatch.setattr(binding, "identity", None)
    async def gateway(_url, _token, tool, args, progress):
        await progress(kind="TOOL_CALL", source="mcp", label="调用 "+tool, details={"arguments": args})
        return {"price": 3000, "simulated": True}
    monkeypatch.setattr("runtimes.agent.call_gateway", gateway)
    with TestClient(app) as runtime:
        response = runtime.post("/invocations", headers={
            "Authorization": "Bearer "+tokens.issue("onchain-runtime", claims),
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": claims["runtime_sid"],
        }, json={"runtime_session_id": claims["runtime_sid"], "run_id": run_id, "message": "查询 ETH",
                 "history": [], "gateway_token": "not-persisted"})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [e["kind"] for e in events] == ["MODEL_STARTED", "INTENT_RECOGNIZED", "TOOL_CALL", "RUN_RESULT"]
    monkeypatch.setattr("onchain.reasoning.recognize", lambda *_: intent.copy())
    control.lease_conversation(alice, sid, run_id)
    asyncio.run(worker.process({
        "kind": "chat", "principal": alice.model_dump(), "sid": sid, "run_id": run_id, "message": "查询 ETH",
        "runtime_token": tokens.issue("onchain-runtime", claims), "gateway_token": tokens.issue("onchain-gateway", claims),
    }))
    run = control.run_record(alice, sid, run_id)
    assert run["status"] == "SUCCEEDED"
    assert {"MODEL_STARTED", "INTENT_RECOGNIZED", "TOOL_CALL", "GATEWAY_ALLOWED", "TOOL_RESULT"} <= {
        e["kind"] for e in run["events"]
    }


def test_read_retention_is_independent_of_execution_expiry(history_env):
    _, store, control, _, alice, _, agent = history_env
    session = control.create_conversation(alice, agent["id"])
    session["active_until"] = time.time()-1
    store.put("SESSION#"+session["id"], session)
    assert control.history_session(alice, session["id"])
    with pytest.raises(Denied):
        control.conversation(alice, session["id"])


def test_blocked_tool_is_visible_as_a_blocked_run(history_env, monkeypatch):
    from onchain import worker
    _, _, control, tokens, alice, _, agent = history_env
    session = control.create_conversation(alice, agent["id"])
    claims = control.capability(alice, session["id"], "blocked-run")
    control.subscribe(alice, "market", False)
    monkeypatch.setattr("onchain.reasoning.recognize", lambda *_: {
        "intent": {"action": "quote", "explanation": "查询 ETH", "trade": None},
    })
    asyncio.run(worker.process({
        "kind": "chat", "principal": alice.model_dump(), "sid": session["id"], "run_id": "blocked-run",
        "message": "查询 ETH", "runtime_token": tokens.issue("onchain-runtime", claims),
        "gateway_token": tokens.issue("onchain-gateway", claims),
    }))
    run = control.run_record(alice, session["id"], "blocked-run")
    assert run["status"] == "BLOCKED"
    assert any(event["kind"] == "GATEWAY_BLOCKED" and event["details"]["code"] == "NOT_SUBSCRIBED"
               for event in run["events"])
