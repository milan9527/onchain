from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from onchain.config import Settings
from onchain.domain import AgentSpec, Control, TradeIntent
from onchain.marketplace import ListingSpec, Marketplace, PurchaseSpec
from onchain.security import Denied, Principal
from onchain.services import execute_tool, quote_eth
from onchain.store import MemoryStore, SQLiteStore


@pytest.fixture(params=["memory", "sqlite"])
def trading(request, tmp_path):
    store = MemoryStore() if request.param == "memory" else SQLiteStore(str(tmp_path / "trading.db"))
    control = Control(store)
    user = Principal(sub="buyer", tenant="buyer")
    agents = [control.create_agent(user, AgentSpec(name=template, template_id=template))
              for template in ("eth-buy", "eth-sell")]
    claims = [control.capability(user, control.create_conversation(user, a["id"])["id"], a["id"])
              for a in agents]
    return control, user, agents, claims


def order(control, claims, amount, asset):
    return execute_tool(control, Settings(), claims, "prepare_trade", {
        "amount": amount, "buy_asset": asset, "chain": "8453", "max_slippage_bps": 50,
    })["operation"]


def confirm(control, user, claims, op):
    control.approve(user, op["id"], op["id"], op["intent_hash"])
    return execute_tool(control, Settings(), claims, "execute_trade", {"operation_id": op["id"]})


def test_buy_then_sell_requires_fresh_confirmation_and_settles_once(trading):
    control, user, _agents, claims = trading
    buy = order(control, claims[0], "30", "ETH")
    confirm(control, user, claims[0], buy)
    sell = order(control, claims[1], "0.005", "USDC")
    assert sell["intent"]["sell_amount"] == "0.005"
    assert sell["intent"]["amount_units"] == 15_000_000
    assert sell["intent"]["minimum_received"] == "14.925000"
    assert sell["intent"]["sell_asset"] == "ETH"
    assert sell["conversation_id"] == claims[1]["sid"]
    assert control.snapshot(user)["wallet"] == {"USDC": "9970", "ETH": "0.01"}
    with pytest.raises(Denied, match="confirmation"):
        execute_tool(control, Settings(), claims[1], "execute_trade", {"operation_id": sell["id"]})
    with pytest.raises(Denied, match="different agent"):
        execute_tool(control, Settings(), claims[0], "execute_trade", {"operation_id": sell["id"]})
    first = confirm(control, user, claims[1], sell)
    again = execute_tool(control, Settings(), claims[1], "execute_trade", {"operation_id": sell["id"]})
    assert first == again
    assert first["execution"]["received_asset"] == "USDC"
    assert Decimal(first["execution"]["received"]) == 15
    assert control.snapshot(user)["wallet"] == {"USDC": "9985", "ETH": "0.005"}


def test_direction_balance_quote_and_grant_boundaries(trading):
    control, user, agents, claims = trading
    for claim, asset, amount in [(claims[0], "USDC", "0.005"), (claims[1], "ETH", "20")]:
        with pytest.raises(Denied) as error:
            order(control, claim, amount, asset)
        assert error.value.code == "TRADE_SIDE_DENIED"
    with pytest.raises(Denied) as error:
        order(control, claims[1], "0.005", "USDC")
    assert error.value.code == "INSUFFICIENT_BALANCE"
    with pytest.raises(Denied) as error:
        control.prepare_grant(user, agents[1]["id"], TradeIntent(amount="0.005", buy_asset="USDC"), "10", 300, 1)
    assert error.value.code == "INVALID_GRANT"
    with pytest.raises(Denied) as error:
        control.prepare_trade(user, agents[1]["id"], TradeIntent(amount="0.005", buy_asset="USDC"), "noquote")
    assert error.value.code == "QUOTE_EXPIRED"
    legacy = control.create_agent(user, AgentSpec(name="Existing trader"))
    with pytest.raises(Denied) as error:
        control.prepare_trade(user, legacy["id"], TradeIntent(amount="0.005", buy_asset="USDC"),
                              "legacy", quote=quote_eth(Settings()))
    assert error.value.code == "TRADE_SIDE_DENIED"
    assert control.snapshot(user)["operations"] == []


def test_sell_quantities_reject_invalid_precision_and_unpayable_dust(trading):
    control, _user, _agents, claims = trading
    for amount in ["0", "-1", "NaN", "Infinity", "0.0000000000000000001",
                   "1.00000000000000000000000000001", "1" * 33]:
        with pytest.raises(Denied) as error:
            order(control, claims[1], amount, "USDC")
        assert error.value.code == "INVALID_AMOUNT"
    with pytest.raises(Denied) as error:
        order(control, claims[1], "0.000000000000000001", "USDC")
    assert error.value.code == "INVALID_AMOUNT"


def test_sell_rechecks_slippage_and_usdc_limits_at_execution(trading):
    control, user, agents, claims = trading
    confirm(control, user, claims[0], order(control, claims[0], "30", "ETH"))
    op = order(control, claims[1], "0.005", "USDC")
    control.approve(user, op["id"], op["id"], op["intent_hash"])
    before = control.snapshot(user)["wallet"]
    for price, code in [(2900, "SLIPPAGE_EXCEEDED"), (21000, "TRADE_LIMIT")]:
        with pytest.raises(Denied) as error:
            control.execute(user, op["id"], {**quote_eth(Settings()), "price": price})
        assert error.value.code == code
        assert control.snapshot(user)["wallet"] == before
    control.mutate(user, lambda state: state["agents"][agents[1]["id"]].update(daily_limit_units=1))
    with pytest.raises(Denied) as error:
        control.execute(user, op["id"], quote_eth(Settings()))
    assert error.value.code == "DAILY_LIMIT"
    assert control.snapshot(user)["wallet"] == before


def test_competing_sales_cannot_oversell_and_retries_retain_original_quote(trading):
    control, user, agents, claims = trading
    confirm(control, user, claims[0], order(control, claims[0], "30", "ETH"))
    first = order(control, claims[1], "0.006", "USDC")
    from onchain.security import digest
    args = {"amount": "0.006", "buy_asset": "USDC", "chain": "8453", "max_slippage_bps": 50}
    retry = control.prepare_trade(user, agents[1]["id"], TradeIntent(**args),
                                  claims[1]["run_id"] + digest(args), quote={**quote_eth(Settings()), "price": 3001})
    assert retry == first
    second = order(control, {**claims[1], "run_id": "second-sale"}, "0.006", "USDC")
    for op in [first, second]:
        control.approve(user, op["id"], op["id"], op["intent_hash"])
    def settle(op):
        try:
            return control.execute(user, op["id"], quote_eth(Settings()))["status"]
        except Denied as e:
            return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(settle, [first, second]))
    assert sorted(results) == ["CONFIRMED", "INSUFFICIENT_BALANCE"]
    assert control.snapshot(user)["wallet"] == {"USDC": "9988", "ETH": "0.004"}


def test_market_buy_and_sell_agents_publish_purchase_and_run_under_buyer_identity(trading):
    control, buyer, _agents, _claims = trading
    market = Marketplace(control)
    seller = Principal(sub="publisher", tenant="publisher")
    purchased = []
    for template, kind in [("eth-buy", "BUY"), ("eth-sell", "SELL")]:
        source = control.create_agent(seller, AgentSpec(name=template, template_id=template))
        listing = market.publish(seller, ListingSpec(agent_id=source["id"], title=template,
                                 publisher="Trading Publisher", description="Reviewed trade with buyer confirmation.", price="1"))
        assert listing["agent_type"] == kind and listing["strategy"] == "TASK"
        op = market.prepare(buyer, listing["id"], PurchaseSpec())
        result = control.approve(buyer, op["id"], op["id"], op["intent_hash"])
        agent = control.agent(buyer, result["agent_id"])
        assert agent["skill_id"] == source["skill_id"]
        session = control.create_conversation(buyer, agent["id"])
        with pytest.raises(Denied):
            control.conversation(seller, session["id"])
        with pytest.raises(Denied):
            control.agent(seller, agent["id"])
        purchased.append(control.capability(buyer, session["id"], template))
    assert not control.snapshot(buyer)["grants"]
    assert control.snapshot(seller)["wallet"]["USDC"] == "10002"
    confirm(control, buyer, purchased[0], order(control, purchased[0], "30", "ETH"))
    confirm(control, buyer, purchased[1], order(control, purchased[1], "0.005", "USDC"))
    assert control.snapshot(buyer)["wallet"] == {"USDC": "9983", "ETH": "0.005"}
    assert len(market.catalog(seller)["sales"]) == 2
    assert purchased[0]["runtime_sid"] != purchased[1]["runtime_sid"]
    assert not ({"agent_id", "wallet", "owner", "sub", "conversation_id"} & market.catalog(seller)["sales"][0].keys())


def test_publisher_cannot_expand_direction_when_selecting_template(trading):
    control, user, agents, _ = trading
    for source, template in [(agents[0], "eth-sell"), (agents[1], "eth-buy"), (agents[1], "eth-dca")]:
        with pytest.raises(Denied) as error:
            Marketplace(control).publish(user, ListingSpec(agent_id=source["id"], template_id=template,
                title="Wrong direction", publisher="Publisher", description="Cannot change reviewed trade direction.", price="1"))
        assert error.value.code == "MARKET_SKILL"
