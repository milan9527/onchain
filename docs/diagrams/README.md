# AWS 架构图

[下载可编辑的 draw.io 文件](onchain-architecture.drawio)。文件包含三页，使用 diagrams.net
内置的 **AWS 4** 图形库，包括 Bedrock AgentCore、Bedrock、Cognito、Lambda、API Gateway、
CloudFront、S3、DynamoDB、SQS、EventBridge、KMS、Secrets Manager 和 CloudWatch 图标。
图标采用 [AWS Architecture Icons](https://aws.amazon.com/architecture/icons/) 风格，
由 diagrams.net 的 AWS 4 图形库提供。

图标、服务卡片、文字和连线均为可编辑对象，主文件不是嵌入的一张图片。
以下 PNG 用于 GitHub 预览；可点击查看原始尺寸。

| 页面 | 内容 | PNG 预览 |
|---|---|---|
| 01 整体部署 | 前后端分离、内部与公共 Gateway、独立 Agent/MCP Runtime、执行器、数据和可选数据源接入 | [整体部署](01-aws-architecture.png) |
| 02 交易认证与拦截 | 意图准备、Cognito 重新认证、一次性确认、Gateway 拦截、原子成交、回执和撤权 | [交易流程](02-trade-control.png) |
| 03 用户会话隔离 | 卖家发布、买家购买独立 Agent、Runtime/MCP 会话绑定、数据归属与删除后的行为 | [用户隔离](03-user-isolation.png) |

## 打开与编辑

1. 将 `onchain-architecture.drawio` 下载到本地。
2. 在最新版 [diagrams.net](https://app.diagrams.net/) 或 draw.io Desktop 中选择
   **File → Open From → Device**，打开文件。
3. 通过底部标签切换三页。服务卡片内的图标和文字也是独立图形，可以分别编辑。
4. 使用 **File → Export as → PNG / SVG / PDF** 导出需要的页面。

较旧编辑器可能没有 Bedrock AgentCore 图标；使用最新版 AWS 4 图形库即可。
主文件使用未压缩 XML，便于 Git 差异审阅。预览图已用 diagrams.net 的图形引擎渲染核对。

## 图中边界

- 描述的是本仓库已实现的平台，区域为 `us-east-1`；CloudFront 是全球边缘服务。
  Bedrock 使用 `us.openai.gpt-6-sol` 推理配置，实际模型路由由该配置决定。
- Cognito **User Pool** 为用户和外部 MCP 提供 OAuth；Gateway 到 MCP Runtime 的 IAM
  role 表示服务身份，用户权限来自已验证的签名上下文和当前授权状态。
- 内部和公共 Gateway 共用 REQUEST/RESPONSE 拦截器及 Policy ENFORCE。图中的控制依赖
  不规定 Policy 与拦截器的内部调用顺序，Executor 仍须在成交前最终核验。
- 聊天及模板任务经过 Agent Runtime；已批准的单笔/定投执行由 Worker 直接调用 Gateway。
  固定模板不必再次调用模型来规划步骤。
- 第三页的 A/B 图标表示同一服务部署中的独立 Agent、Runtime session 与数据归属，
  不表示为每位用户新建一套 AWS 资源。DynamoDB 的两个数据框是逻辑 owner 分区。
- 当前为 `demo` 数据和 `paper` 交易。虚线的 Secrets Manager/数据源路径是可选 live
  数据接入，当前未配置；图中没有实盘 signer 或链上广播组件，也没有配置 Guardrails。

## 维护

图中关系根据以下代码核对：

- 部署资源与角色：[infra/template.py](../../infra/template.py)、[scripts/deploy.py](../../scripts/deploy.py)
- 作业与执行分流：[worker.py](../../backend/onchain/worker.py)、[scheduler.py](../../backend/onchain/scheduler.py)
- Gateway 与工具检查：[interceptors.py](../../backend/onchain/interceptors.py)、[services.py](../../backend/onchain/services.py)
- OAuth、市场与归属：[oauth.py](../../backend/onchain/oauth.py)、[marketplace.py](../../backend/onchain/marketplace.py)、[domain.py](../../backend/onchain/domain.py)

布局生成器只使用 Python 标准库：

```bash
python scripts/draw_architecture.py
```

生成器会重建 `.drawio` 文件；手工修改图后若需继续用生成器维护，也应同步修改生成器。
结构修改后重新导出三张 PNG 预览。

## 01 · 整体部署

![AWS 整体部署架构](01-aws-architecture.png)

## 02 · 交易认证与拦截

![交易认证与拦截流程](02-trade-control.png)

## 03 · 用户会话隔离

![用户与 Session 隔离](03-user-isolation.png)
