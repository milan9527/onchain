# Onchain Control

前后端分离的 Agent 平台：AWS AgentCore Runtime + Gateway、Bedrock GPT-6 Sol、Cognito 用户认证、MCP、意图识别、操作审批与确定规则拦截。**未使用 Guardrails。**

前端为 React/Vite，独立部署到私有 S3 + CloudFront；后端为 FastAPI/Lambda；Agent 与 MCP 使用不同的 AgentCore Runtime。CloudFront 的 `/api/*` 行为转发独立 API，浏览器使用 HttpOnly 会话 Cookie，模型凭据不进入前端。

已部署网站：<https://d2omddnartt627.cloudfront.net>。资源与验收方法见 [部署记录](docs/deployment.md)。

## AWS 架构图

[可编辑 draw.io 文件](docs/diagrams/onchain-architecture.drawio)使用 AWS 原生风格图标，包含三页：
整体部署、交易认证与拦截、用户与 Session 隔离。
[查看 PNG 预览与编辑说明](docs/diagrams/README.md)。

![AWS 平台架构](docs/diagrams/01-aws-architecture.png)

## 已实现

- Cognito managed login：OAuth 授权码 + PKCE、注册、密码恢复及 TOTP MFA。
- 对外 MCP OAuth Gateway：原生授权发现、Cognito Access Token 与 audience 校验、细分 scope、用户主动绑定/撤销 Agent 连接。
- 操作级重新认证，审批绑定用户、操作 ID、不可变意图摘要和有效期。
- 单笔交易点击“验证并执行”后自动提交，聊天卡片显示实时状态与成交回执；提交失败可沿用原操作重试。
- 用户/租户数据分区，服务端 Session 归属校验，独立 Runtime session，以及 microVM 内身份绑定。
- Bedrock `us.openai.gpt-6-sol` 意图识别；严格 schema 校验；模型不能批准交易。
- 独立 MCP Runtime：行情、持仓、交易准备、已批准交易执行、历史查询。
- Gateway REQUEST/RESPONSE Lambda 拦截器、JWT 验证、Policy ENFORCE、工具发现过滤。
- 固定版本 MCP 目录、用户启停订阅、经审核 Skill 与工具权限绑定。
- Agent 创建、模板选择、运行、暂停/恢复与删除；删除撤销旧凭据、持续授权和待执行操作，保留审计历史。
- 模拟交易、原子日额度、重复执行幂等、定投持续授权、撤销授权、定时调度。
- 用户审计中心、拦截验证、操作历史导出。
- Agent 历史会话、刷新恢复、只读历史回看，以及实时 Bedrock/Gateway/MCP/tool 运行时间线。
- Agent 服务市场：卖家发布已审核任务或定投策略，买家经 Cognito 确认购买，双边模拟费用原子结算，创建买方独立 Agent；任务从工作台运行，定投按明确授权自动执行。
- 13 个任务模板、8 个 MCP 产品、18 个工具和 8 个 Skill；覆盖下单、持仓分析、交易预检、代币研究、K 线、风险、信号与兑换路由。
- CloudFormation + AgentCore 部署脚本，安全单元测试和双用户浏览器验收。

## 交易边界

当前版本默认且仅允许 **paper 模拟执行**，初始每用户 10,000 USDC。模拟钱包、成交和参考价格在 UI 与接口中明确标记，不会提交链上交易，不伪造交易哈希。

「Agent 工作台 → 买入下单」提供 Base 上 USDC → ETH 的模拟市价单。先选择具有执行权限的 Agent，再填写金额和滑点，
点击「生成待确认订单」，Agent 经 Gateway/MCP 读取余额、查询报价并准备订单。
在原会话核对参考价和最低接收数量，通过 Cognito 验证后自动成交；余额、持仓和订单回执同步更新。
「订单历史」支持状态筛选、查看会话和撤回尚未执行的订单。新订单的价格下限绑定到用户确认的意图摘要；
执行时价格超出批准范围、余额不足或日额度耗尽都会阻止成交，重复执行不会重复扣款。
也可从「Agent 管理 → 任务模板 → ETH 买入下单」创建专用 Agent。详见 [下单场景](docs/order-scenarios.md)。

默认 `ONCHAIN_DATA_MODE=demo`，无需 OKX Key 即可运行全部任务模板。十个链上研究工具返回明确标注的合成演示数据，模拟交易使用 3,000 USD/ETH 参考价；Cognito、Bedrock、AgentCore Runtime、Gateway 和 MCP 均正常运行，认证与授权不会被跳过。真实数据需显式设置 `ONCHAIN_DATA_MODE=live` 并配置 `OKX_SECRET_ARN`，秘密结构为 `api_key`、`secret_key`、`passphrase`。真实数据模式失败不会回退到演示数据。实盘签名、用户钱包绑定和真实资金执行尚未实现，`TRADING_MODE` 设置为其他值会拒绝启动。

MCP 市场是可用的、平台维护的工具目录；不执行用户上传的 Skill 代码，不自动发布第三方远程 MCP。完整第三方商业市场、支付结算及任意链/币种不在本版本范围内。

## 对外 MCP OAuth

登录后打开「MCP / Skill 管理」，在「外部 MCP · Cognito OAuth」中选择 Agent 与权限，
通过 Cognito 重新认证完成连接授权。面板提供 MCP 地址、客户端 ID 和 OAuth 参数。
连接默认只读，交易准备和执行需要分别开启；允许执行也不能替代具体交易的用户确认。

外部客户端无需 IAM 凭据。Gateway 原生验证 Cognito 令牌，拦截器校验连接、scope、Skill、
订阅和审批。Gateway 到私有 MCP Runtime 的服务调用仍使用 IAM；用户身份由已验证的
工具执行上下文传递。内部 Worker/定投调度使用独立的受限执行凭据。

仓库提供可运行的 PKCE 客户端：`scripts/mcp_oauth_client.py`。客户端需使用固定回调
`http://localhost:8765/callback`；暂不提供动态客户端注册。详见 [OAuth MCP 接入](docs/oauth-mcp.md)。

## Agent 市场与自动交易

平台包含四个主入口：「Agent 市场」「Agent 管理」「Agent 工作台」「MCP / Skill 管理」。
市场包含「发现 Agent」「我购买的」「卖方中心」，支持发布持仓、研究、风险、路由和下单等任务模板。
买方核对模拟服务费、Skill 与 MCP 范围，经 Cognito 验证后获得独立 Agent 并进入工作台。
普通任务购买不创建交易授权；ETH 定投购买需额外确认交易预算与有效期，调度器才会自动执行。
工作台按 Agent 的 Skill 和任务模板展示示例动作；订单仅显示当前 Agent 的记录。
卖方只能看到销售订单和模拟收入，不能读取买方会话或控制买方 Agent。
详见 [Agent 市场](docs/agent-marketplace.md)。当前交易对象为 Agent 服务，资金与成交均为模拟。

## 历史会话与运行过程

「Agent 管理 → 任务模板」可创建专用 Agent，并在工作台配置参数运行。多步骤任务会逐次调用真实
Gateway/MCP 工具，保留结果与运行时间线。当前运行免 Key 演示模式，所有模板均可使用。
选择「代币尽调报告」创建 Agent 后，直接用预填示例运行，即可查看资料、价格、持有人与风险扫描四个步骤。
交易需选择「受控交易」Agent；输入金额并确认操作后，Cognito 验证会返回原会话并自动完成模拟交易。
自动交易可使用「ETH 自动定投」模板，或从 Agent 市场购买策略并确认持续授权。
详见 [Onchain OS 能力与配置](docs/onchain-os-capabilities.md)。

打开「Agent 工作台」，左侧按当前 Agent 列出历史会话，支持搜索与切换；中间显示消息，
右侧显示每次运行的意图识别、MCP 连接、工具参数、Gateway 检查、执行结果、耗时与错误。
展开事件可查看已脱敏的参数和结果。页面位置按用户、按标签页保存，刷新后恢复上次选择的
Agent 和会话。从会话发起交易确认后，Cognito 认证会自动返回原会话，继续显示执行进度和
成交结果；其他标签页的选择不会覆盖此次返回位置。

新会话不会删除旧会话。退出登录、会话到期或 Agent 暂停后仍可回看本人历史，但不会恢复
旧会话的执行权限；点击「新会话」使用当前登录重新开始。

历史默认保留 30 天，每个 Agent 列出最近 100 个会话；每个会话保留最近 30 条消息和
30 次运行，每次运行最多 40 个事件，单个详情预览最多约 4 KiB。旧版会话没有被记录过的
运行事件不会被补造。现有云端历史已迁移；其他既有部署可运行：

```bash
.venv/bin/python scripts/backfill_history.py
```

## 本地运行

需要 Python 3.12、Node.js 22，以及可调用指定 Bedrock 模型的 AWS 凭据。

```bash
python -m venv .venv
.venv/bin/pip install -e '.[runtime,dev]'
.venv/bin/uvicorn onchain.api:app --host 127.0.0.1 --port 8000
```

另一个终端：

```bash
cd frontend
npm ci
npm run dev
```

打开 `http://localhost:5173`。仅本地模式提供两个隔离账号：

| 邮箱 | 本地密码 |
|---|---|
| alice@example.test | local-only-change-me |
| bob@example.test | local-only-change-me |

生产环境禁用本地认证。应用不会在模型不可用时悄悄替换模型或伪造推理结果。

本地 API 会通过相同的 REQUEST interceptor 规则校验工具请求，但不会声称正在运行 AWS Gateway/microVM；生产部署实际调用 Gateway 和 Runtime。

## 验证

```bash
.venv/bin/ruff check backend runtimes infra scripts tests
.venv/bin/pytest -q
cd frontend
npm run build
npx playwright install chromium
npx playwright test
```

浏览器测试需要本地前后端已启动，并会实际调用 Bedrock。单元测试不调用 AWS。

## 部署

```bash
.venv/bin/python scripts/build_backend.py
cd frontend
npm ci
npm run build
cd ..
.venv/bin/python scripts/deploy.py --region us-east-1
```

部署创建真实 AWS 资源并产生费用。输出记录在 `.deployment/resources.json`。该目录不提交到 Git。

仅更新前端：

```bash
.venv/bin/python scripts/deploy.py --frontend-only
```

更新应用和 Runtime 代码：

```bash
.venv/bin/python scripts/build_backend.py
.venv/bin/python scripts/deploy.py --skip-stack
```

云端双用户验收：

```bash
.venv/bin/python scripts/smoke_users.py
cd frontend
TEST_BASE_URL=https://YOUR_DISTRIBUTION.cloudfront.net npx playwright test
```

测试账号使用 `example.invalid`，通过管理接口创建，不发送邮件；密码只写入权限为 0600 的忽略文件。普通用户通过网页“创建账号”注册。
OAuth 云端验收由 `frontend/tests/oauth-cloud.spec.ts` 覆盖，验证真实登录、PKCE、刷新、
scope、跨用户与会话隔离、具体操作确认和撤销连接。旧版直接密码登录脚本仅记录迁移前
的 TOTP 等验收流程，不作为当前生产登录路径。

详见 [架构](docs/architecture.md)、[安全设计](docs/security.md)、[运维](docs/operations.md)。
