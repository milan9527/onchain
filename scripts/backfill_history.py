"""Index existing conversations without reactivating them or inventing past run events."""
import json
import time
from pathlib import Path

from boto3.dynamodb.conditions import Attr

from onchain.domain import Control
from onchain.history import RETENTION
from onchain.store import DynamoStore


def main():
    state = json.loads((Path(__file__).resolve().parents[1] / ".deployment/resources.json").read_text())
    store = DynamoStore(state["outputs"]["TableName"], state["region"])
    control = Control(store)
    params = {"FilterExpression": Attr("pk").begins_with("SESSION#")}
    count = 0
    while True:
        page = store.table.scan(**params)
        for item in page["Items"]:
            current = json.loads(item["data"])
            if not current.get("owner") or not current.get("agent_id"):
                continue
            if not current.get("messages") and not current.get("runs"):
                continue
            def migrate(session):
                if not session:
                    return session, None
                session.setdefault("active_until", session["expires_at"])
                # Retention is based on original creation time; expired execution stays expired.
                session["expires_at"] = max(session["expires_at"], session["created_at"]+RETENTION)
                session.setdefault("kind", "chat" if any(m["role"] == "user" for m in session["messages"]) else "execute")
                title = next((m["content"] for m in session["messages"]
                              if m["role"] == "user" and isinstance(m["content"], str)), None)
                session.setdefault("title", title.replace("\n", " ")[:60] if title else "历史执行记录")
                session.setdefault("updated_at", session["messages"][-1]["at"] if session["messages"] else session["created_at"])
                session.setdefault("runs", [])
                return session, session
            updated = store.mutate(item["pk"], migrate)
            if updated and updated["expires_at"] > time.time():
                control.index_session(updated)
                count += 1
        if "LastEvaluatedKey" not in page:
            break
        params["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    print(f"Indexed {count} conversations. Execution permissions and old run history were not fabricated.")


if __name__ == "__main__":
    main()
