"""Owner-scoped conversation indexes and bounded, credential-free execution journals."""
import copy
import json
import re
import time

from .security import Denied, Principal, uid

RETENTION = 30 * 86400
SECRET_KEYS = {
    "authorization", "cookie", "setcookie", "password", "secret", "secretkey", "apikey",
    "privatekey", "passphrase", "securitycontext", "accesstoken", "refreshtoken", "idtoken",
    "gatewaytoken", "runtimetoken", "authkey", "runtimesessionid", "contextid", "authorizationurl", "workloadaccesstoken",
}


def safe_details(value):
    def clean(item, depth=0):
        if depth > 6:
            return "[omitted]"
        if isinstance(item, dict):
            return {str(k)[:80]: "[REDACTED]" if re.sub(r"[^a-z]", "", str(k).lower()) in SECRET_KEYS
                    else clean(v, depth+1) for k, v in list(item.items())[:30]}
        if isinstance(item, list):
            return [clean(v, depth+1) for v in item[:20]]
        if isinstance(item, str):
            try:
                if item.startswith(("{", "[")):
                    return clean(json.loads(item), depth+1)
            except ValueError:
                pass
            item = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", item)
            item = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED]", item)
            return item[:1500]
        return item if item is None or isinstance(item, (int, float, bool)) else str(item)[:100]
    result = clean(value)
    encoded = json.dumps(result, ensure_ascii=False).encode()
    if len(encoded) > 4096:
        return {"preview": encoded[:3500].decode(errors="ignore"), "truncated": True}
    return result


class History:
    def history_session(self, p, sid):
        session = self.store.get("SESSION#"+sid)
        if not session or session.get("owner") != p.owner or session.get("expires_at", 0) <= time.time():
            raise Denied("SESSION_NOT_FOUND", "Conversation is not available.", 404)
        return session

    def index_session(self, session):
        if session.get("kind") == "probe":
            return
        row = {k: session.get(k) for k in (
            "id", "agent_id", "title", "kind", "created_at", "updated_at", "active", "expires_at",
        )}
        row["message_count"] = len(session.get("messages", []))
        row["last_run_status"] = session["runs"][-1].get("status") if session.get("runs") else None
        row["title"] = row["title"] or "历史会话"
        row["kind"] = row["kind"] or "chat"
        row["updated_at"] = row["updated_at"] or row["created_at"]
        def update(index):
            index = index or {"items": []}
            rows = [r for r in index["items"] if r["id"] != row["id"] and r["expires_at"] > time.time()]
            old = next((r for r in index["items"] if r["id"] == row["id"]), None)
            rows.append(old if old and old["updated_at"] > row["updated_at"] else row)
            index["items"] = sorted(rows, key=lambda r: (r["updated_at"], r["id"]), reverse=True)[:100]
            return index, None
        self.store.mutate("HISTORY#"+session["owner"]+"#"+session["agent_id"], update)

    def list_conversations(self, p, agent_id):
        self.agent(p, agent_id, active=False)
        index = self.store.get("HISTORY#"+p.owner+"#"+agent_id) or {"items": []}
        return [r for r in index["items"] if r["expires_at"] > time.time()]

    def public_conversation(self, p, sid, auth_key):
        session = self.history_session(p, sid)
        can_resume = False
        try:
            active = self.conversation(p, sid)
            can_resume = active.get("auth_key") == auth_key and self.agent(p, active["agent_id"]) is not None
        except Denied:
            pass
        result = {k: copy.deepcopy(session.get(k)) for k in (
            "id", "agent_id", "title", "kind", "messages", "runs", "created_at", "updated_at", "busy_until",
        )}
        result["runs"] = result["runs"] or []
        result["can_resume"] = can_resume
        result["retained_until"] = session["expires_at"]
        return result

    def start_run(self, p, sid, run_id, kind):
        session = self.history_session(p, sid)
        run = {
            "id": run_id, "owner": p.owner, "session_id": sid, "agent_id": session["agent_id"],
            "kind": kind, "status": "QUEUED", "started_at": int(time.time()*1000), "finished_at": None,
            "events": [], "expires_at": session["expires_at"],
        }
        if self.store.create(self.run_key(p, sid, run_id), run):
            self._run_summary(p, sid, run)
            self.trace(p, sid, run_id, "QUEUED", "queue", "任务已排队")
        return self.run_record(p, sid, run_id)

    def run_key(self, p, sid, run_id):
        return "RUN#"+p.owner+"#"+sid+"#"+run_id

    def run_record(self, p, sid, run_id):
        self.history_session(p, sid)
        run = self.store.get(self.run_key(p, sid, run_id))
        if not run or run.get("owner") != p.owner or run.get("session_id") != sid:
            raise Denied("RUN_NOT_FOUND", "Run is not available.", 404)
        result = {k: v for k, v in run.items() if k not in {"owner", "expires_at"}}
        if result["status"] in {"QUEUED", "RUNNING"} and result["started_at"] < (time.time()-300)*1000:
            result["status"] = "INTERRUPTED"
        return result

    def _run_summary(self, p, sid, run):
        summary = {k: run[k] for k in ("id", "kind", "status", "started_at", "finished_at")}
        def update(session):
            if not session or session.get("owner") != p.owner:
                raise Denied("SESSION_NOT_FOUND", "Conversation is not available.", 404)
            runs = session.get("runs", [])
            found = next((r for r in runs if r["id"] == run["id"]), None)
            if found:
                found.update(summary)
            else:
                runs.append(summary)
            session["runs"] = runs[-30:]
            session["updated_at"] = time.time()
            return session, session
        self.index_session(self.store.mutate("SESSION#"+sid, update))

    def trace(self, p, sid, run_id, kind, source, label, details=None, status="OK", duration_ms=None):
        self.history_session(p, sid)
        entry = {
            "id": uid("step_"), "at": int(time.time()*1000), "kind": kind[:60], "source": source[:60],
            "label": label[:120], "status": status, "details": safe_details(details or {}),
            "duration_ms": duration_ms,
        }
        def update(run):
            if not run or run.get("owner") != p.owner or run.get("session_id") != sid:
                raise Denied("RUN_NOT_FOUND", "Run is not available.", 404)
            run["events"].append(entry)
            run["events"] = run["events"][-40:]
            return run, None
        # Older sessions and isolated security probes have no tracked run.
        if self.store.get(self.run_key(p, sid, run_id)):
            self.store.mutate(self.run_key(p, sid, run_id), update)

    def run_status(self, p, sid, run_id, status):
        def update(run):
            if not run or run.get("owner") != p.owner or run.get("session_id") != sid:
                raise Denied("RUN_NOT_FOUND", "Run is not available.", 404)
            run["status"] = status
            if status in {"SUCCEEDED", "FAILED", "BLOCKED"}:
                run["finished_at"] = int(time.time()*1000)
                run["duration_ms"] = run["finished_at"]-run["started_at"]
            return run, run
        self._run_summary(p, sid, self.store.mutate(self.run_key(p, sid, run_id), update))


def trace_claims(control, claims, kind, source, label, details=None, status="OK", duration_ms=None):
    """Observability must not turn a committed trade into an apparent execution failure."""
    try:
        control.trace(Principal(sub=claims["sub"], tenant=claims["tenant"]), claims["sid"], claims["run_id"],
                      kind, source, label, details, status, duration_ms)
    except Exception as error:
        print(json.dumps({"event": "trace_write_failed", "error_type": type(error).__name__}))
