"""Cognito is the OAuth issuer. Platform capabilities only carry internal execution context."""
import base64
import hashlib
import hmac
import secrets
import time
from functools import lru_cache
from urllib.parse import urlencode

import boto3
import httpx
import jwt

from .security import Denied, Principal, b64, digest, uid

PREFIX = "onchain/"
INVOKE = PREFIX + "mcp.invoke"
CONTROL = PREFIX + "control"
APPROVE = PREFIX + "approve"
TOOL_SCOPES = {
    "get_portfolio": PREFIX + "portfolio.read",
    "get_history": PREFIX + "portfolio.read",
    "get_quote": PREFIX + "portfolio.read",
    "get_operation": PREFIX + "portfolio.read",
    "prepare_trade": PREFIX + "trade.prepare",
    "execute_trade": PREFIX + "trade.execute",
}
from .extensions import EXTRA_SCHEMAS, LIVE_TOOLS  # noqa: E402
TOOL_SCOPES.update({tool: PREFIX + ("market.read" if tool in LIVE_TOOLS else "portfolio.read")
                    for tool in EXTRA_SCHEMAS})
MCP_SCOPES = sorted({INVOKE, *TOOL_SCOPES.values()})
WEB_SCOPES = ["openid", "email", "aws.cognito.signin.user.admin", CONTROL, APPROVE, *MCP_SCOPES]
FLOW_COOKIE = "onchain_oauth_state"


@lru_cache(maxsize=8)
def jwks_client(issuer):
    return jwt.PyJWKClient(issuer + "/.well-known/jwks.json", timeout=5, lifespan=300)


class CognitoOAuth:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self.issuer = f"https://cognito-idp.{config.region}.amazonaws.com/{config.cognito_pool_id}"

    def provider_scopes(self, scopes):
        return [self.config.oauth_gateway_url + "/" + s.removeprefix(PREFIX)
                if s.startswith(PREFIX) else s for s in scopes]

    def require_enabled(self):
        if not self.config.cognito_domain or not self.config.oauth_gateway_url:
            raise Denied("OAUTH_UNAVAILABLE", "Cognito OAuth is not configured.", 503)

    def verify(self, token, client_id=None, *, id_token=False, nonce=None):
        self.require_enabled()
        try:
            key = jwks_client(self.issuer).get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key, algorithms=["RS256"], issuer=self.issuer,
                audience=client_id if id_token else self.config.oauth_gateway_url,
                options={"require": ["exp", "iat", "sub", "iss", "token_use"]},
            )
            permitted = {self.config.cognito_client_id, self.config.cognito_external_client_id}
            if claims["token_use"] != ("id" if id_token else "access"):
                raise ValueError("wrong token use")
            if not id_token and (
                not claims.get("jti") or claims.get("client_id") not in permitted
                or (client_id and claims["client_id"] != client_id)
            ):
                raise ValueError("wrong client")
            if nonce is not None and not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
                raise ValueError("wrong nonce")
            if not id_token:
                # Cognito resource-bound custom scopes must use this resource server's URL prefix.
                prefix = self.config.oauth_gateway_url + "/"
                claims["scope"] = " ".join(
                    PREFIX + s.removeprefix(prefix) if s.startswith(prefix) else s
                    for s in claims.get("scope", "").split() if not s.startswith(PREFIX)
                )
            return claims
        except (jwt.PyJWTError, ValueError, TypeError) as e:
            raise Denied("INVALID_OAUTH_TOKEN", "Invalid Cognito access token, client or resource.", 401) from e

    def scope(self, claims, required):
        if required not in claims.get("scope", "").split():
            raise Denied("INSUFFICIENT_SCOPE", "OAuth scope required: " + required)

    def principal(self, claims, identity=None):
        identity = identity or {}
        return Principal(sub=claims["sub"], tenant="personal-" + claims["sub"],
                         email=identity.get("email", ""),
                         admin="platform-admin" in claims.get("cognito:groups", []))

    def seal_refresh(self, token, auth_key):
        result = boto3.client("kms", region_name=self.config.region).encrypt(
            KeyId=self.config.oauth_vault_key_id, Plaintext=token.encode(),
            EncryptionContext={"purpose": "cognito-refresh", "auth_key": auth_key},
        )
        return base64.b64encode(result["CiphertextBlob"]).decode()

    def open_refresh(self, encrypted, auth_key):
        result = boto3.client("kms", region_name=self.config.region).decrypt(
            KeyId=self.config.oauth_vault_key_id, CiphertextBlob=base64.b64decode(encrypted),
            EncryptionContext={"purpose": "cognito-refresh", "auth_key": auth_key},
        )
        return result["Plaintext"].decode()

    def token_request(self, data):
        try:
            response = httpx.post(self.config.cognito_domain + "/oauth2/token", data=data, timeout=10)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as e:
            raise Denied("OAUTH_EXCHANGE_FAILED", "Cognito authentication expired; sign in again.", 401) from e

    def refresh_session(self, auth_key, record):
        if not record.get("oauth"):
            raise Denied("OAUTH_LOGIN_REQUIRED", "Please sign in through Cognito OAuth.", 401)
        if record["oauth"]["exp"] > time.time() + 300:
            return record
        response = self.token_request({
            "grant_type": "refresh_token", "client_id": self.config.cognito_client_id,
            "refresh_token": self.open_refresh(record["oauth_refresh"], auth_key),
        })
        claims = self.verify(response["access_token"], self.config.cognito_client_id)
        if claims["sub"] != record["principal"]["sub"]:
            raise Denied("OAUTH_OWNER", "OAuth identity changed.", 401)
        def update(current):
            if not current or not current.get("active") or current["expires_at"] <= time.time():
                raise Denied("SESSION_EXPIRED", "Sign in again.", 401)
            current["oauth"] = claims
            return current, current
        return self.store.mutate(auth_key, update)

    def begin(self, *, kind="login", auth_key=None, payload=None):
        self.require_enabled()
        state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier, nonce = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        self.store.put("OAUTH_FLOW#" + digest(state), {
            "browser": digest(browser), "verifier": verifier, "nonce": nonce,
            "kind": kind, "auth_key": auth_key, "payload": payload or {},
            "created_at": int(time.time()), "expires_at": int(time.time()) + 300, "used": False,
        })
        params = {
            "response_type": "code", "client_id": self.config.cognito_client_id,
            "redirect_uri": self.config.public_url + "/api/auth/oauth/callback",
            "scope": " ".join(self.provider_scopes(WEB_SCOPES)), "resource": self.config.oauth_gateway_url,
            "state": state, "nonce": nonce, "code_challenge_method": "S256",
            "code_challenge": b64(hashlib.sha256(verifier.encode()).digest()),
            # Managed login must demand credentials, including configured MFA.
            "prompt": "login", "max_age": "0",
        }
        return self.config.cognito_domain + "/oauth2/authorize?" + urlencode(params), browser

    def finish(self, state, browser, code):
        def consume(record):
            if not record or record.get("used") or record["expires_at"] <= time.time():
                raise Denied("OAUTH_STATE_INVALID", "Authentication request expired or was already used.", 401)
            if not browser or not hmac.compare_digest(record["browser"], digest(browser)):
                raise Denied("OAUTH_BROWSER_MISMATCH", "Authentication belongs to another browser.", 401)
            record["used"] = True
            return record, record
        flow = self.store.mutate("OAUTH_FLOW#" + digest(state), consume)
        response = self.token_request({
            "grant_type": "authorization_code", "client_id": self.config.cognito_client_id,
            "redirect_uri": self.config.public_url + "/api/auth/oauth/callback",
            "code": code, "code_verifier": flow["verifier"],
        })
        access = self.verify(response["access_token"], self.config.cognito_client_id)
        identity = self.verify(response["id_token"], self.config.cognito_client_id,
                               id_token=True, nonce=flow["nonce"])
        if identity["sub"] != access["sub"]:
            raise Denied("OAUTH_OWNER", "OAuth identities do not match.", 401)
        if identity.get("auth_time", 0) < flow["created_at"] - 5:
            raise Denied("FRESH_AUTH_REQUIRED", "Cognito must freshly authenticate this action.", 401)
        self.scope(access, CONTROL)
        if flow["kind"] != "login":
            self.scope(access, APPROVE)
            login = self.store.get(flow["auth_key"])
            if (not login or not login.get("active") or login["expires_at"] <= time.time()
                    or login["principal"]["sub"] != access["sub"]):
                raise Denied("OAUTH_OWNER", "Re-authentication must use the same active login.", 401)
        return flow, self.principal(access, identity), access, response

    def connection_key(self, p, client_id):
        return "MCP_CONNECTION#" + p.owner + "#" + client_id

    def save_connection(self, p, spec, control):
        client_id = spec["client_id"]
        if client_id != self.config.cognito_external_client_id:
            raise Denied("OAUTH_CLIENT_DENIED", "This MCP client has not been registered.")
        control.agent(p, spec["agent_id"])
        scopes = set(spec["scopes"])
        if not scopes <= set(MCP_SCOPES) or INVOKE not in scopes:
            raise Denied("INVALID_SCOPE", "Select approved MCP scopes.", 422)
        record = {
            "id": uid("connection_"), "owner": p.owner, "client_id": client_id,
            "agent_id": spec["agent_id"], "scopes": sorted(scopes), "active": True,
            "created_at": int(time.time()), "expires_at": int(time.time()) + 86400,
        }
        self.store.put(self.connection_key(p, client_id), record)
        return record

    def external_context(self, access, headers, request, control, *, create=True):
        """A protocol session is bound to one OAuth token family, owner, client and user grant."""
        self.scope(access, INVOKE)
        p = self.principal(access)
        connection = self.store.get(self.connection_key(p, access["client_id"]))
        if (not connection or not connection.get("active") or connection["expires_at"] <= time.time()):
            raise Denied("MCP_CONNECTION_REQUIRED", "Authorize this MCP client and Agent in the platform's MCP page.")
        granted = set(access.get("scope", "").split()) & set(connection["scopes"])
        self.scope({"scope": " ".join(granted)}, INVOKE)
        mcp_sid = headers.get("mcp-session-id")
        if not mcp_sid:
            if request.get("method") not in {"initialize", "ping", "notifications/initialized"}:
                raise Denied("MCP_SESSION_REQUIRED", "Initialize an isolated MCP session before calling tools.")
            # No data tools are exposed until the Gateway has issued a protocol session ID.
            return None, p, control.agent(p, connection["agent_id"]), granted
        key = "OAUTH_MCP_SESSION#" + digest(mcp_sid)
        family = access.get("origin_jti") or access["jti"]
        binding = {"owner": p.owner, "client_id": access["client_id"], "family": family,
                   "connection_id": connection["id"]}
        existing = self.store.get(key)
        if existing is None and create:
            # Allocate the winning binding before creating any conversation. Concurrent calls use one sid.
            self.store.create(key, {**binding, "sid": uid("conv_"), "runtime_sid": uid("rt_"),
                                    "expires_at": connection["expires_at"]})
            existing = self.store.get(key)
        if not existing or any(existing.get(k) != v for k, v in binding.items()):
            raise Denied("GATEWAY_SESSION_MISMATCH", "MCP session belongs to another OAuth login or connection.")
        sid = existing["sid"]
        if create and self.store.get("SESSION#" + sid) is None:
            from .history import RETENTION
            now = int(time.time())
            session = {
                "id": sid, "owner": p.owner, "agent_id": connection["agent_id"],
                "runtime_session_id": existing["runtime_sid"], "messages": [], "runs": [],
                "active": True, "epoch": 1, "created_at": now, "updated_at": now, "busy_until": 0,
                "active_until": connection["expires_at"], "expires_at": now + RETENTION,
                "auth_key": None, "kind": "external", "title": "外部 MCP 会话",
                "connection_key": self.connection_key(p, access["client_id"]),
                "connection_id": connection["id"],
            }
            self.store.create("SESSION#" + sid, session)
            control.index_session(session)
        # Include the JSON-RPC request and content in the run key: retries are idempotent.
        run_id = "mcp_" + digest({"sid": sid, "id": request.get("id"), "params": request.get("params")})[:36]
        claims = control.capability(p, sid, run_id)
        claims.update(exp=access["exp"], oauth_expires=access["exp"], oauth_scopes=sorted(granted),
                      oauth_client_id=access["client_id"], external=True)
        return claims, p, control.agent(p, connection["agent_id"]), granted
