# Agent service marketplace

The platform now has a shared Agent service market in addition to personal workspaces and the MCP
catalog. A seller publishes an immutable reviewed task template and a one-time paper USDC price.
A buyer reviews the service fee, task, Skill and MCP products. Task purchases create an isolated Agent
and activate the disclosed MCP products, without a trading grant. Buyers then run examples in their
Agent workspace. ETH DCA additionally requires per-trade, daily and cumulative budgets, interval and
expiry; fresh operation-specific Cognito authentication authorizes both fee and bounded automation.

This market sells Agent services. It does not match independent ETH buy/sell limit orders.
Current strategies use the existing Base ETH/USDC paper execution service; arbitrary uploaded
strategy code, live settlement, subscriptions with recurring fees and performance fees are not enabled.

## Buyer and seller flow

1. In **Agent 市场 → 发布 Agent**, choose **从模板新建并发布** to create and publish in one flow,
   or create an Agent from **Agent 工作台 → 任务模板**. In **Agent 市场 → 发布 Agent**, select that Agent
   and a compatible task, then enter a public title, publisher name, task description and price.
   Task plans must use tools and trade directions supported by the source’s reviewed Skill; seller code is never copied.
   Publication pins the task’s Skill without broadening the source’s capabilities. A failed publication
   retains the created source in the form so retrying does not create another Agent.
2. Other users filter **买入 Agent / 卖出 Agent / 自动定投 / 研究与分析**, compare service fees, limits
   and MCP capabilities, and discover the listing and choose **购买 Agent** (task) or **购买并授权** (DCA). The purchase summary separately
   displays the one-time service fee and trading limits. Preparing an order does not charge money.
3. Cognito reauthentication binds confirmation to the exact order hash. A transaction checks that
   the listing is still published, the version and skill are unchanged, and balances and quotas allow
   purchase. It debits the buyer, credits the seller, records both sides of the order, and creates a
   buyer-owned Agent atomically. Only DCA also creates a grant. Ordinary task purchases accept an
   empty parameter object; trading-budget fields are rejected. Task/Skill digests are rechecked.
4. All purchases return to **Agent 工作台**, showing compatible examples and private run history.
   Order preparation requires separate trade confirmation. For DCA, the first authorized trade is due immediately; EventBridge invokes the scheduler every minute.
   Subsequent executions follow the selected interval, subject to balance, daily/cumulative budgets,
   skill/tool subscriptions, grant expiry and revocation. The buyer can be offline.
5. **我购买的** shows automation status, consumed budget and links to the Agent's history/timeline.
   **卖方中心** shows listings, paper revenue and commercial orders.
6. **停止自动交易** revokes the grant and pauses the buyer's Agent, invalidating prior capabilities.
   Task purchases offer **停止任务 Agent**. **Agent 工作台 → 删除 Agent** cancels pending/approved
   operations, revokes all grants and invalidates the Agent epoch atomically. Deleted instances cannot
   be resumed, including through purchase revocation or automatic quarantine. Owned history remains
   read-only; public listings are immutable snapshots and must be withdrawn separately.
   The one-time service fee is not refunded. A seller can withdraw a listing to stop new sales;
   previously purchased snapshots continue under the buyer's original authorization.

## Security and settlement

Published objects contain reviewed configuration only, with no seller messages, credentials, Runtime
IDs or executable third-party code. A purchase creates a new Agent ID owned by the buyer; scheduled
conversations receive distinct platform and Runtime IDs. The seller cannot inspect or control them.
Public catalog serialization excludes owner and source-Agent IDs. Seller orders contain no buyer
identity, wallet, Agent, grant or conversation data.

`POST /api/operations/{id}/approve` remains the only purchase-approval path, with CSRF, Cognito
scope and a single-use proof for the reviewed intent. There is no approval MCP tool. Automation
uses Scheduler → SQS worker → AgentCore Gateway interceptors → OAuth MCP Runtime → executor.
Connect the internal tool account once through Identity Token Vault before enabling unattended runs;
user OAuth credential consent and the financial scheduling grant are independent requirements.
Both Gateway and executor enforce the current buyer-owned grant and Agent permissions.

`Store.transact` uses DynamoDB conditional `TransactWriteItems`, SQLite `BEGIN IMMEDIATE`, or an
in-memory lock. Every selected record has an optimistic version condition. A conflicting concurrent
confirmation retries against fresh records, observes the terminal order and cannot charge twice.
There are no external side effects inside retried transaction callbacks.

The shared catalog is bounded to 200 listings and 20 per publisher. Existing limits of ten non-deleted Agents
per buyer (and 200 retained Agent audit records) and 200 operations apply; seller order retention is bounded to 200 sales. These are
implementation quotas, not an unbounded production exchange design.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/marketplace` | Published listings, own purchases, own sales and paper revenue |
| `POST /api/marketplace/listings` | Publish a snapshot of an owned reviewed task Agent |
| `POST /api/marketplace/listings/{id}/status` | Publish/withdraw own listing |
| `POST /api/marketplace/listings/{id}/purchase` | Prepare immutable task purchase or DCA intent |
| `POST /api/operations/{id}/oauth-stepup` | Begin purchase-specific Cognito authentication |
| `POST /api/operations/{id}/approve` | Atomically settle purchase, create Agent and optionally enable DCA |
| `POST /api/marketplace/purchases/{id}/revoke` | Stop own purchased task or automation |

For cloud acceptance, `scripts/market_test_users.py` creates separate synthetic buyer and seller
accounts without sending email. Credentials stay in the ignored mode-0600
`.deployment/market-credentials.json`. The browser test uses real Cognito and the deployed scheduler,
waits for a paper fill through Gateway/MCP, verifies buyer/seller isolation and stops the test grant.
The clearly labeled demo listing remains available for trying the market with paper funds.

Task marketplace acceptance is covered by `frontend/tests/task-market-cloud.spec.ts`: UI template
creation, seller publishing, fresh Cognito purchase, buyer-only MCP execution, cross-user denial,
Agent deletion, read-only retained history and refusal to revive or rerun a deleted instance.

## Buy and sell trading Agents

`eth-buy` / `eth-buyer` accepts explicit USDC amounts to buy ETH. `eth-sell` / `eth-seller`
accepts explicit ETH quantities and sells only existing buyer-owned holdings for USDC. Both use
`get_portfolio → get_quote → prepare_trade`, then a separate Cognito confirmation before
`execute_trade`. Neither service purchase authorizes asset execution. Seller and buyer are
marketplace service roles; every fill uses the buyer's own paper wallet.

The market type is derived from the reviewed template, not seller-provided text. Gateway and the
executor enforce Skill direction independently of the model. Old trader Skills remain buy-only.
Sale proceeds are rounded down to six USDC decimals; single/daily limits count the greater of the
prepared and execution-time USDC value. Concurrent fills recheck ETH holdings atomically; no short
position is allowed. Standing buy grants cannot authorize a sale.

`frontend/tests/trade-market-cloud.spec.ts` publishes both labeled demo listings through the UI,
buys independent Agents with Cognito, buys 30 USDC of ETH, sells 0.005 ETH for 15 USDC, verifies
replay and seller/buyer isolation, then deletes the buyer's test instances. The two public demo
listings remain available. `tests/test_trade_market.py` covers concurrency, limits and direction.
