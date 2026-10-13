# Onchain OS tools, Skills and task templates

The platform exposes 18 reviewed MCP tools in eight products, eight Skills and thirteen task templates.
The original six trading/portfolio tools and the original two Skill digests are unchanged. Existing
Agents do not silently receive the new capabilities.

Endpoint paths and parameter mappings were checked against the official
[OKX Onchain OS source](https://github.com/okx/onchainos-skills/tree/9d9c66f5c296461b8687a71cdab188acedcb781d):
`cli/src/commands/{market,token,security,signal,swap,gateway}.rs`.
Task ideas follow the official [workflow index](https://github.com/okx/onchainos-skills/blob/9d9c66f5c296461b8687a71cdab188acedcb781d/workflows/INDEX.md).
This deployment wraps fixed read endpoints; it does not install or execute the upstream CLI.

## Added MCP products

| Product | Tools | Data |
|---|---|---|
| 代币与行情 | `search_tokens`, `get_token_info`, `get_token_price`, `get_candles`, `get_token_holders` | OKX public chain data |
| 风险扫描 | `scan_token_risk` | OKX token security report |
| 聪明钱信号 | `get_smart_money_signals` | OKX market signals; no automatic copy trading |
| 路由与 Gas | `get_swap_quote`, `get_gas_price`, `get_supported_chains` | OKX read-only quotes/network data |
| 账户分析与交易预检 | `analyze_portfolio`, `preview_trade` | Authenticated user's own paper account |

Public research accepts explicitly selected Base, Ethereum, Arbitrum, BNB Chain, Polygon, X Layer
or Solana networks. Individual provider endpoint support may differ. EVM and Solana contract formats
are checked against the selected network. Queries and result counts are bounded. Swap quotes take
base-unit amounts (`1000000` means one unit of a six-decimal token); they do not construct transactions.

## Tasks and Skills

| Task | Skill | Execution |
|---|---|---|
| ETH 买入下单 | `controlled-trader` | Own balance → quote → pending order; separate Cognito confirmation → paper fill |
| 持仓健康检查 | `portfolio-auditor` | Own holdings + paper allocation/budget analysis |
| 交易与授权复盘 | `portfolio-auditor` | Own history + aggregate analysis |
| USDC → ETH 交易预检 | `trade-planner` | Own balance and limits; no prepared/approved trade |
| ETH 自动定投 | `controlled-trader` | Existing immutable grant + fresh Cognito confirmation |
| 多链代币发现 | `token-researcher` | Token search |
| 代币尽调报告 | `token-researcher` | Metadata → price → holders → risk scan |
| 价格与 K 线观察 | `market-analyst` | Token price → candles |
| 聪明钱信号观察 | `market-analyst` | Bounded chain signal query |
| 买入前风险筛查 | `risk-reviewer` | Metadata → risk scan |
| DEX 兑换路由查询 | `route-planner` | Aggregator quote → gas price |
| 网络 Gas 检查 | `route-planner` | Current network gas price |
| 交易网络发现 | `route-planner` | Provider-supported networks |

The six new Skills have reviewable Markdown references in `skills/<skill-id>/SKILL.md`. Their
instructions, descriptions and tool lists are pinned in the backend manifest and passed to Bedrock
for conversational intent recognition. Gateway/executor enforcement remains independent of model
instructions. Templates run a fixed tool plan through AgentCore Runtime and Gateway, with results
and real tool traces in the original conversation.

Use **任务模板 → 使用模板** to create a dedicated Agent. The creation form explicitly asks whether
to enable the template's MCP products. Then provide template parameters and run it in the workbench.
An existing Agent can run only templates whose complete tool set is allowed by its Skill and whose
MCP products the user has enabled. Template hashes and parameters are checked at the API and Runtime;
the invocation capability binds the exact task request. Templates stop after a failed tool call.

## Provider configuration and current availability

The deployed environment has no OKX data credential and explicitly runs `ONCHAIN_DATA_MODE=demo`.
All thirteen templates are available: eight research templates use synthetic fixtures, and the five
account/trade templates use the authenticated user's own paper account and existing approval flow.
The four provider MCP products show **演示数据 · 无需 OKX Key**. No external HTTP or credential lookup
occurs in the demo adapter. Cognito, Bedrock chat intent recognition, AgentCore Runtime, Gateway,
MCP, subscriptions, scope/Skill checks, immutable approvals and session isolation all remain active.

Research forms include runnable examples per network; changing the network loads its example.
Use **任务模板 → 代币尽调报告 → 使用模板 → 创建模板 Agent → 运行任务** for the four-tool research flow.
For conversational trading use a **受控交易** Agent, enter an explicit USDC amount, review the operation
and confirm through Cognito. Authentication returns to that conversation and submits the approved
paper trade once. **ETH 自动定投** or an Agent marketplace purchase starts automation only after
confirmation of its amount, cumulative budget and expiry.

Demo outputs carry `simulated: true`, `data_mode: demo`, dataset `onchain-demo-v1`, a fixed scenario
date and a visible non-live notice. Prices, candles and base-unit swap estimates are coherent; Base
ETH's fixture price matches the paper wallet's 3,000 USDC reference. Fixture addresses identify demo
scenarios and do not prove actual contracts exist or are safe. Unknown contracts return unknown/empty
results; all reference risk scans remain `UNKNOWN` with `safeToTrade: false`. The synthetic high-risk
contract `0x000000000000000000000000000000000000dead` illustrates `HIGH` risk and returns no quote.
Signals, holder identities, gas and network support are also synthetic. Quotes contain no executable
transaction. Truncated task reports retain the demo source and notice.

`ONCHAIN_DATA_MODE=live` selects only the real adapter. Missing credentials then show configuration
required, and provider failures never fall back to demo data. Live OKX responses have not been
validated with a provisioned account; endpoint mapping, HMAC signing and failure handling are tested
with controlled HTTP responses.

To enable the read adapter, provision an AWS Secrets Manager secret using the AWS-managed encryption
key, containing `api_key`, `secret_key`, `passphrase` and optionally `project_id`. Keep the credential
out of browser forms and model messages. Then run:

```bash
.venv/bin/python scripts/deploy.py --skip-stack --onchain-data-mode live --okx-secret-arn <existing-secret-arn>
```

The deployer grants only the executor role `secretsmanager:GetSecretValue` for that exact ARN.
Subsequent deployments preserve the configured ARN and data mode. To explicitly restore keyless mode,
deploy with `--onchain-data-mode demo`. Credentials refresh from the vault at most every
five minutes. Provider access/plan permissions still apply. HTTP 402, throttling, redirects, oversized
responses and provider errors stop the request; there is no automatic payment, redirect following,
arbitrary URL, signing or broadcast tool. Truncated output is explicitly labeled and is never a
positive security judgment.

## Authentication and API

New public data tools require Cognito `onchain/market.read` in addition to the selected Skill, enabled
MCP product and isolated session. New private-account tools use `onchain/portfolio.read`. The provider
scope prefix is the public Gateway URL, translated internally to `onchain/` as before.
Existing OAuth connections retain their original scope set. Users select the public-research
permission when authorizing a new external MCP connection. Existing web logins may need a fresh
Cognito sign-in to obtain the new public-data scope.

`GET /api/catalog` includes `templates`, Skill instructions, product availability and `onchain_data_mode`.
Templates/products declare `data_mode` (`demo`, `live` or `paper`); demo templates include per-chain
`examples`. These presentation fields do not alter audited template or Skill digests.
`POST /api/agents` accepts `template_id` and explicit `enable_template_products`.
`POST /api/conversations/{id}/tasks` accepts `template_id`, `template_digest` and `parameters`.
It requires the original active login, owner, Agent/Skill permissions and product subscriptions.
Old-login histories remain read-only. There is no API parameter for another user's identity,
credentials, arbitrary tool plan or user-supplied endpoint.

The `eth-order` template has mode `prepare`: its fixed plan ends at `prepare_trade` and promotes the
authoritative pending operation into the task result for the existing confirmation UI. It cannot
approve or execute as part of the template. Task creation and order placement grant no new scopes.
Execution remains a separate, explicitly confirmed operation. See [order scenarios](order-scenarios.md).
