# Cognito OAuth and external MCP

Open the platform's **MCP / Skill 管理 → 外部 MCP · Cognito OAuth** panel. Select the Agent, choose
read/prepare/execute permissions, and click **验证并授权连接**. Cognito requires fresh authentication
and any configured MFA. The connection lasts at most 24 hours and can be revoked in the same panel.
Replacing a connection also invalidates its old sessions.

The panel displays the deployed Gateway URL, client ID, authorization and token endpoints. The public
client has one exact callback, `http://localhost:8765/callback`. It has no secret and no AWS credentials.
It is intended for local MCP clients. Other applications need a separately registered and reviewed
Cognito app client and exact redirect URI, plus the corresponding server allowlist entry. This build
does not expose dynamic client registration or accept arbitrary client IDs/callback URLs.

| Parameter | Value |
|---|---|
| Grant | `authorization_code` |
| PKCE | `S256` |
| Resource / access-token audience | Exact public OAuth Gateway URL including `/mcp` |
| Required scope | `RESOURCE/mcp.invoke` |
| Read scope | `RESOURCE/portfolio.read` |
| Public Onchain OS research scope | `RESOURCE/market.read` |
| Prepare scope | `RESOURCE/trade.prepare` |
| Execute scope | `RESOURCE/trade.execute` |
| Authorization | `COGNITO_DOMAIN/oauth2/authorize` |
| Token | `COGNITO_DOMAIN/oauth2/token` |
| Discovery issuer | `https://cognito-idp.REGION.amazonaws.com/POOL_ID` |

An unauthenticated Gateway request returns a `WWW-Authenticate` challenge pointing to the Gateway's
native `/.well-known/oauth-protected-resource` metadata. Fetch its advertised authorization server's
OIDC metadata. Use a pre-registered client ID; a metadata document without a registration endpoint
does not mean dynamic registration is supported.

`RESOURCE` is the exact public Gateway URL, including `/mcp`. Cognito requires custom scopes to belong
to the resource server requested in `resource`; an unrelated short identifier is rejected. The deployment
registers this resource server after allocating the Gateway URL and reconciles both Cognito clients.
The platform uses `onchain/...` as internal permission names after cryptographic validation; copy the
complete resource-prefixed values from the MCP panel when configuring an external OAuth client.

The repository includes a complete PKCE client. Run this on the computer with your browser, so the
loopback callback reaches that computer:

```bash
python scripts/mcp_oauth_client.py \
  --gateway PUBLIC_OAUTH_GATEWAY_URL \
  --domain COGNITO_DOMAIN \
  --client-id REGISTERED_CLIENT_ID \
  --tool get_portfolio
```

Within this workspace, deployment metadata supplies these three parameters automatically. The script
opens a browser, validates state, exchanges the code with its private PKCE verifier, initializes MCP,
and invokes the selected tool. It does not print tokens, persist them or use AWS credentials. Use
`--tool list` for scope-filtered discovery; repeat `--scope` to request narrower scopes.

`prepare_trade` returns an immutable pending operation and a platform `approval_url`. Open that link,
sign in if necessary, review the amount/chain/slippage, and explicitly confirm. Login preserves the
operation link and does not grant approval. The BFF sends an operation-bound OAuth
re-authentication request. On return, the same browser login consumes a two-minute single-use proof
and continues the trade. A raw access token, successful MCP login, skill text or tool argument cannot
approve it. `execute_trade` still checks the specific operation's approval, owner, Agent and limits.

External sessions bind `(owner, client_id, OAuth token family, connection version, Mcp-Session-Id)`.
Refresh within the same OAuth family may continue a session; another login must initialize a new one.
No-session tool calls, changed bindings, expired tokens and revoked connections are rejected. The
platform exposes only owner-scoped conversation history and sanitized Gateway/executor events.

The internal Gateway used by trusted workers has a different issuer and audience. It carries delegated
execution context and supports separately approved offline schedules. Neither a Cognito token sent to
that Gateway nor a platform capability sent to the public OAuth Gateway is accepted. Both Gateways
use IAM only to reach the IAM-protected MCP Runtime, which independently verifies exact signed tool context.
The Runtime uses `networkMode=PUBLIC`; it is not a VPC-private endpoint. For the full token flow,
per-hop validation, user/session isolation and implementation links, see
[Runtime → Gateway → MCP authentication](../README.md#runtime--gateway--mcp-的认证与用户隔离).
