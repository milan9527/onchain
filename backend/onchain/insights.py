from datetime import datetime, timezone
from decimal import Decimal

from .domain import amount_units, display_units


def analyze(control, p):
    state = control.ensure(p)
    cash = Decimal(state["wallet"]["USDC"]) / 1_000_000
    eth = Decimal(state["wallet"]["ETH"])
    total = cash + eth * 3000
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "mode": "paper", "source": "本人模拟账户 · ETH 参考价 3000 USD，非实时行情",
        "total_value_usdc": str(total), "cash_usdc": str(cash), "eth": str(eth),
        "eth_weight_percent": str(round(eth * 3000 / total * 100, 2)) if total else "0",
        "confirmed_trades": sum(o["kind"] == "TRADE" and o["status"] == "CONFIRMED" for o in state["operations"].values()),
        "pending_operations": sum(o["status"] == "PENDING" for o in state["operations"].values()),
        "daily_spent_usdc": display_units(sum(v for k, v in state["daily_spend"].items() if k.endswith(day))),
        "active_grants": sum(g["status"] == "ACTIVE" and g["expires_at"] > datetime.now(timezone.utc).timestamp()
                             and g["used_units"] < g["total_units"] for g in state["grants"].values()),
        "notice": "历史统计仅覆盖当前保留的操作记录。估值不是投资建议。",
    }


def preflight(control, p, agent_id, amount):
    units = amount_units(amount)
    state, agent = control.ensure(p), control.agent(p, agent_id)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    spent = state["daily_spend"].get(agent_id + ":" + day, 0)
    checks = [
        {"name": "模拟余额", "passed": units <= state["wallet"]["USDC"]},
        {"name": "单笔额度", "passed": units <= agent["max_trade_units"]},
        {"name": "当日剩余额度", "passed": units + spent <= agent["daily_limit_units"]},
    ]
    return {
        "mode": "paper", "source": "本人账户与当前 Agent 权限",
        "amount_usdc": amount, "checks": checks, "within_limits": all(c["passed"] for c in checks),
        "remaining_daily_usdc": display_units(max(0, agent["daily_limit_units"] - spent)),
        "approved": False, "notice": "预检不创建操作、不批准或执行交易；实际执行时重新核验。",
    }
