# Order placement scenarios

The **Agent 工作台 → 交易下单** panel provides paper market buys and sells between ETH and USDC on Base. It requires no data provider key
in the deployed demo mode. This is an order/settlement workflow against each user's own paper wallet;
it is not a real exchange order book or an onchain broadcast. Limit orders and other execution pairs are not supported. Sell orders require existing ETH holdings.

## Place and settle an order

1. Sign in with Cognito and open **Agent 工作台 → 任务模板 → ETH 买入下单** to create an Agent if needed.
2. Open that Agent’s workspace and select **交易下单**. The order form is bound to the selected Agent;
   creation uses the audited `eth-order` template and explicit MCP enablement.
3. Enter the USDC amount and maximum slippage. The initial example is 20 USDC and 0.5%.
4. Click **生成待确认订单**. A fresh owner/login-bound conversation runs
   `get_portfolio → get_quote → prepare_trade` through AgentCore Runtime, Gateway and MCP.
5. In the resulting conversation, review the order reference price and minimum ETH received, then
   click **查看操作详情 → 验证并执行**. Cognito authentication returns to this conversation.
6. The approved order executes through Gateway/MCP, atomically debits USDC, credits ETH and records
   one `paper_…` receipt with no transaction hash.
7. **Agent 工作台 → 交易下单 → 订单历史** shows the fill, price, receipt and a link to the conversation/timeline.
   Reloading or replaying execution returns the existing result without a second debit.

The same flow is available from **Agent 工作台 → 任务模板 → ETH 买入下单**. Read-only research and preflight Agents
cannot execute orders and do not show the buy-order panel. Order history is filtered to the selected
Agent, while the explicitly labeled paper wallet balance is shared by that user’s Agents. Missing required MCP
subscriptions must be explicitly enabled; the form does not enable them silently.

## Sell existing ETH

Create or purchase an **ETH 卖出 Agent** (`eth-sell`, Skill `eth-seller`), open its
**交易下单** panel, and enter an ETH quantity such as `0.005`. The execution service obtains
a trusted quote, checks holdings and USDC notional limits, and prepares immutable ETH → USDC
terms. Review the ETH input and minimum USDC proceeds before confirming in Cognito.
At a 3,000 USDC/ETH reference, 0.005 ETH yields 15 USDC; at 0.5% slippage the approved
minimum is 14.925 USDC. The fill reduces ETH and credits integer micro-USDC atomically.

The sell Skill cannot prepare buys; the buyer and legacy trader Skills cannot prepare sells.
Sold ETH is limited to existing holdings, with up to 18 decimal input places. Proceeds are rounded
down to six USDC decimals. The greater of prepared/executed USDC valuation counts against
single/daily limits, so a price rise cannot silently exceed an Agent's cap. Manual sell orders
require individual confirmation; existing buy-only DCA grants never authorize sales.

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
data provider quotes and still settles only to the paper wallet. Live provider failures never use demo fallback.

## Verification

- `tests/test_orders.py`: approval gate, signed plan, quote binding, slippage, cancellation, replay,
  owner isolation, invalid prices and pre-submission budgets.
- `frontend/tests/order-desk.spec.ts`: execution-capable Agent selection, exact template/parameters,
  pending state, cancellation and status filtering without implicit execution.
- `frontend/tests/orders-cloud.spec.ts`: real Cognito/AgentCore/Gateway/MCP preparation, operation-specific
  confirmation, return to the original conversation, 20 USDC paper fill, execution replay, history,
  cancellation, cross-user denial and over-limit rejection.

Buy/sell marketplace and financial boundary coverage: `tests/test_trade_market.py`,
`frontend/tests/trade-market.spec.ts`, and `frontend/tests/trade-market-cloud.spec.ts`.
