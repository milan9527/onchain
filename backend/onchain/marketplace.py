"""Published strategy templates, bilateral paper purchases and buyer-owned automation."""
import copy

from pydantic import BaseModel, ConfigDict, Field

from .catalog import skill_by_id
from .domain import amount_units, check_agent_capacity, event, now
from .security import Denied, digest, uid
from .tasks import task_by_id

CATALOG_KEY = "MARKETPLACE#CATALOG"


class ListingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    template_id: str | None = Field(default=None, max_length=80)
    title: str = Field(min_length=2, max_length=60)
    publisher: str = Field(min_length=2, max_length=40)
    description: str = Field(min_length=10, max_length=600)
    price: str


class PurchaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: str | None = None
    total: str | None = None
    daily_limit: str | None = None
    interval_seconds: int = Field(default=3600, ge=300, le=86400)
    hours: int = Field(default=24, ge=1, le=720)


def public_listing(listing, owner):
    return {k: copy.deepcopy(v) for k, v in listing.items() if k not in {"owner", "source_agent_id"}} | {
        "is_mine": listing["owner"] == owner,
    }


class Marketplace:
    def __init__(self, control):
        self.control, self.store = control, control.store

    def catalog(self, p):
        registry = self.store.get(CATALOG_KEY) or {"listings": {}}
        state = self.control.ensure(p)
        return {
            "listings": [public_listing(v, p.owner) for v in reversed(list(registry["listings"].values()))
                         if v["status"] == "PUBLISHED" or v["owner"] == p.owner],
            "purchases": list(reversed(list(state.get("market_purchases", {}).values()))),
            "sales": list(reversed(list(state.get("market_sales", {}).values()))),
            "revenue_units": state.get("market_revenue_units", 0),
            "mode": "paper",
        }

    def publish(self, p, spec: ListingSpec):
        source = self.control.agent(p, spec.agent_id)
        template_id = spec.template_id or source.get("template_id")
        if not template_id and source["skill_id"] == "controlled-trader":
            template_id = "eth-dca"  # Preserve published trading workflows.
        if not template_id:
            raise Denied("MARKET_TEMPLATE", "Choose a reviewed task template to publish.", 422)
        task = task_by_id(template_id)
        skill = skill_by_id(source["skill_id"])
        if (not skill or skill["digest"] != source["skill_digest"]
                or not set(task["tools"]).issubset(skill["tools"])):
            raise Denied("MARKET_SKILL", "Source Agent does not support this reviewed task.", 422)
        price = amount_units(spec.price)
        if price > 1_000_000_000:
            raise Denied("MARKET_PRICE", "Service price must not exceed 1,000 paper USDC.", 422)
        listing = {
            "id": uid("listing_"), "owner": p.owner, "source_agent_id": source["id"],
            "title": spec.title, "publisher": spec.publisher, "description": spec.description,
            "price_units": price, "skill_id": source["skill_id"], "skill_digest": source["skill_digest"],
            "strategy": "ETH_DCA" if task["id"] == "eth-dca" else "TASK",
            "template_id": task["id"], "template_digest": task["digest"],
            "task_name": task["name"], "task_mode": task["mode"],
            "tools": skill["tools"], "products": task["products"],
            "chain": "8453", "buy_asset": "ETH", "max_slippage_bps": 50,
            "max_trade_units": source["max_trade_units"], "daily_limit_units": source["daily_limit_units"],
            "version": 1, "created_at": now(), "mode": "paper",
        }
        listing["terms_hash"] = digest(listing)
        listing.update(status="PUBLISHED", sales_count=0)
        def apply(registry):
            registry = registry or {"listings": {}}
            if len(registry["listings"]) >= 200 or sum(
                item["owner"] == p.owner for item in registry["listings"].values()
            ) >= 20:
                raise Denied("MARKET_QUOTA", "Listing quota reached.", 409)
            registry["listings"][listing["id"]] = listing
            return registry, public_listing(listing, p.owner)
        return self.store.mutate(CATALOG_KEY, apply)

    def status(self, p, listing_id, status):
        if status not in {"PUBLISHED", "WITHDRAWN"}:
            raise Denied("INVALID_STATUS", "Unknown listing status.", 422)
        def apply(registry):
            listing = (registry or {}).get("listings", {}).get(listing_id)
            if not listing or listing["owner"] != p.owner:
                raise Denied("NOT_FOUND", "Listing is not available.", 404)
            listing["status"] = status
            return registry, public_listing(listing, p.owner)
        return self.store.mutate(CATALOG_KEY, apply)

    def prepare(self, p, listing_id, spec: PurchaseSpec):
        listing = (self.store.get(CATALOG_KEY) or {}).get("listings", {}).get(listing_id)
        if not listing or listing["status"] != "PUBLISHED":
            raise Denied("NOT_FOUND", "Listing is not available.", 404)
        if listing["owner"] == p.owner:
            raise Denied("SELF_PURCHASE", "Choose another publisher's Agent.", 422)
        strategy = listing.get("strategy", "ETH_DCA")
        intent = {
            "listing_id": listing_id, "listing_terms_hash": listing["terms_hash"],
            "title": listing["title"], "publisher": listing["publisher"],
            "price_units": listing["price_units"], "seller_owner": listing["owner"],
            "skill_id": listing["skill_id"], "skill_digest": listing["skill_digest"],
            "strategy": strategy, "mode": "paper",
        }
        if listing.get("template_id"):
            intent.update({key: copy.deepcopy(listing[key]) for key in (
                "template_id", "template_digest", "task_name", "task_mode", "tools", "products",
            )})
        if strategy == "TASK":
            if spec.model_fields_set:
                raise Denied("INVALID_PURCHASE", "Task purchases do not authorize a trading schedule.", 422)
            intent.update(max_trade_units=listing["max_trade_units"],
                          daily_limit_units=listing["daily_limit_units"])
        else:
            if any(value is None for value in (spec.amount, spec.total, spec.daily_limit)):
                raise Denied("INVALID_PURCHASE", "Review an explicit trading budget before purchasing.", 422)
            amount, total, daily = map(amount_units, (spec.amount, spec.total, spec.daily_limit))
            if (amount > listing["max_trade_units"] or daily > listing["daily_limit_units"]
                    or amount > daily or amount > total):
                raise Denied("MARKET_LIMIT", "Trading budget exceeds the published strategy limits.", 422)
            intent.update(
                amount_units=amount, total_units=total, daily_limit_units=daily,
                interval_seconds=spec.interval_seconds, expires_at=now()+spec.hours*3600,
                buy_asset=listing["buy_asset"], chain=listing["chain"],
                max_slippage_bps=listing["max_slippage_bps"],
            )
        operation = {
            "id": uid("purchase_"), "owner": p.owner, "agent_id": "", "kind": "MARKET_PURCHASE",
            "intent": intent, "intent_hash": digest(intent), "status": "PENDING",
            "created_at": now(), "expires_at": now()+900,
        }
        def apply(state):
            if len(state["operations"]) >= 200:
                raise Denied("HISTORY_LIMIT", "Archive old operations first.", 409)
            check_agent_capacity(state)
            state["operations"][operation["id"]] = operation
            event(state, "MARKET_PURCHASE_PREPARED", listing["title"], operation_id=operation["id"])
            return operation
        return self.control.mutate(p, apply)

    def approve(self, p, op_id, expected_hash):
        original = self.control.operation(p, op_id)
        buyer_key = self.control.key(p)
        seller_key = "USER#" + original["intent"]["seller_owner"]
        agent_id = uid("agt_")

        def commit(records):
            buyer, seller, registry = records[buyer_key], records[seller_key], records[CATALOG_KEY]
            op = buyer["operations"].get(op_id)
            if not op or op["owner"] != p.owner or op["kind"] != "MARKET_PURCHASE":
                raise Denied("NOT_FOUND", "Purchase is not available.", 404)
            if op["status"] != "PENDING" or op["expires_at"] <= now():
                raise Denied("APPROVAL_EXPIRED", "Purchase is no longer awaiting approval.", 409)
            intent = op["intent"]
            if op["intent_hash"] != expected_hash or digest(intent) != expected_hash:
                raise Denied("INTENT_CHANGED", "Purchase terms changed.", 409)
            listing = (registry or {}).get("listings", {}).get(intent["listing_id"])
            if (not listing or listing["status"] != "PUBLISHED"
                    or listing["terms_hash"] != intent["listing_terms_hash"]
                    or listing["owner"] != intent["seller_owner"] or listing["owner"] == p.owner):
                raise Denied("LISTING_CHANGED", "This offer is no longer available. Review a new offer.", 409)
            skill = skill_by_id(intent["skill_id"])
            if not skill or skill["digest"] != intent["skill_digest"]:
                raise Denied("SKILL_CHANGED", "This strategy requires a new review.")
            if intent.get("template_id"):
                task = task_by_id(intent["template_id"])
                if task["digest"] != intent["template_digest"] or not set(task["tools"]).issubset(skill["tools"]):
                    raise Denied("TEMPLATE_CHANGED", "This task requires a new review.")
            if not seller or seller["owner"] != intent["seller_owner"]:
                raise Denied("NOT_FOUND", "Publisher is not available.", 404)
            check_agent_capacity(buyer)
            if len(seller.get("market_sales", {})) >= 200:
                raise Denied("MARKET_QUOTA", "Agent or market order quota reached.", 409)
            is_task = intent.get("strategy") == "TASK"
            if buyer["wallet"]["USDC"] < intent["price_units"] + (0 if is_task else intent["amount_units"]):
                raise Denied("INSUFFICIENT_BALANCE", "Service fee and the first trade exceed your paper balance.")
            agent = {
                "id": agent_id, "name": intent["title"], "skill_id": intent["skill_id"],
                "skill_digest": intent["skill_digest"],
                "max_trade_units": intent["max_trade_units"] if is_task else intent["amount_units"],
                "daily_limit_units": intent["daily_limit_units"], "status": "ACTIVE", "epoch": 1,
                "created_at": now(), "market_purchase_id": op_id, "listing_id": listing["id"],
            }
            if intent.get("template_id"):
                agent.update(template_id=intent["template_id"], template_digest=intent["template_digest"])
            # Product activation is included in the immutable terms shown at purchase approval.
            buyer["subscriptions"] = sorted(set(buyer["subscriptions"]) | set(intent.get("products", [])))
            buyer["agents"][agent_id] = agent
            if not is_task:
                grant = {k: intent[k] for k in (
                    "amount_units", "total_units", "buy_asset", "chain", "max_slippage_bps",
                    "interval_seconds", "expires_at",
                )}
                grant.update(id=op_id, agent_id=agent_id, status="ACTIVE", used_units=0,
                             next_run_at=now(), market_purchase_id=op_id)
                buyer["grants"][op_id] = grant
            buyer["wallet"]["USDC"] -= intent["price_units"]
            seller["wallet"]["USDC"] += intent["price_units"]
            seller["market_revenue_units"] = seller.get("market_revenue_units", 0) + intent["price_units"]
            order = {
                "id": op_id, "listing_id": listing["id"], "title": intent["title"],
                "publisher": intent["publisher"], "price_units": intent["price_units"],
                "created_at": now(), "status": "ACTIVE", "expires_at": intent.get("expires_at"),
                "strategy": intent.get("strategy", "ETH_DCA"), "template_id": intent.get("template_id"),
                "mode": "paper",
            }
            buyer.setdefault("market_purchases", {})[op_id] = {
                **order, "agent_id": agent_id, "grant_id": None if is_task else op_id,
            }
            # A seller sees only the commercial order, never buyer IDs, grants, wallets or conversations.
            seller.setdefault("market_sales", {})[op_id] = {**order, "status": "PAID"}
            listing["sales_count"] += 1
            op.update(status="CONFIRMED", agent_id=agent_id, approved_at=now(), approved_by=p.sub,
                      execution={"mode": "paper", "receipt": "paper_"+op_id})
            event(buyer, "MARKET_PURCHASED", "Task Agent purchased." if is_task else
                  "Service purchased; isolated Agent and bounded automation activated.",
                  operation_id=op_id, agent_id=agent_id)
            event(seller, "MARKET_SALE", "Paper service fee received.", operation_id=op_id)
            return op
        return self.store.transact([buyer_key, seller_key, CATALOG_KEY], commit)

    def revoke(self, p, purchase_id):
        def apply(state):
            purchase = state.get("market_purchases", {}).get(purchase_id)
            if not purchase:
                raise Denied("NOT_FOUND", "Purchase is not available.", 404)
            if purchase["status"] == "DELETED":
                return purchase
            purchase["status"] = "REVOKED"
            if purchase.get("grant_id"):
                state["grants"][purchase["grant_id"]]["status"] = "REVOKED"
            agent = state["agents"][purchase["agent_id"]]
            agent["status"] = "PAUSED"
            agent["epoch"] += 1
            for op in state["operations"].values():
                if op["agent_id"] == agent["id"] and op["status"] in {"PENDING", "APPROVED"}:
                    op["status"] = "CANCELLED"
            event(state, "MARKET_AUTOMATION_REVOKED", "Purchased automation stopped; service fee is unchanged.",
                  operation_id=purchase_id, agent_id=agent["id"])
            return purchase
        return self.control.mutate(p, apply)
