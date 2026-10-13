import copy
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .catalog import PRODUCTS, SKILLS, TOOL_PRODUCTS, skill_by_id
from .security import Denied, Principal, digest, uid
from .history import History, RETENTION


class TradeIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: str
    buy_asset: Literal["ETH"] = "ETH"
    chain: Literal["8453"] = "8453"
    max_slippage_bps: int = Field(default=50, ge=0, le=100)


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    skill_id: str = Field(default="controlled-trader", max_length=80)
    template_id: str | None = Field(default=None, max_length=80)
    enable_template_products: bool = False
    max_trade: str = "100"
    daily_limit: str = "500"


def amount_units(amount: str) -> int:
    try:
        if len(str(amount)) > 32:
            raise ValueError
        value = Decimal(str(amount))
        if not value.is_finite() or value <= 0 or value > 100_000:
            raise ValueError
        units = value * 1_000_000
        if units != units.to_integral_value():
            raise ValueError
        return int(units)
    except (InvalidOperation, ValueError) as e:
        raise Denied("INVALID_AMOUNT", "Amount must be positive USDC with at most 6 decimals.", 422) from e


def display_units(units: int) -> str:
    return format(Decimal(units) / 1_000_000, "f")


def quote_price(quote):
    try:
        price = Decimal(str(quote["price"]))
        if not price.is_finite() or price <= 0 or quote["expires_at"] <= now():
            raise ValueError
        return price
    except (KeyError, InvalidOperation, TypeError, ValueError) as e:
        raise Denied("QUOTE_EXPIRED", "A fresh, positive quote is required.", 409) from e


def now() -> int:
    return int(time.time())


def default_state(p: Principal):
    return {
        "owner": p.owner, "tenant": p.tenant, "sub": p.sub,
        "agents": {}, "subscriptions": ["market", "portfolio", "trade"],
        "operations": {}, "grants": {}, "wallet": {"USDC": 10_000_000_000, "ETH": "0"},
        "daily_spend": {}, "events": [], "receipts": {},
    }


def check_agent_capacity(state):
    if sum(a["status"] != "DELETED" for a in state["agents"].values()) >= 10:
        raise Denied("QUOTA", "At most 10 active or paused agents per user.", 409)
    if len(state["agents"]) >= 200:
        raise Denied("HISTORY_LIMIT", "Agent audit archive limit reached.", 409)


def event(state, kind, detail, *, severity="info", agent_id="", operation_id=""):
    state["events"].append({
        "id": uid("evt_"), "at": now(), "kind": kind, "detail": detail,
        "severity": severity, "agent_id": agent_id, "operation_id": operation_id,
    })
    state["events"] = state["events"][-150:]


class Control(History):
    def __init__(self, store):
        self.store = store

    def key(self, p):
        return "USER#" + p.owner

    def ensure(self, p):
        state = self.store.get(self.key(p))
        if state is not None:
            return state
        self.store.create(self.key(p), default_state(p))
        return self.store.get(self.key(p))

    def mutate(self, p, fn):
        def apply(state):
            state = state or default_state(p)
            if state["owner"] != p.owner:
                raise Denied("OWNER_MISMATCH", "Resource is not available.")
            return state, fn(state)
        return self.store.mutate(self.key(p), apply)

    def snapshot(self, p):
        state = self.ensure(p)
        result = copy.deepcopy(state)
        result.pop("receipts")
        result["operations"] = list(reversed(list(result["operations"].values())))
        result["agents"] = [a for a in result["agents"].values() if a["status"] != "DELETED"]
        result["grants"] = list(result["grants"].values())
        result["events"] = list(reversed(result["events"]))
        result["wallet"]["USDC"] = display_units(result["wallet"]["USDC"])
        result["model_id"] = "us.openai.gpt-6-sol"
        result["mode"] = "paper"
        return result

    def agent(self, p, agent_id, *, active=True):
        agent = self.ensure(p)["agents"].get(agent_id)
        if not agent:
            raise Denied("NOT_FOUND", "Agent is not available.", 404)
        if active and agent["status"] == "DELETED":
            raise Denied("AGENT_DELETED", "Agent has been deleted.", 410)
        if active and agent["status"] != "ACTIVE":
            raise Denied("AGENT_PAUSED", "Agent is paused.")
        return agent

    def create_agent(self, p, spec: AgentSpec):
        max_trade, daily = amount_units(spec.max_trade), amount_units(spec.daily_limit)
        if max_trade > 1_000_000_000 or daily > 5_000_000_000 or max_trade > daily:
            raise Denied("LIMIT_EXCEEDED", "Single trade ≤ 1,000 USDC; daily limit ≤ 5,000 USDC.", 422)
        template = None
        if spec.template_id:
            from .tasks import task_by_id
            template = task_by_id(spec.template_id)
        skill_id = template["skill_id"] if template else spec.skill_id
        skill = skill_by_id(skill_id)
        if not skill:
            raise Denied("UNKNOWN_SKILL", "Choose a reviewed platform skill.", 422)
        agent = {
            "id": uid("agt_"), "name": spec.name, "skill_id": skill_id,
            "skill_digest": skill["digest"], "max_trade_units": max_trade,
            "daily_limit_units": daily, "status": "ACTIVE", "epoch": 1, "created_at": now(),
        }
        if template:
            agent["template_id"] = template["id"]
        def apply(state):
            check_agent_capacity(state)
            state["agents"][agent["id"]] = agent
            if template and spec.enable_template_products:
                state["subscriptions"] = sorted(set(state["subscriptions"]) | set(template["products"]))
            event(state, "AGENT_CREATED", spec.name, agent_id=agent["id"])
            return agent
        return self.mutate(p, apply)

    def set_agent_status(self, p, agent_id, status):
        if status not in {"ACTIVE", "PAUSED"}:
            raise Denied("INVALID_STATUS", "Unknown agent status.", 422)
        def apply(state):
            agent = state["agents"].get(agent_id)
            if not agent or agent["status"] == "DELETED":
                raise Denied("NOT_FOUND", "Agent is not available.", 404)
            agent["status"] = status
            agent["epoch"] += 1
            if status == "PAUSED":
                for op in state["operations"].values():
                    if op["agent_id"] == agent_id and op["status"] in {"PENDING", "APPROVED"}:
                        op["status"] = "CANCELLED"
            event(state, "AGENT_"+status, "Agent credentials invalidated.", agent_id=agent_id)
            return agent
        return self.mutate(p, apply)

    def delete_agent(self, p, agent_id):
        def apply(state):
            agent = state["agents"].get(agent_id)
            if not agent:
                raise Denied("NOT_FOUND", "Agent is not available.", 404)
            if agent["status"] == "DELETED":
                return {"id": agent_id, "status": "DELETED"}
            agent.update(status="DELETED", deleted_at=now(), epoch=agent["epoch"] + 1)
            for op in state["operations"].values():
                if op["agent_id"] == agent_id and op["status"] in {"PENDING", "APPROVED"}:
                    op["status"] = "CANCELLED"
            for grant in state["grants"].values():
                if grant["agent_id"] == agent_id:
                    grant["status"] = "REVOKED"
            for purchase in state.get("market_purchases", {}).values():
                if purchase["agent_id"] == agent_id:
                    purchase["status"] = "DELETED"
            event(state, "AGENT_DELETED", "Agent deleted; credentials, pending operations and grants revoked.",
                  agent_id=agent_id)
            return {"id": agent_id, "status": "DELETED"}
        return self.mutate(p, apply)

    def create_conversation(self, p, agent_id, auth_key=None, kind="chat"):
        self.agent(p, agent_id)
        sid = uid("conv_")
        session = {
            "id": sid, "owner": p.owner, "agent_id": agent_id, "runtime_session_id": uid("rt_"),
            "messages": [], "active": True, "epoch": 1, "created_at": now(), "busy_until": 0,
            "active_until": now()+86400, "expires_at": now()+RETENTION, "auth_key": auth_key,
            "title": "新会话" if kind == "chat" else "定投自动执行" if kind == "scheduled" else "交易执行",
            "kind": kind, "updated_at": time.time(), "runs": [],
        }
        self.store.put("SESSION#"+sid, session)
        self.index_session(session)
        return session

    def conversation(self, p, sid):
        session = self.store.get("SESSION#"+sid)
        if not session or session.get("owner") != p.owner:
            raise Denied("SESSION_NOT_FOUND", "Conversation is not available.", 404)
        if not session["active"] or session.get("active_until", session["expires_at"]) <= now():
            raise Denied("SESSION_REVOKED", "Conversation has ended.", 401)
        if session.get("auth_key"):
            login = self.store.get(session["auth_key"])
            if not login or not login.get("active") or login["expires_at"] <= now():
                raise Denied("SESSION_REVOKED", "The login session has ended.", 401)
        if session.get("connection_key"):
            connection = self.store.get(session["connection_key"])
            if (not connection or not connection.get("active")
                    or connection.get("id") != session["connection_id"]
                    or connection["expires_at"] <= now()):
                raise Denied("MCP_CONNECTION_REVOKED", "The MCP connection has been revoked.", 401)
        return session

    def append_message(self, p, sid, role, content):
        self.conversation(p, sid)
        def apply(session):
            if not session or session.get("owner") != p.owner or not session["active"]:
                raise Denied("SESSION_NOT_FOUND", "Conversation is not available.", 404)
            session["messages"].append({"role": role, "content": content, "at": now()})
            session["messages"] = session["messages"][-30:]
            session["updated_at"] = time.time()
            if role == "user" and session.get("title", "新会话") == "新会话":
                session["title"] = str(content).replace("\n", " ")[:60]
            return session, session
        self.index_session(self.store.mutate("SESSION#"+sid, apply))

    def close_conversation(self, p, sid):
        self.conversation(p, sid)
        def apply(session):
            if session["owner"] != p.owner:
                raise Denied("SESSION_NOT_FOUND", "Conversation is not available.", 404)
            session["active"] = False
            session["epoch"] += 1
            session["updated_at"] = time.time()
            return session, session
        self.index_session(self.store.mutate("SESSION#"+sid, apply))

    def lease_conversation(self, p, sid, request_id):
        self.conversation(p, sid)
        def apply(session):
            if session["owner"] != p.owner or not session["active"]:
                raise Denied("SESSION_NOT_FOUND", "Conversation is not available.", 404)
            if session["busy_until"] > now():
                raise Denied("SESSION_BUSY", "A request is already running in this conversation.", 409)
            session.update(busy_until=now()+180, lease=request_id)
            return session, None
        self.store.mutate("SESSION#"+sid, apply)

    def release_conversation(self, sid, request_id):
        def apply(session):
            if session and session.get("lease") == request_id:
                session["busy_until"] = 0
            return session, None
        self.store.mutate("SESSION#"+sid, apply)

    def capability(self, p, sid, run_id):
        session = self.conversation(p, sid)
        agent = self.agent(p, session["agent_id"])
        claims = {
            "sub": p.sub, "tenant": p.tenant, "owner": p.owner,
            "sid": sid, "runtime_sid": session["runtime_session_id"], "session_epoch": session["epoch"],
            "agent_id": agent["id"], "agent_epoch": agent["epoch"], "run_id": run_id,
            "skill_digest": agent["skill_digest"], "scope": "tools:invoke",
        }
        if session.get("auth_key"):
            login = self.store.get(session["auth_key"])
            if login and login.get("oauth"):
                claims.update(oauth_scopes=login["oauth"].get("scope", "").split(),
                              oauth_expires=login["oauth"]["exp"])
        return claims

    def validate_capability(self, claims):
        if claims.get("oauth_expires", now()+1) <= now():
            raise Denied("OAUTH_EXPIRED", "Cognito access token expired.", 401)
        p = Principal(sub=claims["sub"], tenant=claims["tenant"])
        if p.owner != claims.get("owner"):
            raise Denied("OWNER_MISMATCH", "Invalid capability owner.")
        session = self.conversation(p, claims["sid"])
        agent = self.agent(p, claims["agent_id"])
        if (
            session["agent_id"] != agent["id"]
            or session["runtime_session_id"] != claims["runtime_sid"]
            or session["epoch"] != claims["session_epoch"]
            or agent["epoch"] != claims["agent_epoch"]
        ):
            raise Denied("CAPABILITY_REVOKED", "Session or agent authorization changed.")
        skill = skill_by_id(agent["skill_id"])
        if claims.get("skill_digest") != skill["digest"] or agent["skill_digest"] != skill["digest"]:
            raise Denied("SKILL_CHANGED", "Skill version must be re-authorized.")
        return p, agent

    def authorize_tool(self, claims, tool, args, *, count=True):
        p, agent = self.validate_capability(claims)
        if "oauth_scopes" in claims:
            from .oauth import TOOL_SCOPES
            if TOOL_SCOPES.get(tool) not in claims.get("oauth_scopes", []):
                raise Denied("INSUFFICIENT_SCOPE", "OAuth permission does not allow this tool.")
        state = self.ensure(p)
        if tool not in TOOL_PRODUCTS:
            raise Denied("TOOL_DENIED", "Tool is not in the approved catalog.")
        if TOOL_PRODUCTS[tool] not in state["subscriptions"]:
            raise Denied("NOT_SUBSCRIBED", "Subscribe to this MCP product before using it.")
        if tool not in skill_by_id(agent["skill_id"])["tools"]:
            raise Denied("SKILL_TOOL_DENIED", "This skill has no permission to use the tool.")
        from jsonschema import validate, ValidationError
        from .catalog import tool_schema
        try:
            validate(args, tool_schema(tool))
        except ValidationError as e:
            raise Denied("INVALID_TOOL_ARGUMENTS", "Tool arguments do not match the approved schema.", 422) from e
        if count:
            bucket = f"RATE#{p.owner}#{now()//60}"
            def apply(rate):
                rate = rate or {"count": 0, "expires_at": now()+120}
                if rate["count"] >= 40:
                    raise Denied("RATE_LIMIT", "User tool limit reached; retry next minute.", 429)
                rate["count"] += 1
                return rate, None
            self.store.mutate(bucket, apply)
        if tool == "prepare_trade":
            self.validate_intent(agent, TradeIntent.model_validate(args))
        if tool in {"execute_trade", "get_operation"}:
            operation = self.operation(p, args["operation_id"])
            if operation["agent_id"] != agent["id"]:
                raise Denied("OPERATION_AGENT_MISMATCH", "Operation belongs to a different agent.")
            if tool == "execute_trade":
                self.check_executable(p, operation, agent)
        return p

    def validate_intent(self, agent, intent):
        units = amount_units(intent.amount)
        if units > agent["max_trade_units"]:
            raise Denied("TRADE_LIMIT", "Amount exceeds this agent's single-trade limit.")
        return units

    def prepare_trade(self, p, agent_id, intent, source_id, grant_id=None, conversation_id=None, quote=None):
        agent = self.agent(p, agent_id)
        units = self.validate_intent(agent, intent)
        # User-owned aggregate is the source of truth; neither receiver nor wallet is model-selectable.
        canonical = {
            "action": "swap", "chain": intent.chain, "sell_asset": "USDC",
            "buy_asset": intent.buy_asset, "amount_units": units,
            "max_slippage_bps": intent.max_slippage_bps, "wallet": f"paper:{p.owner}",
            "receiver": f"paper:{p.owner}", "mode": "paper",
        }
        # The same invocation retains its original order even if its quote changes on retry.
        op_id = "op_" + digest([p.owner, agent_id, source_id, canonical])[:32]
        if quote is not None:
            price = quote_price(quote)
            canonical.update(
                reference_price=str(price),
                minimum_received=str(Decimal(units) / Decimal(1_000_000) / price
                                     * (1 - Decimal(intent.max_slippage_bps) / Decimal(10_000))),
            )
        op = {
            "id": op_id, "owner": p.owner, "agent_id": agent_id, "agent_epoch": agent["epoch"],
            "kind": "TRADE", "intent": canonical, "intent_hash": digest(canonical),
            "status": "PENDING", "created_at": now(), "expires_at": now()+900,
            "grant_id": grant_id,
            "conversation_id": conversation_id,
        }
        def apply(state):
            current = state["agents"].get(agent_id)
            if not current or current["status"] != "ACTIVE" or current["epoch"] != agent["epoch"]:
                raise Denied("AGENT_CHANGED", "Agent is no longer active in this execution context.")
            if op_id in state["operations"]:
                return state["operations"][op_id]
            # Quote-bound manual orders fail before asking for approval; execution rechecks atomically.
            if quote is not None:
                day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if state["wallet"]["USDC"] < units:
                    raise Denied("INSUFFICIENT_BALANCE", "Insufficient paper USDC.")
                if state["daily_spend"].get(agent_id+":"+day, 0) + units > agent["daily_limit_units"]:
                    raise Denied("DAILY_LIMIT", "Daily trading budget would be exceeded.")
            if len(state["operations"]) >= 200:
                raise Denied("HISTORY_LIMIT", "Export and archive terminal operations before adding more.", 409)
            state["operations"][op_id] = op
            event(state, "INTENT_PREPARED", f"Swap {display_units(units)} USDC for ETH",
                  agent_id=agent_id, operation_id=op_id)
            return op
        return self.mutate(p, apply)

    def operation(self, p, op_id):
        op = self.ensure(p)["operations"].get(op_id)
        if not op:
            raise Denied("NOT_FOUND", "Operation is not available.", 404)
        return op

    def approve(self, p, op_id, stepup_operation, expected_hash):
        if stepup_operation != op_id:
            raise Denied("STEP_UP_REQUIRED", "Re-authenticate for this specific operation.", 401)
        if self.operation(p, op_id)["kind"] == "MARKET_PURCHASE":
            from .marketplace import Marketplace
            return Marketplace(self).approve(p, op_id, expected_hash)
        def apply(state):
            op = state["operations"].get(op_id)
            if not op:
                raise Denied("NOT_FOUND", "Operation is not available.", 404)
            if op["status"] != "PENDING" or op["expires_at"] <= now():
                raise Denied("APPROVAL_EXPIRED", "Operation is no longer awaiting approval.", 409)
            if op["intent_hash"] != expected_hash or digest(op["intent"]) != expected_hash:
                raise Denied("INTENT_CHANGED", "The operation changed. Review it again.", 409)
            agent = state["agents"][op["agent_id"]]
            if agent["status"] != "ACTIVE" or agent["epoch"] != op["agent_epoch"]:
                raise Denied("CAPABILITY_REVOKED", "Agent authorization changed.")
            op.update(status="APPROVED", approved_by=p.sub, approved_at=now())
            if op["kind"] == "GRANT":
                grant = {**op["intent"], "id": op["id"], "status": "ACTIVE", "used_units": 0,
                         "next_run_at": now()+op["intent"]["interval_seconds"]}
                state["grants"][op["id"]] = grant
                op["status"] = "CONFIRMED"
            event(state, "USER_APPROVED", "User confirmed the immutable operation.",
                  operation_id=op_id, agent_id=op["agent_id"])
            return op
        return self.mutate(p, apply)

    def reject(self, p, op_id):
        def apply(state):
            op = state["operations"].get(op_id)
            if not op:
                raise Denied("NOT_FOUND", "Operation is not available.", 404)
            if op["status"] not in {"PENDING", "APPROVED"}:
                raise Denied("INVALID_STATE", "Operation cannot be cancelled.", 409)
            op["status"] = "REJECTED"
            event(state, "USER_REJECTED", "Operation cancelled.", operation_id=op_id)
            return op
        return self.mutate(p, apply)

    def check_executable(self, p, op, agent, state=None):
        if op["kind"] != "TRADE":
            raise Denied("INVALID_OPERATION", "Only trade operations can execute.")
        if op["status"] == "CONFIRMED":
            return
        if op["expires_at"] <= now():
            raise Denied("OPERATION_EXPIRED", "Prepare a new operation.", 409)
        if op["owner"] != p.owner or digest(op["intent"]) != op["intent_hash"]:
            raise Denied("INTENT_CHANGED", "Operation integrity check failed.")
        if agent["status"] != "ACTIVE" or agent["epoch"] != op["agent_epoch"]:
            raise Denied("CAPABILITY_REVOKED", "Agent authorization changed.")
        if op.get("grant_id"):
            state = state or self.ensure(p)
            grant = state["grants"].get(op["grant_id"])
            if (
                not grant or grant["status"] != "ACTIVE" or grant["expires_at"] <= now()
                or grant["agent_id"] != agent["id"]
                or op["intent"]["amount_units"] > grant["amount_units"]
                or grant["used_units"] + op["intent"]["amount_units"] > grant["total_units"]
                or op["intent"]["buy_asset"] != grant["buy_asset"]
                or op["intent"]["chain"] != grant["chain"]
                or op["intent"]["max_slippage_bps"] > grant["max_slippage_bps"]
            ):
                raise Denied("GRANT_INVALID", "Automatic trading grant is inactive or exhausted.")
        elif op["status"] != "APPROVED":
            raise Denied("APPROVAL_REQUIRED", "User confirmation is required.", 409)
        if op["status"] not in {"APPROVED", "PENDING"}:
            raise Denied("INVALID_STATE", "Operation is not executable.", 409)

    def execute(self, p, op_id, quote):
        price = quote_price(quote)
        def apply(state):
            op = state["operations"].get(op_id)
            if not op:
                raise Denied("NOT_FOUND", "Operation is not available.", 404)
            agent = state["agents"][op["agent_id"]]
            self.check_executable(p, op, agent, state)
            if op["status"] == "CONFIRMED":
                return op
            units = op["intent"]["amount_units"]
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            spend_key = agent["id"]+":"+day
            spent = state["daily_spend"].get(spend_key, 0)
            if spent + units > agent["daily_limit_units"]:
                raise Denied("DAILY_LIMIT", "Daily trading budget would be exceeded.")
            if state["wallet"]["USDC"] < units:
                raise Denied("INSUFFICIENT_BALANCE", "Insufficient paper USDC.")
            received = Decimal(units) / Decimal(1_000_000) / price
            if op["intent"].get("minimum_received") is not None and received < Decimal(op["intent"]["minimum_received"]):
                raise Denied("SLIPPAGE_EXCEEDED", "Price moved beyond the approved limit; cancel and prepare a new order.", 409)
            state["wallet"]["USDC"] -= units
            state["wallet"]["ETH"] = str(Decimal(state["wallet"]["ETH"]) + received)
            state["daily_spend"] = {k: v for k, v in state["daily_spend"].items() if k.endswith(day)}
            state["daily_spend"][spend_key] = spent + units
            if op.get("grant_id"):
                state["grants"][op["grant_id"]]["used_units"] += units
            op.update(
                status="CONFIRMED", executed_at=now(),
                execution={"mode": "paper", "received": str(received), "price": quote["price"],
                           "source": quote["source"], "receipt": "paper_"+op_id, "tx_hash": None},
            )
            event(state, "PAPER_TRADE_EXECUTED", "Simulated fill; no onchain transaction submitted.",
                  operation_id=op_id, agent_id=op["agent_id"])
            return op
        return self.mutate(p, apply)

    def prepare_grant(self, p, agent_id, intent, total, interval, hours):
        agent = self.agent(p, agent_id)
        units = self.validate_intent(agent, intent)
        total_units = amount_units(total)
        if not 300 <= interval <= 86400 or not 1 <= hours <= 720 or total_units < units:
            raise Denied("INVALID_GRANT", "Invalid schedule, expiry or total budget.", 422)
        canonical = {
            "agent_id": agent_id, "amount_units": units, "total_units": total_units,
            "buy_asset": intent.buy_asset, "chain": intent.chain,
            "max_slippage_bps": intent.max_slippage_bps,
            "interval_seconds": interval, "expires_at": now()+hours*3600,
        }
        op = {
            "id": uid("grant_"), "owner": p.owner, "agent_id": agent_id,
            "agent_epoch": agent["epoch"], "kind": "GRANT", "intent": canonical,
            "intent_hash": digest(canonical), "status": "PENDING",
            "created_at": now(), "expires_at": now()+900,
        }
        def apply(state):
            current = state["agents"].get(agent_id)
            if not current or current["status"] != "ACTIVE" or current["epoch"] != agent["epoch"]:
                raise Denied("AGENT_CHANGED", "Agent is no longer active in this execution context.")
            if len(state["operations"]) >= 200:
                raise Denied("HISTORY_LIMIT", "Archive old operations first.", 409)
            state["operations"][op["id"]] = op
            return op
        return self.mutate(p, apply)

    def revoke_grant(self, p, grant_id):
        def apply(state):
            grant = state["grants"].get(grant_id)
            if not grant:
                raise Denied("NOT_FOUND", "Grant is not available.", 404)
            grant["status"] = "REVOKED"
            event(state, "GRANT_REVOKED", "New scheduled trades are blocked.", operation_id=grant_id)
            return grant
        return self.mutate(p, apply)

    def subscribe(self, p, product, enabled):
        if product not in {x["id"] for x in PRODUCTS}:
            raise Denied("NOT_FOUND", "MCP product is not available.", 404)
        def apply(state):
            subs = set(state["subscriptions"])
            subs.add(product) if enabled else subs.discard(product)
            state["subscriptions"] = sorted(subs)
            event(state, "MCP_SUBSCRIPTION", f"{product}: {'enabled' if enabled else 'disabled'}")
            return state["subscriptions"]
        return self.mutate(p, apply)

    def report_denial(self, p, code, agent_id=""):
        def apply(state):
            event(state, code, "Gateway blocked an operation.", severity="warning", agent_id=agent_id)
            severe = {"OWNER_MISMATCH", "INTENT_CHANGED", "SKILL_CHANGED", "INVALID_TOOL_ARGUMENTS"}
            recent = [e for e in state["events"] if e["kind"] in severe
                      and e["agent_id"] == agent_id and e["at"] > now()-60]
            if (code in severe and len(recent) >= 3 and agent_id in state["agents"]
                    and state["agents"][agent_id]["status"] != "DELETED"):
                state["agents"][agent_id]["status"] = "PAUSED"
                state["agents"][agent_id]["epoch"] += 1
                event(state, "AGENT_QUARANTINED", "Repeated invalid actions; review before resuming.",
                      severity="critical", agent_id=agent_id)
        self.mutate(p, apply)

    def catalog(self):
        return {"products": PRODUCTS, "skills": SKILLS}
