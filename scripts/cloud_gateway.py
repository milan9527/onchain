"""Verify real Gateway discovery, argument interception and conversation isolation."""
import asyncio
import json
from pathlib import Path

import httpx
from mcp import ClientSession, McpError
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from onchain.config import Settings
from onchain.domain import Control
from onchain.security import Principal, TokenService, uid
from onchain.store import DynamoStore

ROOT = Path(__file__).resolve().parents[1]


async def main():
    state = json.loads((ROOT / ".deployment/resources.json").read_text())
    credentials = json.loads((ROOT / ".deployment/smoke-credentials.json").read_text())["bob"]
    config = Settings(**{k.lower(): v for k, v in state["environment"].items()})
    control = Control(DynamoStore(config.table_name, config.region))
    tokens = TokenService(config)
    with httpx.Client(base_url=state["outputs"]["WebsiteUrl"], timeout=30) as api:
        login = api.post("/api/auth/login", json=credentials)
        login.raise_for_status()
        principal = Principal.model_validate(login.json()["user"])
        api.headers["X-CSRF-Token"] = login.json()["csrf"]
        agents = api.get("/api/dashboard").json()["agents"]
        agent = next((a for a in agents if a["name"] == "Gateway boundary test"), None)
        if not agent:
            created = api.post("/api/agents", json={"name": "Gateway boundary test"})
            created.raise_for_status()
            agent = created.json()
        conversations = []
        for _ in range(2):
            response = api.post(f"/api/agents/{agent['id']}/conversations")
            response.raise_for_status()
            conversations.append(response.json())
        caps = [tokens.issue("onchain-gateway", control.capability(principal, c["id"], uid("test_")))
                for c in conversations]
        try:
            async with create_mcp_http_client(headers={"Authorization": "Bearer "+caps[0]}) as client:
                async with streamable_http_client(state["gateway"]["url"], http_client=client) as (r, w, sid):
                    async with ClientSession(r, w) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert len(tools.tools) == 6
                        assert all(not {"security_context", "context_id"} & t.inputSchema.get("properties", {}).keys()
                                   for t in tools.tools)
                        result = await session.call_tool("onchain___get_portfolio", {})
                        assert not result.isError, result
                        assert "wallet" in result.content[0].text
                        print("PASS: Gateway discovery filters internal context; real MCP reads the owned wallet")
                        try:
                            await session.call_tool("onchain___get_portfolio", {"userId": "victim"})
                            raise AssertionError("Gateway accepted an injected identity")
                        except McpError as error:
                            assert error.error.data["code"] == "INVALID_TOOL_ARGUMENTS", error
                        assert sid(), "Gateway must establish a managed MCP session"
                        client.headers["Authorization"] = "Bearer "+caps[1]
                        try:
                            await session.call_tool("onchain___get_portfolio", {})
                            raise AssertionError("Gateway reused a session for another conversation")
                        except McpError as error:
                            assert error.error.data["code"] == "GATEWAY_SESSION_MISMATCH", error
                        print("PASS: actual Gateway rejects identity injection and cross-conversation session reuse")
        finally:
            for conversation in conversations:
                api.delete("/api/conversations/"+conversation["id"])


if __name__ == "__main__":
    asyncio.run(main())
