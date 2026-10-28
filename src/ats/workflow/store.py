"""Durable, per-operation SQLite persistence for workflow and trigger state.

This store is deliberately independent of ``TradingMemory``'s cached connection.
It owns only scheduler lifecycle records; research projections remain in Workflow
Memory and calendar facts remain in the Data Platform.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from threading import Lock
from typing import Any, Iterator, Mapping
import uuid

from ..config import REPO_ROOT

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    trigger_key TEXT UNIQUE,
    request_hash TEXT NOT NULL,
    request_json TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    profile_version TEXT NOT NULL DEFAULT '',
    plan_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status_created
    ON workflow_runs(status, created_at);
CREATE TABLE IF NOT EXISTS workflow_run_status_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
    from_status TEXT NOT NULL, to_status TEXT NOT NULL, reason_code TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}', changed_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_run_history
    ON workflow_run_status_history(run_id,history_id);

CREATE TABLE IF NOT EXISTS agent_runs (
    agent_run_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
    task_instance_key TEXT NOT NULL,
    task_id TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    status TEXT NOT NULL,
    input_refs_json TEXT NOT NULL DEFAULT '[]',
    data_vintage_refs_json TEXT NOT NULL DEFAULT '[]',
    projection_refs_json TEXT NOT NULL DEFAULT '[]',
    reuse_decision_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT NOT NULL DEFAULT '',
    error_detail TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL DEFAULT '',
    UNIQUE(run_id, task_instance_key, attempt_no)
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_run_task
    ON agent_runs(run_id, task_id, status);

CREATE TABLE IF NOT EXISTS trigger_runs (
    trigger_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    request_json TEXT NOT NULL DEFAULT '{}',
    schedule_id TEXT NOT NULL DEFAULT '',
    scheduled_for TEXT NOT NULL DEFAULT '',
    event_id TEXT NOT NULL DEFAULT '',
    event_version TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    owner_id TEXT NOT NULL DEFAULT '',
    lease_until TEXT NOT NULL DEFAULT '',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    superseded_after_run INTEGER NOT NULL DEFAULT 0,
    reason_code TEXT NOT NULL DEFAULT '',
    actual_lag_seconds REAL,
    policy_version TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trigger_runs_state_lease
    ON trigger_runs(status, lease_until);
CREATE INDEX IF NOT EXISTS idx_trigger_runs_workflow_time
    ON trigger_runs(workflow_id, scheduled_for, created_at);
CREATE INDEX IF NOT EXISTS idx_trigger_runs_event
    ON trigger_runs(event_id, event_version, workflow_id);
CREATE TABLE IF NOT EXISTS trigger_status_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT, trigger_key TEXT NOT NULL,
    from_status TEXT NOT NULL, to_status TEXT NOT NULL, owner_id TEXT NOT NULL DEFAULT '',
    reason_code TEXT NOT NULL DEFAULT '', detail_json TEXT NOT NULL DEFAULT '{}',
    changed_at TEXT NOT NULL,
    FOREIGN KEY(trigger_key) REFERENCES trigger_runs(trigger_key)
);
CREATE INDEX IF NOT EXISTS idx_trigger_history_key
    ON trigger_status_history(trigger_key,history_id);
"""

_BOOTSTRAP_LOCK = Lock()


class WorkflowStoreError(RuntimeError):
    """Base class for workflow persistence conflicts."""


class IdentityConflict(WorkflowStoreError):
    """A stable run/trigger identity was replayed with different parameters."""


class LeaseLost(WorkflowStoreError):
    """The caller no longer owns the trigger lease it attempted to update."""


def _stamp(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="microseconds")


def _scheduled_stamp(value: str) -> str:
    if not value:
        return ""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("scheduled_for must include an explicit timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "model_dump_json"):
        value = json.loads(value.model_dump_json())
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def default_workflow_db_path() -> str:
    return os.environ.get("ATS_DB_PATH", str(REPO_ROOT / "var" / "ats.sqlite"))


class WorkflowStore:
    """Per-operation SQLite repository with explicit, additive bootstrap."""

    def __init__(self, path: str | Path | None = None, *, busy_timeout_ms: int = 30_000):
        self.path = str(path or default_workflow_db_path())
        if self.path == ":memory:":
            raise ValueError("WorkflowStore requires a file-backed database for per-operation connections")
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.bootstrap()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000,
                               isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def bootstrap(self) -> None:
        """Create additive scheduler tables once per process, safe to repeat."""
        with _BOOTSTRAP_LOCK:
            conn = self._connect()
            try:
                conn.executescript(_SCHEMA)
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(trigger_runs)")}
                if "request_json" not in columns:
                    conn.execute("ALTER TABLE trigger_runs ADD COLUMN request_json TEXT NOT NULL "
                                 "DEFAULT '{}'")
                if "superseded_after_run" not in columns:
                    conn.execute("ALTER TABLE trigger_runs ADD COLUMN superseded_after_run "
                                 "INTEGER NOT NULL DEFAULT 0")
            finally:
                conn.close()

    @staticmethod
    def _run_history(conn: sqlite3.Connection, run_id: str, old: str, new: str,
                     *, reason: str = "", detail: Any = None,
                     at: datetime | None = None) -> None:
        conn.execute("INSERT INTO workflow_run_status_history(run_id,from_status,to_status,"
                     "reason_code,detail_json,changed_at) VALUES(?,?,?,?,?,?)",
                     (run_id, old, new, reason, _json(detail or {}), _stamp(at)))

    @staticmethod
    def _trigger_history(conn: sqlite3.Connection, trigger_key: str, old: str, new: str,
                         *, owner_id: str = "", reason: str = "", detail: Any = None,
                         at: datetime | None = None) -> None:
        conn.execute("INSERT INTO trigger_status_history(trigger_key,from_status,to_status,"
                     "owner_id,reason_code,detail_json,changed_at) VALUES(?,?,?,?,?,?,?)",
                     (trigger_key, old, new, owner_id, reason, _json(detail or {}), _stamp(at)))

    def create_run(self, *, run_id: str, request: Any, plan: Any,
                   trigger_key: str = "", profile_version: str = "",
                   plan_hash: str = "", status: str = "planned",
                   at: datetime | None = None) -> dict:
        request_json, plan_json = _json(request), _json(plan)
        request_hash = _hash(request)
        plan_hash = plan_hash or _hash(plan)
        stamp = _stamp(at)
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM workflow_runs WHERE run_id=? OR (? != '' AND trigger_key=?)",
                (run_id, trigger_key, trigger_key)).fetchone()
            if existing:
                if (existing["request_hash"] != request_hash
                        or existing["plan_hash"] != plan_hash
                        or existing["profile_version"] != profile_version):
                    raise IdentityConflict(
                        f"workflow run identity {run_id!r}/{trigger_key!r} was reused "
                        "with a different request or plan")
                return self._decode_run(existing)
            conn.execute(
                "INSERT INTO workflow_runs(run_id,trigger_key,request_hash,request_json,"
                "plan_json,profile_version,plan_hash,status,started_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, trigger_key or None, request_hash, request_json, plan_json,
                 profile_version, plan_hash, status, stamp, stamp, stamp))
            self._run_history(conn, run_id, "", status, reason="created", at=at)
            row = conn.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
            return self._decode_run(row)

    def record_trigger(self, *, trigger_key: str, kind: str, workflow_id: str,
                       request: Any, schedule_id: str = "", scheduled_for: str = "",
                       event_id: str = "", event_version: str = "",
                       policy_version: str = "", at: datetime | None = None) -> dict:
        """Persist a planned wake-up before a worker attempts to claim it."""
        stamp = _stamp(at)
        request_hash = _hash(request)
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM trigger_runs WHERE trigger_key=?",
                               (trigger_key,)).fetchone()
            if row is not None:
                if row["request_hash"] != request_hash or row["workflow_id"] != workflow_id:
                    raise IdentityConflict(f"trigger key {trigger_key!r} conflicts with stored request")
                return self._decode_trigger(row)
            conn.execute(
                "INSERT INTO trigger_runs(trigger_key,kind,workflow_id,request_hash,"
                "request_json,schedule_id,scheduled_for,event_id,event_version,status,policy_version,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (trigger_key, kind, workflow_id, request_hash, _json(request), schedule_id,
                 _scheduled_stamp(scheduled_for),
                 event_id, event_version, "planned", policy_version, stamp, stamp))
            self._trigger_history(conn, trigger_key, "", "planned", reason="recorded", at=at)
            return self._decode_trigger(conn.execute(
                "SELECT * FROM trigger_runs WHERE trigger_key=?", (trigger_key,)).fetchone())

    @staticmethod
    def _decode_trigger(row: sqlite3.Row | Mapping[str, Any]) -> dict:
        item = dict(row)
        try:
            item["request"] = json.loads(item.get("request_json") or "{}")
        except json.JSONDecodeError:
            item["request"] = {}
        return item

    @staticmethod
    def _decode_run(row: sqlite3.Row | Mapping[str, Any]) -> dict:
        item = dict(row)
        for column in ("request_json", "plan_json", "result_json"):
            try:
                item[column.removesuffix("_json")] = json.loads(item[column] or "{}")
            except json.JSONDecodeError:
                item[column.removesuffix("_json")] = {}
        return item

    def get_run(self, run_id: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
            return self._decode_run(row) if row else None
        finally:
            conn.close()

    def list_runs(self, *, status: str = "", limit: int = 100) -> list[dict]:
        conn = self._connect()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM workflow_runs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT ?",
                    (limit,)).fetchall()
            return [self._decode_run(row) for row in rows]
        finally:
            conn.close()

    def set_run_state(self, run_id: str, *, status: str,
                      result: Any | None = None, expected: tuple[str, ...] = (),
                      at: datetime | None = None) -> bool:
        stamp = _stamp(at)
        terminal = status in {"complete", "incomplete", "failed", "cancelled"}
        with self._transaction() as conn:
            previous = conn.execute("SELECT status FROM workflow_runs WHERE run_id=?",
                                    (run_id,)).fetchone()
            old_status = previous["status"] if previous else ""
            clause = " AND status IN (" + ",".join("?" for _ in expected) + ")" if expected else ""
            # A non-terminal transition preserves any prior terminal timestamp;
            # expected-state CAS prevents a stale worker from overwriting it.
            sql = ("UPDATE workflow_runs SET status=?,"
                   "ended_at=CASE WHEN ? THEN ? ELSE ended_at END,"
                   "result_json=?,updated_at=? WHERE run_id=?" + clause)
            cur = conn.execute(sql, (status, int(terminal), stamp, _json(result or {}),
                                     stamp, run_id, *expected))
            if cur.rowcount == 1 and old_status != status:
                self._run_history(conn, run_id, old_status, status, at=at)
            return cur.rowcount == 1

    def reopen_run_for_compensation(self, run_id: str, *, actor: str, reason: str,
                                    at: datetime | None = None) -> bool:
        if not actor.strip() or not reason.strip():
            raise ValueError("workflow compensation requires actor and reason")
        with self._transaction() as conn:
            row = conn.execute("SELECT status FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None or row["status"] not in {"failed", "incomplete"}:
                return False
            old = row["status"]
            conn.execute("UPDATE workflow_runs SET status='planned',ended_at='',updated_at=? "
                         "WHERE run_id=? AND status=?", (_stamp(at), run_id, old))
            self._run_history(conn, run_id, old, "planned", reason="explicit_compensation",
                              detail={"actor": actor, "reason": reason}, at=at)
            return True

    def start_attempt(self, *, run_id: str, task_instance_key: str, task_id: str,
                      scope: Any, attempt_no: int, input_refs: Any = (),
                      data_vintage_refs: Any = (), reuse_decision: Any = None,
                      at: datetime | None = None) -> dict:
        stamp = _stamp(at)
        agent_run_id = uuid.uuid4().hex
        with self._transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO agent_runs(agent_run_id,run_id,task_instance_key,"
                "task_id,scope_json,attempt_no,status,input_refs_json,data_vintage_refs_json,"
                "reuse_decision_json,started_at) VALUES(?,?,?,?,?,?,'running',?,?,?,?)",
                (agent_run_id, run_id, task_instance_key, task_id, _json(scope), attempt_no,
                 _json(input_refs), _json(data_vintage_refs), _json(reuse_decision or {}), stamp))
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE run_id=? AND task_instance_key=? AND attempt_no=?",
                (run_id, task_instance_key, attempt_no)).fetchone()
            return self._decode_agent_run(row)

    @staticmethod
    def _decode_agent_run(row: sqlite3.Row | Mapping[str, Any]) -> dict:
        item = dict(row)
        for column in ("scope_json", "input_refs_json", "data_vintage_refs_json",
                       "projection_refs_json", "reuse_decision_json"):
            key = column.removesuffix("_json")
            try:
                item[key] = json.loads(item[column] or ("{}" if key in
                            {"scope", "reuse_decision"} else "[]"))
            except json.JSONDecodeError:
                item[key] = {} if key in {"scope", "reuse_decision"} else []
        return item

    def finish_attempt(self, agent_run_id: str, *, status: str,
                       projection_refs: Any = (), error_code: str = "",
                       error_detail: str = "", at: datetime | None = None) -> bool:
        if status not in {"succeeded", "failed", "blocked", "missing", "stale", "cancelled"}:
            raise ValueError(f"invalid terminal task status: {status}")
        stamp = _stamp(at)
        with self._transaction() as conn:
            cur = conn.execute(
                "UPDATE agent_runs SET status=?,projection_refs_json=?,error_code=?,"
                "error_detail=?,ended_at=? WHERE agent_run_id=? AND status='running'",
                (status, _json(projection_refs), error_code, error_detail, stamp, agent_run_id))
            return cur.rowcount == 1

    def attempts_for_run(self, run_id: str) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM agent_runs WHERE run_id=? ORDER BY started_at,task_id,attempt_no",
                (run_id,)).fetchall()
            return [self._decode_agent_run(row) for row in rows]
        finally:
            conn.close()

    def claim_trigger(self, *, trigger_key: str, kind: str, workflow_id: str,
                      request: Any, owner_id: str, lease_seconds: int = 120,
                      run_id: str = "", schedule_id: str = "", scheduled_for: str = "",
                      event_id: str = "", event_version: str = "",
                      policy_version: str = "", at: datetime | None = None) -> dict:
        now = at or datetime.now(timezone.utc)
        stamp = _stamp(now)
        lease_until = _stamp(now + timedelta(seconds=lease_seconds))
        request_hash = _hash(request)
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM trigger_runs WHERE trigger_key=?",
                               (trigger_key,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO trigger_runs(trigger_key,kind,workflow_id,request_hash,"
                    "request_json,schedule_id,scheduled_for,event_id,event_version,status,run_id,owner_id,"
                    "lease_until,attempt_count,policy_version,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (trigger_key, kind, workflow_id, request_hash, _json(request), schedule_id,
                     _scheduled_stamp(scheduled_for),
                     event_id, event_version, "running", run_id, owner_id, lease_until, 1,
                     policy_version, stamp, stamp))
                row = conn.execute("SELECT * FROM trigger_runs WHERE trigger_key=?",
                                   (trigger_key,)).fetchone()
                self._trigger_history(conn, trigger_key, "", "running", owner_id=owner_id,
                                      reason="claimed", at=now)
                return {**self._decode_trigger(row), "acquired": True, "duplicate": False}
            if row["request_hash"] != request_hash or row["workflow_id"] != workflow_id:
                raise IdentityConflict(f"trigger key {trigger_key!r} conflicts with stored request")
            if run_id and row["run_id"] and run_id != row["run_id"]:
                raise IdentityConflict(f"trigger key {trigger_key!r} is already bound to another run")
            if row["status"] in {"complete", "incomplete", "failed", "skipped",
                                  "superseded", "waiting_material"}:
                return {**self._decode_trigger(row), "acquired": False, "duplicate": True}
            old_until = row["lease_until"]
            if old_until and old_until > stamp:
                return {**self._decode_trigger(row), "acquired": False, "duplicate": True}
            if row["superseded_after_run"]:
                conn.execute("UPDATE trigger_runs SET status='superseded',owner_id='',lease_until='',"
                             "reason_code='event_version_changed_in_flight',updated_at=? "
                             "WHERE trigger_key=? AND status='running'",
                             (stamp, trigger_key))
                self._trigger_history(conn, trigger_key, row["status"], "superseded",
                                      owner_id=row["owner_id"],
                                      reason="event_version_changed_in_flight", at=now)
                current = conn.execute("SELECT * FROM trigger_runs WHERE trigger_key=?",
                                       (trigger_key,)).fetchone()
                return {**self._decode_trigger(current), "acquired": False,
                        "duplicate": True}
            old_status = row["status"]
            reason = "lease_takeover" if old_status == "running" else "claimed"
            conn.execute(
                "UPDATE trigger_runs SET status='running',owner_id=?,lease_until=?,"
                "attempt_count=attempt_count+1,reason_code='',actual_lag_seconds=NULL,"
                "run_id=CASE WHEN run_id='' THEN ? ELSE run_id END,updated_at=? "
                "WHERE trigger_key=?",
                (owner_id, lease_until, run_id, stamp, trigger_key))
            row = conn.execute("SELECT * FROM trigger_runs WHERE trigger_key=?",
                               (trigger_key,)).fetchone()
            self._trigger_history(conn, trigger_key, old_status, "running", owner_id=owner_id,
                                  reason=reason, at=now)
            return {**self._decode_trigger(row), "acquired": True, "duplicate": True}

    def renew_trigger(self, trigger_key: str, *, owner_id: str, lease_seconds: int = 120,
                      at: datetime | None = None) -> bool:
        now = at or datetime.now(timezone.utc)
        with self._transaction() as conn:
            cur = conn.execute(
                "UPDATE trigger_runs SET lease_until=?,updated_at=? WHERE trigger_key=? "
                "AND owner_id=? AND status='running'",
                (_stamp(now + timedelta(seconds=lease_seconds)), _stamp(now), trigger_key,
                 owner_id))
            return cur.rowcount == 1

    def finish_trigger(self, trigger_key: str, *, owner_id: str, status: str,
                       reason_code: str = "", actual_lag_seconds: float | None = None,
                       at: datetime | None = None) -> bool:
        if status not in {"complete", "incomplete", "failed", "skipped", "superseded"}:
            raise ValueError(f"invalid trigger terminal status: {status}")
        with self._transaction() as conn:
            previous = conn.execute("SELECT status,superseded_after_run FROM trigger_runs "
                                    "WHERE trigger_key=?",
                                     (trigger_key,)).fetchone()
            final_status = ("superseded" if previous and previous["superseded_after_run"]
                            else status)
            final_reason = ("event_version_changed_in_flight"
                            if previous and previous["superseded_after_run"] else reason_code)
            cur = conn.execute(
                "UPDATE trigger_runs SET status=?,reason_code=?,actual_lag_seconds=?,"
                "lease_until='',updated_at=? WHERE trigger_key=? AND owner_id=? AND status='running'",
                (final_status, final_reason, actual_lag_seconds, _stamp(at), trigger_key, owner_id))
            if cur.rowcount == 1:
                self._trigger_history(conn, trigger_key, previous["status"], final_status,
                                      owner_id=owner_id, reason=final_reason, at=at)
            if cur.rowcount == 0:
                row = conn.execute("SELECT owner_id,status FROM trigger_runs WHERE trigger_key=?",
                                   (trigger_key,)).fetchone()
                if row is None or row["owner_id"] != owner_id or row["status"] != status:
                    raise LeaseLost(f"worker {owner_id!r} no longer owns trigger {trigger_key!r}")
            return cur.rowcount == 1

    def resolve_planned_trigger(self, trigger_key: str, *, status: str,
                                reason_code: str, actual_lag_seconds: float | None = None,
                                at: datetime | None = None) -> bool:
        """Resolve a wake-up before claim (for misfire skip or calendar supersession)."""
        if status not in {"skipped", "superseded", "waiting_material"}:
            raise ValueError(
                "pre-claim trigger resolution must be skipped, superseded or waiting_material")
        with self._transaction() as conn:
            previous = conn.execute("SELECT status FROM trigger_runs WHERE trigger_key=?",
                                     (trigger_key,)).fetchone()
            cur = conn.execute(
                "UPDATE trigger_runs SET status=?,reason_code=?,actual_lag_seconds=?,"
                "updated_at=? WHERE trigger_key=? AND status IN ('planned','pending')",
                (status, reason_code, actual_lag_seconds, _stamp(at), trigger_key))
            if cur.rowcount == 1:
                self._trigger_history(conn, trigger_key, previous["status"], status,
                                      reason=reason_code, at=at)
            return cur.rowcount == 1

    def resolve_expired_trigger(self, trigger_key: str, *, status: str,
                                reason_code: str, actual_lag_seconds: float | None = None,
                                at: datetime | None = None) -> bool:
        """Resolve an expired in-flight scheduled claim without touching a live lease."""
        if status != "skipped":
            raise ValueError("expired trigger resolution currently supports only skipped")
        instant = at or datetime.now(timezone.utc)
        stamp = _stamp(instant)
        with self._transaction() as conn:
            previous = conn.execute("SELECT status,lease_until,owner_id FROM trigger_runs "
                                    "WHERE trigger_key=?", (trigger_key,)).fetchone()
            if previous is None or previous["status"] != "running":
                return False
            if previous["lease_until"] and previous["lease_until"] > stamp:
                return False
            cur = conn.execute(
                "UPDATE trigger_runs SET status='skipped',reason_code=?,actual_lag_seconds=?,"
                "owner_id='',lease_until='',updated_at=? WHERE trigger_key=? AND status='running' "
                "AND lease_until<=?",
                (reason_code, actual_lag_seconds, stamp, trigger_key, stamp))
            if cur.rowcount == 1:
                self._trigger_history(conn, trigger_key, "running", "skipped",
                                      owner_id=previous["owner_id"], reason=reason_code, at=instant)
            return cur.rowcount == 1

    def bind_trigger_run(self, trigger_key: str, *, owner_id: str, run_id: str) -> bool:
        """Bind the deterministic workflow run while the caller still owns the lease."""
        with self._transaction() as conn:
            cur = conn.execute(
                "UPDATE trigger_runs SET run_id=?,updated_at=? WHERE trigger_key=? "
                "AND owner_id=? AND status='running' AND (run_id='' OR run_id=?)",
                (run_id, _stamp(), trigger_key, owner_id, run_id))
            if cur.rowcount == 0:
                row = conn.execute("SELECT run_id,owner_id,status FROM trigger_runs "
                                   "WHERE trigger_key=?", (trigger_key,)).fetchone()
                if (row is None or row["owner_id"] != owner_id or row["status"] != "running"
                        or row["run_id"] != run_id):
                    raise LeaseLost(f"worker {owner_id!r} cannot bind run to {trigger_key!r}")
            return cur.rowcount == 1

    def supersede_event(self, *, event_id: str, current_version: str,
                        at: datetime | None = None) -> int:
        stamp = _stamp(at)
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT trigger_key,status,owner_id FROM trigger_runs WHERE event_id=? "
                "AND event_version!=? AND status IN ('pending','planned','running',"
                "'waiting_material')",
                (event_id, current_version)).fetchall()
            for row in rows:
                if row["status"] == "running":
                    conn.execute("UPDATE trigger_runs SET superseded_after_run=1,"
                                 "reason_code='event_version_changed_in_flight',updated_at=? "
                                 "WHERE trigger_key=? AND status='running'",
                                 (stamp, row["trigger_key"]))
                    to_status, reason = "running", "event_version_changed_in_flight"
                else:
                    conn.execute("UPDATE trigger_runs SET status='superseded',"
                                 "reason_code='event_version_changed',lease_until='',updated_at=? "
                                 "WHERE trigger_key=?", (stamp, row["trigger_key"]))
                    to_status, reason = "superseded", "event_version_changed"
                self._trigger_history(conn, row["trigger_key"], row["status"], to_status,
                                      owner_id=row["owner_id"], reason=reason,
                                      detail={"current_version": current_version}, at=at)
            return len(rows)

    def compensate_trigger(self, trigger_key: str, *, actor: str, reason: str,
                           at: datetime | None = None) -> bool:
        """Reopen a failed/incomplete logical trigger without changing its identity."""
        if not actor.strip() or not reason.strip():
            raise ValueError("trigger compensation requires actor and reason")
        with self._transaction() as conn:
            row = conn.execute("SELECT status FROM trigger_runs WHERE trigger_key=?",
                               (trigger_key,)).fetchone()
            if row is None or row["status"] not in {"failed", "incomplete"}:
                return False
            old = row["status"]
            conn.execute("UPDATE trigger_runs SET status='planned',owner_id='',lease_until='',"
                         "reason_code='explicit_compensation_requested',updated_at=? "
                         "WHERE trigger_key=? AND status=?", (_stamp(at), trigger_key, old))
            self._trigger_history(conn, trigger_key, old, "planned",
                                  reason="explicit_compensation",
                                  detail={"actor": actor, "reason": reason}, at=at)
            return True

    def trigger_history(self, trigger_key: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM trigger_status_history WHERE trigger_key=? "
                                "ORDER BY history_id", (trigger_key,)).fetchall()
            result = [dict(row) for row in rows]
            for row in result:
                row["detail"] = json.loads(row.pop("detail_json") or "{}")
            return result
        finally:
            conn.close()

    def run_history(self, run_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM workflow_run_status_history WHERE run_id=? "
                                "ORDER BY history_id", (run_id,)).fetchall()
            result = [dict(row) for row in rows]
            for row in result:
                row["detail"] = json.loads(row.pop("detail_json") or "{}")
            return result
        finally:
            conn.close()

    def list_triggers(self, *, workflow_id: str = "", event_id: str = "",
                      schedule_id: str = "", scheduled_from: str = "",
                      scheduled_to: str = "", status: str = "", limit: int = 200) -> list[dict]:
        where, args = [], []
        for column, value in (("workflow_id", workflow_id), ("event_id", event_id),
                              ("schedule_id", schedule_id), ("status", status)):
            if value:
                where.append(f"{column}=?")
                args.append(value)
        if scheduled_from:
            where.append("scheduled_for>=?")
            args.append(_scheduled_stamp(scheduled_from))
        if scheduled_to:
            where.append("scheduled_for<=?")
            args.append(_scheduled_stamp(scheduled_to))
        sql = "SELECT * FROM trigger_runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        conn = self._connect()
        try:
            return [self._decode_trigger(row) for row in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def get_trigger(self, trigger_key: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM trigger_runs WHERE trigger_key=?",
                               (trigger_key,)).fetchone()
            return self._decode_trigger(row) if row else None
        finally:
            conn.close()


__all__ = [
    "IdentityConflict", "LeaseLost", "WorkflowStore", "WorkflowStoreError",
    "default_workflow_db_path",
]
