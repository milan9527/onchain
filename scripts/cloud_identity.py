"""Verify deployed OAuth/context boundaries using previously authorized synthetic accounts only."""
import asyncio
import json
import subprocess
from pathlib import Path

import boto3
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from onchain.config import Settings
from onchain.domain import AgentSpec, Control
from onchain.security import Principal, TokenService, digest, uid
from onchain.store import DynamoStore

ROOT = Path(__file__).resolve().parents[1]


async def main():
    state = json.loads((ROOT / ".deployment/resources.json").read_text())
    config = Settings(**{k.lower(): v for k, v in state["environment"].items()})
    session = boto3.Session(region_name=config.region)
    cognito, identity, functions = (session.client(name) for name in ("cognito-idp", "bedrock-agentcore", "lambda"))
    control, tokens = Control(DynamoStore(config.table_name, config.region)), TokenService(config)
    # A separate test workload obtains real Cognito tokens for adversarial MCP calls.
    # These credentials cannot preauthorize either production Gateway workload.
    probe_name = state.get("identity_broker", {}).get("name", "onchain-control-identity-acceptance")
    management = session.client("bedrock-agentcore-control")
    try:
        management.get_workload_identity(name=probe_name)
    except management.exceptions.ResourceNotFoundException:
        management.create_workload_identity(
            name=probe_name, allowedResourceOauth2ReturnUrls=[config.public_url + "/api/identity/callback/internal"])

    def account(credentials):
        # Deliberately exclude the primary user and arbitrary real accounts.
        assert credentials["email"].startswith("onchain-") and credentials["email"].endswith("@example.invalid")
        user = cognito.admin_get_user(UserPoolId=config.cognito_pool_id, Username=credentials["email"])
        sub = next(a["Value"] for a in user["UserAttributes"] if a["Name"] == "sub")
        p = Principal(sub=sub, tenant="personal-" + sub)
        original = tokens.issue("onchain-gateway", {"sub": sub, "tenant": p.tenant})
        workload = identity.get_workload_access_token_for_jwt(workloadName=probe_name, userToken=original)
        options = dict(
            workloadIdentityToken=workload["workloadAccessToken"],
            resourceCredentialProviderName=config.identity_provider_name,
            scopes=[config.mcp_oauth_scope], resources=[config.mcp_oauth_audience],
            oauth2Flow="USER_FEDERATION",
            resourceOauth2ReturnUrl=config.public_url + "/api/identity/callback/internal",
            customParameters={"prompt": "login"})
        result = identity.get_resource_oauth2_token(**options)
        if not result.get("accessToken"):
            uri = subprocess.check_output(["node", str(ROOT / "frontend/tests/identity-consent.cjs")],
                input=json.dumps({**credentials, "authorization_url": result["authorizationUrl"],
                                  "return_url": config.public_url + "/api/identity/callback/internal"}),
                text=True, timeout=60).strip()
            assert uri == result["sessionUri"]
            identity.complete_resource_token_auth(userIdentifier={"userToken": original}, sessionUri=uri)
            result = identity.get_resource_oauth2_token(**options)
        assert result.get("accessToken"), "Synthetic identity consent did not produce a credential."
        return p, result["accessToken"]

    alice, alice_token = account(json.loads((ROOT / ".deployment/identity-credentials.json").read_text()))
    bob, bob_token = account(json.loads((ROOT / ".deployment/smoke-credentials.json").read_text())["bob"])
    assert alice.owner != bob.owner
    initial_wallet = control.snapshot(alice)["wallet"]
    agent = control.create_agent(alice, AgentSpec(name="Identity boundary acceptance", skill_id="portfolio-assistant"))
    conversations = []

    def invoke(function, event):
        response = functions.invoke(FunctionName=state["outputs"][function + "FunctionArn"], Payload=json.dumps(event))
        if response.get("FunctionError"):
            raise RuntimeError(function + " failed")
        return json.loads(response["Payload"].read())

    def grant():
        conversation = control.create_conversation(alice, agent["id"], kind="probe")
        conversations.append(conversation)
        capability = tokens.issue("onchain-gateway", control.capability(alice, conversation["id"], uid("run_")))
        result = invoke("Request", {"mcp": {"gatewayRequest": {
            "headers": {"Authorization": "Bearer " + capability},
            "body": {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "onchain___get_portfolio", "arguments": {}}},
        }}})
        return result["mcp"]["transformedGatewayRequest"]["body"]["params"]["arguments"]["context_id"]

    try:
        reference, other_session = grant(), grant()
        def execute(token, ref=reference, tool="get_portfolio", args=None):
            return invoke("Executor", {"phase": "execute", "access_token": token, "context_id": ref,
                                       "tool": tool, "arguments": args or {}})
        assert execute(bob_token)["error"]["code"] == "CONTEXT_OWNER_MISMATCH"
        assert execute(alice_token, tool="get_history")["error"]["code"] == "ARGUMENTS_CHANGED"
        assert execute(alice_token, ref="")["error"]["code"] == "CONTEXT_REQUIRED"
        assert execute(alice_token)["result"]["wallet"] == initial_wallet
        print("PASS: deployed Executor independently rejects another user, substituted tools and missing references")

        # Native JWT Runtime access alone cannot authorize a tool. Pinning precedes any tool effects.
        async with create_mcp_http_client(headers={
            "Authorization": "Bearer " + alice_token,
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": uid("identity_boundary_"),
        }) as client:
            async with streamable_http_client(config.mcp_oauth_audience, http_client=client) as (read, write, _):
                async with ClientSession(read, write) as mcp:
                    await mcp.initialize()
                    missing = await mcp.call_tool("get_portfolio", {})
                    assert "CONTEXT_REQUIRED" in missing.content[0].text
                    own = await mcp.call_tool("get_portfolio", {"context_id": reference})
                    assert json.loads(own.content[0].text)["wallet"] == initial_wallet
                    changed = await mcp.call_tool("get_portfolio", {"context_id": other_session})
                    assert changed.isError and "another session" in changed.content[0].text
        control.store.mutate("CALL#" + digest(reference), lambda row: ({**row, "expires_at": 1}, None))
        assert execute(alice_token)["error"]["code"] == "CONTEXT_EXPIRED"
        assert control.snapshot(alice)["wallet"] == initial_wallet
        print("PASS: real MCP Runtime enforces references and same-user session pinning; expired grants fail; no balance changes")
    finally:
        for conversation in conversations:
            control.close_conversation(alice, conversation["id"])
        control.delete_agent(alice, agent["id"])


if __name__ == "__main__":
    asyncio.run(main())
