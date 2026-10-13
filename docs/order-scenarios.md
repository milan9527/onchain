# Order placement scenarios

The **Agent 工作台 → 买入下单** panel provides paper market buys of ETH with USDC on Base. It requires no OKX key
in the deployed demo mode. This is an order/settlement workflow against each user's own paper wallet;
it is not a real exchange order book or an onchain broadcast. Limit orders, sell orders and other
execution pairs are not advertised by this screen.

## Place and settle an order

1. Sign in with Cognito and open **Agent 管理 → 任务模板 → ETH 买入下单** to create an Agent if needed.
2. Open that Agent’s workspace and select **买入下单**. The order form is bound to the selected Agent;
   creation uses the audited `eth-order` template and explicit MCP enablement.
3. Enter the USDC amount and maximum slippage. The initial example is 20 USDC and 0.5%.
4. Click **生成待确认订单**. A fresh owner/login-bound conversation runs
   `get_portfolio → get_quote → prepare_trade` through AgentCore Runtime, Gateway and MCP.
5. In the resulting conversation, review the order reference price and minimum ETH received, then
   click **查看操作详情 → 验证并执行**. Cognito authentication returns to this conversation.
6. The approved order executes through Gateway/MCP, atomically debits USDC, credits ETH and records
   one `paper_…` receipt with no transaction hash.
7. **Agent 工作台 → 买入下单 → 订单历史** shows the fill, price, receipt and a link to the conversation/timeline.
   Reloading or replaying execution returns the existing result without a second debit.

The same flow is available from **Agent 管理 → 任务模板 → ETH 买入下单**. Read-only research and preflight Agents
cannot execute orders and do not show the buy-order panel. Order history is filtered to the selected
Agent, while the explicitly labeled paper wallet balance is shared by that user’s Agents. Missing required MCP
subscriptions must be explicitly enabled; the form does not enable them silently.

## Withdraw an order

Before settlement, use **订单历史 → 撤单** for a pending or approved order. Cancellation is owner/CSRF
authenticated and atomically changes its state to `REJECTED` (displayed as **已撤单** on this page).
Subsequent execution is denied. If settlement won a concurrent race, cancellation fails rather than
pretending to reverse the fill. No cancellation tool or approval-issuing tool is granted to the model.

## Bound price and financial controls

New `prepare_trade` tool calls obtain a trusted executor-side quote. Its `reference_price` and
`minimum_received` are included in the canonical intent and approval hash. The minimum is:

`USDC amount / reference price × (1 - maximum slippage basis points / 10,000)`.

The quote and the minimum cannot be supplied or overridden by model/tool arguments. A fresh quote
at execution must be positive, finite and unexpired, and must produce at least the approved minimum.
A price beyond that bound yields `SLIPPAGE_EXCEEDED` before any wallet or budget mutation.
Amounts, balances and daily limits are checked when preparing quote-bound orders and again at the
atomic execution commit. Pending orders do not reserve balances, so concurrent orders can be rejected
at execution if another order has spent the available balance or budget.

Retries of the same preparation invocation retain the original order and approval terms even if a
later quote changes. Legacy orders and scheduled grants retain their existing approved terms;
this release does not rewrite old intents or broaden any Skill digest.

Demo quotes remain 3,000 USDC/ETH. In explicit live data mode, order preparation requires configured
OKX quotes and still settles only to the paper wallet. Live provider failures never use demo fallback.

## Verification

- `tests/test_orders.py`: approval gate, signed plan, quote binding, slippage, cancellation, replay,
  owner isolation, invalid prices and pre-submission budgets.
- `frontend/tests/order-desk.spec.ts`: execution-capable Agent selection, exact template/parameters,
  pending state, cancellation and status filtering without implicit execution.
- `frontend/tests/orders-cloud.spec.ts`: real Cognito/AgentCore/Gateway/MCP preparation, operation-specific
  confirmation, return to the original conversation, 20 USDC paper fill, execution replay, history,
  cancellation, cross-user denial and over-limit rejection.
