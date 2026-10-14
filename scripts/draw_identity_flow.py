"""Two focused, editable AWS diagrams for the README authentication walkthrough."""
from html import escape
from pathlib import Path
import xml.etree.ElementTree as ET

from draw_architecture import Page, AWS, BLUE, GREEN, INK, ORANGE, RED

OUTPUT = Path(__file__).resolve().parents[1] / "docs/diagrams/identity-auth-flow.drawio"


class FlowPage(Page):
    def card(self, id, x, y, w, h, icon, title, detail):
        super().card(id, x, y, w, h, icon, title, detail)
        for suffix, size in (("-title", 23), ("-detail", 20)):
            cell = self.root.find(f"mxCell[@id='{id + suffix}']")
            cell.set("style", cell.get("style") + f"fontSize={size};")

    def edge(self, id, source, target, label="", *args, **kwargs):
        super().edge(id, source, target, label, *args, **kwargs)
        cell = self.root.find(f"mxCell[@id='{id}']")
        cell.set("value", escape(label).replace("\n", "<br>"))
        cell.set("style", cell.get("style") + "fontSize=19;")

    def message(self, id, x1, x2, y, label, color=BLUE, dashed=False):
        if id in self.ids:
            raise ValueError(id)
        self.ids.add(id)
        cell = ET.SubElement(
            self.root, "mxCell", id=id, edge="1", parent="1",
            style=f"endArrow=block;endFill=1;strokeWidth=2;strokeColor={color};"
                  f"dashed={int(dashed)};rounded=0;",
        )
        geo = ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
        ET.SubElement(geo, "mxPoint", x=str(x1), y=str(y), **{"as": "sourcePoint"})
        ET.SubElement(geo, "mxPoint", x=str(x2), y=str(y), **{"as": "targetPoint"})
        self.text(id + "-label", min(x1, x2) + 10, y - 48, abs(x2-x1) - 20, 43, label, 19, color)


def relationship(file):
    p = FlowPage(file, "01 每次工具调用 / Authentication", 1, "每次工具调用：凭证怎么传，在哪里验证",
                 "蓝线 = 工具调用；结果经 Gateway RESPONSE 脱敏后返回    绿线 = 登录与凭证管理    下游 token 不进入模型或浏览器",
                 width=1600, height=1180, total=2)
    p.card("user", 60, 165, 350, 155, "users", "用户 / 浏览器", "Cognito 登录后获得会话 Cookie\n发送任务，不指定其他用户身份")
    p.card("bff", 610, 165, 350, 155, "lambda", "平台后端", "校验登录、Agent 与会话\n签发短期 JWT\nWorker 调用 Agent Runtime")
    p.card("cognito", 1140, 165, 400, 155, "cognito", "Amazon Cognito", "签发登录与 MCP 用户 token\n首次工具账户授权见图 2")
    p.card("agent", 60, 435, 350, 175, "bedrock_agentcore", "Agent Runtime", "验 runtime_token；绑定会话\n生成工具名与参数\n用 gateway_token 调 Gateway")
    p.card("gateway", 610, 435, 350, 175, "bedrock_agentcore", "AgentCore Gateway", "验 JWT；检查 Skill / 工具权限\nREQUEST 生成 context_id\nPolicy ENFORCE；失败即拦截")
    p.card("mcp", 1140, 435, 400, 175, "bedrock_agentcore", "MCP Runtime", "验证实际 Cognito access token\n调用 Executor 授权并绑定会话\n通过后才进入 execute")
    p.card("vault", 60, 770, 350, 160, "bedrock_agentcore", "Token Vault", "Identity 内的托管凭证存储\n按 workload + 用户隔离\n保管 access / refresh token")
    p.card("identity", 610, 770, 350, 160, "bedrock_agentcore", "AgentCore Identity", "按工作负载 + 用户取凭证\n缺少凭证 → 发起 OAuth 授权\n已有凭证 → 取用 / 刷新")
    p.card("executor", 1140, 770, 400, 160, "lambda", "Executor → 用户数据", "再验 token、调用记录与当前权限\n只读写已验证 owner 的钱包 / 订单\n成交另外校验用户批准与额度")
    p.edge("user-bff", "user", "bff", "Cookie + 任务")
    p.edge("bff-cognito", "bff", "cognito", "登录 OAuth / PKCE", GREEN)
    p.edge("bff-agent", "bff", "agent", "runtime_token（入站）+ gateway_token（供出站）",
           points=((785, 370), (235, 370)), exit=(.5, 1), entry=(.5, 0))
    p.edge("agent-gateway", "agent", "gateway", "gateway_token")
    p.edge("gateway-mcp", "gateway", "mcp", "access token\n+ context_id")
    p.edge("gateway-identity", "gateway", "identity", "IAM / workload\n+ 当前用户", GREEN,
           exit=(.25, 1), entry=(.25, 0), label_offset=(-70, 0))
    p.edge("identity-gateway", "identity", "gateway", "本人下游\naccess token", GREEN,
           exit=(.8, 0), entry=(.8, 1), label_offset=(65, 0))
    p.edge("identity-vault", "identity", "vault", "读写用户凭证", GREEN, exit=(0, .5), entry=(1, .5))
    p.edge("mcp-executor", "mcp", "executor", "IAM 服务调用\n传 token + context_id",
           exit=(.5, 1), entry=(.5, 0))
    p.cell("record-box", 60, 975, 900, 135,
           style="rounded=1;fillColor=#F0F8F6;strokeColor=none;")
    p.text("record-title", 80, 990, 850, 30, "context_id 是服务端调用记录的编号，不是用户 token", 23, GREEN, True)
    p.text("record-detail", 80, 1030, 850, 65,
           "记录绑定：用户 + Agent + 会话 + 工具 + 参数摘要；最长有效 120 秒。\nExecutor 比对 token 用户与记录用户，再检查会话、权限和撤销状态。", 20)
    p.cell("deny-box", 1010, 975, 530, 135,
           style="rounded=1;fillColor=#FFF2F3;strokeColor=none;")
    p.text("deny-title", 1030, 990, 490, 30, "用户或会话不匹配 → 拒绝", 23, RED, True)
    p.text("deny-detail", 1030, 1030, 490, 65,
           "共享 AWS 服务角色，不共享用户权限。\n授权工具账户不等于批准交易。", 20)


def consent(file):
    p = FlowPage(file, "02 首次授权 / Consent", 2, "首次没有工具凭证：用户授权，再回原会话继续",
                 "平台 = BFF / Worker / Agent Runtime    本图展开图 1 的 Identity 旁路；已有有效凭证时跳过用户授权",
                 width=1600, height=1570, total=2)
    actors = [
        ("user", 170, "users", "用户 / 浏览器"),
        ("platform", 430, "lambda", "平台"),
        ("gateway", 690, "bedrock_agentcore", "Gateway"),
        ("identity", 950, "bedrock_agentcore", "Identity"),
        ("cognito", 1210, "cognito", "Cognito"),
        ("vault", 1470, "bedrock_agentcore", "Token Vault"),
    ]
    for id, x, icon, label in actors:
        p.cell(id, x-110, 170, 220, 105,
               style=f"rounded=1;fillColor=#FFFFFF;strokeColor={AWS[icon]};strokeWidth=1.5;")
        p.cell(id+"-icon", x-20, 180, 40, 40,
               style=f"shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{icon};"
                     f"fillColor={AWS[icon]};strokeColor=#FFFFFF;gradientColor=none;")
        p.text(id+"-name", x-100, 232, 200, 30, label, 22, INK, True)
        p.cell(id+"-line", x, 280, 1, 1100,
               style="shape=line;direction=south;strokeColor=#CDD4DC;dashed=1;strokeWidth=1;")

    def band(id, y, label):
        p.cell(id, 40, y, 1520, 40, style="rounded=0;fillColor=#F0F8F6;strokeColor=none;")
        p.text(id+"-label", 55, y+4, 1480, 32, label, 21, GREEN, True)

    band("lookup", 295, "1 · 首次调用：Gateway 找不到当前用户的下游凭证")
    p.message("lookup-identity", 690, 950, 395, "取当前用户的 token", GREEN)
    p.message("lookup-vault", 950, 1470, 455, "按 Gateway workload + 用户查询凭证", GREEN)
    p.message("no-token", 1470, 950, 510, "没有已授权凭证", GREEN, True)
    p.message("challenge", 950, 170, 575, "授权链接经 Gateway → 平台返回；任务暂停，显示「授权并继续任务」", GREEN, True)

    band("authorize", 600, "2 · 用户授权：Cognito 验证本人；平台将此次授权绑定回原用户")
    p.message("user-login", 170, 1210, 700, "点击链接，经 Identity 跳转到 Cognito，登录 / 授权本人的工具账户", GREEN)
    p.message("oauth-code", 1210, 950, 760, "回调携带授权码 code", GREEN, True)
    p.message("return-platform", 950, 170, 820, "浏览器返回平台，携带 session_id（授权流程编号，不是 access token）", GREEN, True)
    p.message("browser-complete", 170, 430, 900, "Cookie + CSRF\n+ session_id", GREEN)
    p.message("complete", 430, 950, 985, "校验原用户 / 登录 / 会话；CompleteResourceTokenAuth\n提交 session_id + 发起时原始 gateway_token", GREEN)
    p.message("code-exchange", 950, 1210, 1050, "兑换授权码", GREEN)
    p.message("provider-tokens", 1210, 950, 1115, "access / refresh token", GREEN, True)
    p.message("store-tokens", 950, 1470, 1180, "按 workload + 用户保存凭证", GREEN)
    p.message("complete-result", 950, 430, 1245, "授权完成；下游 token 不返回浏览器", GREEN, True)

    band("resume", 1270, "3 · 返回原 Agent 会话：重试原任务，重新通过图 1 的每一层检查")
    p.message("retry", 430, 690, 1370, "原任务\n+ 新 gateway_token")
    p.cell("result-note", 60, 1410, 1480, 95,
           style="rounded=1;fillColor=#FFFAF0;strokeColor=none;")
    p.text("result-text", 80, 1425, 1440, 65,
           "此后：Gateway 取得本人 access token + 新 context_id → MCP → Executor。\n"
           "首次授权只连接工具账户；下单成交仍需要独立的用户确认。授权了另一个账号，会被用户匹配校验拒绝。",
           22, ORANGE)


def main():
    file = ET.Element("mxfile", host="app.diagrams.net", agent="Onchain Control identity flow generator",
                      version="29.0.0", type="device", compressed="false")
    relationship(file)
    consent(file)
    ET.indent(file, space="  ")
    ET.ElementTree(file).write(OUTPUT, encoding="utf-8", xml_declaration=True)
    print(f"Wrote {OUTPUT.name} ({len(file)} editable pages)")


if __name__ == "__main__":
    main()
