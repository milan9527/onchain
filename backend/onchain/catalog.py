from .security import digest
from .extensions import EXTRA_PRODUCTS, EXTRA_SKILLS, EXTRA_SCHEMAS

PRODUCTS = [
    {"id": "market", "name": "Onchain Market", "description": "行情与兑换报价，明确标记数据来源。",
     "category": "market", "risk": "read", "version": "1.0.0", "publisher": "Onchain Control",
     "tools": ["get_quote"], "status": "PUBLISHED"},
    {"id": "portfolio", "name": "Private Portfolio", "description": "仅访问当前用户的持仓与成交记录。",
     "category": "portfolio", "risk": "private-read", "version": "1.0.0", "publisher": "Onchain Control",
     "tools": ["get_portfolio", "get_history"], "status": "PUBLISHED"},
    {"id": "trade", "name": "Controlled Execution", "description": "意图准备、审批校验与模拟交易执行。",
     "category": "execution", "risk": "asset-write", "version": "1.0.0", "publisher": "Onchain Control",
     "tools": ["prepare_trade", "execute_trade", "get_operation"], "status": "PUBLISHED"},
]
LEGACY_TOOLS = [tool for p in PRODUCTS for tool in p["tools"]]
PRODUCTS += EXTRA_PRODUCTS
TOOL_PRODUCTS = {tool: p["id"] for p in PRODUCTS for tool in p["tools"]}
SKILLS = [
    {"id": "portfolio-assistant", "name": "资产研究", "version": "1.0.0",
     "description": "读取持仓、解释报价；无交易权限。",
     "tools": ["get_portfolio", "get_history", "get_quote"]},
    {"id": "controlled-trader", "name": "受控交易", "version": "1.0.0",
     "description": "准备交易意图，只有有效审批或持续授权才能执行。",
     "tools": LEGACY_TOOLS},
    {"id": "eth-buyer", "name": "ETH 买入", "version": "1.0.0",
     "description": "仅允许用 USDC 买入 ETH；每笔订单需本人确认。",
     "instructions": ["用户须明确 USDC 金额，只能在 Base 用 USDC 买入 ETH。",
                      "先读取本人余额与报价，准备订单后等待操作级确认，不得声称已成交。",
                      "不索取密码或验证码；用户须在 Cognito 可信界面确认。"],
     "trade_sides": ["BUY"], "tools": LEGACY_TOOLS},
    {"id": "eth-seller", "name": "ETH 卖出", "version": "1.0.0",
     "description": "仅允许卖出本人持有的 ETH 换回 USDC；不允许做空，每笔订单需本人确认。",
     "instructions": ["用户须明确 ETH 数量，只能在 Base 卖出本人持仓换回 USDC，不得做空。",
                      "prepare_trade 的 amount 使用 ETH 数量、buy_asset=USDC；不得把 USDC 金额当成 ETH。",
                      "准备订单后等待本人确认；余额不足、报价失败或超限时停止。",
                      "不索取密码或验证码；用户须在 Cognito 可信界面确认。"],
     "trade_sides": ["SELL"], "tools": LEGACY_TOOLS},
]
SKILLS += EXTRA_SKILLS
for skill in SKILLS:
    skill["digest"] = digest(skill)


def skill_by_id(skill_id):
    return next((s for s in SKILLS if s["id"] == skill_id), None)


def tool_schema(name: str) -> dict:
    schemas = {
        "get_quote": {"type": "object", "properties": {"symbol": {"type": "string", "enum": ["ETH"]}},
                      "required": ["symbol"], "additionalProperties": False},
        "get_portfolio": {"type": "object", "properties": {}, "additionalProperties": False},
        "get_history": {"type": "object", "properties": {}, "additionalProperties": False},
        "prepare_trade": {"type": "object", "properties": {
            "amount": {"type": "string", "description": "Input amount: USDC when buying ETH; ETH when buying USDC."},
            "buy_asset": {"type": "string", "enum": ["ETH", "USDC"]},
            "chain": {"type": "string", "enum": ["8453"]},
            "max_slippage_bps": {"type": "integer", "minimum": 0, "maximum": 100},
        }, "required": ["amount", "buy_asset", "chain", "max_slippage_bps"], "additionalProperties": False},
        "execute_trade": {"type": "object", "properties": {"operation_id": {"type": "string"}},
                          "required": ["operation_id"], "additionalProperties": False},
        "get_operation": {"type": "object", "properties": {"operation_id": {"type": "string"}},
                          "required": ["operation_id"], "additionalProperties": False},
    }
    return {**schemas, **EXTRA_SCHEMAS}[name]
