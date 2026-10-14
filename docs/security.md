# Security controls and boundaries

No Amazon Bedrock Guardrails are configured. Authorization is deterministic and checked outside model code.

For the current Runtime → Gateway → MCP credential flow, signature checks, user/session bindings,
IAM/OAuth choices and code references, see
[the implementation walkthrough in the README](../README.md#runtime--gateway--mcp-的认证与用户隔离).

| Threat | Enforcement |
|---|---|
| Cross-user conversation / order access | Authenticated owner lookup on every API and executor request |
| Runtime session substitution | Signed binding, actual Runtime header comparison, microVM-local identity pin |
| MCP session reuse | Gateway JWT principal plus interceptor conversation/run binding |
| User/tenant injection in tool arguments | Strict public tool JSON schema rejects extra properties |
| Skill escalation | Platform-pinned manifest digest and tool allowlist checked on each call |
| Approval fabrication | Only BFF accepts fresh user auth; no approval-issuing tool is exposed |
| Changed operation arguments | Immutable intent digest, operation-specific approval, server-side invocation argument digest |
| Order execution at an unapproved price | New manual orders bind reference price/minimum output in the approval hash; executor checks a fresh quote before settlement |
| JWT audience confusion | Separate Runtime/Gateway routing audiences and a dedicated Cognito MCP resource audience |
| Replay / duplicate execution | One atomic operation transition; duplicate execution returns stored paper receipt |
| Concurrent overspend | User aggregate conditional update atomically changes balance, daily budget and grant spend |
| Revocation race | Capability epoch and active grant rechecked at execution commit |
| Changed/disabled subscriptions | Tool call rechecks database state, not merely the tool listing |
| Agent bypass of Gateway | No data/signing credentials in Agent runtime; MCP requires dedicated user OAuth plus a short-lived Gateway-created invocation record |
| Secrets in outputs | Backend-only auth flow, output redaction, sanitized validation and application errors |
| Infrastructure check failure | Interceptor and executor fail closed |

Gateway Policy permits only the explicitly named platform tools in ENFORCE mode.
Dynamic user subscriptions, permissions, current sessions, approvals and limits are enforced by the
request interceptor and trusted executor. Neither Agent nor MCP role can alter Gateway/Policy configuration.

Only `initialize`, `notifications/initialized`, `ping`, `tools/list` and approved `tools/call` operations
are exposed. Prompts/resources/sampling and arbitrary remote tool registration are rejected.
No secrets are sent through streaming notifications.

## Authentication

Production login and operation confirmation use Cognito managed login with authorization code + S256
PKCE, a one-use server-side state record, browser-binding cookie and ID-token nonce. Confirmation uses
`prompt=login`, `max_age=0` and server verification of `auth_time`. The callback must match the original
active login and immutable operation hash. The BFF session cookie remains SameSite=Strict; only the
short-lived OAuth transaction cookie is SameSite=Lax. A same-origin CSRF-protected POST consumes proof.

Legacy production password-login and password-step-up endpoints are disabled once OAuth is configured.
TOTP enrollment in the security center retains Cognito's authenticated enrollment flow; its password
verification does not issue a platform login or transaction approval. Enabled MFA is enforced by Cognito
during managed login. The web frontend never receives access or refresh tokens. External MCP clients
receive their own Cognito tokens through their own registered OAuth callback.

External MCP calls require a resource-bound access token, a registered client and an active user-approved
connection. Read/prepare/execute scopes are distinct, and the external client cannot request the BFF's
`control` or `approve` scopes. Scope permission to execute never constitutes approval of a transaction.

Public onchain data research additionally uses `market.read`; private paper-account analysis remains
under `portfolio.read`. New tools require explicit MCP enablement and a reviewed Skill. Existing
Skill digests retain their original tool lists. Task invocations bind the template version and exact
parameters into the signed Runtime capability, then recheck each tool at Gateway and executor.
Provider payment requests, redirects and data failures never grant signing or spending authority.
Keyless demo mode changes only the data adapter. It retains the same identity, session, scope,
subscription, Skill, request-schema and executor checks. It cannot be selected by a tool argument,
does not fetch provider credentials, and does not grant any trade approval. Synthetic risk results
never certify a real contract as safe. Explicit live mode fails closed instead of substituting demo
results. Switching sources requires deployment configuration; Skill/template permissions are unchanged.
Access tokens expire after 15 minutes. Connection revocation is checked against authoritative storage
on every invocation and again by the executor. Cognito JWT signature validation alone does not provide
instant provider-side revocation; use connection revocation/Agent pause to stop platform access immediately.

This implementation supports password + optional TOTP. It does not claim to provide Passkeys, WebAuthn
transaction signatures or mandatory phishing-resistant authentication. Those require a separate
credential enrollment and user-verification design.

Login and operation approvals have separate state. Approval challenges are tied to the browser login
and operation; step-up proof expires after two minutes and is consumed once. A rejected digest requires
fresh re-authentication. Session records are revoked on logout; conversations bound to that login can
no longer invoke tools. Approved scheduled grants remain active until explicitly revoked or expired.

## Managed downstream credentials

Both Gateway targets use AgentCore Identity Token Vault with OAuth `AUTHORIZATION_CODE`. A dedicated
confidential Cognito client issues resource-bound access tokens for MCP; its client secret is managed
by the credential provider. Gateway obtains user credentials through Identity, and the MCP Runtime
uses a Cognito JWT authorizer. The old IAM-only Runtime resource policy has been removed. The public
network endpoint still requires valid OAuth, and no tool effect can occur without a valid invocation record.

Vault credentials are associated with the workload and user identity, not a business Agent/session.
Both Gateways initiate their own native authorization flows; a standalone broker's consent cannot
preauthorize a Gateway, and service-linked workload tokens cannot be obtained directly by the BFF.
Internal connection checks invoke a permitted read-only tool through the actual Gateway.
Authorization completion submits the exact original inbound JWT to `CompleteResourceTokenAuth`,
after verifying the current owner/login. AWS rejects a freshly issued JWT even for the same issuer/sub.
The original proof is temporarily KMS encrypted with owner/continuation AAD and removed on completion.
Worker and RESPONSE interceptor can only encrypt such proofs, not decrypt them. Internal connection
checks and task continuations also bind to the original platform login. External consent proofs bind
to the verified original MCP grant/session and must be completed by the same user with a current BFF
login and CSRF token. Completion rechecks the external session/grant and connection epoch.
A different downstream Cognito account fails owner comparison.
The BFF completion role also has `secretsmanager:GetSecretValue` restricted to this provider's client
secret, which Identity needs for the authorization-code exchange. It has no permission to obtain
Gateway workload tokens or retrieve users' downstream Vault tokens directly.

The REQUEST interceptor writes `CALL#sha256(context_id)` with a maximum 120-second lifetime,
user/Agent/session/run, exact tool/arguments hash and connection epoch. Public tool schemas reject
caller-supplied references. MCP verifies the actual access token, asks Executor to authorize without
side effects, pins its process identity, then calls execute. Executor independently verifies the token
and record at both phases. The MCP role has no database access; the request role no longer signs JWTs.

Revoking a tool connection increments its epoch and stops future Gateway/Executor authorization even
while Vault tokens remain cryptographically valid. It does not delete provider tokens or reverse committed
effects. The reference allows retries; financial replay protection remains the atomic order transition.
An authenticated user may explicitly restore platform tool access; still-valid Vault credentials can
then be reused. This is not a promise to force a new provider login.
Calls already beyond their final authorization check may finish; pause/revoke financial grants use the
existing checks at settlement. No claim of per-user IAM roles or database-layer ABAC is made.

The BFF login refresh-token store remains separately KMS encrypted. Downstream refresh tokens stay
in Token Vault, not DynamoDB. Downstream access tokens pass only through trusted Gateway/MCP/Executor
components and are excluded from model input, browser responses, invocation records, history and logs.

## Incident handling

Per-user limits apply across conversations. Repeated malformed or tampered tool requests in a one-minute
window pause the relevant agent and increment its capability epoch. Users can pause agents and revoke
grants immediately. Existing approved but unexecuted operations are cancelled on agent pause.

Response suppression cannot undo an external effect. The executor performs all financial checks before
committing paper state. There is no live transaction broadcaster or wallet private key in this build.

## Operational limitations

The platform supports personal tenants: one Cognito user maps to one isolated tenant. It does not
implement organization invitations or team resource sharing.

Application audit is limited to the last 150 records per user, with export. CloudWatch retains function
logs for 30 days. Longer immutable audit retention needs a dedicated append-only store.

Deterministic controls do not classify every natural-language attack. Untrusted instructions may alter
model proposals, but cannot grant additional tool capabilities or approve operations.

The Gateway's internal REQUEST/Policy ordering is not assumed; final executor checks are mandatory.

## Agent deletion and task purchases

`DELETE /api/agents/{id}` requires the current user cookie and CSRF token. A single owner-aggregate
mutation marks the Agent `DELETED`, increments its epoch, cancels pending/approved operations,
revokes all grants and marks the associated purchase deleted. Gateway/executor status and epoch
checks reject old capabilities, queued work and subsequent OAuth MCP access through that Agent.
An already-settled transaction remains a historical receipt. History retains its owner checks and
becomes read-only; deletion cannot be undone by the status endpoint, quarantine or purchase-stop.
Preparation rechecks Agent status/epoch at commit to prevent a concurrent deletion creating new
orders or grants. Tombstones free the ten-instance quota but have a separate 200-record audit cap.

Published tasks pin Skill and template digests, products and service price into purchase intent.
Only source-compatible reviewed templates may be listed. Fresh Cognito proof binds to that exact
purchase; atomic bilateral settlement creates a separate buyer-owned Agent. Plain task purchases
create no trading grant and accept no DCA budget. Order tasks still require independent operation
approval. Purchased default task versions are checked again when enqueuing the task.
