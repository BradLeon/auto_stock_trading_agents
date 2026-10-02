"""Durable queue for every governed persistent-data ingestion trigger.

This queue stores commands, not market/runtime reads or workflow jobs. Producers
may enqueue work; only a worker holding an unexpired lease may execute it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import uuid
from typing import Any, Sequence

from ..config import REPO_ROOT


STATUSES = {
    "queued", "leased", "succeeded", "no_change", "quarantined", "partial",
    "retry_wait", "failed", "cancelled",
}
TRIGGERS = {"scheduled", "event", "manual", "cache_miss", "fallback"}
_SAFE_COMMANDS = {
    "ingest", "release-check", "frontier-capability-audit", "article-ingest",
    "document-ingest",
    "fixed-ingest",
    "factset-import", "factset-reprocess", "factset-refresh",
    "factset-semantic-refresh",
    "chain-source-collect", "calendar-refresh", "calendar-finalize",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class PersistentIngestionTask:
    task_id: str
    idempotency_key: str
    source_id: str
    scope: dict[str, Any]
    trigger_kind: str
    trigger_ref: str
    requested_as_of: str
    policy_fingerprint: str
    priority: int
    command: tuple[str, ...]
    max_attempts: int
    backoff_seconds: tuple[int, ...]
    parent_run_id: str = ""


class PersistentIngestionQueue:
    """SQLite queue with stable identity, leases, attempts, retries and events."""

    def __init__(self, path: str | Path | None = None, *, lease_seconds: int = 1800):
        self.path = Path(path or os.environ.get(
            "ATS_PERSISTENT_QUEUE_PATH", REPO_ROOT / "var" / "persistent_ingestion.sqlite"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lease_seconds = max(30, int(lease_seconds))
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS persistent_ingestion_tasks (
                    task_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    source_id TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    trigger_kind TEXT NOT NULL,
                    trigger_ref TEXT NOT NULL,
                    requested_as_of TEXT NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    command_json TEXT NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    backoff_json TEXT NOT NULL,
                    parent_run_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_expires_at TEXT NOT NULL DEFAULT '',
                    heartbeat_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    lineage_refs_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS ix_persistent_queue_ready
                    ON persistent_ingestion_tasks(status, available_at, priority, created_at);
                CREATE TABLE IF NOT EXISTS persistent_ingestion_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    at TEXT NOT NULL,
                    event TEXT NOT NULL,
                    detail_json TEXT NOT NULL
                );
            """)
            columns = {row[1] for row in conn.execute(
                "PRAGMA table_info(persistent_ingestion_tasks)")}
            if "lineage_refs_json" not in columns:
                conn.execute("ALTER TABLE persistent_ingestion_tasks ADD COLUMN "
                             "lineage_refs_json TEXT NOT NULL DEFAULT '{}'")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _validate_command(command: Sequence[str], *, trigger_kind: str) -> tuple[str, ...]:
        tokens = tuple(str(token) for token in command)
        if len(tokens) < 3 or tokens[:2] != ("ats", "data") or tokens[2] not in _SAFE_COMMANDS:
            raise ValueError("persistent queue accepts only allowlisted ats data ingestion commands")
        forbidden = {"--force", "--confirm", "--purge"}
        if trigger_kind != "manual":
            forbidden.add("--approve-title-url-review")
        if forbidden.intersection(tokens):
            raise ValueError("queue command contains a prohibited destructive/approval override")
        return tokens

    def enqueue(self, *, source_id: str, scope: dict[str, Any], trigger_kind: str,
                trigger_ref: str, command: Sequence[str], policy_fingerprint: str,
                requested_as_of: str = "", priority: int = 50, max_attempts: int = 3,
                backoff_seconds: Sequence[int] = (30, 120, 600),
                parent_run_id: str = "", now: datetime | None = None) -> tuple[str, bool]:
        if not source_id.strip():
            raise ValueError("source_id is required")
        if trigger_kind not in TRIGGERS:
            raise ValueError(f"unsupported persistent trigger kind: {trigger_kind}")
        if max_attempts < 1 or any(int(delay) < 0 for delay in backoff_seconds):
            raise ValueError("invalid retry policy")
        command = self._validate_command(command, trigger_kind=trigger_kind)
        now = now or _now()
        now_text = _stamp(now)
        identity = {
            "source": source_id, "scope": scope, "trigger": trigger_kind,
            "trigger_ref": trigger_ref, "policy": policy_fingerprint,
        }
        key = hashlib.sha256(_canonical(identity).encode()).hexdigest()
        task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT task_id,status FROM persistent_ingestion_tasks "
                               "WHERE idempotency_key=?", (key,)).fetchone()
            if row:
                return str(row["task_id"]), False
            conn.execute("""INSERT INTO persistent_ingestion_tasks(
                task_id,idempotency_key,source_id,scope_json,trigger_kind,trigger_ref,
                requested_as_of,policy_fingerprint,priority,command_json,max_attempts,
                backoff_json,parent_run_id,status,available_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'queued',?,?,?)""",
                (task_id, key, source_id, _canonical(scope), trigger_kind, trigger_ref,
                 requested_as_of, policy_fingerprint, int(priority), _canonical(command),
                 int(max_attempts), _canonical([int(x) for x in backoff_seconds]),
                 parent_run_id, now_text, now_text, now_text))
            self._event(conn, task_id, now_text, "enqueued", {"trigger_kind": trigger_kind})
        return task_id, True

    @staticmethod
    def _event(conn: sqlite3.Connection, task_id: str, at: str, event: str,
               detail: dict[str, Any]) -> None:
        conn.execute("INSERT INTO persistent_ingestion_events(task_id,at,event,detail_json) "
                     "VALUES(?,?,?,?)", (task_id, at, event, _canonical(detail)))

    def claim(self, worker_id: str, *, now: datetime | None = None,
              task_id: str = "") -> dict[str, Any] | None:
        now = now or _now()
        now_text = _stamp(now)
        lease_until = _stamp(now + timedelta(seconds=self.lease_seconds))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""UPDATE persistent_ingestion_tasks
                SET status=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'retry_wait' END,
                lease_owner='',lease_expires_at='',updated_at=?
                WHERE status='leased' AND lease_expires_at<>'' AND lease_expires_at<=?""",
                (now_text, now_text))
            if task_id:
                row = conn.execute("""SELECT * FROM persistent_ingestion_tasks
                    WHERE task_id=? AND status IN ('queued','retry_wait') AND available_at<=?
                    AND attempts<max_attempts""", (task_id, now_text)).fetchone()
            else:
                row = conn.execute("""SELECT * FROM persistent_ingestion_tasks
                    WHERE status IN ('queued','retry_wait') AND available_at<=?
                    AND attempts<max_attempts ORDER BY priority ASC,created_at ASC LIMIT 1""",
                    (now_text,)).fetchone()
            if not row:
                return None
            attempt = int(row["attempts"]) + 1
            conn.execute("""UPDATE persistent_ingestion_tasks SET status='leased',attempts=?,
                lease_owner=?,lease_expires_at=?,heartbeat_at=?,updated_at=? WHERE task_id=?""",
                (attempt, worker_id, lease_until, now_text, now_text, row["task_id"]))
            self._event(conn, row["task_id"], now_text, "leased",
                        {"worker_id": worker_id, "attempt": attempt, "lease_expires_at": lease_until})
            return self.get(str(row["task_id"]), conn=conn)

    def heartbeat(self, task_id: str, worker_id: str, *, now: datetime | None = None) -> bool:
        now = now or _now()
        now_text = _stamp(now)
        lease_until = _stamp(now + timedelta(seconds=self.lease_seconds))
        with self._connect() as conn:
            changed = conn.execute("""UPDATE persistent_ingestion_tasks SET heartbeat_at=?,
                lease_expires_at=?,updated_at=? WHERE task_id=? AND status='leased'
                AND lease_owner=? AND lease_expires_at>?""",
                (now_text, lease_until, now_text, task_id, worker_id, now_text)).rowcount
            if changed:
                self._event(conn, task_id, now_text, "heartbeat", {"worker_id": worker_id})
            return bool(changed)

    def valid_lease(self, task_id: str, worker_id: str, source_id: str) -> bool:
        now_text = _stamp(_now())
        with self._connect() as conn:
            row = conn.execute("""SELECT 1 FROM persistent_ingestion_tasks WHERE task_id=?
                AND lease_owner=? AND source_id=? AND status='leased' AND lease_expires_at>?""",
                (task_id, worker_id, source_id, now_text)).fetchone()
        return bool(row)

    def valid_source_lease(self, task_id: str, worker_id: str, source_id: str) -> bool:
        task = self.get(task_id)
        if not task or task["status"] != "leased" or task["lease_owner"] != worker_id:
            return False
        if task["lease_expires_at"] <= _stamp(_now()):
            return False
        return task["source_id"] == source_id or source_id in (task["scope"].get("sources") or [])


    @staticmethod
    def require_worker_lease(source_id: str) -> None:
        """Reject a production persistence write without a valid worker lease."""
        task_id = os.environ.get("ATS_PERSISTENT_QUEUE_TASK_ID", "")
        worker_id = os.environ.get("ATS_PERSISTENT_QUEUE_LEASE_OWNER", "")
        if not task_id or not worker_id or not PersistentIngestionQueue().valid_source_lease(
                task_id, worker_id, source_id):
            raise PermissionError(
                f"persistent ingestion for {source_id} requires an active managed-queue lease")

    def finish(self, task_id: str, worker_id: str, *, outcome: str,
               result: dict[str, Any] | None = None, error: str = "",
               now: datetime | None = None) -> str:
        if outcome not in STATUSES - {"queued", "leased", "retry_wait", "cancelled"}:
            raise ValueError(f"invalid worker outcome: {outcome}")
        now = now or _now()
        now_text = _stamp(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM persistent_ingestion_tasks WHERE task_id=?",
                               (task_id,)).fetchone()
            if not row or row["status"] != "leased" or row["lease_owner"] != worker_id:
                raise RuntimeError("worker does not own the task lease")
            if outcome == "failed" and int(row["attempts"]) < int(row["max_attempts"]):
                delays = json.loads(row["backoff_json"])
                delay = int(delays[min(int(row["attempts"]) - 1, len(delays) - 1)]) if delays else 0
                status = "retry_wait"
                available = _stamp(now + timedelta(seconds=delay))
            else:
                status = outcome
                available = row["available_at"]
            conn.execute("""UPDATE persistent_ingestion_tasks SET status=?,available_at=?,
                lease_owner='',lease_expires_at='',updated_at=?,result_json=?,last_error=?
                WHERE task_id=?""", (status, available, now_text,
                _canonical(result or {}), error[:1000], task_id))
            self._event(conn, task_id, now_text, "finished" if status != "retry_wait" else "retry_wait",
                        {"status": status, "error": error[:300]})
            return status

    def cancel(self, task_id: str, *, reason: str = "operator_cancelled") -> bool:
        now_text = _stamp(_now())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute("""UPDATE persistent_ingestion_tasks SET status='cancelled',
                lease_owner='',lease_expires_at='',updated_at=?,last_error=?
                WHERE task_id=? AND status IN ('queued','retry_wait')""",
                (now_text, reason[:300], task_id)).rowcount
            if changed:
                self._event(conn, task_id, now_text, "cancelled", {"reason": reason[:300]})
            return bool(changed)

    def record_lineage(self, task_id: str, refs: dict[str, list[str]]) -> None:
        """Attach sanitized ingestion/raw/admission/publication references."""
        safe = {key: sorted({str(value) for value in values if value})
                for key, values in refs.items()
                if key in {"runs", "checks", "raw", "admission", "published"}}
        now_text = _stamp(_now())
        with self._connect() as conn:
            changed = conn.execute("UPDATE persistent_ingestion_tasks SET lineage_refs_json=?,"
                                   "updated_at=? WHERE task_id=?",
                                   (_canonical(safe), now_text, task_id)).rowcount
            if not changed:
                raise KeyError(f"unknown queue task: {task_id}")
            self._event(conn, task_id, now_text, "lineage_attached", safe)

    def get(self, task_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        if conn is not None:
            row = conn.execute("SELECT * FROM persistent_ingestion_tasks WHERE task_id=?",
                               (task_id,)).fetchone()
            return self._decode(row) if row else None
        with self._connect() as own:
            row = own.execute("SELECT * FROM persistent_ingestion_tasks WHERE task_id=?",
                              (task_id,)).fetchone()
            return self._decode(row) if row else None

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [self._decode(row) for row in conn.execute(
                "SELECT * FROM persistent_ingestion_tasks ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 1000)),)).fetchall()]

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for column, key in (("scope_json", "scope"), ("command_json", "command"),
                            ("backoff_json", "backoff_seconds"), ("result_json", "result")):
            value[key] = json.loads(value.pop(column))
        value["lineage_refs"] = json.loads(value.pop("lineage_refs_json", "{}"))
        return value

    def run_one(self, *, worker_id: str | None = None, timeout_seconds: int = 1800,
                task_id: str = "") -> dict[str, Any] | None:
        """Claim and execute one task. Test/operation hooks may target an exact ID."""
        worker_id = worker_id or f"{os.getpid()}:{uuid.uuid4().hex[:8]}"
        task = self.claim(worker_id, task_id=task_id)
        if not task:
            return None
        tokens = task["command"]
        argv = [sys.executable, "-m", "ats.runtime.cli", *tokens[1:]]
        from .refresh import _refresh_environment

        env = _refresh_environment()
        env["ATS_PERSISTENT_QUEUE_PATH"] = str(self.path)
        env["ATS_PERSISTENT_QUEUE_TASK_ID"] = task["task_id"]
        env["ATS_PERSISTENT_QUEUE_LEASE_OWNER"] = worker_id
        env["ATS_PERSISTENT_QUEUE_SOURCE_ID"] = task["source_id"]
        reported = ""
        heartbeat_stop = threading.Event()
        lease_lost = threading.Event()
        try:
            proc = subprocess.Popen(argv, cwd=REPO_ROOT, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=env)

            def pulse_lease() -> None:
                while not heartbeat_stop.wait(max(10, self.lease_seconds // 3)):
                    if not self.heartbeat(task["task_id"], worker_id):
                        lease_lost.set()
                        try:
                            proc.terminate()
                        except OSError:
                            pass
                        return

            heartbeat_thread = threading.Thread(target=pulse_lease, daemon=True)
            heartbeat_thread.start()
            try:
                stdout, stderr = proc.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                raise
            finally:
                heartbeat_stop.set()
                heartbeat_thread.join(timeout=2)
            try:
                payload = json.loads(stdout)
                payload = payload if isinstance(payload, dict) else {"output": stdout.decode(errors="replace")}
            except (ValueError, TypeError):
                payload = {"output": stdout.decode(errors="replace")[-4000:]}
            reported = str(payload.get("status") or "")
            if proc.returncode == 0 and reported in {"succeeded", "no_change"}:
                outcome = reported
                error = ""
            elif proc.returncode == 0 and reported in {"quarantined", "partial", "pending_human_review"}:
                outcome = "partial" if reported in {"partial", "pending_human_review"} else "quarantined"
                error = reported
            else:
                outcome = "failed"
                error = f"exit={proc.returncode};status={reported or 'unknown'}"
            if lease_lost.is_set() and outcome != "failed":
                outcome, error = "failed", "worker_lease_lost"
            result = {"exit_code": proc.returncode, "status": reported,
                      "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                      "stderr_sha256": hashlib.sha256(stderr).hexdigest()}
        except subprocess.TimeoutExpired:
            outcome, error, result = "failed", "worker_timeout", {"exit_code": None}
        status = self.finish(task["task_id"], worker_id, outcome=outcome,
                             result=result, error=error)
        return {"task_id": task["task_id"], "status": status, "attempt": task["attempts"],
                "source_id": task["source_id"], "reported_status": reported,
                "stdout": payload if "proc" in locals() else {},
                "exit_code": result.get("exit_code"), "result": result, "error": error}


def require_queue_worker(source_id: str) -> None:
    PersistentIngestionQueue.require_worker_lease(source_id)


def cache_miss_policy(*, source_id: str, dataset_id: str) -> dict[str, Any] | None:
    """Return an explicitly registered cache-miss policy, if permitted."""
    from .catalog.structured import StructuredCatalog

    registry = StructuredCatalog.load().raw
    source = (registry.get("sources") or {}).get(source_id) or {}
    dataset = (registry.get("datasets") or {}).get(dataset_id) or {}
    policy = dataset.get("cache_miss_policy") or {}
    if (not policy.get("enabled") or source_id not in
            [*(dataset.get("primary_sources") or []),
              *(dataset.get("fallback_sources") or [])] or
            source.get("persistence") != "persistent"):
        return None
    return {**policy, "_source_budget": source.get("internal_request_budget") or {}}


def enqueue_cache_miss(*, source_id: str, dataset_id: str, entity: str,
                       command: Sequence[str], now: datetime | None = None
                       ) -> dict[str, Any] | None:
    """Enqueue an explicitly permitted persistent-data cache miss.

    Policy comes from the authoritative structured registry. This producer
    never executes the task; an Agent read can therefore return its current
    snapshot (or a visible gap) without synchronously calling a provider.
    """
    policy = cache_miss_policy(source_id=source_id, dataset_id=dataset_id)
    if policy is None:
        return None
    entity = entity.strip().upper()
    if not entity:
        raise ValueError("cache-miss task requires a stable entity")
    now = now or _now()
    window_hours = max(1, int(policy.get("dedupe_window_hours") or
                              policy.get("max_age_hours") or 168))
    window = int(now.timestamp()) // (window_hours * 3600)
    policy_fingerprint = hashlib.sha256(_canonical({
        "source_id": source_id,
        "dataset_id": dataset_id,
        "policy": policy,
    }).encode()).hexdigest()
    task_id, created = PersistentIngestionQueue().enqueue(
        source_id=source_id,
        scope={"dataset": dataset_id, "entity": entity},
        trigger_kind="cache_miss",
        trigger_ref=f"{dataset_id}:{entity}:window:{window}",
        command=command,
        policy_fingerprint=policy_fingerprint,
        requested_as_of=_stamp(now),
        priority=int(policy.get("priority", 80)),
        max_attempts=int(policy.get("max_attempts", 3)),
        parent_run_id="",
        now=now,
    )
    return {"task_id": task_id, "created": created, "status": "queued",
            "trigger_ref": f"{dataset_id}:{entity}:window:{window}"}


def run_worker(*, once: bool = False, max_tasks: int = 25, timeout_seconds: int = 1800,
               path: str | Path | None = None) -> list[dict[str, Any]]:
    queue = PersistentIngestionQueue(path)
    results: list[dict[str, Any]] = []
    for _ in range(1 if once else max(1, max_tasks)):
        item = queue.run_one(timeout_seconds=timeout_seconds)
        if item is None:
            break
        results.append(item)
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Managed persistent-ingestion queue")
    sub = parser.add_subparsers(dest="action", required=True)
    enqueue = sub.add_parser("enqueue", help="enqueue a persistent ingestion command")
    enqueue.add_argument("--source", required=True)
    enqueue.add_argument("--scope-json", default="{}")
    enqueue.add_argument("--trigger", choices=sorted(TRIGGERS), required=True)
    enqueue.add_argument("--trigger-ref", required=True)
    enqueue.add_argument("--policy-fingerprint", required=True)
    enqueue.add_argument("--priority", type=int, default=50)
    enqueue.add_argument("command", nargs=argparse.REMAINDER)
    worker = sub.add_parser("worker", help="process queued persistent ingestion")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--max-tasks", type=int, default=25)
    worker.add_argument("--timeout", type=int, default=1800)
    status = sub.add_parser("status", help="show queue metadata")
    status.add_argument("--limit", type=int, default=100)
    cancel = sub.add_parser("cancel", help="cancel a queued task")
    cancel.add_argument("task_id")
    cancel.add_argument("--reason", default="operator_cancelled")
    args = parser.parse_args(argv)
    queue = PersistentIngestionQueue()
    if args.action == "enqueue":
        try:
            scope = json.loads(args.scope_json)
            if not isinstance(scope, dict):
                raise ValueError("scope must be a JSON object")
            command = list(args.command)
            if command and command[0] == "--":
                command = command[1:]
            if command and command[0] == "ats":
                command[0] = "ats"
            task_id, created = queue.enqueue(
                source_id=args.source, scope=scope, trigger_kind=args.trigger,
                trigger_ref=args.trigger_ref, command=command,
                policy_fingerprint=args.policy_fingerprint, priority=args.priority)
        except (ValueError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
        print(json.dumps({"task_id": task_id, "created": created, "status": "queued"}))
        return 0
    if args.action == "worker":
        result = run_worker(once=args.once, max_tasks=args.max_tasks,
                            timeout_seconds=args.timeout)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 1 if any(item["status"] in {"failed", "retry_wait"} for item in result) else 0
    if args.action == "status":
        print(json.dumps(queue.list(limit=args.limit), ensure_ascii=False, default=str))
        return 0
    cancelled = queue.cancel(args.task_id, reason=args.reason)
    print(json.dumps({"task_id": args.task_id, "cancelled": cancelled}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["PersistentIngestionQueue", "PersistentIngestionTask", "run_worker",
           "require_queue_worker", "STATUSES"]
