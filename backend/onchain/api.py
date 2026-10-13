import asyncio
import json
import time

import boto3
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.exceptions import RequestValidationError
from mangum import Mangum
from pydantic import BaseModel, ConfigDict, Field
from botocore.exceptions import ClientError

from .auth import Auth, COOKIE
from .domain import AgentSpec, TradeIntent
from .security import Denied, uid
from .services import dependencies
from .oauth import CognitoOAuth, FLOW_COOKIE, MCP_SCOPES, APPROVE
from .marketplace import Marketplace, ListingSpec, PurchaseSpec
from .tasks import task_catalog, task_plan
from .security import digest

app = FastAPI(title="Onchain Control API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")


def context():
    config, store, control, tokens = dependencies()
    return config, store, control, tokens, Auth(config, store)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.update({
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin",
        "Cache-Control": "no-store", "X-Frame-Options": "DENY",
    })
    return response


def current(request, mutating=False):
    config, _store, _control, _tokens, auth = context()
    if mutating:
        origin = request.headers.get("origin")
        if origin and origin != config.public_url:
            raise Denied("ORIGIN_DENIED", "Request origin is not allowed.")
    return auth.principal(request.cookies.get(COOKIE), request.headers.get("x-csrf-token"), mutating)


def set_cookie(response, cookie):
    config, *_ = context()
    response.set_cookie(COOKIE, cookie, httponly=True, secure=config.production,
                        samesite="strict", path="/api", max_age=3600)


@app.exception_handler(Denied)
async def denied(_request, e):
    return JSONResponse({"error": {"code": e.code, "message": e.message}}, status_code=e.status)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request, _error):
    # Default validation errors can echo rejected passwords or capability tokens.
    return JSONResponse({"error": {"code": "INVALID_REQUEST", "message": "Request does not match the schema."}},
                        status_code=422)


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class Challenge(BaseModel):
    challenge_id: str
    code: str = Field(min_length=6, max_length=8)


class AccountCode(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(default="", max_length=8)
    password: str = Field(default="", max_length=256)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)


class Prepare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    intent: TradeIntent


class Approve(BaseModel):
    intent_hash: str


class ConnectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    client_id: str
    scopes: list[str] = Field(min_length=1, max_length=5)


class GrantSpec(Prepare):
    total: str = "200"
    interval_seconds: int = 3600
    hours: int = 24


def login_limit(email):
    _config, store, *_ = context()
    from .security import digest
    key = "LOGIN_RATE#"+digest(email.lower())+"#"+str(int(time.time())//60)
    def apply(record):
        record = record or {"count": 0, "expires_at": int(time.time())+120}
        if record["count"] >= 8:
            raise Denied("RATE_LIMIT", "Too many authentication attempts. Try again later.", 429)
        record["count"] += 1
        return record, None
    store.mutate(key, apply)


@app.get("/api/health")
def health():
    config, *_ = context()
    return {"status": "ok", "mode": "paper", "runtime": config.runtime_mode,
            "model": config.model_id, "auth": "cognito" if config.production else "local",
            "oauth": bool(config.cognito_domain)}


def flow_cookie(response, value):
    config, *_ = context()
    # This cookie only binds an OAuth transaction. The application session stays SameSite=Strict.
    response.set_cookie(FLOW_COOKIE, value, httponly=True, secure=config.production,
                        samesite="lax", path="/api/auth/oauth", max_age=300)


@app.get("/api/auth/oauth/start")
def oauth_start(request: Request, review_operation: str = ""):
    config, store, *_ = context()
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise Denied("ORIGIN_DENIED", "Begin authentication from the application.")
    if review_operation:
        import re
        if not re.fullmatch(r"[a-zA-Z0-9_]{1,80}", review_operation):
            raise Denied("INVALID_OPERATION", "Invalid operation link.", 422)
    url, browser = CognitoOAuth(config, store).begin(payload={"review_operation": review_operation})
    response = RedirectResponse(url, status_code=303)
    flow_cookie(response, browser)
    return response


@app.get("/api/auth/oauth/callback")
def oauth_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    config, store, control, _tokens, auth = context()
    oauth = CognitoOAuth(config, store)
    try:
        if error or not state or not code:
            raise Denied("OAUTH_CANCELLED", "Cognito authentication was cancelled.", 401)
        flow, p, access, result = oauth.finish(state, request.cookies.get(FLOW_COOKIE), code)
        destination = config.public_url + "/"
        cookie = None
        if flow["kind"] == "login":
            cookie, _record = auth.new_session(p, access, result["refresh_token"])
            control.ensure(p)
            if flow["payload"].get("review_operation"):
                destination += "?review_operation=" + flow["payload"]["review_operation"]
        elif flow["kind"] == "operation":
            op = control.operation(p, flow["payload"]["operation_id"])
            if op["status"] != "PENDING" or op["expires_at"] <= time.time():
                raise Denied("OPERATION_EXPIRED", "Operation is no longer pending.", 409)
            if op["intent_hash"] != flow["payload"]["intent_hash"]:
                raise Denied("INTENT_CHANGED", "Operation content changed.")
            def mark(record):
                if not record or not record.get("active") or record["expires_at"] <= time.time():
                    raise Denied("SESSION_EXPIRED", "Sign in again.", 401)
                record["stepups"][op["id"]] = {
                    "expires_at": int(time.time()) + 120, "intent_hash": op["intent_hash"],
                    "auth_time": access.get("auth_time"), "method": "cognito-oauth",
                }
                return record, None
            store.mutate(flow["auth_key"], mark)
            destination += "?verified_operation=" + op["id"]
        elif flow["kind"] == "connection":
            oauth.save_connection(p, flow["payload"], control)
            destination += "?mcp=connected"
        else:
            raise Denied("OAUTH_FLOW_INVALID", "Unknown authentication action.")
        response = RedirectResponse(destination, status_code=303)
        if cookie:
            set_cookie(response, cookie)
    except Denied as e:
        response = RedirectResponse(config.public_url + "/?auth_error=" + e.code, status_code=303)
    response.delete_cookie(FLOW_COOKIE, path="/api/auth/oauth")
    return response


@app.post("/api/operations/{op_id}/oauth-stepup")
def oauth_stepup(op_id: str, body: Approve, request: Request, response: Response):
    p = current(request, True)
    config, store, control, _tokens, auth = context()
    op = control.operation(p, op_id)
    if op["status"] != "PENDING" or op["expires_at"] <= time.time():
        raise Denied("OPERATION_EXPIRED", "Operation is no longer pending.", 409)
    if body.intent_hash != op["intent_hash"]:
        raise Denied("INTENT_CHANGED", "Operation content changed.")
    key, _ = auth.record(request.cookies.get(COOKIE))
    url, browser = CognitoOAuth(config, store).begin(
        kind="operation", auth_key=key, payload={"operation_id": op_id, "intent_hash": body.intent_hash},
    )
    flow_cookie(response, browser)
    return {"authorization_url": url}


@app.get("/api/mcp/connections")
def mcp_connections(request: Request):
    p = current(request)
    config, store, *_ = context()
    oauth = CognitoOAuth(config, store)
    return {
        "gateway_url": config.oauth_gateway_url, "issuer": oauth.issuer,
        "authorization_endpoint": config.cognito_domain + "/oauth2/authorize",
        "token_endpoint": config.cognito_domain + "/oauth2/token",
        "client_id": config.cognito_external_client_id,
        "redirect_uri": "http://localhost:8765/callback", "scopes": oauth.provider_scopes(MCP_SCOPES),
        "connection": store.get(oauth.connection_key(p, config.cognito_external_client_id)),
    }


@app.post("/api/mcp/connections")
def mcp_connection_authorize(body: ConnectionSpec, request: Request, response: Response):
    p = current(request, True)
    config, store, control, _tokens, auth = context()
    control.agent(p, body.agent_id)
    if body.client_id != config.cognito_external_client_id or not set(body.scopes) <= set(MCP_SCOPES):
        raise Denied("INVALID_MCP_CONNECTION", "Choose a registered client and approved scopes.", 422)
    key, _ = auth.record(request.cookies.get(COOKIE))
    url, browser = CognitoOAuth(config, store).begin(kind="connection", auth_key=key, payload=body.model_dump())
    flow_cookie(response, browser)
    return {"authorization_url": url}


@app.delete("/api/mcp/connections/{client_id}")
def mcp_connection_revoke(client_id: str, request: Request):
    p = current(request, True)
    config, store, *_ = context()
    key = CognitoOAuth(config, store).connection_key(p, client_id)
    def revoke(record):
        if not record or record.get("owner") != p.owner:
            raise Denied("CONNECTION_NOT_FOUND", "Connection is not available.", 404)
        record["active"] = False
        return record, None
    store.mutate(key, revoke)
    return {"revoked": True}


@app.get("/api/identity/.well-known/openid-configuration")
def discovery():
    config, *_ = context()
    return {"issuer": config.issuer, "jwks_uri": config.issuer+"/jwks",
            "id_token_signing_alg_values_supported": ["RS256"],
            "response_types_supported": ["id_token"], "subject_types_supported": ["public"]}


@app.get("/api/identity/jwks")
def jwks():
    return context()[3].jwks()


@app.post("/api/auth/login")
def login(body: Login, response: Response, request: Request):
    config, _store, control, _tokens, auth = context()
    if request.headers.get("origin") not in {None, config.public_url}:
        raise Denied("ORIGIN_DENIED", "Request origin is not allowed.")
    login_limit(body.email)
    principal, challenge = auth.authenticate(body.email, body.password)
    if challenge:
        return auth.challenge(challenge, "login")
    cookie, record = auth.new_session(principal)
    set_cookie(response, cookie)
    control.ensure(principal)
    return {"user": principal.model_dump(), "csrf": record["csrf"]}


@app.post("/api/auth/signup")
def signup(body: Login, request: Request):
    config, _store, _control, _tokens, auth = context()
    if request.headers.get("origin") not in {None, config.public_url}:
        raise Denied("ORIGIN_DENIED", "Request origin is not allowed.")
    if not config.production:
        raise Denied("LOCAL_ACCOUNTS", "Use the two documented local development accounts.", 422)
    login_limit(body.email)
    try:
        auth.client().sign_up(
            ClientId=config.cognito_client_id, Username=body.email, Password=body.password,
            UserAttributes=[{"Name": "email", "Value": body.email}],
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidPasswordException":
            raise Denied("PASSWORD_POLICY", "Use 12+ characters including upper/lowercase, number and symbol.", 422) from e
        raise Denied("SIGNUP_FAILED", "Cannot create this account. Try signing in or resetting the password.", 422) from e
    return {"confirmation_required": True}


@app.post("/api/auth/confirm")
def confirm_account(body: AccountCode):
    config, _store, _control, _tokens, auth = context()
    if not config.production:
        raise Denied("LOCAL_ACCOUNTS", "Local accounts are already confirmed.", 422)
    login_limit(body.email)
    try:
        auth.client().confirm_sign_up(ClientId=config.cognito_client_id, Username=body.email,
                                     ConfirmationCode=body.code)
    except ClientError as e:
        raise Denied("CONFIRM_FAILED", "Verification code is invalid or expired.", 422) from e
    return {"confirmed": True}


@app.post("/api/auth/password-reset")
def reset_password(body: AccountCode):
    config, _store, _control, _tokens, auth = context()
    if not config.production:
        raise Denied("LOCAL_ACCOUNTS", "Local accounts use the documented development password.", 422)
    login_limit(body.email)
    try:
        if not body.password:
            auth.client().forgot_password(ClientId=config.cognito_client_id, Username=body.email)
        else:
            auth.client().confirm_forgot_password(
                ClientId=config.cognito_client_id, Username=body.email,
                ConfirmationCode=body.code, Password=body.password,
            )
    except ClientError as e:
        raise Denied("RESET_FAILED", "Cannot complete password recovery. Check the code and password policy.", 422) from e
    return {"ok": True}


@app.post("/api/auth/challenge")
def complete_challenge(body: Challenge, request: Request, response: Response):
    config, _store, control, _tokens, auth = context()
    if request.headers.get("origin") not in {None, config.public_url}:
        raise Denied("ORIGIN_DENIED", "Request origin is not allowed.")
    principal, challenge = auth.resolve_challenge(body.challenge_id, body.code, request.cookies.get(COOKIE))
    if challenge["kind"] == "stepup":
        auth.mark_stepup(request.cookies.get(COOKIE), principal, challenge["operation_id"])
        return {"verified": True}
    cookie, record = auth.new_session(principal)
    set_cookie(response, cookie)
    control.ensure(principal)
    return {"user": principal.model_dump(), "csrf": record["csrf"]}


@app.get("/api/auth/me")
def me(request: Request):
    p = current(request)
    *_, auth = context()
    _, record = auth.record(request.cookies.get(COOKIE))
    return {"user": p.model_dump(), "csrf": record["csrf"]}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    current(request, True)
    context()[4].logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/api")
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard(request: Request):
    return context()[2].snapshot(current(request))


@app.get("/api/catalog")
def catalog(request: Request):
    current(request)
    config, _store, control, *_ = context()
    from copy import deepcopy
    result = deepcopy(control.catalog())
    for product in result["products"]:
        uses_provider = product.get("provider") == "okx" or (
            config.onchain_data_mode == "live" and "get_quote" in product["tools"]
        )
        product["data_mode"] = config.onchain_data_mode if uses_provider else "paper"
        product["availability"] = (
            "REQUIRES_OKX" if uses_provider and config.onchain_data_mode == "live" and not config.okx_secret_arn else "READY"
        )
    result["onchain_data_mode"] = config.onchain_data_mode
    result["templates"] = task_catalog(config)
    return result


@app.post("/api/catalog/{product}/subscription")
def subscribe(product: str, body: dict, request: Request):
    return {"subscriptions": context()[2].subscribe(current(request, True), product, bool(body.get("enabled")))}


@app.post("/api/agents")
def create_agent(body: AgentSpec, request: Request):
    return context()[2].create_agent(current(request, True), body)


@app.get("/api/marketplace")
def marketplace(request: Request):
    return Marketplace(context()[2]).catalog(current(request))


@app.post("/api/marketplace/listings")
def publish_listing(body: ListingSpec, request: Request):
    return Marketplace(context()[2]).publish(current(request, True), body)


class ListingStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str


@app.post("/api/marketplace/listings/{listing_id}/status")
def listing_status(listing_id: str, body: ListingStatus, request: Request):
    return Marketplace(context()[2]).status(current(request, True), listing_id, body.status)


@app.post("/api/marketplace/listings/{listing_id}/purchase")
def prepare_purchase(listing_id: str, body: PurchaseSpec, request: Request):
    return Marketplace(context()[2]).prepare(current(request, True), listing_id, body)


@app.post("/api/marketplace/purchases/{purchase_id}/revoke")
def revoke_purchase(purchase_id: str, request: Request):
    return Marketplace(context()[2]).revoke(current(request, True), purchase_id)


@app.post("/api/agents/{agent_id}/status")
def agent_status(agent_id: str, body: dict, request: Request):
    return context()[2].set_agent_status(current(request, True), agent_id, body.get("status"))


@app.delete("/api/agents/{agent_id}")
def agent_delete(agent_id: str, request: Request):
    return context()[2].delete_agent(current(request, True), agent_id)


@app.post("/api/agents/{agent_id}/conversations")
def conversation_create(agent_id: str, request: Request):
    p = current(request, True)
    key, _ = context()[4].record(request.cookies.get(COOKIE))
    control = context()[2]
    session = control.create_conversation(p, agent_id, key)
    return control.public_conversation(p, session["id"], key)


@app.get("/api/agents/{agent_id}/conversations")
def conversation_list(agent_id: str, request: Request):
    return {"conversations": context()[2].list_conversations(current(request), agent_id),
            "retention_days": 30, "limit": 100}


@app.get("/api/conversations/{sid}")
def conversation_get(sid: str, request: Request):
    p = current(request)
    key, _ = context()[4].record(request.cookies.get(COOKIE))
    return context()[2].public_conversation(p, sid, key)


@app.get("/api/conversations/{sid}/runs/{run_id}")
def run_get(sid: str, run_id: str, request: Request):
    return context()[2].run_record(current(request), sid, run_id)


@app.delete("/api/conversations/{sid}")
def conversation_close(sid: str, request: Request):
    context()[2].close_conversation(current(request, True), sid)
    return {"ok": True}


async def enqueue(p, sid, kind, **kwargs):
    config, _store, control, tokens, _auth = context()
    run_id = uid("run_")
    control.lease_conversation(p, sid, run_id)
    claims = control.capability(p, sid, run_id)
    if kind == "task":
        claims["task_hash"] = digest(kwargs["task"])
    job = {
        "kind": kind, "sid": sid, "run_id": run_id, "principal": p.model_dump(),
        "runtime_token": tokens.issue("onchain-runtime", claims, ttl=300),
        "gateway_token": tokens.issue("onchain-gateway", claims, ttl=300), **kwargs,
    }
    if kind in {"chat", "task"}:
        control.append_message(p, sid, "user", kwargs["message"])
    try:
        control.start_run(p, sid, run_id, kind)
        if config.queue_url:
            boto3.client("sqs", region_name=config.region).send_message(
                QueueUrl=config.queue_url, MessageBody=json.dumps(job),
            )
        else:
            from .worker import process
            # Local development only. Production jobs are durable SQS messages.
            asyncio.create_task(process(job))
    except Exception:
        control.trace(p, sid, run_id, "QUEUE_FAILED", "queue", "任务提交失败", status="ERROR")
        control.run_status(p, sid, run_id, "FAILED")
        control.release_conversation(sid, run_id)
        raise
    return {"run_id": run_id, "conversation_id": sid, "status": "QUEUED"}


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str = Field(max_length=80)
    template_digest: str = Field(min_length=64, max_length=64)
    parameters: dict = Field(default_factory=dict)


@app.post("/api/conversations/{sid}/tasks", status_code=202)
async def run_task(sid: str, body: TaskRequest, request: Request):
    p = current(request, True)
    _config, _store, control, _tokens, auth = context()
    key, _ = auth.record(request.cookies.get(COOKIE))
    session = control.conversation(p, sid)
    if session.get("auth_key") != key:
        raise Denied("SESSION_READ_ONLY", "历史会话仅供查看，请新建会话继续。", 409)
    template, plan = task_plan(body.template_id, body.parameters, body.template_digest)
    agent = control.agent(p, session["agent_id"])
    if (agent.get("template_id") == template["id"] and agent.get("template_digest")
            and agent["template_digest"] != template["digest"]):
        raise Denied("TEMPLATE_CHANGED", "Purchased task version requires a new review.", 409)
    claims = control.capability(p, sid, uid("task_check_"))
    for item in plan:
        control.authorize_tool(claims, item["tool"], item["arguments"], count=False)
    return await enqueue(p, sid, "task", task=body.model_dump(), message="运行任务模板：" + template["name"])


@app.post("/api/conversations/{sid}/messages", status_code=202)
async def chat(sid: str, body: Message, request: Request):
    p = current(request, True)
    key, _ = context()[4].record(request.cookies.get(COOKIE))
    session = context()[2].conversation(p, sid)
    if session.get("auth_key") != key:
        raise Denied("SESSION_READ_ONLY", "历史会话仅供查看，请新建会话继续。", 409)
    return await enqueue(p, sid, "chat", message=body.message)


@app.post("/api/operations/prepare")
def prepare(body: Prepare, request: Request):
    p = current(request, True)
    from .services import quote_eth
    config, _store, control, *_ = context()
    return control.prepare_trade(p, body.agent_id, body.intent, uid("manual_"), quote=quote_eth(config))


@app.get("/api/operations/{op_id}")
def operation(op_id: str, request: Request):
    return context()[2].operation(current(request), op_id)


@app.post("/api/operations/{op_id}/stepup")
def stepup(op_id: str, body: Login, request: Request):
    p = current(request, True)
    _config, _store, control, _tokens, auth = context()
    control.operation(p, op_id)
    login_limit(body.email)
    verified, challenge = auth.authenticate(body.email, body.password)
    if challenge:
        key, _ = auth.record(request.cookies.get(COOKIE))
        return auth.challenge(challenge, "stepup", op_id, key)
    if verified.sub != p.sub:
        raise Denied("STEP_UP_OWNER", "Use the same user account.")
    auth.mark_stepup(request.cookies.get(COOKIE), verified, op_id)
    return {"verified": True}


@app.post("/api/operations/{op_id}/approve")
def approve(op_id: str, body: Approve, request: Request):
    p = current(request, True)
    config, store, control, _tokens, auth = context()
    if config.production and config.cognito_domain:
        _key, record = auth.record(request.cookies.get(COOKIE))
        CognitoOAuth(config, store).scope(record["oauth"], APPROVE)
        proof = record["stepups"].get(op_id)
        if not isinstance(proof, dict) or proof.get("intent_hash") != body.intent_hash:
            raise Denied("STEP_UP_REQUIRED", "Verify this exact operation through Cognito.", 401)
    stepup_op = auth.consume_stepup(request.cookies.get(COOKIE), op_id)
    return control.approve(p, op_id, stepup_op, body.intent_hash)


@app.post("/api/operations/{op_id}/reject")
def reject(op_id: str, request: Request):
    return context()[2].reject(current(request, True), op_id)


@app.post("/api/operations/{op_id}/execute", status_code=202)
async def execute(op_id: str, request: Request):
    p = current(request, True)
    control = context()[2]
    op = control.operation(p, op_id)
    agent = control.agent(p, op["agent_id"])
    control.check_executable(p, op, agent)
    key, _ = context()[4].record(request.cookies.get(COOKIE))
    session = None
    if op.get("conversation_id"):
        try:
            candidate = control.conversation(p, op["conversation_id"])
            if candidate.get("auth_key") == key and candidate["agent_id"] == agent["id"]:
                session = candidate
        except Denied:
            pass
    if session is None:
        session = control.create_conversation(p, agent["id"], key, kind="execute")
    return await enqueue(p, session["id"], "execute", operation_id=op_id)


@app.post("/api/grants/prepare")
def grant_prepare(body: GrantSpec, request: Request):
    return context()[2].prepare_grant(
        current(request, True), body.agent_id, body.intent, body.total, body.interval_seconds, body.hours,
    )


@app.post("/api/grants/{grant_id}/revoke")
def grant_revoke(grant_id: str, request: Request):
    return context()[2].revoke_grant(current(request, True), grant_id)


@app.post("/api/operations/archive")
def archive(request: Request):
    p = current(request, True)
    def apply(state):
        archived = [op for op in state["operations"].values()
                    if op["status"] in {"CONFIRMED", "REJECTED", "CANCELLED"} and op["kind"] == "TRADE"]
        state["operations"] = {k: op for k, op in state["operations"].items() if op not in archived}
        return {"archived": archived}
    # Export is returned to the user's browser; active grants and pending operations remain.
    return context()[2].mutate(p, apply)


@app.get("/api/export")
def export(request: Request):
    return context()[2].snapshot(current(request))


@app.post("/api/security/probe")
def security_probe(body: dict, request: Request):
    """Exercise exactly the Gateway request interceptor without executing any tool."""
    p = current(request, True)
    _config, _store, control, tokens, _auth = context()
    agent_id = body.get("agent_id", "")
    session = control.create_conversation(p, agent_id, kind="probe")
    claims = control.capability(p, session["id"], uid("probe_"))
    from .interceptors import request_handler
    test = {"mcp": {"gatewayRequest": {
        "headers": {"Authorization": "Bearer "+tokens.issue("onchain-gateway", claims)},
        "body": {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": body.get("tool", ""), "arguments": body.get("arguments", {})}},
    }}}
    try:
        result = request_handler(test, None)["mcp"]
        if "transformedGatewayResponse" in result:
            return {"decision": "DENY", "detail": result["transformedGatewayResponse"]["body"].get("error")}
        return {"decision": "ALLOW", "detail": "Checks passed; probe does not execute the tool."}
    finally:
        control.close_conversation(p, session["id"])


@app.post("/api/auth/mfa/enroll")
def enroll_mfa(body: Login, request: Request):
    p = current(request, True)
    config, store, _control, _tokens, auth = context()
    if not config.production:
        raise Denied("COGNITO_REQUIRED", "MFA enrollment is available in the deployed Cognito environment.", 422)
    login_limit(body.email)
    try:
        login_result = auth.client().initiate_auth(
            ClientId=config.cognito_client_id, AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": body.email, "PASSWORD": body.password},
        )
        if "AuthenticationResult" not in login_result:
            raise Denied("MFA_ALREADY_ENABLED", "MFA is already enabled or an additional challenge is required.")
        access = login_result["AuthenticationResult"]["AccessToken"]
        user = auth.client().get_user(AccessToken=access)
        attributes = {a["Name"]: a["Value"] for a in user["UserAttributes"]}
        if attributes["sub"] != p.sub:
            raise Denied("MFA_OWNER", "Use your current account.")
        result = auth.client().associate_software_token(AccessToken=access)
        enrollment = uid("mfa_")
        store.put("MFA#"+enrollment, {
            "owner": p.owner, "access_token": access,
            "expires_at": int(time.time())+180, "used": False,
        })
        return {"enrollment_id": enrollment, "secret": result["SecretCode"]}
    except ClientError as e:
        raise Denied("MFA_ENROLLMENT_FAILED", "MFA enrollment could not be started.", 422) from e


@app.post("/api/auth/mfa/verify")
def verify_mfa(body: dict, request: Request):
    p = current(request, True)
    _config, store, _control, _tokens, auth = context()
    def claim(record):
        if not record or record.get("owner") != p.owner or record["used"] or record["expires_at"] <= time.time():
            raise Denied("MFA_EXPIRED", "Enrollment is unavailable or expired.", 422)
        record["used"] = True
        return record, record
    record = store.mutate("MFA#"+str(body.get("enrollment_id")), claim)
    try:
        result = auth.client().verify_software_token(
            AccessToken=record["access_token"], UserCode=str(body.get("code", "")),
        )
        if result["Status"] != "SUCCESS":
            raise Denied("MFA_INVALID_CODE", "Authenticator code was not accepted.", 422)
        auth.client().set_user_mfa_preference(
            AccessToken=record["access_token"],
            SoftwareTokenMfaSettings={"Enabled": True, "PreferredMfa": True},
        )
        store.delete("MFA#"+str(body.get("enrollment_id")))
        return {"enabled": True}
    except ClientError as e:
        raise Denied("MFA_INVALID_CODE", "Authenticator code was not accepted. Restart enrollment.", 422) from e


app.add_middleware(
    CORSMiddleware,
    allow_origins=[__import__("os").getenv("PUBLIC_URL", "http://localhost:5173")],
    allow_credentials=True, allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)
handler = Mangum(app, lifespan="off")
