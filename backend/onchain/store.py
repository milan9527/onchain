"""Optimistic, serializable aggregate updates. No external effects inside update callbacks."""

import copy
import json
import sqlite3
import time
from pathlib import Path
from typing import Callable, TypeVar

import boto3
from botocore.exceptions import ClientError

T = TypeVar("T")


class Conflict(Exception):
    pass


class Store:
    def get(self, key: str) -> dict | None:
        raise NotImplementedError

    def mutate(self, key: str, fn: Callable[[dict | None], tuple[dict, T]]) -> T:
        raise NotImplementedError

    def transact(self, keys: list[str], fn: Callable[[dict], T]) -> T:
        """Atomically commit all selected records. The callback may be retried."""
        raise NotImplementedError

    def put(self, key: str, value: dict) -> None:
        self.mutate(key, lambda _: (value, None))

    def create(self, key: str, value: dict) -> bool:
        def apply(current):
            return (current, False) if current is not None else (value, True)
        return self.mutate(key, apply)

    def delete(self, key: str) -> None:
        self.put(key, {"deleted": True, "expires_at": int(time.time())})


class MemoryStore(Store):
    def __init__(self):
        import threading
        self.lock = threading.RLock()
        self.items = {}

    def get(self, key):
        with self.lock:
            return copy.deepcopy(self.items.get(key))

    def mutate(self, key, fn):
        with self.lock:
            value, result = fn(copy.deepcopy(self.items.get(key)))
            self.items[key] = copy.deepcopy(value)
            return result

    def transact(self, keys, fn):
        with self.lock:
            records = {key: copy.deepcopy(self.items.get(key)) for key in set(keys)}
            result = fn(records)
            self.items.update(copy.deepcopy(records))
            return result


class SQLiteStore(Store):
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, data TEXT NOT NULL)")

    def connect(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def get(self, key):
        with self.connect() as c:
            row = c.execute("SELECT data FROM records WHERE id = ?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def mutate(self, key, fn):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT data FROM records WHERE id = ?", (key,)).fetchone()
            value, result = fn(json.loads(row[0]) if row else None)
            c.execute(
                "INSERT INTO records VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (key, json.dumps(value, separators=(",", ":"))),
            )
            return result

    def transact(self, keys, fn):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            records = {}
            for key in set(keys):
                row = c.execute("SELECT data FROM records WHERE id = ?", (key,)).fetchone()
                records[key] = json.loads(row[0]) if row else None
            result = fn(records)
            for key, value in records.items():
                c.execute("INSERT INTO records VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                          (key, json.dumps(value, separators=(",", ":"))))
            return result


class DynamoStore(Store):
    def __init__(self, table_name: str, region: str):
        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def get(self, key):
        item = self.table.get_item(Key={"pk": key}, ConsistentRead=True).get("Item")
        return json.loads(item["data"]) if item else None

    def mutate(self, key, fn):
        for attempt in range(12):
            item = self.table.get_item(Key={"pk": key}, ConsistentRead=True).get("Item")
            current = json.loads(item["data"]) if item else None
            version = int(item["version"]) if item else 0
            value, result = fn(current)
            serialized = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
            if len(serialized.encode()) > 350_000:
                raise ValueError("Aggregate storage limit reached. Export/archive old records first.")
            record = {"pk": key, "data": serialized, "version": version + 1}
            if value.get("expires_at"):
                record["ttl"] = int(value["expires_at"])
            params = {"Item": record, "ExpressionAttributeNames": {"#v": "version"}}
            if item:
                params.update(ConditionExpression="#v = :v", ExpressionAttributeValues={":v": version})
            else:
                params["ConditionExpression"] = "attribute_not_exists(#v)"
            try:
                self.table.put_item(**params)
                return result
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                time.sleep(min(0.005 * 2**attempt, 0.1))
        raise Conflict("Concurrent update could not be committed; retry the original operation.")

    def transact(self, keys, fn):
        from boto3.dynamodb.types import TypeSerializer
        serializer = TypeSerializer()
        keys = sorted(set(keys))
        if not 1 <= len(keys) <= 100:
            raise ValueError("Transaction must select between 1 and 100 records.")
        for attempt in range(12):
            items = {key: self.table.get_item(Key={"pk": key}, ConsistentRead=True).get("Item") for key in keys}
            records = {key: json.loads(item["data"]) if item else None for key, item in items.items()}
            result = fn(records)
            writes = []
            for key in keys:
                value, old = records[key], items[key]
                serialized = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
                if len(serialized.encode()) > 350_000:
                    raise ValueError("Aggregate storage limit reached. Export/archive old records first.")
                version = int(old["version"]) if old else 0
                record = {"pk": key, "data": serialized, "version": version + 1}
                if value.get("expires_at"):
                    record["ttl"] = int(value["expires_at"])
                put = {"TableName": self.table.name,
                       "Item": {k: serializer.serialize(v) for k, v in record.items()},
                       "ConditionExpression": "#v = :v" if old else "attribute_not_exists(#v)",
                       "ExpressionAttributeNames": {"#v": "version"}}
                if old:
                    put["ExpressionAttributeValues"] = {":v": serializer.serialize(version)}
                writes.append({"Put": put})
            try:
                # A separate low-level client avoids the resource client's attribute marshalling hooks.
                boto3.client("dynamodb", region_name=self.table.meta.client.meta.region_name).transact_write_items(
                    TransactItems=writes,
                )
                return result
            except ClientError as e:
                code = e.response["Error"]["Code"]
                reasons = e.response.get("CancellationReasons", [])
                retryable = code == "TransactionConflictException" or (
                    code == "TransactionCanceledException" and reasons
                    and all(r["Code"] in {"None", "ConditionalCheckFailed", "TransactionConflict"} for r in reasons)
                )
                if not retryable:
                    raise
                time.sleep(min(0.005 * 2**attempt, 0.1))
        raise Conflict("Concurrent transaction could not be committed; retry the original operation.")


def make_store():
    from .config import settings
    s = settings()
    return DynamoStore(s.table_name, s.region) if s.table_name else SQLiteStore(s.local_path)
