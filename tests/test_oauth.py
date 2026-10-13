import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi.testclient import TestClient

from onchain.auth import Auth, COOKIE
from onchain.config import Settings
from onchain.domain import AgentSpec, Control, TradeIntent
from onchain.oauth import CognitoOAuth, CONTROL, APPROVE, INVOKE, TOOL_SCOPES, FLOW_COOKIE
from onchain.security import Denied, Principal, TokenService
from onchain.store import MemoryStore


@pytest.fixture
def oauth_env(tmp_path, monkeypatch):
    config = Settings(local_key_path=str(tmp_path / "key.pem"), public_url="https://testserver",
                      cognito_domain="https://pool.auth.example.test", cognito_pool_id="pool",
                      cognito_client_id="web", cognito_external_client_id="external",
                      oauth_gateway_url="https://gateway.example.test/mcp")
    store = MemoryStore()
    control, tokens = Control(store), TokenService(config)
    oauth = CognitoOAuth(config, store)
    p = Principal(sub="alice", tenant="personal-alice", email="alice@example.test")
    agent = control.create_agent(p, AgentSpec(name="OAuth agent"))
    class Keys:
        def get_signing_key_from_jwt(self, _token):
            return type("Key", (), {"key": tokens.public})()
    monkeypatch.setattr("onchain.oauth.jwks_client", lambda _: Keys())
    monkeypatch.setattr(CognitoOAuth, "seal_refresh", lambda *_: "encrypted")
    bundle = config, store, control, tokens
    for module in ["api", "services", "interceptors"]:
        monkeypatch.setattr("onchain." + module + ".dependencies", lambda: bundle)
    def encode(**extra):
        claims = {
            "iss": oauth.issuer, "aud": config.oauth_gateway_url, "sub": p.sub,
            "iat": int(time.time()), "exp": int(time.time()) + 900, "token_use": "access",
            "client_id": "external", "jti": "one", "origin_jti": "login-family",
            "scope": " ".join({INVOKE, *TOOL_SCOPES.values()}), **extra,
        }
        claims["scope"] = " ".join(oauth.provider_scopes(claims["scope"].split()))
        return jwt.encode(claims, tokens.private, algorithm="RS256", headers={"kid": "cognito"})
    return config, store, control, tokens, oauth, p, agent, encode


def test_cognito_access_requires_correct_issuer_client_resource_type_scope(oauth_env):
    *_, oauth, _p, _agent, encode = oauth_env
    assert oauth.verify(encode())["sub"] == "alice"
    for values in [{"iss": "https://evil.test"}, {"aud": "different-resource"},
                   {"client_id": "unregistered"}, {"token_use": "id"}, {"exp": 1}]:
        with pytest.raises(Denied):
            oauth.verify(encode(**values))
    with pytest.raises(Denied, match="scope"):
        oauth.scope(oauth.verify(encode(scope=INVOKE)), APPROVE)
    with pytest.raises(Denied):
        oauth.verify(encode(token_use="id", aud="web", nonce="wrong"), "web",
                     id_token=True, nonce="expected")


def test_oauth_callback_is_pkce_nonce_browser_bound_fresh_and_single_use(oauth_env, monkeypatch):
    config, _store, _control, _tokens, oauth, _p, _agent, encode = oauth_env
    url, browser = oauth.begin()
    query = parse_qs(urlparse(url).query)
    assert query["prompt"] == ["login"] and query["max_age"] == ["0"]
    assert query["code_challenge_method"] == ["S256"]
    with pytest.raises(Denied, match="another browser"):
        oauth.finish(query["state"][0], "wrong-browser", "code")
    def exchange(data):
        assert data["code_verifier"]
        return {"access_token": encode(client_id="web", scope=CONTROL),
                "id_token": encode(aud=config.cognito_client_id, token_use="id",
                                   nonce=query["nonce"][0], auth_time=int(time.time())),
                "refresh_token": "refresh"}
    monkeypatch.setattr(oauth, "token_request", exchange)
    flow, p, _, _ = oauth.finish(query["state"][0], browser, "code")
    assert flow["kind"] == "login" and p.sub == "alice"
    with pytest.raises(Denied, match="already used"):
        oauth.finish(query["state"][0], browser, "code")
    url, browser = oauth.begin()
    query = parse_qs(urlparse(url).query)
    monkeypatch.setattr(oauth, "token_request", lambda _: {
        "access_token": encode(client_id="web", scope=CONTROL),
        "id_token": encode(aud="web", token_use="id", nonce=query["nonce"][0], auth_time=1),
    })
    with pytest.raises(Denied, match="freshly"):
        oauth.finish(query["state"][0], browser, "code")


def authorize_connection(env, scopes=None):
    _config, _store, control, _tokens, oauth, p, agent, _encode = env
    return oauth.save_connection(p, {
        "client_id": "external", "agent_id": agent["id"],
        "scopes": scopes or [INVOKE, *set(TOOL_SCOPES.values())],
    }, control)


def test_external_mcp_scopes_binding_and_revocation(oauth_env):
    _config, store, control, _tokens, oauth, p, _agent, encode = oauth_env
    authorize_connection(oauth_env, [INVOKE, TOOL_SCOPES["get_portfolio"]])
    access = oauth.verify(encode())
    request = {"method": "tools/call", "id": 2, "params": {"name": "get_portfolio", "arguments": {}}}
    claims, *_ = oauth.external_context(access, {"mcp-session-id": "one"}, request, control)
    assert control.authorize_tool(claims, "get_portfolio", {}).sub == p.sub
    with pytest.raises(Denied, match="permission"):
        control.authorize_tool(claims, "prepare_trade", TradeIntent(amount="20").model_dump())
    second, *_ = oauth.external_context(access, {"mcp-session-id": "two"}, request, control)
    assert claims["sid"] != second["sid"] and claims["runtime_sid"] != second["runtime_sid"]
    with pytest.raises(Denied, match="another OAuth login"):
        oauth.external_context({**access, "origin_jti": "other-login"}, {"mcp-session-id": "one"}, request, control)
    def revoke(record):
        record["active"] = False
        return record, None
    store.mutate(oauth.connection_key(p, "external"), revoke)
    with pytest.raises(Denied, match="revoked"):
        control.validate_capability(claims)


def test_external_cannot_select_other_owner_or_use_token_after_expiry(oauth_env):
    _config, _store, control, _tokens, oauth, _p, _agent, encode = oauth_env
    authorize_connection(oauth_env)
    request = {"method": "tools/call", "id": 1, "params": {"name": "get_portfolio"}}
    access = oauth.verify(encode())
    claims, *_ = oauth.external_context(access, {"mcp-session-id": "shared"}, request, control)
    other = Principal(sub="bob", tenant="personal-bob")
    agent = control.create_agent(other, AgentSpec(name="Bob"))
    oauth.save_connection(other, {"client_id": "external", "agent_id": agent["id"],
                                 "scopes": [INVOKE, TOOL_SCOPES["get_portfolio"]]}, control)
    with pytest.raises(Denied, match="another OAuth login"):
        oauth.external_context({**access, "sub": "bob"}, {"mcp-session-id": "shared"}, request, control)
    with pytest.raises(Denied, match="expired"):
        control.validate_capability({**claims, "oauth_expires": 1})
    with pytest.raises(Denied, match="Initialize"):
        oauth.external_context(access, {}, request, control)


def test_oauth_confirmation_bound_to_login_operation_and_intent(oauth_env, monkeypatch):
    from onchain.api import app
    config, store, control, _tokens, oauth, p, agent, encode = oauth_env
    config.environment = "production"
    access = oauth.verify(encode(client_id="web", scope=CONTROL + " " + APPROVE))
    auth = Auth(config, store)
    cookie, record = auth.new_session(p, access, "refresh")
    op = control.prepare_trade(p, agent["id"], TradeIntent(amount="20"), "oauth-test")
    with TestClient(app, base_url=config.public_url) as client:
        client.cookies.set(COOKIE, cookie)
        headers = {"X-CSRF-Token": record["csrf"]}
        assert client.post(f"/api/operations/{op['id']}/oauth-stepup", headers=headers,
                           json={"intent_hash": "substitution"}).status_code == 403
        start = client.post(f"/api/operations/{op['id']}/oauth-stepup", headers=headers,
                            json={"intent_hash": op["intent_hash"]}).json()
        query = parse_qs(urlparse(start["authorization_url"]).query)
        monkeypatch.setattr(CognitoOAuth, "token_request", lambda *_: {
            "access_token": encode(client_id="web", scope=CONTROL + " " + APPROVE),
            "id_token": encode(aud="web", token_use="id", nonce=query["nonce"][0], auth_time=int(time.time())),
        })
        callback = client.get("/api/auth/oauth/callback", params={"state": query["state"][0], "code": "code"},
                              follow_redirects=False)
        assert callback.status_code == 303
        assert "verified_operation=" + op["id"] in callback.headers["location"]
        # Callback only records proof. A same-session CSRF-protected POST still commits approval.
        assert control.operation(p, op["id"])["status"] == "PENDING"
        approved = client.post(f"/api/operations/{op['id']}/approve", headers=headers,
                               json={"intent_hash": op["intent_hash"]})
        assert approved.status_code == 200
        assert client.post(f"/api/operations/{op['id']}/approve", headers=headers,
                           json={"intent_hash": op["intent_hash"]}).status_code == 401
        assert FLOW_COOKIE not in client.cookies


def test_external_gateway_hides_internal_arguments_and_filters_tools(oauth_env):
    from onchain.interceptors import request_handler, response_handler
    from onchain.catalog import tool_schema
    *_, encode = oauth_env
    authorize_connection(oauth_env, [INVOKE, TOOL_SCOPES["get_portfolio"]])
    request = {"headers": {"Authorization": "Bearer " + encode(), "Mcp-Session-Id": "scope-test"},
               "body": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}}
    assert "transformedGatewayRequest" in request_handler({"mcp": {"gatewayRequest": request}}, None)["mcp"]
    result = response_handler({"mcp": {"gatewayRequest": request, "gatewayResponse": {
        "body": {"jsonrpc": "2.0", "id": 1, "result": {
            "tools": [{"name": "onchain___" + t, "inputSchema": tool_schema(t)} for t in TOOL_SCOPES],
        }},
    }}}, None)["mcp"]["transformedGatewayResponse"]["body"]["result"]["tools"]
    assert {t["name"] for t in result} == {
        "onchain___get_portfolio", "onchain___get_quote", "onchain___get_history", "onchain___get_operation"}


def test_interactive_internal_capability_inherits_cognito_scope_and_expiry(oauth_env):
    config, store, control, _tokens, oauth, p, agent, encode = oauth_env
    access = oauth.verify(encode(client_id="web", scope=CONTROL + " " + TOOL_SCOPES["get_portfolio"]))
    cookie, _record = Auth(config, store).new_session(p, access, "refresh")
    key, _ = Auth(config, store).record(cookie)
    session = control.create_conversation(p, agent["id"], key)
    claims = control.capability(p, session["id"], "web-run")
    assert claims["oauth_expires"] == access["exp"]
    assert control.authorize_tool(claims, "get_portfolio", {}).sub == p.sub
    with pytest.raises(Denied, match="permission"):
        control.authorize_tool(claims, "prepare_trade", TradeIntent(amount="20").model_dump())


def test_stepup_cannot_switch_account_or_reuse_old_login(oauth_env, monkeypatch):
    config, store, _control, _tokens, oauth, p, _agent, encode = oauth_env
    auth = Auth(config, store)
    cookie, _ = auth.new_session(p)
    key, _ = auth.record(cookie)
    url, browser = oauth.begin(kind="operation", auth_key=key)
    query = parse_qs(urlparse(url).query)
    monkeypatch.setattr(oauth, "token_request", lambda _: {
        "access_token": encode(client_id="web", scope=CONTROL + " " + APPROVE, sub="bob"),
        "id_token": encode(aud="web", token_use="id", nonce=query["nonce"][0],
                           auth_time=int(time.time()), sub="bob"),
    })
    with pytest.raises(Denied, match="same active login"):
        oauth.finish(query["state"][0], browser, "code")
    url, browser = oauth.begin(kind="operation", auth_key=key)
    query = parse_qs(urlparse(url).query)
    auth.logout(cookie)
    monkeypatch.setattr(oauth, "token_request", lambda _: {
        "access_token": encode(client_id="web", scope=CONTROL + " " + APPROVE),
        "id_token": encode(aud="web", token_use="id", nonce=query["nonce"][0], auth_time=int(time.time())),
    })
    with pytest.raises(Denied, match="same active login"):
        oauth.finish(query["state"][0], browser, "code")


def test_login_preserves_approval_link_without_approving(oauth_env, monkeypatch):
    from onchain.api import app
    config, _store, control, _tokens, _oauth, p, agent, encode = oauth_env
    config.environment = "production"
    op = control.prepare_trade(p, agent["id"], TradeIntent(amount="20"), "external-link")
    with TestClient(app, base_url=config.public_url) as client:
        started = client.get("/api/auth/oauth/start", params={"review_operation": op["id"]},
                             follow_redirects=False)
        query = parse_qs(urlparse(started.headers["location"]).query)
        monkeypatch.setattr(CognitoOAuth, "token_request", lambda *_: {
            "access_token": encode(client_id="web", scope=CONTROL + " " + APPROVE),
            "id_token": encode(aud="web", token_use="id", nonce=query["nonce"][0], auth_time=int(time.time())),
            "refresh_token": "refresh",
        })
        result = client.get("/api/auth/oauth/callback", params={"state": query["state"][0], "code": "code"},
                            follow_redirects=False)
        assert result.headers["location"] == config.public_url + "/?review_operation=" + op["id"]
        assert control.operation(p, op["id"])["status"] == "PENDING"
        me = client.get("/api/auth/me").json()
        assert client.post(f"/api/operations/{op['id']}/approve",
                           headers={"X-CSRF-Token": me["csrf"]},
                           json={"intent_hash": op["intent_hash"]}).status_code == 401
