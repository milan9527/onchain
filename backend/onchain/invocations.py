"""Server-side tool grants. OAuth authenticates the user; a reference never grants authority alone."""
import re
import time

import jwt

from .oauth import jwks_client
from .security import Denied, Principal, TokenService, digest, uid


def target_identity(config, access_token):
    """Independently validate the downstream access token at MCP and Executor."""
    if not config.production and not config.cognito_pool_id:
        claims = TokenService(config).verify(access_token, "onchain-mcp-local")
        return Principal(sub=claims["sub"], tenant=claims["tenant"])
    if not config.mcp_oauth_client_id or not config.mcp_oauth_audience or not config.mcp_oauth_scope:
        raise Denied("MCP_AUTH_UNAVAILABLE", "MCP identity configuration is unavailable.", 503)
    issuer = f"https://cognito-idp.{config.region}.amazonaws.com/{config.cognito_pool_id}"
    try:
        key = jwks_client(issuer).get_signing_key_from_jwt(access_token).key
        claims = jwt.decode(access_token, key, algorithms=["RS256"], issuer=issuer,
                            audience=config.mcp_oauth_audience,
                            options={"require": ["iss", "sub", "exp", "iat", "token_use", "client_id"]})
        if (claims["token_use"] != "access" or claims["client_id"] != config.mcp_oauth_client_id
                or config.mcp_oauth_scope not in claims.get("scope", "").split()):
            raise ValueError("Invalid downstream client or scope")
    except (jwt.PyJWTError, ValueError, TypeError) as e:
        raise Denied("INVALID_MCP_TOKEN", "Invalid or expired MCP access token.", 401) from e
    return Principal(sub=claims["sub"], tenant="personal-" + claims["sub"])


def create_context(store, claims, tool, arguments):
    from .identity import connection_key
    key = connection_key(claims["owner"], "external" if claims.get("external") else "internal")
    store.create(key, {"owner": claims["owner"], "active": True, "epoch": 1, "connected": False})
    connection = store.get(key)
    if not connection["active"]:
        raise Denied("IDENTITY_CONNECTION_REVOKED", "工具连接已撤销，请在 MCP 管理中重新授权。")
    reference = uid("ctx_")
    now = int(time.time())
    expires = min(now + 120, claims.get("exp", now + 120), claims.get("oauth_expires", now + 120))
    if expires <= now:
        raise Denied("CONTEXT_EXPIRED", "Invocation authorization expired.", 401)
    store.create("CALL#" + digest(reference), {
        "claims": {k: v for k, v in claims.items() if k not in {"iss", "aud", "iat", "nbf", "exp", "jti"}},
        "tool": tool, "arguments_hash": digest(arguments), "expires_at": expires,
        "connection_key": key, "connection_epoch": connection["epoch"],
        "created_at": now,
    })
    return reference


def authorize_context(store, control, principal, reference, tool, arguments):
    if not isinstance(reference, str) or not re.fullmatch(r"ctx_[0-9a-f]{36}", reference):
        raise Denied("CONTEXT_REQUIRED", "A Gateway-issued invocation reference is required.", 401)
    record = store.get("CALL#" + digest(reference))
    if not record or record["expires_at"] <= time.time():
        raise Denied("CONTEXT_EXPIRED", "Invocation authorization expired.", 401)
    claims = record["claims"]
    if principal.owner != claims.get("owner") or principal.sub != claims.get("sub"):
        raise Denied("CONTEXT_OWNER_MISMATCH", "Invocation is not available to this user.")
    if record["tool"] != tool or record["arguments_hash"] != digest(arguments):
        raise Denied("ARGUMENTS_CHANGED", "Tool or arguments do not match the authorized invocation.")
    connection = store.get(record["connection_key"])
    if not connection or not connection.get("active") or connection["epoch"] != record["connection_epoch"]:
        raise Denied("IDENTITY_CONNECTION_REVOKED", "Tool connection authorization changed.")
    control.authorize_tool(claims, tool, arguments, count=False)
    return claims


def local_execute(config, control, tokens, claims, tool, arguments):
    """Local-only equivalent of the same context and independent identity checks."""
    if config.production:
        raise Denied("LOCAL_AUTH_DISABLED", "Local identity is unavailable.", 401)
    p = Principal(sub=claims["sub"], tenant=claims["tenant"])
    reference = create_context(control.store, claims, tool, arguments)
    token = tokens.issue("onchain-mcp-local", {"sub": p.sub, "tenant": p.tenant})
    verified = target_identity(config, token)
    checked = authorize_context(control.store, control, verified, reference, tool, arguments)
    from .services import execute_tool
    return execute_tool(control, config, checked, tool, arguments)
