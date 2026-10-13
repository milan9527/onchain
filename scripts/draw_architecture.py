"""Generate editable, uncompressed diagrams.net pages using native AWS 4 shapes."""
from html import escape
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/diagrams/onchain-architecture.drawio"
FONT = "Arial"
INK = "#232F3E"
BLUE = "#147EBA"
GREEN = "#087F75"
ORANGE = "#B56500"
RED = "#C5354A"
GRAY = "#69788A"
AWS = {
    "lambda": "#ED7100",
    "s3": "#7AA116",
    "dynamodb": "#C925D1",
    "bedrock": "#01A88D",
    "bedrock_agentcore": "#01A88D",
    "cognito": "#DD344C",
    "key_management_service": "#DD344C",
    "secrets_manager": "#DD344C",
    "cloudfront": "#8C4FFF",
    "api_gateway": "#E7157B",
    "sqs": "#E7157B",
    "eventbridge": "#E7157B",
    "cloudwatch": "#E7157B",
    "users": "#232F3E",
    "client": "#232F3E",
    "general": "#232F3E",
}


class Page:
    def __init__(self, file, name, number, title, subtitle, width=2520, height=1740):
        diagram = ET.SubElement(file, "diagram", id=f"page-{number}", name=name)
        model = ET.SubElement(
            diagram, "mxGraphModel", dx="2520", dy="1740", grid="1", gridSize="10",
            guides="1", tooltips="1", connect="1", arrows="1", fold="1",
            page="1", pageScale="1", pageWidth=str(width), pageHeight=str(height),
            math="0", shadow="0", background="#FFFFFF",
        )
        self.root = ET.SubElement(model, "root")
        ET.SubElement(self.root, "mxCell", id="0")
        ET.SubElement(self.root, "mxCell", id="1", parent="0")
        self.ids = {"0", "1"}
        self.text("title", 60, 30, width-300, 50, title, 32, INK, bold=True)
        self.text("subtitle", 60, 86, width-160, 45, subtitle, 17, GRAY)
        self.text("number", width-200, 35, 140, 40, f"0{number} / 03", 22, GREEN, bold=True)
        self.text("footer", 60, height-52, width-120, 28,
                  "ONCHAIN CONTROL  ·  2026-10-13  ·  当前部署：demo 数据 + paper 交易  ·  AWS 4 原生可编辑图形",
                  13, GRAY)

    def cell(self, id, x, y, w, h, value="", style="", parent="1"):
        if id in self.ids:
            raise ValueError(f"Duplicate cell: {id}")
        self.ids.add(id)
        cell = ET.SubElement(
            self.root, "mxCell", id=id, value=value,
            style=style, vertex="1", parent=parent,
        )
        ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h), **{"as": "geometry"})
        return id

    def text(self, id, x, y, w, h, text, size=16, color=INK, bold=False, parent="1"):
        return self.cell(
            id, x, y, w, h, escape(text).replace("\n", "<br>"),
            f"text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;"
            f"whiteSpace=wrap;overflow=hidden;fontFamily={FONT};fontSize={size};fontColor={color};"
            f"fontStyle={1 if bold else 0};spacing=0;", parent,
        )

    def zone(self, id, x, y, w, h, label, color=GRAY, fill="#FAFBFC", dashed=False):
        self.cell(
            id, x, y, w, h,
            style=f"rounded=0;html=1;fillColor={fill};strokeColor={color};"
                  f"strokeWidth=1.5;dashed={int(dashed)};container=0;pointerEvents=0;",
        )
        self.text(id+"-label", x+20, y+12, w-40, 30, label, 16, color, bold=True)

    def card(self, id, x, y, w, h, icon, title, detail, *, color=None, dashed=False):
        color = color or AWS[icon]
        self.cell(
            id, x, y, w, h,
            style=f"rounded=1;arcSize=10;whiteSpace=wrap;html=1;fillColor=#FFFFFF;"
                  f"strokeColor={color};strokeWidth=1.5;dashed={int(dashed)};container=0;",
        )
        self.cell(
            id+"-icon", 18, 18, 46, 46,
            style=f"shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{icon};"
                  f"fillColor={color};strokeColor=#FFFFFF;gradientColor=none;html=1;"
                  "outlineConnect=0;pointerEvents=1;", parent=id,
        )
        self.text(id+"-title", 80, 16, w-96, 51, title, 18, INK, bold=True, parent=id)
        self.text(id+"-detail", 20, 77, w-40, h-84, detail, 15, GRAY, parent=id)
        return id

    def note(self, id, x, y, w, h, title, body, color=GREEN, fill="#F0F8F6"):
        self.cell(id, x, y, w, h,
                  style=f"rounded=1;arcSize=8;html=1;fillColor={fill};strokeColor=none;")
        self.text(id+"-title", 20, 12, w-40, 30, title, 17, color, bold=True, parent=id)
        self.text(id+"-body", 20, 51, w-40, h-60, body, 15, INK, parent=id)

    def edge(self, id, source, target, label="", color=BLUE, dashed=False,
             points=(), exit=(1, .5), entry=(0, .5), label_offset=None):
        if source not in self.ids or target not in self.ids or id in self.ids:
            raise ValueError(f"Invalid edge: {id}")
        self.ids.add(id)
        cell = ET.SubElement(
            self.root, "mxCell", id=id, value=escape(label), source=source, target=target,
            edge="1", parent="1",
            style=f"edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=20;"
                  f"html=1;endArrow=block;endFill=1;strokeWidth=2;strokeColor={color};"
                  f"dashed={int(dashed)};fontFamily={FONT};fontSize=14;fontColor={color};"
                  f"labelBackgroundColor=#FFFFFF;jumpStyle=arc;jumpSize=10;"
                  f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;"
                  f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;",
        )
        geo = ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
        if points:
            array = ET.SubElement(geo, "Array", **{"as": "points"})
            for x, y in points:
                ET.SubElement(array, "mxPoint", x=str(x), y=str(y))
        if label_offset:
            ET.SubElement(geo, "mxPoint", x=str(label_offset[0]), y=str(label_offset[1]), **{"as": "offset"})


def overview(file):
    p = Page(file, "01 整体部署 / AWS architecture", 1, "Onchain Control · AWS 部署架构",
             "前后端分离  |  Agent 市场 · Agent 工作台（含管理）· MCP / Skill 管理  |  AWS Region: us-east-1")
    p.zone("aws", 370, 145, 2090, 1385, "AWS Cloud  ·  us-east-1 区域服务 + CloudFront 全球边缘服务")
    p.zone("gateway-zone", 830, 770, 1120, 440, "两座 AgentCore Gateway 共用 REQUEST / RESPONSE 拦截器 + Policy", GREEN, "#F3FAF8")
    p.card("browser", 60, 190, 265, 155, "users", "用户浏览器", "React / Vite\n买卖双方各自的工作空间")
    p.card("external", 60, 850, 265, 170, "client", "外部 MCP 客户端", "Cognito Access Token\n连接授权 + MCP scopes\n无需 AWS IAM 凭据")
    p.card("data-provider", 60, 1300, 265, 165, "general", "链上数据服务", "外部实时数据服务（可选）\n实时数据源尚未配置\n不含实盘签名与广播", dashed=True)
    p.card("cf", 440, 190, 260, 140, "cloudfront", "Amazon CloudFront", "HTTPS 静态站点 / API 代理\nAPI 请求不缓存")
    p.card("s3", 440, 380, 260, 135, "s3", "Amazon S3", "私有前端资源\nOrigin Access Control")
    p.card("http", 800, 190, 290, 140, "api_gateway", "Amazon API Gateway", "HTTP API\n/api/*")
    p.card("api", 1200, 190, 330, 140, "lambda", "AWS Lambda · API / BFF", "用户与会话 · 市场交易 · Agent 管理\n意图确认 / CSRF / 操作审批")
    p.card("cognito", 800, 380, 310, 140, "cognito", "Amazon Cognito", "User Pool / managed login\nOAuth code + PKCE / 重新认证")
    p.card("kms", 1580, 365, 300, 150, "key_management_service", "AWS KMS · 两把密钥", "执行凭据签名 + 公钥验证\nOAuth refresh-token 加密\n仅 BFF 可解密 OAuth 凭据")
    p.card("db", 2050, 190, 330, 150, "dynamodb", "Amazon DynamoDB", "用户 / Session / 操作 / 授权 / 历史\n市场目录 / 买卖双方订单\n条件更新 + 原子事务")
    p.card("model", 2050, 380, 330, 135, "bedrock", "Amazon Bedrock", "us.openai.gpt-6-sol\n自然语言意图识别")
    p.card("events", 440, 565, 260, 140, "eventbridge", "Amazon EventBridge", "定时 Rule · 每分钟\n仅触发调度器")
    p.card("scheduler", 800, 565, 290, 140, "lambda", "AWS Lambda · Scheduler", "已批准的定投 grant\n额度 / 到期 / 撤销校验")
    p.card("queue", 1200, 565, 330, 140, "sqs", "Amazon SQS", "持久任务队列 + DLQ\n稳定 job / operation ID")
    p.card("worker", 1640, 565, 280, 140, "lambda", "AWS Lambda · Worker", "核验签名与归属\n调用 Runtime / 保存 SSE 运行事件")
    p.card("agent", 2050, 565, 330, 140, "bedrock_agentcore", "AgentCore · Agent Runtime", "独立 runtime_session_id\n聊天意图 / 固定模板任务\n无数据库写权限，无批准工具")
    p.card("public-gw", 880, 840, 300, 150, "bedrock_agentcore", "公共 OAuth Gateway", "Cognito JWT / audience / scope\n用户已授权的 Agent 连接")
    p.card("internal-gw", 1280, 840, 300, 150, "bedrock_agentcore", "内部 Gateway", "KMS 签名的短期 capability\nowner / session / run 绑定")
    p.card("request", 875, 1030, 295, 155, "lambda", "REQUEST 拦截器", "身份 / 会话 / Skill / MCP 订阅\n工具 schema / 审批 / 额度\n签名绑定工具与参数摘要")
    p.card("response", 1210, 1030, 295, 155, "lambda", "RESPONSE 拦截器", "过滤可发现工具\n清理敏感输出")
    p.card("policy", 1545, 1030, 360, 155, "bedrock_agentcore", "AgentCore Policy", "Cedar · ENFORCE\n固定已审核工具 allowlist\n最终执行检查仍由 Executor 完成")
    p.card("mcp", 2050, 850, 330, 170, "bedrock_agentcore", "AgentCore · MCP Runtime", "独立托管的私有 MCP 服务\n仅 Gateway IAM role 可调用\n验证签名工具上下文")
    p.card("executor", 2050, 1150, 330, 170, "lambda", "AWS Lambda · Executor", "重新读取归属 / epoch / 授权\n余额 / 日额度 / 滑点 / 幂等检查\n原子提交 paper 交易")
    p.card("demo", 2050, 1360, 330, 130, "general", "内置演示适配器", "明确标注 demo / paper\n无需 API 密钥即可体验完整流程")
    p.card("secrets", 1580, 1360, 325, 130, "secrets_manager", "AWS Secrets Manager", "可选 live 数据凭据\n当前未配置", dashed=True)
    p.card("logs", 440, 1100, 300, 150, "cloudwatch", "Amazon CloudWatch", "Lambda / Runtime 服务日志\n用户运行历史另存 DynamoDB")
    p.note("data-note", 440, 1300, 1050, 150, "状态与权限依赖",
           "API、拦截器、Executor 与调度器按服务角色访问 DynamoDB / KMS。\nWorker 将 SSE 事件写入 owner 绑定的运行记录；前端通过 BFF 轮询。\nAgent Runtime 不直接访问用户数据库，也不能授权或签名交易。")
    p.edge("web-cf", "browser", "cf", "HTTPS")
    p.edge("cf-api", "cf", "http", "/api/*")
    p.edge("api-bff", "http", "api", "HTTP")
    p.edge("cf-s3", "cf", "s3", "静态资源", exit=(.5, 1), entry=(.5, 0))
    p.edge("login", "browser", "cognito", "OAuth / PKCE", GREEN,
           points=((192, 540), (750, 540), (750, 450)), exit=(.5, 1), entry=(0, .5))
    p.edge("oauth-callback", "cognito", "api", "回调 / code exchange", GREEN,
           points=((1145, 450), (1145, 355), (1320, 355)), exit=(1, .5), entry=(.36, 1))
    p.edge("bff-state", "api", "db", "会话 / 市场事务 / 操作", GRAY)
    p.edge("bff-kms", "api", "kms", "签名 / OAuth vault", GREEN, True,
           points=((1730, 320),), exit=(1, .85), entry=(.5, 0))
    p.edge("bff-job", "api", "queue", "签名任务", exit=(.5, 1), entry=(.5, 0))
    p.edge("tick", "events", "scheduler", "触发")
    p.edge("schedule-job", "scheduler", "queue", "已批准定投", ORANGE)
    p.edge("queue-worker", "queue", "worker", "SQS event")
    p.edge("worker-agent", "worker", "agent", "chat / task")
    p.edge("agent-model", "agent", "model", "聊天调用模型", exit=(.5, 0), entry=(.5, 1))
    p.edge("agent-gateway", "agent", "internal-gw", "工具请求", points=((2010, 745), (1490, 745)),
           exit=(.15, 1), entry=(.7, 0))
    p.edge("execute-gateway", "worker", "internal-gw", "已批准 execute（不再经过模型）", ORANGE,
           points=((1780, 730), (1380, 730)), exit=(.5, 1), entry=(.34, 0), label_offset=(0, -20))
    p.edge("external-gw", "external", "public-gw", "Cognito OAuth MCP", GREEN)
    p.edge("public-mcp", "public-gw", "mcp", "IAM 服务调用", GREEN,
           points=((1030, 815), (1990, 815), (1990, 895)), exit=(.5, 0), entry=(0, .26))
    p.edge("internal-mcp", "internal-gw", "mcp", "IAM + 签名用户工具上下文")
    for gw, gx in (("public-gw", 1030), ("internal-gw", 1430)):
        for control, cx in (("request", 1022), ("response", 1357), ("policy", 1725)):
            p.edge(f"{gw}-{control}", gw, control, "", GREEN, True,
                   points=((gx, 1010), (cx, 1010)), exit=(.5, 1), entry=(.5, 0))
    p.edge("mcp-executor", "mcp", "executor", "仅调用受信任 Executor", exit=(.5, 1), entry=(.5, 0))
    p.edge("executor-state", "executor", "db", "", GRAY, True,
           points=((2420, 1235), (2420, 265)), exit=(1, .5), entry=(1, .5))
    p.edge("executor-demo", "executor", "demo", "demo", exit=(.5, 1), entry=(.5, 0))
    p.edge("executor-secrets", "executor", "secrets", "仅 live", ORANGE, True,
           exit=(0, .8), entry=(1, .5))
    p.edge("live-data-provider", "executor", "data-provider", "可选 live adapter · 当前关闭", ORANGE, True,
           points=((1990, 1290), (1990, 1510), (192, 1510)), exit=(0, .8), entry=(.5, 1))
    p.note("legend", 60, 1570, 2380, 100, "阅读说明",
           "蓝线：业务 / 工具调用    绿线：身份与权限    橙线：已批准自动执行或可选 live    虚线：控制依赖 / 可选配置\n"
           "Gateway 的 Policy 与拦截器顺序不作假设；Executor 必须最终核验。当前没有 Guardrails、实盘 signer 或链上广播。",
           color=INK, fill="#F3F5F7")


def trade(file):
    p = Page(file, "02 交易认证与拦截 / Trade control", 2, "从用户意图到受控成交",
             "示例：Base 上用 20 USDC 买入 ETH  |  Cognito 确认绑定不可变 intent_hash  |  当前为模拟成交",
             width=2520, height=1680)
    p.zone("prepare", 60, 160, 2380, 340, "A · 准备订单：提出意图不等于授权", BLUE, "#F3F8FC")
    p.zone("consent", 60, 600, 2380, 340, "B · 用户确认：独立于模型与 MCP", GREEN, "#F1F9F6")
    p.zone("settle", 60, 1040, 2380, 350, "C · 执行与回执：同一订单只结算一次", ORANGE, "#FFFAF0")
    xs = [90, 695, 1300, 1905]
    w = 500
    nodes = [
        ("intent", 0, 230, "users", "01 · 用户在 Agent 工作台提出意图",
         "选择本人的 Agent / 会话\n输入「用 20 USDC 买入 ETH」\n或选择该 Agent 的下单示例"),
        ("recognize", 1, 230, "bedrock_agentcore", "02 · Agent Runtime 准备请求",
         "聊天：Bedrock GPT-6 Sol 识别意图\n模板：按已签名的固定步骤运行\n模型输出是待验证的输入，不是授权"),
        ("prepare-tools", 2, 230, "bedrock_agentcore", "03 · Gateway → MCP → Executor",
         "余额 / 报价 / prepare_trade\n逐次检查用户、Session、Skill、订阅\n工具 schema 与交易单笔上限"),
        ("pending", 3, 230, "dynamodb", "04 · 保存 PENDING 操作",
         "绑定 owner / Agent / conversation_id\n金额、资产、网络、滑点、最低接收量\n生成不可变 intent_hash；尚未扣款"),
        ("review", 0, 670, "users", "05 · 用户核对并勾选确认",
         "查看金额、参考价与最低接收量\n点击「验证并执行」\n浏览器保存原 Agent / 会话返回位置"),
        ("stepup", 1, 670, "cognito", "06 · Cognito 重新认证",
         "Authorization Code + PKCE\nprompt=login / max_age=0\n校验 state / nonce / auth_time / 用户"),
        ("approve", 2, 670, "lambda", "07 · BFF 消费一次性确认凭据",
         "CSRF + 同一登录 + 操作 ID + intent_hash\n凭据仅供当前操作使用且短期有效\n恢复原会话，批准并提交该笔执行"),
        ("queued", 3, 670, "sqs", "08 · SQS 持久化执行任务",
         "只携带已批准的 operation_id\nKMS 签名 owner / Session / Agent / run\n不存储用户密码"),
        ("execution-worker", 0, 1110, "lambda", "09 · Worker 直接调用 Gateway",
         "验证 capability 与当前生命周期\n调用 execute_trade(operation_id)\n交易执行不重新依赖模型做决定"),
        ("enforce", 1, 1110, "bedrock_agentcore", "10 · Gateway / MCP 权限检查",
         "REQUEST 拦截器 + Policy ENFORCE\nAgent epoch / scope / Skill / MCP 订阅\nMCP 验签后仅调用受信任 Executor"),
        ("commit", 2, 1110, "lambda", "11 · Executor 原子核验并成交",
         "重读 owner / operation / grant / epoch\n检查余额、日额度、滑点与意图摘要\n按本人 paper 钱包结算；拒绝未批准操作"),
        ("receipt", 3, 1110, "dynamodb", "12 · CONFIRMED + 唯一回执",
         "扣减 USDC、增加 ETH、记录预算消耗\n同一原子更新写入 paper_… 回执\n重试返回原结果，前端轮询原会话展示"),
    ]
    for id, col, y, icon, title, detail in nodes:
        p.card(id, xs[col], y, w, 215, icon, title, detail)
    for index, (source, target) in enumerate([
        ("intent", "recognize"), ("recognize", "prepare-tools"), ("prepare-tools", "pending"),
        ("review", "stepup"), ("stepup", "approve"), ("approve", "queued"),
        ("execution-worker", "enforce"), ("enforce", "commit"), ("commit", "receipt"),
    ]):
        color = BLUE if index < 3 else GREEN if index < 6 else ORANGE
        p.edge(f"step-{index}", source, target, color=color)
    p.edge("pending-review", "pending", "review", "订单交给用户核对，不自动批准", GREEN,
           points=((2155, 550), (340, 550)), exit=(.5, 1), entry=(.5, 0))
    p.edge("queued-worker", "queued", "execution-worker", "持久任务 → 当前执行状态重新核验", ORANGE,
           points=((2155, 990), (340, 990)), exit=(.5, 1), entry=(.5, 0))
    p.note("fail", 60, 1430, 1160, 150, "任一安全检查失败：阻止执行，不扣款",
           "越权 / 会话串用 / 非法工具参数 / 意图变更 / 过期授权 / 超额 / 滑点不满足 → 拒绝并记录。\n"
           "一分钟内至少 3 次严重越权事件可触发 Agent 暂停与 epoch 递增。",
           RED, "#FFF2F3")
    p.note("return", 1260, 1430, 1180, 150, "用户行为可控，认证返回与执行结果可追踪",
           "Gateway 输出过滤不替代成交前校验；模型没有批准工具，OAuth scope 也不等于交易批准。\n"
           "定投只在用户已确认的 grant 范围内运行；暂停、撤销或删除后，旧凭据不能继续执行。")


def isolation(file):
    p = Page(file, "03 用户会话隔离 / User isolation", 3, "卖家与买家的 Agent、Session 与数据隔离",
             "共享平台与服务部署，分别绑定身份、会话和数据权限  |  购买复制经审核的配置，不复制卖家私有会话",
             width=2520, height=1740)
    p.zone("seller-zone", 60, 170, 670, 1280, "用户 A · 卖家 / owner A", BLUE, "#F2F8FC")
    p.zone("shared-zone", 840, 170, 830, 1280, "共享控制面与服务（不是共享用户权限）", GREEN, "#F3F9F7")
    p.zone("buyer-zone", 1780, 170, 670, 1280, "用户 B · 买家 / owner B", "#8C4FFF", "#F7F4FC")
    p.card("seller-id", 100, 230, 590, 160, "cognito", "Cognito 用户 A", "服务端从认证结果确定 tenant / sub\n个人租户：一个用户对应一个 owner")
    p.card("buyer-id", 1820, 230, 590, 160, "cognito", "Cognito 用户 B", "独立登录 Cookie / OAuth token family\n不能使用 A 的 Session 或操作批准")
    p.card("catalog", 890, 230, 730, 160, "dynamodb", "共享 Agent 市场目录", "MARKETPLACE#CATALOG：任务版本、Skill 摘要、价格、发布介绍\n对外不公开 source_agent_id / owner；不含私有消息或凭据")
    p.card("source-agent", 100, 470, 590, 190, "bedrock_agentcore", "卖家来源 Agent A", "本人创建 / 选择任务模板\n仅发布该 Skill 支持的已审核任务\n发布版本与价格形成不可变条款")
    p.card("purchase", 890, 470, 730, 190, "lambda", "BFF · Cognito 购买确认与原子结算",
           "校验 listing / template / Skill digest、买方身份与服务费\nDynamoDB 事务：买方扣费、卖方收入、双边订单、买方新 Agent\n普通任务不创建交易 grant；ETH 定投须另有明确交易范围")
    p.card("buyer-agent", 1820, 470, 590, 190, "bedrock_agentcore", "买家独立 Agent B", "新 agent_id / owner B / epoch\n独立运行任务，按 Skill 展示示例动作\n购买任务不会自动批准单笔交易")
    p.card("seller-session", 100, 760, 590, 205, "bedrock_agentcore", "A 的 Runtime / MCP 会话",
           "conversation_A → runtime_session_A\n签名绑定 owner A / Agent A / run A\nmicroVM 首次身份绑定\n每次调用使用新的 MCP ClientSession")
    p.card("buyer-session", 1820, 760, 590, 205, "bedrock_agentcore", "B 的 Runtime / MCP 会话",
           "conversation_B → runtime_session_B\n签名绑定 owner B / Agent B / run B\n不能指定另一个用户的 Runtime session\n认证返回仅恢复经过后端核实的本人会话")
    p.card("shared-gateway", 890, 760, 730, 205, "bedrock_agentcore", "Gateway 拦截器 / 私有 MCP Runtime",
           "JWT principal + conversation / run 绑定；禁止同用户跨会话复用\n外部连接还绑定 client / token family / connection version\n工具参数禁止注入 tenant、userId、wallet 或 runtime_sid\n工具能力 = OAuth scopes ∩ 连接授权 ∩ MCP 订阅 ∩ Skill allowlist")
    p.card("seller-data", 100, 1070, 590, 250, "dynamodb", "DynamoDB · owner A 数据",
           "USER#A：Agents / operations / grants / paper wallet\nSESSION：每条记录带 owner，读取时校验\nHISTORY#A#agent_id / RUN#A#conversation#run\n卖家可见自己的收入与销售订单\n不返回买方 Agent、钱包、会话或运行数据")
    p.card("buyer-data", 1820, 1070, 590, 250, "dynamodb", "DynamoDB · owner B 数据",
           "USER#B：Agents / operations / grants / paper wallet\n每个会话拥有独立平台 ID 与 Runtime ID\nHISTORY#B#agent_id / RUN#B#conversation#run\n订单、历史、审批、预算均按 owner 校验\n本人的 Agents 共用本人钱包，不共享跨用户资金")
    p.card("shared-executor", 890, 1070, 730, 250, "lambda", "Executor · 最终数据访问与写入边界",
           "验证 audience=onchain-tools、工具名与 arguments_hash\n依据签名 owner 查找资源，不信任模型或客户端传入的身份\n重读 Agent 状态 / epoch / 用户批准 / grant / 预算\n交易只修改当前 owner 的状态；重复执行返回同一回执\n市场服务费的双边事务仅由已确认的购买流程处理")
    p.edge("seller-agent-edge", "seller-id", "source-agent", "本人创建", exit=(.5, 1), entry=(.5, 0))
    p.edge("buyer-agent-edge", "buyer-id", "buyer-agent", "本人持有", exit=(.5, 1), entry=(.5, 0))
    p.edge("publish", "source-agent", "catalog", "发布快照", points=((770, 565), (770, 310)), entry=(0, .5))
    p.edge("listing-purchase", "catalog", "purchase", "已审核条款", GREEN, exit=(.5, 1), entry=(.5, 0))
    p.edge("purchase-clone", "purchase", "buyer-agent", "创建独立实例", GREEN)
    p.edge("seller-runtime", "source-agent", "seller-session", "独立会话", exit=(.5, 1), entry=(.5, 0))
    p.edge("buyer-runtime", "buyer-agent", "buyer-session", "独立会话", exit=(.5, 1), entry=(.5, 0))
    p.edge("seller-tool", "seller-session", "shared-gateway", "A 的签名调用")
    p.edge("buyer-tool", "buyer-session", "shared-gateway", "B 的签名调用", "#8C4FFF", exit=(0, .5), entry=(1, .5))
    p.edge("checked-execution", "shared-gateway", "shared-executor", "验证后的工具上下文", GREEN, exit=(.5, 1), entry=(.5, 0))
    p.edge("owner-a-access", "shared-executor", "seller-data", "仅 owner=A", BLUE, exit=(0, .5), entry=(1, .5))
    p.edge("owner-b-access", "shared-executor", "buyer-data", "仅 owner=B", "#8C4FFF")
    p.text("seller-retention", 105, 1345, 580, 80, "A 不能读取 / 删除 B 的 Agent、Session、run 或订单。\n共享 Runtime 部署不等于共享会话或权限。", 15, BLUE)
    p.text("buyer-retention", 1825, 1345, 580, 80, "B 不继承卖家会话、授权 grant、钱包或密钥。\n卖家下架不会改变已售任务的买方批准范围。", 15, "#8C4FFF")
    p.note("denial", 890, 1340, 730, 92, "跨 owner / 跨 Session 请求 → DENY",
           "不以资源 ID、模型指令或 IAM 服务角色替代用户授权。", RED, "#FFF1F3")
    p.note("bound-claims", 60, 1490, 1170, 150, "短期签名绑定与后台强制检查",
           "owner + conversation + runtime_sid + Agent epoch + Skill digest + run\n"
           "工具执行上下文再绑定 tool + arguments_hash；篡改、过期或权限撤销均拒绝。\n"
           "同一 AWS 服务角色只表示服务身份，用户权限由签名上下文与当前状态共同决定。")
    p.note("revoke-history", 1270, 1490, 1180, 150, "删除 / 撤销后的行为",
           "删除 Agent：标记 DELETED、递增 epoch、撤销 grants、取消 PENDING / APPROVED 操作。\n"
           "旧 capability、旧 MCP 连接与排队执行无法恢复权限；历史保留只读。\n"
           "会话与运行默认保留 30 天；用户身份从 Cognito 获取，模型不能选择 owner。")


def main():
    file = ET.Element("mxfile", host="app.diagrams.net", agent="Onchain Control architecture generator",
                      version="29.0.0", type="device", compressed="false")
    overview(file)
    trade(file)
    isolation(file)
    ET.indent(file, space="  ")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(file).write(OUTPUT, encoding="utf-8", xml_declaration=True)
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(file)} editable pages)")


if __name__ == "__main__":
    main()
