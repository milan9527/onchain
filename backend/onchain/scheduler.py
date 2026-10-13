"""Trusted schedule runner: deterministic DCA under a user-approved, revocable grant."""
import json

import boto3
from boto3.dynamodb.conditions import Attr

from .domain import TradeIntent, display_units, now
from .security import Denied, Principal
from .services import dependencies


def due_grants(store):
    if hasattr(store, "table"):
        params = {"FilterExpression": Attr("pk").begins_with("USER#"),
                  "ProjectionExpression": "#d", "ExpressionAttributeNames": {"#d": "data"}}
        while True:
            page = store.table.scan(**params)
            for item in page.get("Items", []):
                state = json.loads(item["data"])
                yield state
            if "LastEvaluatedKey" not in page:
                break
            params["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    elif hasattr(store, "items"):
        for key, value in list(store.items.items()):
            if key.startswith("USER#"):
                yield value
    else:
        with store.connect() as conn:
            for row in conn.execute("SELECT data FROM records WHERE id LIKE 'USER#%'"):
                yield json.loads(row[0])


def handler(_event, _context):
    config, store, control, tokens = dependencies()
    scheduled = 0
    for state in due_grants(store):
        p = Principal(sub=state["sub"], tenant=state["tenant"])
        for grant in state.get("grants", {}).values():
            if grant["status"] != "ACTIVE" or grant["expires_at"] <= now() or grant["next_run_at"] > now():
                continue
            slot = grant["next_run_at"]
            grant_id = grant["id"]
            try:
                agent = control.agent(p, grant["agent_id"])
                operation = control.prepare_trade(
                    p, agent["id"], TradeIntent(
                        amount=display_units(grant["amount_units"]), buy_asset=grant["buy_asset"],
                        chain=grant["chain"], max_slippage_bps=grant["max_slippage_bps"],
                    ), source_id=f"{grant_id}:{slot}", grant_id=grant_id,
                )
                control.check_executable(p, operation, agent)
                # A stable job ID and operation ID make retries safe even across schedule invocations.
                run_id = "schedule_"+operation["id"]
                session_key = "SCHEDULE_SESSION#"+operation["id"]
                existing = store.get(session_key)
                if existing:
                    session = control.conversation(p, existing["sid"])
                else:
                    session = control.create_conversation(p, agent["id"], kind="scheduled")
                    store.create(session_key, {"sid": session["id"], "expires_at": now()+86400})
                    # Resolve the winner if two schedulers created conversations concurrently.
                    session = control.conversation(p, store.get(session_key)["sid"])
                claims = control.capability(p, session["id"], run_id)
                job = {
                    "kind": "execute", "sid": session["id"], "run_id": run_id,
                    "principal": p.model_dump(), "operation_id": operation["id"],
                    "runtime_token": tokens.issue("onchain-runtime", claims, ttl=300),
                    "gateway_token": tokens.issue("onchain-gateway", claims, ttl=300),
                }
                if not config.queue_url:
                    import asyncio
                    from .worker import process
                    asyncio.run(process(job))
                else:
                    boto3.client("sqs", region_name=config.region).send_message(
                        QueueUrl=config.queue_url, MessageBody=json.dumps(job),
                    )
                def advance(current, grant_id=grant_id, slot=slot):
                    g = current["grants"][grant_id]
                    if g["next_run_at"] == slot:
                        g["next_run_at"] = now()+g["interval_seconds"]
                    return None
                control.mutate(p, advance)
                scheduled += 1
            except Denied:
                # Revoked, paused, expired or exhausted grants fail closed.
                continue
    return {"scheduled": scheduled}
