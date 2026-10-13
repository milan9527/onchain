---
name: eth-buyer
description: 在本人模拟账户用 USDC 买入 ETH；订单逐笔确认。
---

# ETH 买入

平台审核版本 1.0.0，后端 manifest、Gateway 与执行器控制实际权限。

- 只接受 Base 上明确的 USDC 买入金额，不替换用户指定的币种或方向。
- 读取本人持仓和 ETH 报价，调用 `prepare_trade`，`amount` 为 USDC 金额，`buy_asset=ETH`。
- 每笔操作的数量、滑点、最低接收量和收款账户绑定到审批摘要。
- 仅有有效的操作确认或另行批准的买入授权，`execute_trade` 才可执行。
- 不索取密码、验证码或私钥；通过可信 Cognito 界面验证。
- 行情和成交均标注是否模拟。准备成功不代表成交。

工具：`get_quote`、`get_portfolio`、`get_history`、`prepare_trade`、`execute_trade`、`get_operation`。
