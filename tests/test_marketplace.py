from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from onchain.domain import AgentSpec, Control, TradeIntent
from onchain.marketplace import CATALOG_KEY, ListingSpec, Marketplace, PurchaseSpec
from onchain.security import Denied, Principal
from onchain.store import MemoryStore, SQLiteStore


@pytest.fixture(params=["memory", "sqlite"])
def market(request, tmp_path):
    store = MemoryStore() if request.param == "memory" else SQLiteStore(str(tmp_path / "market.db"))
    control = Control(store)
    seller = Principal(sub="seller", tenant="seller")
    buyer = Principal(sub="buyer", tenant="buyer")
    stranger = Principal(sub="stranger", tenant="stranger")
    source = control.create_agent(seller, AgentSpec(name="Source"))
    control.ensure(buyer)
    marketplace = Marketplace(control)
    listing = marketplace.publish(seller, ListingSpec(
        agent_id=source["id"], title="ETH DCA", publisher="Test Publisher",
        description="Fixed reviewed ETH purchase schedule.", price="5",
    ))
    return control, marketplace, seller, buyer, stranger, source, listing


def prepare(market):
    return market[1].prepare(market[3], market[6]["id"], PurchaseSpec(
        amount="20", total="40", daily_limit="40", interval_seconds=300, hours=24,
    ))


def approve(market, op):
    return market[0].approve(market[3], op["id"], op["id"], op["intent_hash"])


def test_purchase_is_atomic_and_creates_buyer_owned_authorization(market):
    control, marketplace, seller, buyer, stranger, source, listing = market
    op = prepare(market)
    with pytest.raises(Denied, match="Re-authenticate"):
        control.approve(buyer, op["id"], "", op["intent_hash"])
    assert control.snapshot(buyer)["wallet"]["USDC"] == "10000"
    result = approve(market, op)
    assert result["status"] == "CONFIRMED"
    assert control.snapshot(buyer)["wallet"]["USDC"] == "9995"
    assert control.snapshot(seller)["wallet"]["USDC"] == "10005"
    agent = control.agent(buyer, result["agent_id"])
    assert agent["id"] != source["id"]
    assert agent["max_trade_units"] == 20_000_000
    buyer_session = control.create_conversation(buyer, agent["id"])
    seller_session = control.create_conversation(seller, source["id"])
    assert buyer_session["runtime_session_id"] != seller_session["runtime_session_id"]
    for other in (seller, stranger):
        with pytest.raises(Denied):
            control.conversation(other, buyer_session["id"])
        with pytest.raises(Denied):
            control.agent(other, agent["id"])
        with pytest.raises(Denied):
            marketplace.revoke(other, op["id"])
    sales = marketplace.catalog(seller)["sales"]
    assert len(sales) == 1
    assert not ({"owner", "sub", "agent_id", "grant_id", "conversation_id", "wallet"} & sales[0].keys())
    assert "source_agent_id" not in marketplace.catalog(buyer)["listings"][0]
    assert marketplace.catalog(stranger)["sales"] == []
    assert marketplace.catalog(stranger)["purchases"] == []


def test_concurrent_approval_charges_once(market):
    control, marketplace, seller, buyer, *_ = market
    op = prepare(market)
    def attempt(_):
        try:
            return approve(market, op)["status"]
        except Denied as e:
            return e.code
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count("CONFIRMED") == 1
    assert control.snapshot(buyer)["wallet"]["USDC"] == "9995"
    assert control.snapshot(seller)["wallet"]["USDC"] == "10005"
    assert len(control.snapshot(buyer)["agents"]) == 1
    assert marketplace.catalog(seller)["listings"][0]["sales_count"] == 1


def test_withdrawn_changed_terms_and_insufficient_balance_do_not_partially_settle(market):
    control, marketplace, seller, buyer, _, _, listing = market
    op = prepare(market)
    marketplace.status(seller, listing["id"], "WITHDRAWN")
    with pytest.raises(Denied, match="no longer available"):
        approve(market, op)
    marketplace.status(seller, listing["id"], "PUBLISHED")
    with pytest.raises(Denied, match="terms changed"):
        control.approve(buyer, op["id"], op["id"], "forged")
    control.mutate(buyer, lambda state: state["wallet"].update(USDC=1))
    with pytest.raises(Denied, match="paper balance"):
        approve(market, op)
    assert control.snapshot(seller)["wallet"]["USDC"] == "10000"
    assert control.snapshot(buyer)["agents"] == []
    assert control.snapshot(buyer)["grants"] == []
    assert control.operation(buyer, op["id"])["status"] == "PENDING"
    assert marketplace.catalog(seller)["sales"] == []


def test_automation_uses_gateway_scope_and_buyer_limits_then_revokes(market):
    control, marketplace, seller, buyer, _, _, listing = market
    purchase = approve(market, prepare(market))
    aid = purchase["agent_id"]
    session = control.create_conversation(buyer, aid, kind="scheduled")
    claims = control.capability(buyer, session["id"], "automatic-run")
    trade = control.prepare_trade(buyer, aid, TradeIntent(amount="20"), "slot-1", purchase["id"])
    control.authorize_tool(claims, "execute_trade", {"operation_id": trade["id"]})
    # Published snapshots do not give the seller control of existing buyer grants.
    marketplace.status(seller, listing["id"], "WITHDRAWN")
    from onchain.config import Settings
    from onchain.services import execute_tool
    execute_tool(control, Settings(), claims, "execute_trade", {"operation_id": trade["id"]})
    assert control.snapshot(buyer)["wallet"]["USDC"] == "9975"
    assert control.snapshot(buyer)["grants"][0]["used_units"] == 20_000_000
    trade2 = control.prepare_trade(buyer, aid, TradeIntent(amount="20"), "slot-2", purchase["id"])
    marketplace.revoke(buyer, purchase["id"])
    with pytest.raises(Denied):
        control.authorize_tool(claims, "execute_trade", {"operation_id": trade2["id"]})
    assert control.snapshot(seller)["wallet"]["USDC"] == "10005"


def test_seller_cannot_publish_others_agent_or_buy_own_listing(market):
    control, marketplace, seller, buyer, stranger, source, listing = market
    with pytest.raises(Denied):
        marketplace.publish(buyer, ListingSpec(agent_id=source["id"], title="stolen",
                            publisher="evil", description="Try to publish stolen source", price="1"))
    with pytest.raises(Denied):
        marketplace.status(stranger, listing["id"], "WITHDRAWN")
    with pytest.raises(Denied, match="another publisher"):
        marketplace.prepare(seller, listing["id"], PurchaseSpec(amount="20", total="20", daily_limit="20"))
    with pytest.raises(Denied, match="strategy limits"):
        marketplace.prepare(buyer, listing["id"], PurchaseSpec(amount="101", total="200", daily_limit="200"))


def test_transactions_rollback_all_records_on_exception(market):
    control, *_ = market
    store = control.store
    store.put("test-a", {"balance": 10})
    store.put("test-b", {"balance": 0})
    def fail(records):
        records["test-a"]["balance"] = 0
        records["test-b"]["balance"] = 10
        raise ValueError("do not commit")
    with pytest.raises(ValueError):
        store.transact(["test-a", "test-b"], fail)
    assert store.get("test-a")["balance"] == 10
    assert store.get("test-b")["balance"] == 0


def test_task_purchase_creates_isolated_task_agent_without_trading_grant(market):
    control, marketplace, seller, buyer, stranger, *_ = market
    source = control.create_agent(seller, AgentSpec(name="Research", template_id="token-research"))
    listing = marketplace.publish(seller, ListingSpec(
        agent_id=source["id"], title="Research task", publisher="Task Seller",
        description="Reviewed research task with private buyer sessions.", price="1",
    ))
    assert listing["strategy"] == "TASK"
    assert listing["template_id"] == "token-research"
    with pytest.raises(Denied) as denied:
        marketplace.prepare(buyer, listing["id"], PurchaseSpec(amount="20"))
    assert denied.value.code == "INVALID_PURCHASE"
    op = marketplace.prepare(buyer, listing["id"], PurchaseSpec())
    assert "amount_units" not in op["intent"]
    result = approve(market, op)
    state = control.snapshot(buyer)
    agent = control.agent(buyer, result["agent_id"])
    assert agent["template_id"] == source["template_id"]
    assert agent["skill_id"] == "token-researcher"
    assert state["wallet"]["USDC"] == "9999"
    assert not state["grants"]
    assert set(listing["products"]).issubset(state["subscriptions"])
    assert marketplace.catalog(buyer)["purchases"][0]["grant_id"] is None
    session = control.create_conversation(buyer, agent["id"])
    claims = control.capability(buyer, session["id"], "task")
    control.authorize_tool(claims, "get_token_info", {
        "chain": "8453", "token_address": "0x4200000000000000000000000000000000000006",
    })
    with pytest.raises(Denied):
        control.authorize_tool(claims, "execute_trade", {"operation_id": "anything"})
    with pytest.raises(Denied):
        control.delete_agent(seller, agent["id"])
    marketplace.revoke(buyer, op["id"])
    with pytest.raises(Denied):
        control.validate_capability(claims)
    control.delete_agent(buyer, agent["id"])
    marketplace.revoke(buyer, op["id"])  # Stopping an old purchase cannot resurrect a deleted Agent.
    assert control.agent(buyer, agent["id"], active=False)["status"] == "DELETED"
    assert marketplace.catalog(buyer)["purchases"][0]["status"] == "DELETED"
    assert not marketplace.catalog(stranger)["purchases"]


def test_market_rejects_unsupported_task_and_changed_template_before_payment(market):
    control, marketplace, seller, buyer, _, source, _ = market
    with pytest.raises(Denied) as denied:
        marketplace.publish(seller, ListingSpec(
            agent_id=source["id"], template_id="token-research", title="Unsupported",
            publisher="Seller", description="Cannot publish tools the source does not have.", price="1",
        ))
    assert denied.value.code == "MARKET_SKILL"
    listing = marketplace.publish(seller, ListingSpec(
        agent_id=source["id"], template_id="eth-order", title="Order task",
        publisher="Seller", description="One reviewed paper order, no standing authorization.", price="1",
    ))
    op = marketplace.prepare(buyer, listing["id"], PurchaseSpec())
    from onchain.security import digest
    def tamper(state):
        state["operations"][op["id"]]["intent"]["template_digest"] = "0"*64
        state["operations"][op["id"]]["intent_hash"] = digest(state["operations"][op["id"]]["intent"])
    control.mutate(buyer, tamper)
    changed = control.operation(buyer, op["id"])
    with pytest.raises(Denied) as denied:
        approve(market, changed)
    assert denied.value.code == "TEMPLATE_CHANGED"
    assert control.snapshot(buyer)["wallet"]["USDC"] == "10000"
    assert not control.snapshot(buyer)["agents"]


def test_api_purchase_requires_csrf_and_operation_specific_stepup(tmp_path, monkeypatch):
    from onchain.api import app
    from onchain.config import Settings
    from onchain.security import TokenService
    config = Settings(local_key_path=str(tmp_path / "key.pem"))
    store = MemoryStore()
    control = Control(store)
    monkeypatch.setattr("onchain.api.dependencies", lambda: (config, store, control, TokenService(config)))
    with TestClient(app) as seller, TestClient(app) as buyer:
        sl = seller.post("/api/auth/login", json={"email": "alice@example.test", "password": "local-only-change-me"}).json()
        bl = buyer.post("/api/auth/login", json={"email": "bob@example.test", "password": "local-only-change-me"}).json()
        seller.headers["X-CSRF-Token"] = sl["csrf"]
        buyer.headers["X-CSRF-Token"] = bl["csrf"]
        agent = seller.post("/api/agents", json={"name": "Seller"}).json()
        listing = seller.post("/api/marketplace/listings", json={
            "agent_id": agent["id"], "title": "Strategy", "publisher": "Seller",
            "description": "Reviewed ETH scheduled purchases", "price": "5",
        }).json()
        op = buyer.post(f"/api/marketplace/listings/{listing['id']}/purchase",
                        json={"amount": "20", "total": "40", "daily_limit": "40"}).json()
        assert buyer.post(f"/api/operations/{op['id']}/approve", json={"intent_hash": op["intent_hash"]}).status_code == 401
        assert seller.get(f"/api/operations/{op['id']}").status_code == 404
        buyer.post(f"/api/operations/{op['id']}/stepup",
                   json={"email": "bob@example.test", "password": "local-only-change-me"}).raise_for_status()
        result = buyer.post(f"/api/operations/{op['id']}/approve", json={"intent_hash": op["intent_hash"]})
        assert result.status_code == 200, result.text
        assert buyer.get("/api/dashboard").json()["wallet"]["USDC"] == "9995"
        buyer.headers.pop("X-CSRF-Token")
        assert buyer.post(f"/api/marketplace/purchases/{op['id']}/revoke").status_code == 403
        assert store.get(CATALOG_KEY)["listings"][listing["id"]]["sales_count"] == 1
