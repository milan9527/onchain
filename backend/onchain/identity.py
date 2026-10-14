"""AgentCore Identity consent continuation. Downstream tokens remain in the managed Token Vault."""
import copy
import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import boto3
import jwt
from botocore.exceptions import ClientError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .security import Denied, digest


def consent_proof(config, value, owner, binding, *, decrypt=False):
    """Short-lived original inbound JWT, required verbatim by CompleteResourceTokenAuth."""
    aad = {"purpose": "identity-consent", "owner": owner, "binding": binding}
    try:
        if config.production:
            kms = boto3.client("kms", region_name=config.region)
            if decrypt:
                return kms.decrypt(KeyId=config.oauth_vault_key_id, CiphertextBlob=base64.b64decode(value),
                                   EncryptionContext=aad)["Plaintext"].decode()
            sealed = kms.encrypt(KeyId=config.oauth_vault_key_id, Plaintext=value.encode(),
                                 EncryptionContext=aad)["CiphertextBlob"]
        else:
            cipher = AESGCM(hashlib.sha256(Path(config.local_key_path).read_bytes()).digest())
            context = json.dumps(aad, sort_keys=True).encode()
            if decrypt:
                raw = base64.b64decode(value)
                return cipher.decrypt(raw[:12], raw[12:], context).decode()
            nonce = os.urandom(12)
            sealed = nonce + cipher.encrypt(nonce, value.encode(), context)
        return base64.b64encode(sealed).decode()
    except (ClientError, ValueError, TypeError, InvalidTag) as e:
        raise Denied("IDENTITY_PROOF_INVALID", "Authorization proof is unavailable.", 403) from e


def proof_expiry(token):
    # Only called for a server-issued or already verified token. Completion still verifies current login.
    return min(int(time.time()) + 600, jwt.decode(token, options={"verify_signature": False})["exp"])


def connection_key(owner, source="internal"):
    return f"IDENTITY_CONNECTION#{owner}#{source}"


def pending_consent(config, store, p, original_token, url, source, epoch, *, auth_key=None, claims=None):
    uri = parse_qs(urlsplit(url).query).get("request_uri", [""])[0]
    if not re.fullmatch(r"urn:ietf:params:oauth:request_uri:[a-zA-Z0-9._~-]{1,980}", uri):
        raise Denied("IDENTITY_SESSION_INVALID", "Invalid Identity authorization session.", 422)
    store.put("IDENTITY_PENDING#" + digest(uri), {
        "owner": p.owner, "auth_key": auth_key, "source": source, "epoch": epoch, "claims": claims,
        "proof": consent_proof(config, original_token, p.owner, "pending:" + digest(uri)),
        "expires_at": proof_expiry(original_token),
    })


async def gateway_connection(config, store, control, tokens, p, auth_key):
    """Probe the actual Gateway workload with an owned, permitted read-only tool."""
    from .catalog import skill_by_id, TOOL_PRODUCTS
    from runtimes.agent import call_gateway
    candidates = {
        "get_portfolio": {},
        "get_token_info": {"chain": "8453", "token_address": "0x4200000000000000000000000000000000000006"},
        "get_token_price": {"chain": "8453", "token_address": "0x4200000000000000000000000000000000000006"},
        "get_supported_chains": {},
    }
    state = control.ensure(p)
    selection = next(((agent, tool, args) for agent in state["agents"].values() if agent["status"] == "ACTIVE"
                      for tool, args in candidates.items() if tool in skill_by_id(agent["skill_id"])["tools"]
                      and TOOL_PRODUCTS[tool] in state["subscriptions"]), None)
    if not selection:
        raise Denied("IDENTITY_AGENT_REQUIRED", "请先创建一个 Agent 并启用其 MCP 工具。", 409)
    agent, tool, args = selection
    session = control.create_conversation(p, agent["id"], auth_key, kind="probe")
    from .security import uid
    original = tokens.issue("onchain-gateway", control.capability(p, session["id"], uid("run_")))
    try:
        result = await call_gateway(config.gateway_url, original, tool, args)
        error = result.get("error", {})
        if error.get("code") == "MCP_AUTH_REQUIRED":
            return {"authorization_url": error["authorization_url"], "original_token": original}
        if error:
            raise Denied("IDENTITY_CONNECT_FAILED", "工具连接检查失败，请在 Agent 工作台查看工具权限。", 409)
        return {"connected": True}
    finally:
        control.close_conversation(p, session["id"])


def authorization_challenge(value, config):
    """Accept the regional Identity authorization endpoint or the configured Cognito client."""
    if not config.mcp_oauth_client_id:
        return None
    serialized = json.dumps(value, ensure_ascii=False)
    for candidate in re.findall(r'https://[^\s"<>\\]+', serialized):
        candidate = candidate.rstrip(".,)")
        parsed = urlsplit(candidate)
        query = parse_qs(parsed.query)
        identity_url = (
            parsed.netloc == f"bedrock-agentcore.{config.region}.amazonaws.com"
            and parsed.path == "/identities/oauth2/authorize"
            and len(query.get("request_uri", [])) == 1
            and re.fullmatch(r"urn:ietf:params:oauth:request_uri:[a-zA-Z0-9._~-]{1,980}", query["request_uri"][0])
        )
        cognito_url = (parsed.netloc == urlsplit(config.cognito_domain).netloc
                       and parsed.path == "/oauth2/authorize"
                       and query.get("client_id") == [config.mcp_oauth_client_id])
        if parsed.scheme == "https" and (identity_url or cognito_url):
            return {"code": "MCP_AUTH_REQUIRED", "message": "首次使用工具前，请授权连接本人账户。",
                    "authorization_url": candidate}
    return None


def hold_for_consent(result, job, store, principal, config):
    """Persist a bounded retry and encrypted original JWT; never put credentials in chat/trace."""
    outcome = result.get("tool_result", result)
    error = outcome.get("error") if isinstance(outcome, dict) else None
    if not isinstance(error, dict) or error.get("code") != "MCP_AUTH_REQUIRED":
        return result
    url = error.get("authorization_url")
    if not url:
        return result
    store.put("IDENTITY_RESUME#" + job["run_id"], {
        "owner": principal.owner, "sid": job["sid"], "authorization_url": url,
        "job": {k: job[k] for k in ("kind", "message", "task", "operation_id") if k in job},
        "proof": consent_proof(config, job["gateway_token"], principal.owner, "resume:" + job["run_id"]),
        "session_uri_hash": digest(parse_qs(urlsplit(url).query)["request_uri"][0])
        if parse_qs(urlsplit(url).query).get("request_uri") else None,
        "expires_at": proof_expiry(job["gateway_token"]), "used": False,
    })
    def clean(value):
        if isinstance(value, dict):
            filtered = {k: clean(v) for k, v in value.items() if k != "authorization_url"}
            if filtered.get("code") == "MCP_AUTH_REQUIRED":
                filtered["resume_id"] = job["run_id"]
            return filtered
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    return clean(copy.deepcopy(result))


def resume_record(store, control, p, auth_key, reference):
    if not re.fullmatch(r"run_[0-9a-f]{36}", reference):
        raise Denied("IDENTITY_RESUME_INVALID", "Invalid authorization continuation.", 422)
    record = store.get("IDENTITY_RESUME#" + reference)
    if (not record or record.get("used") or record["owner"] != p.owner
            or record["expires_at"] <= time.time()):
        raise Denied("IDENTITY_RESUME_EXPIRED", "Authorization continuation expired.", 409)
    session = control.conversation(p, record["sid"])
    if session.get("auth_key") != auth_key:
        raise Denied("IDENTITY_LOGIN_MISMATCH", "Continue from the original login session.", 401)
    return record, session
