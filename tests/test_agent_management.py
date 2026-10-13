import pytest
from fastapi.testclient import TestClient

from onchain.domain import AgentSpec, Control, TradeIntent
from onchain.security import Denied, Principal
from onchain.store import MemoryStore, SQLiteStore


@pytest.fixture(params=["memory", "sqlite"])
def owned(request, tmp_path):
    store = MemoryStore() if request.param == "memory" else SQLiteStore(str(tmp_path / "agents.db"))
    control = Control(store)
    owner = Principal(sub="owner", tenant="owner")
    stranger = Principal(sub="other", tenant="other")
    agent = control.create_agent(owner, AgentSpec(name="delete me", template_id="eth-order"))
    return control, owner, stranger, agent


def test_delete_revokes_capabilities_orders_grants_but_keeps_owned_history(owned):
    control, owner, stranger, agent = owned
    aid = agent["id"]
    session = control.create_conversation(owner, aid)
    claims = control.capability(owner, session["id"], "run")
    grant = control.prepare_grant(owner, aid, TradeIntent(amount="20"), "100", 300, 24)
    control.approve(owner, grant["id"], grant["id"], grant["intent_hash"])
    pending = control.prepare_trade(owner, aid, TradeIntent(amount="20"), "pending")
    approved = control.prepare_trade(owner, aid, TradeIntent(amount="20"), "approved")
    control.approve(owner, approved["id"], approved["id"], approved["intent_hash"])
    before = control.snapshot(owner)["wallet"]
    with pytest.raises(Denied) as denied:
        control.delete_agent(stranger, aid)
    assert denied.value.code == "NOT_FOUND"
    control.delete_agent(owner, aid)
    control.delete_agent(owner, aid)  # Idempotent, including epoch.
    state = control.snapshot(owner)
    assert not state["agents"]
    assert state["wallet"] == before
    assert state["grants"][0]["status"] == "REVOKED"
    assert all(control.operation(owner, op["id"])["status"] == "CANCELLED" for op in (pending, approved))
    assert control.public_conversation(owner, session["id"], None)["can_resume"] is False
    assert control.list_conversations(owner, aid)[0]["id"] == session["id"]
    for action in (
        lambda: control.set_agent_status(owner, aid, "ACTIVE"),
        lambda: control.create_conversation(owner, aid),
        lambda: control.authorize_tool(claims, "get_portfolio", {}),
        lambda: control.prepare_trade(owner, aid, TradeIntent(amount="20"), "after-delete"),
        lambda: control.execute(owner, approved["id"], {"price": "3000", "expires_at": 9999999999}),
        lambda: control.public_conversation(stranger, session["id"], None),
    ):
        with pytest.raises(Denied):
            action()
    for _ in range(3):
        control.report_denial(owner, "INVALID_TOOL_ARGUMENTS", aid)
    assert control.agent(owner, aid, active=False)["status"] == "DELETED"
    assert control.snapshot(owner)["wallet"] == before


def test_deleted_agent_frees_live_quota_without_reusing_identity(owned):
    control, owner, _, agent = owned
    for i in range(9):
        control.create_agent(owner, AgentSpec(name=f"Agent {i}"))
    with pytest.raises(Denied) as denied:
        control.create_agent(owner, AgentSpec(name="over quota"))
    assert denied.value.code == "QUOTA"
    control.delete_agent(owner, agent["id"])
    replacement = control.create_agent(owner, AgentSpec(name="replacement"))
    assert replacement["id"] != agent["id"]
    assert len(control.snapshot(owner)["agents"]) == 10


def test_delete_endpoint_requires_csrf_and_owner(tmp_path, monkeypatch):
    from onchain.api import app
    from onchain.config import Settings
    from onchain.security import TokenService
    config = Settings(local_key_path=str(tmp_path / "key.pem"))
    store = MemoryStore()
    control = Control(store)
    monkeypatch.setattr("onchain.api.dependencies", lambda: (config, store, control, TokenService(config)))
    with TestClient(app) as alice, TestClient(app) as bob:
        def login(client, email):
            return client.post("/api/auth/login", json={
                "email": email, "password": "local-only-change-me",
            }).json()["csrf"]
        alice_csrf = login(alice, "alice@example.test")
        bob.headers["X-CSRF-Token"] = login(bob, "bob@example.test")
        response = alice.post("/api/agents", headers={"X-CSRF-Token": alice_csrf}, json={"name": "Owned"})
        aid = response.json()["id"]
        assert alice.delete("/api/agents/"+aid).status_code == 403
        assert bob.delete("/api/agents/"+aid).status_code == 404
        assert alice.delete("/api/agents/"+aid, headers={"X-CSRF-Token": alice_csrf}).status_code == 200
        assert alice.get("/api/dashboard").json()["agents"] == []
