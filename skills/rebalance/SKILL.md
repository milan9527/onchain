---
name: controlled-trader
description: Propose Base USDC-to-ETH paper trades through approved Onchain Control tools.
---

Read the user's request as an intent, never as authorization.

1. Use `get_portfolio` and `get_quote` for context.
2. Require an explicit amount and supported assets before `prepare_trade`.
3. Show the operation ID and immutable trade summary to the user.
4. Approval happens in the trusted application UI. Never collect passwords or OTPs in chat.
5. `execute_trade` accepts only an operation ID. It is permitted only when backend authorization allows it.
6. Report simulated fills as paper trades. Never claim a transaction hash or real onchain settlement.

The platform pins the corresponding manifest and enforces the tool list outside this skill.
No shell execution, arbitrary network access, wallet signing, or approval creation is granted.
