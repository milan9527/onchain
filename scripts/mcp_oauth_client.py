"""External MCP client: Cognito authorization code + PKCE, without AWS credentials."""
import argparse
import asyncio
import hashlib
import json
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from onchain.oauth import MCP_SCOPES
from onchain.security import b64


def authenticate(domain, client_id, gateway, scopes):
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    result = {}
    redirect = "http://localhost:8765/callback"

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            if urlparse(self.path).path != "/callback" or query.get("state") != [state]:
                self.send_error(400, "Invalid OAuth state")
                return
            result.update(code=query.get("code", [""])[0], error=query.get("error", [""])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"Authentication received. Return to the terminal.")

        def log_message(self, *_):
            pass  # Never log the authorization code or callback query.

    params = {
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect,
        "scope": " ".join(gateway + "/" + scope.removeprefix("onchain/")
                          if scope.startswith("onchain/") else scope for scope in scopes),
        "resource": gateway, "state": state,
        "code_challenge_method": "S256", "code_challenge": b64(hashlib.sha256(verifier.encode()).digest()),
    }
    with HTTPServer(("127.0.0.1", 8765), Callback) as server:
        server.timeout = 1
        url = domain + "/oauth2/authorize?" + urlencode(params)
        print("Open this URL in your browser:\n" + url, flush=True)
        webbrowser.open(url)
        deadline = time.monotonic() + 300
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if not result.get("code") or result.get("error"):
        raise RuntimeError("Cognito authentication was cancelled or timed out")
    response = httpx.post(domain + "/oauth2/token", timeout=15, data={
        "grant_type": "authorization_code", "client_id": client_id, "redirect_uri": redirect,
        "code": result["code"], "code_verifier": verifier,
    })
    if response.status_code != 200:
        raise RuntimeError("OAuth code exchange failed")
    return response.json()["access_token"]


async def invoke(gateway, token, tool, arguments):
    async with create_mcp_http_client(headers={"Authorization": "Bearer " + token}) as client:
        async with streamable_http_client(gateway, http_client=client) as (read, write, _sid):
            async with ClientSession(read, write) as session:
                await session.initialize()
                if tool == "list":
                    result = await session.list_tools()
                else:
                    result = await session.call_tool("onchain___" + tool, arguments)
                print(result.model_dump_json(indent=2))


def main():
    state_path = Path(__file__).resolve().parents[1] / ".deployment/resources.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default=state.get("oauth_gateway", {}).get("url"))
    parser.add_argument("--domain", default=state.get("outputs", {}).get("CognitoDomain"))
    parser.add_argument("--client-id", default=state.get("outputs", {}).get("ExternalMcpClientId"))
    parser.add_argument("--scope", action="append", help="Repeat for each requested scope")
    parser.add_argument("--tool", default="list")
    parser.add_argument("--arguments", default="{}")
    args = parser.parse_args()
    if not all([args.gateway, args.domain, args.client_id]):
        parser.error("--gateway, --domain and --client-id are required outside this deployment")
    token = authenticate(args.domain, args.client_id, args.gateway, args.scope or MCP_SCOPES)
    asyncio.run(invoke(args.gateway, token, args.tool, json.loads(args.arguments)))


if __name__ == "__main__":
    main()
