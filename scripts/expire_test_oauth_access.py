"""Acceptance helper: expire only Bob's cached access claims to exercise real refresh on next API call."""
import hashlib
import json
import sys
from pathlib import Path

from onchain.config import Settings
from onchain.store import DynamoStore

root = Path(__file__).resolve().parents[1]
deployment = json.loads((root / ".deployment/resources.json").read_text())
expected_email = json.loads((root / ".deployment/smoke-credentials.json").read_text())["bob"]["email"]
config = Settings(**{k.lower(): v for k, v in deployment["environment"].items()})
store = DynamoStore(config.table_name, config.region)
key = "AUTH#" + hashlib.sha256(json.load(sys.stdin)["cookie"].encode()).hexdigest()


def expire(record):
    if not record or record.get("principal", {}).get("email") != expected_email or not record.get("active"):
        raise RuntimeError("Helper only accepts the active synthetic Bob login")
    if not record.get("oauth_refresh") or not record.get("oauth"):
        raise RuntimeError("No encrypted Cognito refresh credential")
    record["oauth"]["exp"] = 1
    return record, None


store.mutate(key, expire)
print("Synthetic access claims expired; login and encrypted refresh token preserved.")
