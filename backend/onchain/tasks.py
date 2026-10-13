"""Fixed, versioned task plans composed of reviewed tools; template text is not authority."""
import copy

from jsonschema import ValidationError, validate

from .catalog import TOOL_PRODUCTS, skill_by_id, tool_schema
from .extensions import AMOUNT, CHAIN, LIMIT, TOKEN, schema, LIVE_TOOLS
from .security import Denied, digest


def step(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


TASKS = [
    {"id": "eth-buy", "name": "ETH 买入 Agent", "category": "交易",
     "description": "专注用 USDC 买入 ETH。读取余额和报价，生成订单，由买方确认后执行。",
     "skill_id": "eth-buyer", "trade_side": "BUY", "mode": "prepare",
     "parameters": schema({"amount": AMOUNT, "max_slippage_bps": {
         "type": "integer", "minimum": 0, "maximum": 100, "default": 50, "title": "最大滑点（基点）",
     }}),
     "steps": [step("get_portfolio"), step("get_quote", symbol="ETH"),
               step("prepare_trade", amount="$amount", buy_asset="ETH", chain="8453",
                    max_slippage_bps="$max_slippage_bps")]},
    {"id": "eth-sell", "name": "ETH 卖出 Agent", "category": "交易",
     "description": "卖出本人持有的 ETH 换回 USDC。输入 ETH 数量，核对最低接收金额并确认后执行；不允许做空。",
     "skill_id": "eth-seller", "trade_side": "SELL", "mode": "prepare",
     "parameters": schema({"amount": {
         "type": "string", "pattern": r"^(?:0|[1-9]\d*)(?:\.\d{1,18})?$", "maxLength": 32,
         "default": "0.005", "title": "卖出数量（ETH）",
     }, "max_slippage_bps": {
         "type": "integer", "minimum": 0, "maximum": 100, "default": 50, "title": "最大滑点（基点）",
     }}),
     "steps": [step("get_portfolio"), step("get_quote", symbol="ETH"),
               step("prepare_trade", amount="$amount", buy_asset="USDC", chain="8453",
                    max_slippage_bps="$max_slippage_bps")]},
    {"id": "eth-order", "name": "ETH 买入下单", "category": "交易",
     "description": "读取余额与报价，创建 Base 上的 USDC → ETH 模拟订单；核对并通过 Cognito 确认后执行。",
     "skill_id": "controlled-trader", "mode": "prepare",
     "parameters": schema({"amount": AMOUNT, "max_slippage_bps": {
         "type": "integer", "minimum": 0, "maximum": 100, "default": 50, "title": "最大滑点（基点，50 = 0.5%）",
     }}),
     "steps": [step("get_portfolio"), step("get_quote", symbol="ETH"),
               step("prepare_trade", amount="$amount", buy_asset="ETH", chain="8453",
                    max_slippage_bps="$max_slippage_bps")]},
    {"id": "portfolio-health", "name": "持仓健康检查", "category": "账户",
     "description": "查看本人模拟持仓、估值占比与当日额度使用情况。", "skill_id": "portfolio-auditor",
     "parameters": schema(), "steps": [step("get_portfolio"), step("analyze_portfolio")]},
    {"id": "trade-audit", "name": "交易与授权复盘", "category": "账户",
     "description": "汇总成交、待批准操作与持续授权；不读取其他用户数据。", "skill_id": "portfolio-auditor",
     "parameters": schema(), "steps": [step("get_history"), step("analyze_portfolio")]},
    {"id": "trade-preflight", "name": "USDC → ETH 交易预检", "category": "交易",
     "description": "检查余额、单笔和每日额度，输出模拟交易预检结果，不下单。", "skill_id": "trade-planner",
     "parameters": schema({"amount": AMOUNT}),
     "steps": [step("get_portfolio"), step("preview_trade", amount="$amount")]},
    {"id": "eth-dca", "name": "ETH 自动定投", "category": "交易",
     "description": "设置每笔金额、累计额度与有效期，经 Cognito 确认后自动模拟交易。",
     "skill_id": "controlled-trader", "mode": "approval", "parameters": schema(), "steps": []},
    {"id": "token-discovery", "name": "多链代币发现", "category": "研究",
     "description": "按名称或合约搜索代币，核对网络和合约身份。", "skill_id": "token-researcher",
     "parameters": tool_schema("search_tokens"),
     "steps": [step("search_tokens", chain="$chain", query="$query")]},
    {"id": "token-research", "name": "代币尽调报告", "category": "研究",
     "description": "组合资料、价格、持有人和风险扫描，研究指定合约。", "skill_id": "token-researcher",
     "parameters": schema(TOKEN), "steps": [
         step(tool, chain="$chain", token_address="$token_address", **({"limit": 10} if tool == "get_token_holders" else {}))
         for tool in ["get_token_info", "get_token_price", "get_token_holders", "scan_token_risk"]]},
    {"id": "candle-analysis", "name": "价格与 K 线观察", "category": "行情",
     "description": "查询指定周期的价格和 K 线，区分未完成蜡烛和历史数据。", "skill_id": "market-analyst",
     "parameters": tool_schema("get_candles"), "steps": [
         step("get_token_price", chain="$chain", token_address="$token_address"),
         step("get_candles", chain="$chain", token_address="$token_address", bar="$bar", limit="$limit")]},
    {"id": "smart-money", "name": "聪明钱信号观察", "category": "行情",
     "description": "读取链上聪明钱信号，作为研究线索，不授予跟单权限。", "skill_id": "market-analyst",
     "parameters": schema({"chain": CHAIN, "limit": LIMIT}),
     "steps": [step("get_smart_money_signals", chain="$chain", limit="$limit")]},
    {"id": "token-risk", "name": "买入前风险筛查", "category": "风险",
     "description": "核对合约资料和风险报告；无报告时不作安全结论。", "skill_id": "risk-reviewer",
     "parameters": schema(TOKEN), "steps": [step(tool, chain="$chain", token_address="$token_address")
                                         for tool in ["get_token_info", "scan_token_risk"]]},
    {"id": "swap-route", "name": "DEX 兑换路由查询", "category": "交易",
     "description": "查询聚合器路由报价和 Gas；金额使用代币最小单位，不创建签名交易。", "skill_id": "route-planner",
     "parameters": tool_schema("get_swap_quote"), "steps": [
         step("get_swap_quote", chain="$chain", from_token="$from_token", to_token="$to_token", amount_raw="$amount_raw"),
         step("get_gas_price", chain="$chain")]},
    {"id": "gas-check", "name": "网络 Gas 检查", "category": "行情",
     "description": "查询指定网络当前 Gas 价格，用于评估交易成本。", "skill_id": "route-planner",
     "parameters": schema({"chain": CHAIN}), "steps": [step("get_gas_price", chain="$chain")]},
    {"id": "chain-discovery", "name": "交易网络发现", "category": "研究",
     "description": "查询 Onchain OS 交易网关当前支持的网络。", "skill_id": "route-planner",
     "parameters": schema(), "steps": [step("get_supported_chains")]},
]
for task in TASKS:
    task.setdefault("mode", "read")
    task["version"] = "1.0.0"
    task["tools"] = [s["tool"] for s in task["steps"]] or skill_by_id(task["skill_id"])["tools"]
    task["products"] = sorted({TOOL_PRODUCTS[t] for t in task["tools"]})
    task["digest"] = digest(task)


def task_by_id(template_id):
    task = next((t for t in TASKS if t["id"] == template_id), None)
    if not task:
        raise Denied("TASK_NOT_FOUND", "Task template is not available.", 404)
    return task


def compatible_task(task, skill):
    if not skill or not set(task["tools"]).issubset(skill["tools"]):
        return False
    side = task.get("trade_side") or ("BUY" if task["id"] in {"eth-order", "eth-dca"} else None)
    return not side or side in skill.get("trade_sides", ["BUY"])


def task_plan(template_id, parameters, expected_digest=None):
    task = task_by_id(template_id)
    if expected_digest is not None and expected_digest != task["digest"]:
        raise Denied("TASK_CHANGED", "Task version changed; review the template again.", 409)
    if task["mode"] not in {"read", "prepare"}:
        raise Denied("TASK_APPROVAL_REQUIRED", "Use the operation confirmation flow for automatic trading.", 409)
    try:
        validate(parameters, task["parameters"])
    except ValidationError as e:
        raise Denied("INVALID_TASK_ARGUMENTS", "Task parameters do not match the reviewed schema.", 422) from e
    plan = []
    for item in task["steps"]:
        args = {k: parameters[v[1:]] if isinstance(v, str) and v.startswith("$") else v
                for k, v in item["arguments"].items()}
        validate(args, tool_schema(item["tool"]))
        plan.append({"tool": item["tool"], "arguments": args})
    return task, plan


def task_catalog(config):
    tasks = copy.deepcopy(TASKS)
    for task in tasks:
        # Presentation-only wording preserves existing Agents and signed template versions.
        if task["id"] == "chain-discovery":
            task["description"] = "查询链上数据网关当前支持的网络。"
        uses_provider = bool(set(task["tools"]) & LIVE_TOOLS) or (
            config.onchain_data_mode == "live" and "get_quote" in task["tools"]
        )
        task["data_mode"] = config.onchain_data_mode if uses_provider else "paper"
        task["availability"] = (
            "REQUIRES_DATA_PROVIDER" if uses_provider and config.onchain_data_mode == "live" and not config.data_provider_secret_arn else "READY"
        )
        if uses_provider and config.onchain_data_mode == "demo":
            from .demo_data import example_parameters
            from .extensions import CHAINS
            task["examples"] = {chain: example_parameters(task["id"], chain) for chain in CHAINS}
    return tasks
