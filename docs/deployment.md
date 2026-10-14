# Deployed environment

- Website: https://d2omddnartt627.cloudfront.net
- API: https://nlaqtyuf78.execute-api.us-east-1.amazonaws.com
- Region: `us-east-1`
- CloudFormation stack: `OnchainControl`
- Bedrock inference profile: `us.openai.gpt-6-sol`
- Trading mode: `paper`
- Research data mode: `demo` (no data provider key required)
- Public OAuth MCP: https://onchain-oauth-gateway-ud4iglagnm.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
- External public client ID: `17drk512rc7nr51esoflfonpd8`
- External callback: `http://localhost:8765/callback`

The frontend is independently deployed to S3/CloudFront. The FastAPI backend runs on Lambda behind
HTTP API. Agent reasoning and MCP tools run in separate AgentCore Runtimes. The actual Gateway uses
JWT authentication, REQUEST/RESPONSE interceptors and a Cedar policy engine in `ENFORCE` mode.

Exact resource identifiers and environment configuration are saved in `.deployment/resources.json`.
No wallet or data provider secret has been provisioned. Registration is available through the website; the
synthetic acceptance accounts are retained in Cognito, with credentials kept only in the ignored,
mode-0600 `.deployment/smoke-credentials.json`.

## Acceptance

Provider-neutral presentation release, 2026-10-13:
MCP products, task forms, data notices, errors, tool results, documentation and the editable
architecture diagram use generic chain-data labels. Historical result metadata is normalized
only for display; stored audit records and signed operation data are not rewritten.
All 10 Skill digests and 15 template digests remain unchanged, preserving existing Agents and listings.
The documented credential setting is `DATA_PROVIDER_SECRET_ARN`, with
`--data-provider-secret-arn` for deployment; previous configuration names remain accepted during upgrades.

Ruff, all 109 backend tests, six focused frontend regressions and the production build pass.
The backend bundle SHA256 is `665b3590ef070aab89991684e0cec18e850c695462ce8570184bf915f4876ba2`;
the frontend entry is `index-DqSbib0X.js`. All six Lambda hashes, both READY Runtime artifacts,
both Gateway ENFORCE configurations and CloudFront's index were checked against this release.
Cloud acceptance used an existing synthetic research account: Cognito login, neutral template/MCP
catalogs, a 390px mobile layout, and the real four-tool research plan through Agent Runtime,
Gateway and MCP all passed. Fresh results retain demo disclosure, contain generic provider labels,
and leave the paper wallet unchanged. Screenshots are retained under `.deployment/neutral-*.png`.

Trading Agent marketplace and unified workspace release, 2026-10-13:
the three primary entries are **Agent 市场**, **Agent 工作台** and **MCP / Skill 管理**.
The workbench contains Agent creation/templates, pause/resume/delete, conversations and buy/sell
orders. The market supports direction filters, price/sales sorting, task/capability inspection and
creating a source Agent directly in the publication form. Purchased Agents open in their workbench.
There are 15 templates, 10 Skills, 8 MCP products and 18 tool names.

New `eth-buy` and `eth-sell` templates pin independent reviewed Skills; legacy Skills remain
buy-only. Sales use owned ETH holdings, quote-bound minimum USDC proceeds, conservative USDC
valuation for limits, atomic balance updates and replay protection. Service purchases and each
manual asset trade have separate operation-bound Cognito confirmation.

Backend tests: the existing 94 tests plus 14 buy/sell boundary and marketplace cases pass.
All 13 focused UI tests, Ruff and production builds pass. All six Lambda hashes, both READY
Runtime artifacts and CloudFront's index match the deployed builds in demo/paper mode.
Cloud acceptance is implemented in `frontend/tests/trade-market-cloud.spec.ts`, alongside
the existing DCA, external MCP OAuth and task marketplace journeys.
All four cloud journeys passed. The new trading journey passed its final run in 2.6 minutes:
two Cognito purchases, a 30 USDC buy, a 0.005 ETH sale receiving 15 USDC, execution replay without
a second settlement, cross-user denial, precision rejection, GPT-6 Sol recognition of an
explicit sell request, cancellation of that proposal, and workspace pause/resume. Earlier UI
publication created the two labeled demo listings; they remain for users to purchase.
Test buyer instances were deleted. Desktop and mobile screenshots are retained under
`.deployment/trade-agent-*`, `.deployment/market-{buy,sell}-completed.png` and
`.deployment/unified-agent-workspace.png`; the mobile workbench also fits a 390px viewport.
Local HTTP and cloud HTTPS Playwright runs now use separate artifact directories, preventing
concurrent test cleanup from deleting another run's trace files.

Four-module platform release, 2026-10-13: navigation is organized into **Agent 市场**, **Agent 管理**,
**Agent 工作台**, and **MCP / Skill 管理**. Agent management includes templates, creation, workspace
launch, pause/resume and deletion. Examples and order history are scoped to the selected Agent;
buy orders live inside its workspace. The service market now supports reviewed task templates as
well as DCA. Ordinary purchases activate disclosed MCP products and create an isolated task Agent,
without a standing trade authorization; Cognito returns buyers to that Agent's workspace.
Deletion atomically invalidates its epoch, grants and pending operations, while retaining read-only
owned history and preventing revival. MCP management exposes service enablement, Skill rules and
bindings, and external OAuth connections. There are 13 templates, 8 Skills and 8 MCP products.
94 backend tests and 11 focused UI regressions pass, as do Ruff and the production frontend build.
All six Lambda code hashes, both READY Runtime artifacts and CloudFront's index were verified against
this release in demo mode without a data-provider secret. Test accounts are separate from the platform owner's
account. Task marketplace acceptance: `frontend/tests/task-market-cloud.spec.ts`.
All four cloud journeys passed in 4.4 minutes: DCA purchase/automatic settlement (1.2 minutes), external
Cognito MCP OAuth and trade continuation (56.6 seconds), the Agent-embedded 20 USDC buy/replay/cancel
journey (1.6 minutes), and task creation/publication/purchase/MCP execution/deletion (35.9 seconds).
The task test verifies no trading grant is created, the seller cannot read/delete the buyer's Agent,
and deleted Agents reject new conversations, messages and resume requests while retaining history.
Screenshots: `.deployment/agent-management.png`, `.deployment/task-agent-{market,workspace}.png`,
and `.deployment/order-{desk,history,confirmed-session}.png`. Test task instances were deleted;
DCA test authorization was revoked.


Order placement release, 2026-10-13: 85 backend tests and ten focused frontend regressions pass, along
with the production build. The catalog now contains thirteen templates, including the fixed
`eth-order` preparation plan. The **Agent 工作台 → 买入下单** panel exposes owned execution-capable Agents,
amount/slippage entry, status filters, fill receipts, cancellation and conversation links.
New manual orders bind the quote/minimum output to approval, recheck price and funds at execution,
and preserve the original order on retried preparation. See [order scenarios](order-scenarios.md).
Both order-entry paths start a fresh conversation under the current login, preserving earlier
conversations and avoiding reuse of a Runtime session from an older deployment.
Real cloud order acceptance passed in 1.5 minutes: a separate test account prepared a 20 USDC order
through three MCP calls, confirmed via Cognito, returned to its original conversation, settled once,
replayed execution without an extra debit, viewed its receipt/history, cancelled a second 5 USDC
order and verified cancellation, over-limit and cross-user denials. Market purchase/automation
regression passed in 34.4 seconds. External Cognito MCP OAuth regression passed in 56.5 seconds,
including scope filtering, connection revocation and the original six-tool Skill.
All six Lambda bundles, both Runtime artifacts and CloudFront's frontend were verified against the
current builds, in demo mode without a data-provider secret. Screenshots are retained as
`.deployment/order-{desk,history,confirmed-session}.png`.
Local and cloud Playwright runs now use separate artifact directories to prevent trace-cleanup collisions.

Keyless demo release, 2026-10-13: 75 backend tests, eight focused frontend regressions and the production
build pass. All six Lambda functions and both AgentCore Runtimes were verified against the current
bundle and `ONCHAIN_DATA_MODE=demo`; no data provider secret is configured. CloudFront's index matches the local
production build. All twelve templates are available, with explicit synthetic-data badges and
per-network examples for the eight research templates. Backend coverage executes every research
plan on all seven supported demo networks, checks coherent quotes/unknown risks, retained-secret
non-use, live failure behavior, signed task plans and scope/Skill/owner enforcement.
The UI regression also covers the delayed Agent refresh after template creation so example parameters
populate correctly, and checks network switching, user edits and restoring the selected network's sample.
Real cloud acceptance passed: the keyless journey (4.6 minutes) executes all eleven read templates
through Runtime/Gateway/MCP, including four-step token research and all ten synthetic provider tools;
a real GPT-6 Sol chat selects the demo token-search tool. It also verifies read-only Skill rejection,
cross-user history/task denial, unchanged paper balances and visible demo-source labels. The Cognito
return journey (48.6 seconds) confirms one paper trade and reload without duplicate settlement.
The seller/buyer marketplace journey (54.9 seconds) verifies purchase consent, atomic paper fee
settlement, isolated scheduler execution, budget accounting and revocation. Its test grant was revoked.
Screenshots are retained under `.deployment/keyless-{task-templates,research-run,mcp-skills}.png`.

Task template / onchain data release, 2026-10-13: 63 backend tests and seven frontend regressions pass;
the production build is deployed. Both real cloud journeys passed in 1.9 minutes. The new template
journey (41.6 seconds) creates a scoped Agent, runs `get_portfolio → analyze_portfolio` through Agent
Runtime, Gateway and MCP, rejects trading under its read-only Skill, and verifies a Bedrock chat
request can select the new analysis tool. The existing external OAuth journey still exposes exactly
six tools for the original trader Skill and four for its read-only OAuth scope, with trade approval,
isolation and revocation checks intact. The catalog now contains 12 templates, eight products,
eight Skills and 18 tools. That release required credentials for eight research templates; the current keyless
demo release removes that dependency with explicitly labeled synthetic data.
See [capabilities and configuration](data-capabilities.md).

Agent marketplace release, 2026-10-13: 51 backend tests, seven focused UI regressions and the production
build pass. The real two-user cloud journey passed in 40.7 seconds: seller publication, operation-specific
Cognito purchase, atomic DynamoDB paper-fee settlement, an independent buyer Agent, a scheduler-triggered
paper trade through Gateway/MCP, seller denial when reading buyer history, budget accounting and revocation.
The acceptance purchase consumed 1 paper USDC in service fees and 1 paper USDC in trading; its grant
is revoked and Agent paused. The labeled demonstration listing remains published.

Current OAuth release: 38 backend tests and the three focused UI regressions pass. Real cloud acceptance
verifies Cognito managed login, S256 PKCE (including wrong-verifier rejection), native Gateway OAuth
discovery, KMS-encrypted BFF refresh, external token refresh, rejection of ID tokens, scope-filtered tools,
external trade preparation, unapproved-execution rejection, same-operation reauthentication and automatic
paper execution, preservation of an external approval link across logout/login without implicit approval,
proof replay rejection, cross-user reads, cross-login MCP session rejection, connection
revocation, and a real Bedrock → Agent Runtime → internal Gateway → MCP portfolio call.

Conversation return fix, 2026-10-12: the production build and seven focused UI regressions pass.
The deployed Cognito return test passed in 56.4 seconds with the separate Bob test account: a
1 USDC paper trade returns to its original Agent and conversation, displays Gateway/MCP execution
events, remains resumable under the existing login, and survives reload without a second deduction.
Local regressions also cover another tab changing shared selection, missing return checkpoints,
read-only history under a new login, and rejection of another user's return operation.

The existing user pool and accounts are preserved. Refresh the website and sign in through Cognito;
legacy password sessions require a fresh OAuth login. Public MCP access uses a pre-registered client.
At this earlier release, Gateway-to-MCP authentication used IAM. The 2026-10-14 Identity migration
replaces that hop with user OAuth; see the current README and migration section below.

Verified on 2026-10-12: 23 backend security tests passed, frontend production build passed,
and all four cloud acceptance paths below passed. The final browser regression completed
in 48.1 seconds using real Cognito, Bedrock, AgentCore Runtime and Gateway resources.

Approval-flow fix, 2026-10-12: the frontend now submits a trade immediately after explicit
“验证并执行” confirmation and keeps the conversation card current. The updated cloud browser
suite passed all three tests in 37.1 seconds, including invalid-password rejection, a single execution
request after approval, queue-submission recovery, and grant approval without immediate execution.
The live acceptance trade uses the separate Bob test account.

History and execution journals, 2026-10-12: indexed ten existing nonempty conversations without
reactivating their execution credentials. The backend suite now passes 29 tests. Three focused browser
regressions pass, and the cloud journey passes in 55.8 seconds with real Bedrock/Gateway/MCP calls,
trace inspection, cross-user history/run rejection, refresh restoration, logout/login read-only history,
and a second isolated conversation. The Gateway and executor contribute their own trace events;
Agent Runtime progress is streamed over SSE and persisted by the trusted worker.

The security suite covers cross-user and cross-conversation isolation, audience-bound capabilities,
operation-specific fresh authentication, immutable intent approval, revocation, atomic budgets,
idempotent execution, hidden tool context, fail-closed dependency errors and agent quarantine.

The password-based scripts below record the pre-OAuth acceptance runs and are not the current
production login path. Current cloud acceptance uses `oauth-cloud.spec.ts`; local password fixtures
remain supported only in local development.

| Check | Command |
|---|---|
| Real Cognito TOTP, replay, CSRF, Origin, Gateway/MCP access boundary | `python scripts/cloud_security.py` |
| Real Gateway discovery filtering, identity injection and conversation isolation | `python scripts/cloud_gateway.py` |
| Approved automatic DCA through deployed Scheduler/SQS/Gateway/MCP | `python scripts/cloud_automation.py` |
| Current OAuth browser + external MCP journey | `TEST_BASE_URL=https://d2omddnartt627.cloudfront.net npx playwright test oauth-cloud.spec.ts` from `frontend/` |
| Cognito return to original conversation, execution timeline and reload | `TEST_BASE_URL=https://d2omddnartt627.cloudfront.net npx playwright test auth-return-cloud.spec.ts` from `frontend/` |
| Agent marketplace: seller, buyer, Cognito purchase and automatic execution | `TEST_BASE_URL=https://d2omddnartt627.cloudfront.net npx playwright test marketplace-cloud.spec.ts` from `frontend/` |
| Keyless templates, all read plans, Skill/session constraints and Bedrock intent | `TEST_BASE_URL=https://d2omddnartt627.cloudfront.net npx playwright test templates-cloud.spec.ts` from `frontend/` |
| Order ticket, Cognito confirmation, receipt, replay, cancellation and isolation | `TEST_BASE_URL=https://d2omddnartt627.cloudfront.net npx playwright test orders-cloud.spec.ts` from `frontend/` |

The keyless acceptance journey uses its own synthetic account, provisioned once with
`.venv/bin/python scripts/demo_test_user.py`; credentials remain in the ignored mode-0600
`.deployment/demo-credentials.json`. It reuses its own Agents across runs and does not change user accounts.

The automatic DCA test advances only the synthetic test grant's due time in DynamoDB, then invokes
the actual scheduler twice. It requires one paper fill, one budget deduction and no onchain hash.
It revokes its grant and pauses its agent on completion.

## Operating limits

This is a deployed paper-trading implementation. Real wallet signatures, live settlement and arbitrary
third-party MCP publication are not enabled. All templates can run in explicit demo mode without a key;
synthetic chain data and paper transactions are disclosed throughout the UI and tool results.
The live onchain data adapter requires `ONCHAIN_DATA_MODE=live`, a data-provider secret and scoped Secrets Manager
access. Live failures never fall back to synthetic data.

The [Agent service marketplace](agent-marketplace.md) supports priced, immutable task and ETH DCA templates,
bilateral paper-fee settlement and buyer-owned automatic execution. It is separate from the reviewed
MCP catalog and does not implement an asset order book. A labeled demo publisher and strategy are
retained for exploring the market with paper funds.

The bounded aggregate database and recent-event audit view are suitable for this implementation's
documented limits. A production financial deployment needs partitioned transaction records, an
immutable audit sink, operational alarms and independent wallet/security review.

See [operations](operations.md) for independent releases, stopping automation, test-user removal
and retained AWS resources.

## Identity Token Vault migration (2026-10-14)

The current downstream path is Gateway → user OAuth → MCP Runtime. `scripts/identity_setup.py`
reconciles a dedicated confidential Cognito client and a CustomOauth2 credential provider.
The existing login pool, public MCP client and user records are
preserved. No client secret value is saved in deployment metadata; Identity stores it in Secrets Manager.
The provider callback is the only callback registered on the downstream client. Both Gateways
register their permitted application return URLs.

Gateway MCP targets use `OAUTH / AUTHORIZATION_CODE`, the `openid` scope, the Cognito
resource audience, and inline tool schemas. Static schema provisioning avoids requiring an admin user’s
OAuth consent during tool synchronization. The MCP Runtime uses Cognito custom JWT authorization and
forwards the Authorization header to application code. Before switching an existing IAM Runtime to JWT,
the deployment removes its old runtime and DEFAULT-endpoint resource policies, as required by AgentCore.

The Gateway role requires `GetWorkloadAccessTokenForJWT` for its two managed workloads. Credential
retrieval also requires `GetResourceOauth2Token` on the workload directory, permitted workload identities,
Vault and specific provider—not only the provider ARN. The BFF needs `CompleteResourceTokenAuth`
and `secretsmanager:GetSecretValue` on the provider's exact client-secret ARN. Identity uses the latter
permission when exchanging the authorization code on the completion caller's behalf. Omitting it
causes consent completion to fail, even with the correct original JWT. The BFF does not retrieve
Gateway workload tokens or downstream Vault tokens. The REQUEST role no longer has `kms:Sign`,
the MCP role no longer reads the platform KMS key, and Gateway no longer invokes MCP with SigV4.
The MCP role still invokes the designated Executor Lambda with IAM; Executor validates user OAuth itself.

New configuration keys are `MCP_OAUTH_CLIENT_ID`, `MCP_OAUTH_AUDIENCE`, `MCP_OAUTH_SCOPE`, `IDENTITY_PROVIDER_ARN`,
`IDENTITY_PROVIDER_NAME`. The deployment supplies them; do not paste
client secrets or tokens into environment examples. Both Runtime and Lambda bundles need redeployment.
Callers using an old active Runtime session should create a new conversation after the migration.

The old `security_context` JWT is no longer accepted. The request interceptor creates bounded
`CALL#...` records, and a user token plus current invocation record is required at MCP/Executor.
Revocation uses `IDENTITY_CONNECTION#...` epochs. Consent continuation uses owner/login-bound
`IDENTITY_RESUME#...`, `IDENTITY_PENDING#...` and `IDENTITY_CALLBACK#...` records with TTLs.
Downstream refresh tokens remain in Token Vault; the existing encrypted BFF login store remains separate.

Before an unattended schedule runs, its user must connect the internal tool account once. If authorization
is missing or revoked, the scheduled call is blocked; a scheduling grant never supplies OAuth consent.
Tool-account authorization does not approve a financial operation. Internal and external entry points
have different workloads and issuers, so the UI exposes separate connection controls.

MCP Gateway automatically supplies the target endpoint as RFC 8707 `resource`. The Runtime audience
therefore equals its full encoded invocation URL (including `?qualifier=DEFAULT`). The dedicated client requests only
`openid`. Gateway limits individual scopes to 128 characters; a Cognito custom scope prefixed by the
188-character Runtime resource URL exceeds that limit. Cognito also rejects custom scopes from another
resource. Exact tool authorization remains in the interceptor and invocation record; the downstream
OAuth token only proves the user/client/resource identity. Do not add `resource` to target
`customParameters`: it conflicts with Gateway’s managed `resources` input. Standalone acceptance probes use
the same resource URL and custom parameters explicitly. Identity returns `session_id` to the application callback, which forwards it
through a same-origin CSRF-protected completion request. Native Identity authorization URLs use the
regional `/identities/oauth2/authorize?request_uri=...` endpoint before redirecting to Cognito.

`CompleteResourceTokenAuth` requires the **exact original user JWT**, not a newly issued token with
the same issuer/sub (verified against the live API). Pending Gateway flows and internal consent continuations
therefore keep a short-lived KMS-encrypted proof with owner/continuation encryption context. The BFF
revalidates current login and CSRF before decrypting it, and deletes proof after successful completion.
External proof expires within 10 minutes; internal proof is also bounded by its original 5-minute
Gateway JWT. Worker and RESPONSE interceptor receive only `kms:Encrypt` for `purpose=identity-consent`;
only BFF can decrypt. These are inbound authorization proofs, not stored downstream refresh tokens.
External clients must negotiate MCP 2025-11-25 or newer and send the protocol header on subsequent calls.
Gateway otherwise returns a generic tool error instead of the URL authorization challenge.
An independent live check completed authorization after normal SDK transport/session cleanup and
closure of the application's read-only probe conversation. The platform retains normal SDK cleanup.

Vault credentials are workload-scoped. A standalone BFF workload's completed consent does not authorize
either Gateway. AWS also rejects direct `GetWorkloadAccessTokenForJWT` calls for service-linked workloads.
The platform therefore initiates all real tool authorization through the relevant Gateway. Internal
connection checks use an owned Agent's permitted read-only tool; external clients receive native URL
elicitation and retry after browser completion. Restoring platform tool access can reuse a still-valid
Gateway credential; it does not force provider reauthentication or approve a trade.

Identity acceptance uses dedicated synthetic Cognito accounts; it does not modify the primary user's
Agents, wallet or approvals. Create a fresh first-consent account once:

```bash
.venv/bin/python scripts/demo_test_user.py --identity
cd frontend
TEST_BASE_URL=https://d2omddnartt627.cloudfront.net npx playwright test identity-cloud.spec.ts
TEST_BASE_URL=https://d2omddnartt627.cloudfront.net npx playwright test oauth-cloud.spec.ts trade-market-cloud.spec.ts
```

After provisioning the synthetic Identity and OAuth users, run
`.venv/bin/python scripts/cloud_identity.py` from the repository root. It checks the deployed Executor
and real MCP Runtime with both users' Vault-issued access tokens, without printing those tokens.
The script uses its own standalone acceptance workload and completes consent there when necessary;
that test workload cannot preauthorize the production Gateways and is not accessible to the BFF role.
The probe creates and deletes its own temporary Agent and verifies that its wallet is unchanged.

The first-consent browser acceptance passed on 2026-10-14 (2.2 minutes): a new user's task received
the native Gateway challenge, completed Cognito/Identity authorization, returned to the original Agent
and conversation, and completed all four research tools. Revoking the internal connection blocked a
new task despite cached Vault credentials; explicit restoration recovered access. History omitted
tokens, invocation references and authorization URLs. External native Gateway consent is covered separately.

Final release validation on 2026-10-14:

- 126 backend tests, Ruff and the production frontend build passed. Seven browser regressions passed
  for consent return, transaction return, account changes and conversation-history navigation.
- External Cognito/MCP acceptance passed in 1.4 minutes: native Gateway consent with the original
  user JWT, PKCE and refresh, ID-token rejection, scope filtering, cross-user/session isolation,
  unapproved-execution rejection, fresh transaction confirmation and connection revocation.
- Published buy/sell Agent acceptance passed in 2.8 minutes: two purchases with independent buyer
  Agents, confirmed buy and sell paper fills, duplicate-execution protection, seller/buyer privacy,
  Agent management and a natural-language sell proposal through real Bedrock.
- `scripts/cloud_identity.py` passed against the deployed Executor and MCP Runtime: another user's
  reference, substituted tool, missing/expired references and same-user session substitution were
  rejected. The probe wallet was unchanged and its temporary Agent/conversations were closed.
- Six Lambda functions and both Runtimes matched the final bundle; both Gateways were READY with
  Policy ENFORCE, and CloudFront matched the frontend build. Temporary diagnostic log deliveries and
  their log groups were removed; Gateway DEBUG mode is off.

All transaction acceptance used isolated synthetic users and paper funds. The primary user's wallet,
Agents and approvals were not used for these tests.
