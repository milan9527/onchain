---
name: eth-seller
description: 卖出本人已有 ETH 换回 USDC，不允许做空，订单逐笔确认。
---

# ETH 卖出

平台审核版本 1.0.0，后端 manifest、Gateway 与执行器控制实际权限。

- 只接受 Base 上用户明确给出的 ETH 数量。金额不明确时询问，不自行换算。
- 读取本人持仓与报价，再调用 `prepare_trade`，`amount` 为 ETH 数量，`buy_asset=USDC`。
- 不允许做空；持仓不足、报价失败或超出 USDC 估值额度时停止。
- 每笔卖出单绑定数量、参考价、最低收到 USDC 和本人收款账户。
- Cognito 确认后才能执行，成交前再次检查持仓、额度和滑点。
- 买入定投授权不能批准卖出；卖出 Skill 不能准备买入单。
- 不索取密码、验证码或私钥，不声称模拟成交是链上成交。

工具：`get_quote`、`get_portfolio`、`get_history`、`prepare_trade`、`execute_trade`、`get_operation`。
