"""AgentCore MCP interceptor contract. No Guardrails and no model-made authorization decisions."""
import copy
import json

from .catalog import TOOL_PRODUCTS, skill_by_id
from .security import Denied, Principal, bearer, digest
from .services import dependencies
from .history import trace_claims
from .oauth import CognitoOAuth, TOOL_SCOPES


def response(body, status=200):
    return {"interceptorOutputVersion": "1.0",
            "mcp": {"transformedGatewayResponse": {"statusCode": status, "body": body}}}


def error_response(request, error):
    return response({
        "jsonrpc": "2.0", "id": request.get("id"),
        "error": {"code": -32003, "message": error.message, "data": {"code": error.code}},
    }, 200)


def raw_tool(name):
    # Target prefixes are part of Gateway's namespace; only the pinned target is accepted.
    if "___" in name:
        target, name = name.split("___", 1)
        if target != "onchain":
            raise Denied("TARGET_DENIED", "Target has not been approved.")
    return name


def gateway_identity(config, store, control, tokens, headers, request, *, create=True):
    headers = {k.lower(): v for k, v in headers.items()}
    token = bearer(headers)
    # Issuer is used only to select a verifier; both paths perform full cryptographic verification.
    import jwt
    try:
        issuer = jwt.decode(token, options={"verify_signature": False}).get("iss")
    except jwt.PyJWTError as e:
        raise Denied("INVALID_OAUTH_TOKEN", "Invalid authentication token.", 401) from e
    if config.cognito_domain and issuer != config.issuer:
        oauth = CognitoOAuth(config, store)
        access = oauth.verify(token, config.cognito_external_client_id)
        return oauth.external_context(access, headers, request, control, create=create)
    claims = tokens.verify(token, "onchain-gateway")
    p, agent = control.validate_capability(claims)
    return claims, p, agent, None


def request_handler(event, _context):
    config, store, control, tokens = dependencies()
    request = copy.deepcopy(event.get("mcp", {}).get("gatewayRequest", {}).get("body", {}))
    headers = event.get("mcp", {}).get("gatewayRequest", {}).get("headers", {})
    claims = None
    try:
        claims, p, agent, _scopes = gateway_identity(config, store, control, tokens, headers, request)
        method = request.get("method")
        mcp_sid = next((v for k, v in headers.items() if k.lower() == "mcp-session-id"), None)
        if mcp_sid and claims and not claims.get("external"):
            # Gateway binds sessions to sub; this additionally separates conversations of the SAME user.
            key = "GWSESSION#" + digest(mcp_sid)
            binding = {"owner": p.owner, "sid": claims["sid"], "run_id": claims["run_id"],
                       "expires_at": claims["exp"]}
            if store.get(key) is None:
                store.create(key, binding)
            existing = store.get(key)
            if any(existing[k] != binding[k] for k in ("owner", "sid", "run_id")):
                raise Denied("GATEWAY_SESSION_MISMATCH", "MCP session belongs to another invocation.")
        if method in {"initialize", "notifications/initialized", "ping", "tools/list"}:
            return {"interceptorOutputVersion": "1.0",
                    "mcp": {"transformedGatewayRequest": {"body": request}}}
        if method != "tools/call":
            raise Denied("MCP_METHOD_DENIED", "Only approved MCP tools are exposed.")
        params = request.get("params", {})
        tool, args = raw_tool(params.get("name", "")), params.get("arguments", {})
        if claims.get("external"):
            control.start_run(p, claims["sid"], claims["run_id"], "external")
            control.run_status(p, claims["sid"], claims["run_id"], "RUNNING")
        control.authorize_tool(claims, tool, args)
        trace_claims(control, claims, "GATEWAY_ALLOWED", "gateway", "Gateway 权限检查通过",
                     {"mcp_server": "onchain", "tool": tool, "arguments": args,
                      "checks": ["身份与会话", "Skill 与 MCP 订阅", "参数规则", "操作权限"]})
        # The caller cannot supply this reference; OAuth and the authoritative record bind it to a user.
        from .invocations import create_context
        request["params"]["arguments"]["context_id"] = create_context(store, claims, tool, args)
        return {"interceptorOutputVersion": "1.0",
                "mcp": {"transformedGatewayRequest": {"body": request}}}
    except Denied as e:
        if claims:
            p = Principal(sub=claims["sub"], tenant=claims["tenant"])
            control.report_denial(p, e.code, claims.get("agent_id", ""))
            trace_claims(control, claims, "GATEWAY_BLOCKED", "gateway", "Gateway 拦截调用",
                         {"code": e.code, "message": e.message}, "BLOCKED")
            if claims.get("external") and request.get("method") == "tools/call":
                control.run_status(p, claims["sid"], claims["run_id"], "BLOCKED")
        return error_response(request, e)
    except Exception:
        # Unknown infrastructure/auth failures must not allow a tool call.
        return error_response(request, Denied("CONTROL_UNAVAILABLE", "Authorization checks unavailable."))


SENSITIVE_KEYS = {"security_context", "context_id", "workload_access_token", "access_token", "refresh_token", "id_token", "private_key", "api_key",
                  "secret_key", "passphrase", "authorization", "gateway_token", "runtime_token"}


def redact(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if k.lower() in SENSITIVE_KEYS else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        if len(value) > 120_000:
            return "[Response exceeded the permitted size]"
        if value.startswith(("{", "[")):
            try:
                return json.dumps(redact(json.loads(value)), ensure_ascii=False)
            except ValueError:
                pass
    return value


def response_handler(event, _context):
    config, store, control, tokens = dependencies()
    mcp = event.get("mcp", {})
    req = mcp.get("gatewayRequest", {})
    result = copy.deepcopy(mcp.get("gatewayResponse", {}))
    try:
        body = result.get("body", {})
        if req.get("body", {}).get("method") == "tools/call" and body.get("error", {}).get("code") == -32042:
            from .identity import authorization_challenge, connection_key, pending_consent
            challenge = authorization_challenge(body, config)
            if challenge:
                claims, p, _agent, _scopes = gateway_identity(
                    config, store, control, tokens, req.get("headers", {}), req["body"], create=False)
                if claims and claims.get("external"):
                    key = connection_key(p.owner, "external")
                    def pending(value):
                        if not value or not value["active"]:
                            raise Denied("IDENTITY_CONNECTION_REVOKED", "Tool connection authorization changed.")
                        value["pending"] = True
                        return value, value["epoch"]
                    epoch = store.mutate(key, pending)
                    pending_consent(config, store, p, bearer(req["headers"]), challenge["authorization_url"],
                                    "external", epoch, claims=claims)
                    control.run_status(p, claims["sid"], claims["run_id"], "BLOCKED")
                    trace_claims(control, claims, "IDENTITY_REQUIRED", "gateway", "请完成工具账户授权", {}, "BLOCKED")
        if req.get("body", {}).get("method") == "tools/list" and "result" in body:
            claims, p, agent, scopes = gateway_identity(
                config, store, control, tokens, req.get("headers", {}), req.get("body", {}), create=False,
            )
            subscribed = control.ensure(p)["subscriptions"]
            allowed = skill_by_id(agent["skill_id"])["tools"]
            filtered = []
            for tool in body["result"].get("tools", []):
                name = raw_tool(tool["name"])
                if name not in allowed or TOOL_PRODUCTS.get(name) not in subscribed:
                    continue
                if scopes is not None and TOOL_SCOPES.get(name) not in scopes:
                    continue
                schema = tool.get("inputSchema", {})
                for internal in ("context_id", "security_context"):
                    schema.get("properties", {}).pop(internal, None)
                schema["required"] = [x for x in schema.get("required", []) if x not in {"context_id", "security_context"}]
                filtered.append(tool)
            body["result"]["tools"] = filtered
        return response(redact(body), result.get("statusCode", 200))
    except Exception:
        return error_response(req.get("body", {}), Denied("RESPONSE_REJECTED", "Response checks failed."))
