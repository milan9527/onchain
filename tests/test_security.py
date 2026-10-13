import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from onchain.config import Settings
from onchain.domain import AgentSpec, Control, TradeIntent
from onchain.security import Denied, Principal, TokenService
from onchain.store import MemoryStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    config = Settings(local_key_path=str(tmp_path / "key.pem"))
    store = MemoryStore()
    control = Control(store)
    tokens = TokenService(config)
    bundle = config, store, control, tokens
    monkeypatch.setattr("onchain.services.dependencies", lambda: bundle)
    monkeypatch.setattr("onchain.interceptors.dependencies", lambda: bundle)
    monkeypatch.setattr("onchain.api.dependencies", lambda: bundle)
    alice = Principal(sub="alice", tenant="tenant-a", email="alice@example.test")
    bob = Principal(sub="bob", tenant="tenant-b", email="bob@example.test")
    agent = control.create_agent(alice, AgentSpec(name="Alice", max_trade="100", daily_limit="100"))
    return config, store, control, tokens, alice, bob, agent


def test_session_ownership_and_runtime_ids(env):
    _, _, c, _, a, b, agent = env
    s1 = c.create_conversation(a, agent["id"])
    s2 = c.create_conversation(a, agent["id"])
    assert len(s1["runtime_session_id"]) >= 33
    assert s1["runtime_session_id"] != s2["runtime_session_id"]
    with pytest.raises(Denied, match="not available"):
        c.conversation(b, s1["id"])
    with pytest.raises(Denied):
        c.append_message(b, s1["id"], "user", "steal")
    assert c.conversation(a, s1["id"])["messages"] == []


def test_immutable_intent_and_cross_user_approval(env):
    _, _, c, _, a, b, agent = env
    op = c.prepare_trade(a, agent["id"], TradeIntent(amount="20"), "request")
    with pytest.raises(Denied):
        c.approve(b, op["id"], op["id"], op["intent_hash"])
    with pytest.raises(Denied, match="changed"):
        c.approve(a, op["id"], op["id"], "forged")
    with pytest.raises(Denied, match="Re-authenticate"):
        c.approve(a, op["id"], "some-other-op", op["intent_hash"])
    assert c.operation(a, op["id"])["status"] == "PENDING"


def test_replay_executes_once_and_budget_is_atomic(env):
    config, _, c, _, a, _, agent = env
    from onchain.services import quote_eth
    ops = []
    for i in range(2):
        op = c.prepare_trade(a, agent["id"], TradeIntent(amount="60"), str(i))
        c.approve(a, op["id"], op["id"], op["intent_hash"])
        ops.append(op)
    def execute(op):
        try:
            return c.execute(a, op["id"], quote_eth(config))
        except Denied as e:
            return e.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(execute, [ops[0]]*4+[ops[1]]*4))
    assert c.snapshot(a)["wallet"]["USDC"] == "9940"
    assert "DAILY_LIMIT" in results
    assert len([o for o in c.snapshot(a)["operations"] if o["status"] == "CONFIRMED"]) == 1


def test_unapproved_expired_and_cancelled_are_blocked(env):
    config, _, c, _, a, _, agent = env
    from onchain.services import quote_eth
    op = c.prepare_trade(a, agent["id"], TradeIntent(amount="20"), "one")
    with pytest.raises(Denied) as caught:
        c.execute(a, op["id"], quote_eth(config))
    assert caught.value.code == "APPROVAL_REQUIRED"
    c.reject(a, op["id"])
    with pytest.raises(Denied):
        c.execute(a, op["id"], quote_eth(config))


def capability(env):
    _, _, c, tokens, a, _, agent = env
    session = c.create_conversation(a, agent["id"])
    claims = c.capability(a, session["id"], "run-1")
    return claims, tokens.issue("onchain-gateway", claims)


def gateway_event(token, tool, args, session="gw-one"):
    return {"mcp": {"gatewayRequest": {
        "headers": {"Authorization": "Bearer "+token, "Mcp-Session-Id": session},
        "body": {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "onchain___"+tool, "arguments": args}},
    }}}


def test_interceptor_cannot_be_fooled_by_user_parameters(env):
    from onchain.interceptors import request_handler
    claims, token = capability(env)
    result = request_handler(gateway_event(token, "get_portfolio", {"userId": "bob"}), None)
    assert result["mcp"]["transformedGatewayResponse"]["body"]["error"]["data"]["code"] == "INVALID_TOOL_ARGUMENTS"
    result = request_handler(gateway_event(token, "get_portfolio", {}), None)
    signed = result["mcp"]["transformedGatewayRequest"]["body"]["params"]["arguments"]["security_context"]
    checked = env[3].verify(signed, "onchain-tools")
    assert checked["owner"] == claims["owner"]
    assert checked["tool"] == "get_portfolio"


def test_same_user_cannot_reuse_gateway_session_for_another_conversation(env):
    from onchain.interceptors import request_handler
    _, token1 = capability(env)
    _, token2 = capability(env)
    request_handler(gateway_event(token1, "get_portfolio", {}), None)
    result = request_handler(gateway_event(token2, "get_portfolio", {}), None)
    assert result["mcp"]["transformedGatewayResponse"]["body"]["error"]["data"]["code"] == "GATEWAY_SESSION_MISMATCH"


def test_pause_revokes_capabilities_and_pending_approvals(env):
    _, _, c, _, a, _, agent = env
    claims, _ = capability(env)
    op = c.prepare_trade(a, agent["id"], TradeIntent(amount="20"), "one")
    c.approve(a, op["id"], op["id"], op["intent_hash"])
    c.set_agent_status(a, agent["id"], "PAUSED")
    assert c.operation(a, op["id"])["status"] == "CANCELLED"
    with pytest.raises(Denied):
        c.validate_capability(claims)
    c.set_agent_status(a, agent["id"], "ACTIVE")
    with pytest.raises(Denied) as caught:
        c.validate_capability(claims)
    assert caught.value.code == "CAPABILITY_REVOKED"


def test_subscription_and_skill_enforced_on_call_not_just_listing(env):
    _, _, c, _, a, _, agent = env
    claims, _ = capability(env)
    c.subscribe(a, "portfolio", False)
    with pytest.raises(Denied) as caught:
        c.authorize_tool(claims, "get_portfolio", {})
    assert caught.value.code == "NOT_SUBSCRIBED"
    reader = c.create_agent(a, AgentSpec(name="reader", skill_id="portfolio-assistant"))
    session = c.create_conversation(a, reader["id"])
    claims = c.capability(a, session["id"], "reader-run")
    with pytest.raises(Denied) as caught:
        c.authorize_tool(claims, "prepare_trade", TradeIntent(amount="20").model_dump())
    assert caught.value.code == "SKILL_TOOL_DENIED"


def test_no_implicit_permission_from_model_claims(env):
    _, _, c, tokens, _, _, _ = env
    claims, _ = capability(env)
    forged = tokens.issue("onchain-runtime", claims)
    with pytest.raises(Denied):
        tokens.verify(forged, "onchain-gateway")
    tampered = copy.deepcopy(claims)
    tampered["tenant"] = "victim"
    with pytest.raises(Denied):
        c.validate_capability(tampered)


def test_revoked_grant_cannot_execute(env):
    config, _, c, _, a, _, agent = env
    from onchain.services import quote_eth
    grant = c.prepare_grant(a, agent["id"], TradeIntent(amount="20"), "100", 300, 24)
    c.approve(a, grant["id"], grant["id"], grant["intent_hash"])
    op = c.prepare_trade(a, agent["id"], TradeIntent(amount="20"), "schedule-slot", grant["id"])
    c.revoke_grant(a, grant["id"])
    with pytest.raises(Denied) as caught:
        c.execute(a, op["id"], quote_eth(config))
    assert caught.value.code == "GRANT_INVALID"


def test_executor_rejects_argument_substitution(env):
    from onchain.interceptors import request_handler
    from onchain.services import executor_handler
    _, token = capability(env)
    response = request_handler(gateway_event(token, "get_portfolio", {}), None)
    signed = response["mcp"]["transformedGatewayRequest"]["body"]["params"]["arguments"]["security_context"]
    result = executor_handler({"security_context": signed, "tool": "get_history", "arguments": {}}, None)
    assert result["error"]["code"] == "ARGUMENTS_CHANGED"


def test_api_cookie_csrf_and_cross_account_isolation(env):
    from onchain.api import app
    with TestClient(app) as a, TestClient(app) as b:
        la = a.post("/api/auth/login", json={"email": "alice@example.test", "password": "local-only-change-me"}).json()
        lb = b.post("/api/auth/login", json={"email": "bob@example.test", "password": "local-only-change-me"}).json()
        assert a.post("/api/agents", json={"name": "blocked"}).status_code == 403
        ha, hb = {"X-CSRF-Token": la["csrf"]}, {"X-CSRF-Token": lb["csrf"]}
        agent = a.post("/api/agents", headers=ha, json={"name": "isolated"}).json()
        session = a.post(f"/api/agents/{agent['id']}/conversations", headers=ha).json()
        assert b.get(f"/api/conversations/{session['id']}").status_code == 404
        assert b.post(f"/api/agents/{agent['id']}/status", headers=hb, json={"status": "PAUSED"}).status_code == 404
        assert a.post("/api/agents", headers={**ha, "Origin": "https://evil.test"}, json={"name": "bad"}).status_code == 403
        assert a.post("/api/auth/logout", headers=ha).status_code == 200
        assert a.get("/api/dashboard").status_code == 401


def test_stepup_is_single_use_and_bound_to_operation(env):
    from onchain.api import app
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"email": "alice@example.test", "password": "local-only-change-me"}).json()
        headers = {"X-CSRF-Token": login["csrf"]}
        agent = client.post("/api/agents", headers=headers, json={"name": "approval"}).json()
        op = client.post("/api/operations/prepare", headers=headers,
                         json={"agent_id": agent["id"], "intent": {"amount": "20"}}).json()
        assert client.post(f"/api/operations/{op['id']}/approve", headers=headers,
                           json={"intent_hash": op["intent_hash"]}).status_code == 401
        assert client.post(f"/api/operations/{op['id']}/stepup", headers=headers,
                           json={"email": "bob@example.test", "password": "local-only-change-me"}).status_code == 403
        assert client.post(f"/api/operations/{op['id']}/stepup", headers=headers,
                           json={"email": "alice@example.test", "password": "local-only-change-me"}).status_code == 200
        assert client.post(f"/api/operations/{op['id']}/approve", headers=headers,
                           json={"intent_hash": op["intent_hash"]}).status_code == 200
        assert client.post(f"/api/operations/{op['id']}/approve", headers=headers,
                           json={"intent_hash": op["intent_hash"]}).status_code == 401


def test_cognito_mfa_access_token_flow_does_not_require_session(env, monkeypatch):
    from onchain.api import app
    from onchain.auth import Auth
    config, store, _, _, principal, _, _ = env
    cookie, record = Auth(config, store).new_session(principal)
    config.environment = "production"
    config.public_url = "https://testserver"
    calls = []

    class Cognito:
        def initiate_auth(self, **kwargs):
            return {"AuthenticationResult": {"AccessToken": "test-access"}}

        def get_user(self, **kwargs):
            return {"UserAttributes": [{"Name": "sub", "Value": principal.sub}]}

        def associate_software_token(self, **kwargs):
            # Cognito omits Session when enrollment is authorized by an access token.
            return {"SecretCode": "test-secret"}

        def verify_software_token(self, **kwargs):
            calls.append(kwargs)
            return {"Status": "SUCCESS"}

        def set_user_mfa_preference(self, **kwargs):
            return {}

    monkeypatch.setattr(Auth, "client", lambda self: Cognito())
    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set("onchain_session", cookie)
        headers = {"X-CSRF-Token": record["csrf"]}
        enrolled = client.post("/api/auth/mfa/enroll", headers=headers,
                              json={"email": principal.email, "password": "fresh-password"})
        assert enrolled.status_code == 200
        verified = client.post("/api/auth/mfa/verify", headers=headers, json={
            "enrollment_id": enrolled.json()["enrollment_id"], "code": "123456",
        })
        assert verified.status_code == 200
        assert calls == [{"AccessToken": "test-access", "UserCode": "123456"}]


def test_limits_reject_nan_negative_and_precision():
    from onchain.domain import amount_units
    for amount in ["NaN", "Infinity", "-1", "0", "0.0000001", "1e9999"]:
        with pytest.raises(Denied):
            amount_units(amount)


def test_sensitive_response_redaction():
    from onchain.interceptors import redact
    value = redact({"content": [{"text": '{"private_key":"secret","amount":"20"}'}]})
    assert "secret" not in str(value)
    assert "20" in str(value)


def test_microvm_rejects_another_user_or_conversation():
    from runtimes.isolation import SessionBinding
    binding = SessionBinding()
    first = {"owner": "alice", "sid": "conversation-1", "runtime_sid": "runtime-1"}
    binding.check(first)
    binding.check(dict(first))
    with pytest.raises(Denied):
        binding.check({**first, "owner": "bob"})
    with pytest.raises(Denied):
        binding.check({**first, "sid": "conversation-2"})


def test_gateway_blocks_non_tool_methods_and_unknown_targets(env):
    from onchain.interceptors import request_handler
    _, token = capability(env)
    event = gateway_event(token, "get_portfolio", {})
    event["mcp"]["gatewayRequest"]["body"]["method"] = "resources/read"
    result = request_handler(event, None)
    assert result["mcp"]["transformedGatewayResponse"]["body"]["error"]["data"]["code"] == "MCP_METHOD_DENIED"
    event = gateway_event(token, "get_portfolio", {})
    event["mcp"]["gatewayRequest"]["body"]["params"]["name"] = "unreviewed___get_portfolio"
    result = request_handler(event, None)
    assert result["mcp"]["transformedGatewayResponse"]["body"]["error"]["data"]["code"] == "TARGET_DENIED"


def test_dependency_failure_is_fail_closed(env, monkeypatch):
    from onchain.interceptors import request_handler
    _, token = capability(env)
    monkeypatch.setattr(env[1], "get", lambda _: (_ for _ in ()).throw(RuntimeError("database down")))
    result = request_handler(gateway_event(token, "get_portfolio", {}), None)
    assert "transformedGatewayRequest" not in result["mcp"]
    assert result["mcp"]["transformedGatewayResponse"]["body"]["error"]["data"]["code"] == "CONTROL_UNAVAILABLE"


def test_three_malformed_calls_quarantine_agent(env):
    from onchain.interceptors import request_handler
    _, _, c, _, a, _, agent = env
    _, token = capability(env)
    for _ in range(3):
        request_handler(gateway_event(token, "get_portfolio", {"userId": "victim"}), None)
    assert c.agent(a, agent["id"], active=False)["status"] == "PAUSED"


def test_logout_invalidates_already_issued_runtime_capability(env):
    from onchain.auth import Auth
    config, store, c, _, a, _, agent = env
    auth = Auth(config, store)
    cookie, _ = auth.new_session(a)
    key, _ = auth.record(cookie)
    session = c.create_conversation(a, agent["id"], key)
    claims = c.capability(a, session["id"], "running-before-logout")
    auth.logout(cookie)
    with pytest.raises(Denied) as caught:
        c.validate_capability(claims)
    assert caught.value.code == "SESSION_REVOKED"


def test_concurrent_auto_trades_cannot_exceed_grant_total(env):
    config, _, c, _, a, _, agent = env
    from onchain.services import quote_eth
    g = c.prepare_grant(a, agent["id"], TradeIntent(amount="20"), "20", 300, 24)
    c.approve(a, g["id"], g["id"], g["intent_hash"])
    ops = [c.prepare_trade(a, agent["id"], TradeIntent(amount="20"), f"slot-{n}", g["id"]) for n in range(2)]
    def run(op):
        try:
            return c.execute(a, op["id"], quote_eth(config))["status"]
        except Denied as e:
            return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ops))
    assert sorted(results) == ["CONFIRMED", "GRANT_INVALID"]
    assert c.ensure(a)["grants"][g["id"]]["used_units"] == 20_000_000


def test_validation_error_does_not_echo_password(env):
    from onchain.api import app
    with TestClient(app) as client:
        secret = "do-not-reflect-this-password"
        response = client.post("/api/auth/login", json={"email": "a", "password": secret})
        assert response.status_code == 422
        assert secret not in response.text
