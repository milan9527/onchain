import asyncio
from decimal import Decimal

import pytest

from onchain.config import Settings
from onchain.domain import AgentSpec, Control, TradeIntent
from onchain.security import Denied, Principal, digest
from onchain.services import execute_tool, quote_eth
from onchain.store import MemoryStore
from onchain.tasks import task_by_id, task_plan
from runtimes.task_runner import run_template


@pytest.fixture
def order():
    config, control = Settings(), Control(MemoryStore())
    owner = Principal(sub="order-owner", tenant="order-owner")
    agent = control.create_agent(owner, AgentSpec(name="Orders", template_id="eth-order", enable_template_products=True))
    session = control.create_conversation(owner, agent["id"])
    claims = control.capability(owner, session["id"], "order-run")
    return config, control, owner, agent, claims


def prepare(order, amount="20"):
    config, control, _owner, _agent, claims = order
    return execute_tool(control, config, claims, "prepare_trade",
                        {"amount": amount, "buy_asset": "ETH", "chain": "8453", "max_slippage_bps": 50})["operation"]


def test_order_template_stops_for_specific_approval_and_keeps_actionable_operation(order):
    config, control, owner, _agent, claims = order
    task = task_by_id("eth-order")
    spec = {"template_id": task["id"], "template_digest": task["digest"],
            "parameters": {"amount": "20", "max_slippage_bps": 50}}
    called = []
    async def call(tool, args):
        called.append(tool)
        return execute_tool(control, config, claims, tool, args)
    async def progress(**_event):
        pass
    result = asyncio.run(run_template(spec, {**claims, "task_hash": digest(spec)}, call, progress))
    assert called == ["get_portfolio", "get_quote", "prepare_trade"]
    assert result["tool_result"]["status"] == "approval_required"
    op = result["tool_result"]["operation"]
    assert op["status"] == "PENDING" and op["conversation_id"] == claims["sid"]
    assert op["intent"]["reference_price"] == "3000"
    assert Decimal(op["intent"]["minimum_received"]) > 0
    assert digest(op["intent"]) == op["intent_hash"]
    assert control.snapshot(owner)["wallet"] == {"USDC": "10000", "ETH": "0"}
    with pytest.raises(Denied) as rejected:
        execute_tool(control, config, claims, "execute_trade", {"operation_id": op["id"]})
    assert rejected.value.code == "APPROVAL_REQUIRED"
    forged = {**spec, "parameters": {"amount": "99", "max_slippage_bps": 50}}
    with pytest.raises(Denied):
        asyncio.run(run_template(forged, {**claims, "task_hash": digest(spec)}, call, progress))
    assert len(control.snapshot(owner)["operations"]) == 1


def test_approved_order_settles_once_and_receipt_matches_balance(order):
    config, control, owner, _agent, claims = order
    op = prepare(order)
    with pytest.raises(Denied):
        control.approve(owner, op["id"], "different-operation", op["intent_hash"])
    control.approve(owner, op["id"], op["id"], op["intent_hash"])
    first = execute_tool(control, config, claims, "execute_trade", {"operation_id": op["id"]})
    again = execute_tool(control, config, claims, "execute_trade", {"operation_id": op["id"]})
    assert first == again and first["status"] == "CONFIRMED"
    assert first["execution"]["tx_hash"] is None
    assert first["execution"]["receipt"] == "paper_" + op["id"]
    wallet = control.snapshot(owner)["wallet"]
    assert wallet["USDC"] == "9980"
    assert wallet["ETH"] == first["execution"]["received"]


def test_price_limit_is_immutable_and_checked_before_settlement(order):
    config, control, owner, _agent, _claims = order
    op = prepare(order)
    changed = {**op["intent"], "minimum_received": "0"}
    with pytest.raises(Denied):
        control.approve(owner, op["id"], op["id"], digest(changed))
    control.approve(owner, op["id"], op["id"], op["intent_hash"])
    with pytest.raises(Denied) as error:
        control.execute(owner, op["id"], {**quote_eth(config), "price": 3100})
    assert error.value.code == "SLIPPAGE_EXCEEDED"
    assert control.operation(owner, op["id"])["status"] == "APPROVED"
    assert control.snapshot(owner)["wallet"] == {"USDC": "10000", "ETH": "0"}
    result = control.execute(owner, op["id"], {**quote_eth(config), "price": 3001})
    assert result["status"] == "CONFIRMED" and result["execution"]["price"] == 3001


def test_same_invocation_retains_its_original_order_when_quote_changes(order):
    config, control, owner, agent, claims = order
    op = prepare(order)
    args = {"amount": "20", "buy_asset": "ETH", "chain": "8453", "max_slippage_bps": 50}
    retried = control.prepare_trade(owner, agent["id"], TradeIntent(**args), claims["run_id"] + digest(args),
                                    conversation_id=claims["sid"], quote={**quote_eth(config), "price": 3100})
    assert retried == op
    assert len(control.snapshot(owner)["operations"]) == 1


def test_cancelled_order_and_other_user_cannot_execute(order):
    config, control, owner, _agent, claims = order
    op = prepare(order)
    stranger = Principal(sub="stranger", tenant="stranger")
    for action in (lambda: control.operation(stranger, op["id"]),
                   lambda: control.reject(stranger, op["id"]),
                   lambda: control.approve(stranger, op["id"], op["id"], op["intent_hash"])):
        with pytest.raises(Denied):
            action()
    control.approve(owner, op["id"], op["id"], op["intent_hash"])
    control.reject(owner, op["id"])
    with pytest.raises(Denied):
        execute_tool(control, config, claims, "execute_trade", {"operation_id": op["id"]})
    assert control.snapshot(owner)["wallet"]["USDC"] == "10000"


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf")])
def test_invalid_quotes_cannot_create_or_execute_orders(order, price):
    config, control, owner, agent, _claims = order
    quote = {**quote_eth(config), "price": price}
    with pytest.raises(Denied):
        control.prepare_trade(owner, agent["id"], TradeIntent(amount="20"), "invalid-quote", quote=quote)
    op = prepare(order)
    control.approve(owner, op["id"], op["id"], op["intent_hash"])
    with pytest.raises(Denied):
        control.execute(owner, op["id"], quote)
    assert control.snapshot(owner)["wallet"]["USDC"] == "10000"


def test_orders_require_allowed_amount_parameters_and_remaining_budget(order):
    config, control, owner, agent, _claims = order
    for params in ({"amount": "20", "max_slippage_bps": 50, "execute": True},
                   {"amount": "20", "max_slippage_bps": 101},
                   {"amount": "-1", "max_slippage_bps": 50}):
        with pytest.raises(Denied):
            task_plan("eth-order", params)
    with pytest.raises(Denied) as error:
        prepare(order, "101")
    assert error.value.code == "TRADE_LIMIT"
    control.mutate(owner, lambda state: state["wallet"].update(USDC=1_000_000))
    with pytest.raises(Denied) as error:
        prepare(order)
    assert error.value.code == "INSUFFICIENT_BALANCE"
    assert control.snapshot(owner)["operations"] == []
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    def spend(state):
        state["wallet"]["USDC"] = 10_000_000_000
        state["daily_spend"][agent["id"]+":"+day] = agent["daily_limit_units"]
    control.mutate(owner, spend)
    with pytest.raises(Denied) as error:
        prepare(order)
    assert error.value.code == "DAILY_LIMIT"
