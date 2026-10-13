"""Exercise approved DCA through the deployed scheduler, SQS, Gateway and MCP."""
import json
import time
from pathlib import Path

import boto3
import httpx

from onchain.domain import Control
from onchain.security import Principal
from onchain.store import DynamoStore

ROOT = Path(__file__).resolve().parents[1]


def main():
    deployment = json.loads((ROOT / ".deployment/resources.json").read_text())
    credentials = json.loads((ROOT / ".deployment/smoke-credentials.json").read_text())["bob"]
    assert credentials["email"].startswith("onchain-smoke-")
    assert credentials["email"].endswith("@example.invalid")
    control = Control(DynamoStore(deployment["outputs"]["TableName"], deployment["region"]))
    functions = boto3.client("lambda", region_name=deployment["region"])
    with httpx.Client(base_url=deployment["outputs"]["WebsiteUrl"], timeout=30) as api:
        login = api.post("/api/auth/login", json=credentials)
        login.raise_for_status()
        principal = Principal.model_validate(login.json()["user"])
        api.headers["X-CSRF-Token"] = login.json()["csrf"]

        def post(path, body=None):
            result = api.post(path, json=body)
            result.raise_for_status()
            return result.json()

        agent = post("/api/agents", {"name": "Approved DCA acceptance"})
        grant = post("/api/grants/prepare", {
            "agent_id": agent["id"], "intent": {"amount": "20"},
            "total": "20", "interval_seconds": 300, "hours": 1,
        })
        post(f"/api/operations/{grant['id']}/stepup", credentials)
        post(f"/api/operations/{grant['id']}/approve", {"intent_hash": grant["intent_hash"]})
        before = api.get("/api/dashboard").json()
        try:
            def due_test_fixture(state):
                assert state["agents"][agent["id"]]["name"] == "Approved DCA acceptance"
                state["grants"][grant["id"]]["next_run_at"] = int(time.time())-1
            # Only this synthetic paper grant's clock is advanced; no public API bypass exists.
            control.mutate(principal, due_test_fixture)
            for _ in range(2):
                response = functions.invoke(
                    FunctionName=deployment["outputs"]["SchedulerFunctionArn"], Payload=b"{}",
                )
                assert not response.get("FunctionError")
            deadline = time.time()+140
            while time.time() < deadline:
                snapshot = api.get("/api/dashboard").json()
                trades = [o for o in snapshot["operations"] if o.get("grant_id") == grant["id"]]
                if trades and trades[0]["status"] == "CONFIRMED":
                    break
                time.sleep(2)
            else:
                raise AssertionError("The approved scheduled paper trade did not complete")
            assert len(trades) == 1
            assert float(snapshot["wallet"]["USDC"]) == float(before["wallet"]["USDC"])-20
            assert next(g for g in snapshot["grants"] if g["id"] == grant["id"])["used_units"] == 20_000_000
            assert trades[0]["execution"]["tx_hash"] is None
            print("PASS: approved DCA executes once through Scheduler → SQS → Gateway → MCP; budget consumed atomically")
        finally:
            post(f"/api/grants/{grant['id']}/revoke")
            post(f"/api/agents/{agent['id']}/status", {"status": "PAUSED"})
        print("PASS: test grant revoked and automation agent paused")


if __name__ == "__main__":
    main()
