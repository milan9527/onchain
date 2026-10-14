import io
import json
import time

import jwt
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from onchain.auth import Auth, COOKIE
from onchain.config import Settings
from onchain.domain import AgentSpec, Control
from onchain.identity import authorization_challenge, connection_key, hold_for_consent
from onchain.invocations import authorize_context, create_context, target_identity
from onchain.security import Denied, Principal, TokenService, digest, uid
from onchain.store import MemoryStore


@pytest.fixture
def vault(tmp_path, monkeypatch):
    config = Settings(local_key_path=str(tmp_path / "key.pem"), public_url="http://testserver",
                      cognito_domain="https://pool.auth.example.test", cognito_pool_id="pool",
                      mcp_oauth_client_id="downstream", mcp_oauth_audience="https://testserver/mcp/tools",
                      mcp_oauth_scope="openid",
                      identity_provider_arn="arn:test:provider", identity_provider_name="provider")
    store, tokens = MemoryStore(), TokenService(config)
    control = Control(store)
    principal = Principal(sub="alice", tenant="personal-alice")
    agent = control.create_agent(principal, AgentSpec(name="Identity boundary"))
    auth = Auth(config, store)
    cookie, login = auth.new_session(principal)
    auth_key = auth.record(cookie)[0]
    conversation = control.create_conversation(principal, agent["id"], auth_key)
    claims = control.capability(principal, conversation["id"], uid("run_"))
    bundle = config, store, control, tokens
    for module in ("api", "services", "interceptors"):
        monkeypatch.setattr("onchain." + module + ".dependencies", lambda: bundle)
    class Keys:
        def get_signing_key_from_jwt(self, _token):
            return type("Key", (), {"key": tokens.public})()
    monkeypatch.setattr("onchain.invocations.jwks_client", lambda _: Keys())
    def encode(**extra):
        return jwt.encode({
            "iss": f"https://cognito-idp.{config.region}.amazonaws.com/pool",
            "aud": config.mcp_oauth_audience, "sub": "alice", "iat": int(time.time()),
            "exp": int(time.time()) + 900, "client_id": "downstream", "token_use": "access",
            "scope": "openid", **extra,
        }, tokens.private, algorithm="RS256")
    return config, store, control, tokens, principal, claims, cookie, login, encode


@pytest.mark.parametrize("alter", [
    {"sub": "bob"}, {"iss": "https://evil.test"}, {"aud": "another-resource"},
    {"client_id": "web"}, {"token_use": "id"}, {"scope": "profile"}, {"exp": 1},
])
def test_executor_requires_correct_oauth_and_context_owner(vault, alter):
    from onchain.services import executor_handler
    config, store, _c, _tokens, _p, claims, _cookie, _login, encode = vault
    reference = create_context(store, claims, "get_portfolio", {})
    response = executor_handler({"phase": "execute", "tool": "get_portfolio", "arguments": {},
                                 "context_id": reference, "access_token": encode(**alter)}, None)
    assert not response["ok"]
    assert response["error"]["code"] == ("CONTEXT_OWNER_MISMATCH" if "sub" in alter else "INVALID_MCP_TOKEN")
    assert target_identity(config, encode()).sub == "alice"


def test_context_binds_arguments_revocation_and_current_session(vault):
    _config, store, control, _tokens, p, claims, *_ = vault
    reference = create_context(store, claims, "get_portfolio", {})
    assert authorize_context(store, control, p, reference, "get_portfolio", {})["sid"] == claims["sid"]
    for tool, args in [("get_history", {}), ("get_portfolio", {"userId": "bob"})]:
        with pytest.raises(Denied) as denied:
            authorize_context(store, control, p, reference, tool, args)
        assert denied.value.code == "ARGUMENTS_CHANGED"
    store.mutate(connection_key(p.owner), lambda row: ({**row, "epoch": row["epoch"] + 1}, None))
    with pytest.raises(Denied, match="authorization changed"):
        authorize_context(store, control, p, reference, "get_portfolio", {})
    reference = create_context(store, claims, "get_portfolio", {})
    control.close_conversation(p, claims["sid"])
    with pytest.raises(Denied):
        authorize_context(store, control, p, reference, "get_portfolio", {})


def test_expired_reference_and_revoked_connection_cannot_be_recreated(vault):
    _config, store, control, _tokens, p, claims, *_ = vault
    reference = create_context(store, claims, "get_portfolio", {})
    store.mutate("CALL#" + digest(reference), lambda row: ({**row, "expires_at": 1}, None))
    with pytest.raises(Denied, match="expired"):
        authorize_context(store, control, p, reference, "get_portfolio", {})
    store.mutate(connection_key(p.owner), lambda row: ({**row, "active": False}, None))
    with pytest.raises(Denied):
        create_context(store, claims, "get_portfolio", {})


def test_internal_and_external_platform_revocation_are_separate(vault):
    _config, store, _control, _tokens, p, claims, *_ = vault
    store.put(connection_key(p.owner), {"owner": p.owner, "active": True, "connected": True, "epoch": 1})
    external = {**claims, "external": True}
    assert create_context(store, external, "get_portfolio", {}).startswith("ctx_")
    key = connection_key(p.owner, "external")
    store.mutate(key, lambda row: ({**row, "active": False, "pending": True}, None))
    with pytest.raises(Denied) as denied:
        create_context(store, external, "get_portfolio", {})
    assert denied.value.code == "IDENTITY_CONNECTION_REVOKED"
    store.mutate(key, lambda row: ({**row, "active": True, "pending": False, "connected": True}, None))
    assert create_context(store, external, "get_portfolio", {}).startswith("ctx_")


def test_mcp_pins_session_before_any_execution(vault, monkeypatch):
    from runtimes import mcp_server
    from runtimes.isolation import SessionBinding
    config, _store, _control, _tokens, _p, _claims, *_rest, encode = vault
    monkeypatch.setattr(mcp_server, "settings", lambda: config)
    context = type("Context", (), {"request_context": type("RequestContext", (), {
        "request": type("Request", (), {"headers": {"Authorization": "Bearer " + encode()}})(),
    })()})()
    monkeypatch.setattr(mcp_server.mcp, "get_context", lambda: context)
    monkeypatch.setattr(mcp_server, "binding", SessionBinding())
    events = []
    class Lambda:
        owner = {"owner": "alice", "sid": "one", "runtime_sid": "runtime-one"}
        def invoke(self, **kwargs):
            event = json.loads(kwargs["Payload"])
            events.append(event["phase"])
            result = self.owner if event["phase"] == "authorize" else {"wallet": {}}
            return {"Payload": io.BytesIO(json.dumps({"ok": True, "result": result}).encode())}
    client = Lambda()
    monkeypatch.setattr(mcp_server.boto3, "client", lambda *_a, **_k: client)
    assert mcp_server.dispatch("get_portfolio", {}, "ctx") == {"wallet": {}}
    client.owner = {**client.owner, "sid": "two"}
    with pytest.raises(Denied):
        mcp_server.dispatch("get_portfolio", {}, "ctx")
    assert events == ["authorize", "execute", "authorize"]


def test_consent_urls_are_allowlisted_and_history_has_no_tokens(vault):
    from onchain.history import safe_details
    config, store, _control, tokens, p, claims, *_ = vault
    url = config.cognito_domain + "/oauth2/authorize?client_id=downstream&state=opaque"
    error = authorization_challenge({"message": url}, config)
    assert error["code"] == "MCP_AUTH_REQUIRED"
    assert not authorization_challenge({"message": url.replace("client_id=downstream", "client_id=evil")}, config)
    assert not authorization_challenge({"message": url.replace("pool.auth.example.test", "evil.test")}, config)
    native = "https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?request_uri=urn%3Aietf%3Aparams%3Aoauth%3Arequest_uri%3Aexample"
    assert authorization_challenge({"message": native}, config)
    assert not authorization_challenge({"message": native.replace("request_uri=", "other=")}, config)
    assert not authorization_challenge({"message": native.replace("us-east-1", "eu-west-1")}, config)
    job = {"run_id": uid("run_"), "kind": "chat", "sid": claims["sid"], "message": "my portfolio",
           "gateway_token": tokens.issue("onchain-gateway", claims)}
    result = hold_for_consent({"tool_result": {"error": error}}, job, store, p, config)
    assert result["tool_result"]["error"]["resume_id"] == job["run_id"]
    assert "authorization_url" not in json.dumps(result)
    stored = store.get("IDENTITY_RESUME#" + job["run_id"])
    assert job["gateway_token"] not in json.dumps(stored)
    from onchain.identity import consent_proof
    assert consent_proof(config, stored["proof"], p.owner, "resume:" + job["run_id"], decrypt=True) == job["gateway_token"]
    with pytest.raises(Denied):
        consent_proof(config, stored["proof"], "another-owner", "resume:" + job["run_id"], decrypt=True)
    assert safe_details({"context_id": "ctx_secret", "access_token": "secret",
                         "authorization_url": url}) == {
        "context_id": "[REDACTED]", "access_token": "[REDACTED]", "authorization_url": "[REDACTED]",
    }


def test_callback_requires_csrf_original_login_and_resumes_once(vault, monkeypatch):
    from onchain.api import app
    config, store, _control, tokens, p, claims, cookie, login, _encode = vault
    calls, queued = [], []
    original_token = tokens.issue("onchain-gateway", claims)
    class Identity:
        def complete_resource_token_auth(self, **kwargs):
            assert kwargs["userIdentifier"]["userToken"] == original_token
            original = tokens.verify(kwargs["userIdentifier"]["userToken"], "onchain-gateway")
            assert original["sub"] == p.sub
            calls.append(kwargs["sessionUri"])
    monkeypatch.setattr("onchain.api.boto3.client", lambda *_a, **_k: Identity())
    async def enqueue(_p, sid, kind, **kwargs):
        queued.append((sid, kind, kwargs))
        return {"status": "QUEUED"}
    monkeypatch.setattr("onchain.api.enqueue", enqueue)
    run_id = uid("run_")
    hold_for_consent({"error": {"code": "MCP_AUTH_REQUIRED", "authorization_url":
                      config.cognito_domain + "/oauth2/authorize?client_id=downstream"}}, {
        "run_id": run_id, "sid": claims["sid"], "kind": "chat", "message": "read portfolio",
        "gateway_token": original_token,
    }, store, p, config)
    body = {"session_uri": "urn:ietf:params:oauth:request_uri:one", "resume_id": run_id}
    with TestClient(app) as client:
        client.cookies.set(COOKIE, cookie)
        landing = client.get("/api/identity/callback/internal", params={"session_id": body["session_uri"]},
                             follow_redirects=False)
        assert landing.status_code == 303 and "identity_session=" in landing.headers["location"]
        assert client.post("/api/identity/complete", json=body).status_code == 403
        headers = {"X-CSRF-Token": login["csrf"]}
        callback = client.post("/api/identity/complete", json=body, headers=headers)
        assert callback.status_code == 200, callback.text
        assert callback.json()["conversation_id"] == claims["sid"]
        assert client.post("/api/identity/complete", json=body, headers=headers).json() == callback.json()
        assert len(calls) == len(queued) == 1
        assert "proof" not in store.get("IDENTITY_RESUME#" + run_id)
        assert client.post("/api/identity/complete", json={**body, "source": "external"}, headers=headers).status_code == 403
        assert client.delete("/api/identity/connections/internal", headers=headers).json()["revoked"]
        with pytest.raises(Denied):
            create_context(store, claims, "get_portfolio", {})


def test_callback_rejects_new_login_and_aws_session_binding_failure(vault, monkeypatch):
    from onchain.api import app
    config, store, _c, tokens, p, claims, cookie, login, _encode = vault
    run_id = uid("run_")
    hold_for_consent({"error": {"code": "MCP_AUTH_REQUIRED", "authorization_url": "https://unused.test"}}, {
        "run_id": run_id, "sid": claims["sid"], "kind": "chat", "message": "read",
        "gateway_token": tokens.issue("onchain-gateway", claims),
    }, store, p, config)
    new_cookie, new_login = Auth(config, store).new_session(p)
    body = {"session_uri": "urn:ietf:params:oauth:request_uri:two", "resume_id": run_id}
    with TestClient(app) as client:
        client.cookies.set(COOKIE, new_cookie)
        response = client.post("/api/identity/complete", json=body, headers={"X-CSRF-Token": new_login["csrf"]})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "IDENTITY_LOGIN_MISMATCH"
    class Identity:
        def complete_resource_token_auth(self, **_kwargs):
            raise ClientError({"Error": {"Code": "ValidationException", "Message": "wrong original user"}}, "CompleteResourceTokenAuth")
    monkeypatch.setattr("onchain.api.boto3.client", lambda *_a, **_k: Identity())
    with TestClient(app) as client:
        client.cookies.set(COOKIE, cookie)
        response = client.post("/api/identity/complete", json=body,
                               headers={"X-CSRF-Token": login["csrf"]})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "IDENTITY_CONSENT_FAILED"
        assert not store.get(connection_key(p.owner))


def test_native_gateway_consent_cannot_override_a_later_revocation(vault, monkeypatch):
    from onchain.api import app
    _config, store, _c, tokens, p, claims, cookie, login, _encode = vault
    calls = []
    uri = "urn:ietf:params:oauth:request_uri:gateway-session"
    original = tokens.issue("onchain-gateway", claims)
    async def probe(*_args):
        return {"authorization_url": "https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?request_uri=" + uri,
                "original_token": original}
    monkeypatch.setattr("onchain.identity.gateway_connection", probe)
    class Identity:
        def complete_resource_token_auth(self, **kwargs):
            assert kwargs["userIdentifier"]["userToken"] == original
            calls.append("complete")
    client_identity = Identity()
    monkeypatch.setattr("onchain.api.boto3.client", lambda *_a, **_k: client_identity)
    with TestClient(app) as client:
        client.cookies.set(COOKIE, cookie)
        headers = {"X-CSRF-Token": login["csrf"]}
        assert client.post("/api/identity/connections/internal", headers=headers).status_code == 200
        record = store.get(connection_key(p.owner))
        assert record["pending"] and record["active"] and not record["connected"]
        assert client.delete("/api/identity/connections/internal", headers=headers).status_code == 200
        result = client.post("/api/identity/complete", json={"session_uri": uri}, headers=headers)
        assert result.status_code == 403 and result.json()["error"]["code"] == "IDENTITY_CONNECTION_REVOKED"
        assert not calls
        # A new explicitly initiated authorization can enable the connection only after completion.
        uri = "urn:ietf:params:oauth:request_uri:new-gateway-session"
        assert client.post("/api/identity/connections/internal", headers=headers).status_code == 200
        result = client.post("/api/identity/complete", json={"session_uri": uri}, headers=headers)
        assert result.status_code == 200
        assert store.get(connection_key(p.owner))["active"] and calls == ["complete"]


@pytest.mark.parametrize("revoked", [False, True])
def test_external_gateway_response_binds_original_token_and_current_grant(vault, monkeypatch, revoked):
    from onchain.api import app
    from onchain.interceptors import response_handler
    config, store, control, _tokens, p, claims, cookie, login, encode = vault
    claims = {**claims, "external": True}
    create_context(store, claims, "get_portfolio", {})
    control.start_run(p, claims["sid"], claims["run_id"], "external")
    monkeypatch.setattr("onchain.interceptors.gateway_identity", lambda *_a, **_k: (claims, p, {}, set()))
    original = encode()
    uri = "urn:ietf:params:oauth:request_uri:external-native"
    url = "https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?request_uri=" + uri
    result = response_handler({"mcp": {
        "gatewayRequest": {"headers": {"Authorization": "Bearer " + original},
                           "body": {"id": 1, "method": "tools/call", "params": {"name": "get_portfolio"}}},
        "gatewayResponse": {"body": {"error": {"code": -32042, "data": {"elicitations": [{"url": url}]}}}},
    }}, None)
    assert result["mcp"]["transformedGatewayResponse"]["body"]["error"]["code"] == -32042
    pending = store.get("IDENTITY_PENDING#" + digest(uri))
    assert pending["owner"] == p.owner and pending["auth_key"] is None
    assert original not in json.dumps(pending)
    calls = []
    class Identity:
        def complete_resource_token_auth(self, **kwargs):
            assert kwargs["userIdentifier"]["userToken"] == original
            calls.append(kwargs["sessionUri"])
    monkeypatch.setattr("onchain.api.boto3.client", lambda *_a, **_k: Identity())
    with TestClient(app) as client:
        client.cookies.set(COOKIE, cookie)
        # A revoked original MCP conversation must fail even with the right browser user.
        if revoked:
            control.close_conversation(p, claims["sid"])
        response = client.post("/api/identity/complete", json={"session_uri": uri, "source": "external"},
                               headers={"X-CSRF-Token": login["csrf"]})
        if revoked:
            assert response.status_code != 200 and not calls
        else:
            assert response.status_code == 200 and calls == [uri]
            assert "proof" not in store.get("IDENTITY_PENDING#" + digest(uri))
            assert store.get(connection_key(p.owner, "external"))["gateway_bound"]
