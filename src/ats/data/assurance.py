"""Append-only dataflow verification evidence and fail-closed read qualification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

import yaml

from .runtime.repository import platform_data_db_path

_COVERAGE = Path(__file__).resolve().parents[3] / "config/data/target_dataflow_coverage.yaml"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS dataflow_assurance_events (
  event_id TEXT PRIMARY KEY, event_kind TEXT NOT NULL, domain_id TEXT NOT NULL,
  consumer_id TEXT NOT NULL, contract_version TEXT NOT NULL, evidence_type TEXT NOT NULL,
  outcome TEXT NOT NULL, scope_json TEXT NOT NULL, manifest_hash TEXT NOT NULL,
  manifest_version TEXT NOT NULL, dependencies_json TEXT NOT NULL,
  as_of TEXT NOT NULL, created_at TEXT NOT NULL,
  max_age_days INTEGER NOT NULL, prerequisites_json TEXT NOT NULL,
  command_summary TEXT NOT NULL, environment_names_json TEXT NOT NULL,
  result_summary TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  revokes_event_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dataflow_assurance_scope
  ON dataflow_assurance_events(domain_id,consumer_id,contract_version,created_at);
CREATE TRIGGER IF NOT EXISTS dataflow_assurance_no_update
  BEFORE UPDATE ON dataflow_assurance_events BEGIN
    SELECT RAISE(ABORT, 'assurance events are append-only');
  END;
CREATE TRIGGER IF NOT EXISTS dataflow_assurance_no_delete
  BEFORE DELETE ON dataflow_assurance_events BEGIN
    SELECT RAISE(ABORT, 'assurance events are append-only');
  END;
"""


def _manifest(path: str | Path = _COVERAGE) -> tuple[dict[str, Any], str]:
    target = Path(path).resolve()
    raw_bytes = target.read_bytes()
    return yaml.safe_load(raw_bytes) or {}, hashlib.sha256(raw_bytes).hexdigest()


def _consumer(consumer_id: str, path: str | Path = _COVERAGE) -> tuple[dict, str, str]:
    manifest, digest = _manifest(path)
    row = next((item for item in manifest.get("consumers", [])
                if item.get("id") == consumer_id), None)
    if row is None:
        raise ValueError(f"unknown target dataflow consumer: {consumer_id}")
    if not row.get("contract_version") or not row.get("required_evidence"):
        raise ValueError(f"consumer {consumer_id} lacks qualification contract")
    return row, digest, str(manifest.get("schema_version", ""))


def _dependencies(paths: list[str] | None) -> dict[str, str]:
    from ..config import REPO_ROOT

    result = {}
    for raw in paths or []:
        path = Path(raw)
        path = (path if path.is_absolute() else REPO_ROOT / path).resolve()
        if not path.is_file():
            raise ValueError(f"fingerprint dependency is not a file: {path.name}")
        try:
            label = path.relative_to(REPO_ROOT.resolve()).as_posix()
        except ValueError:
            raise ValueError("fingerprint paths must be inside the repository")
        result[label] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _clean_summary(value: str, *, limit: int = 1000) -> str:
    text = re.sub(r"(?i)(api[_-]?key|token|password|secret|account)\s*[=:]\s*[^\s,;]+",
                  r"\1=[REDACTED]", str(value or ""))
    return text[:limit]


def _connect(path: str | Path, *, writable: bool) -> sqlite3.Connection:
    target = Path(path)
    if writable:
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(target, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript(_SCHEMA)
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(dataflow_assurance_events)")}
        if "manifest_version" not in columns:
            conn.execute("ALTER TABLE dataflow_assurance_events ADD COLUMN "
                         "manifest_version TEXT NOT NULL DEFAULT ''")
    else:
        if not target.is_file():
            raise FileNotFoundError(target)
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def record_evidence(*, domain_id: str, consumer_id: str, evidence_type: str,
                    outcome: str, scope: dict[str, Any], as_of: datetime,
                    command_summary: str, result_summary: str,
                    environment_names: list[str] | None = None,
                    prerequisite_event_ids: list[str] | None = None,
                    dependency_paths: list[str] | None = None,
                    max_age_days: int | None = None,
                    db_path: str | Path | None = None,
                    coverage_path: str | Path = _COVERAGE) -> str:
    """Append one sanitized evidence event. This never changes consumer routing."""
    if outcome not in {"passed", "failed"}:
        raise ValueError("outcome must be passed or failed")
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if not isinstance(scope, dict) or not scope:
        raise ValueError("scope must be a non-empty object")
    if not command_summary.strip() or not result_summary.strip():
        raise ValueError("sanitized command_summary and result_summary are required")
    consumer, manifest_hash, manifest_version = _consumer(consumer_id, coverage_path)
    if domain_id != str(consumer.get("domain")):
        raise ValueError("domain_id does not match the target consumer contract")
    required = set(consumer["required_evidence"])
    if evidence_type not in required:
        raise ValueError(f"evidence_type is not required by {consumer_id}: {evidence_type}")
    ttl = int(max_age_days or consumer.get("evidence_ttl_days") or 90)
    if ttl <= 0:
        raise ValueError("max_age_days must be positive")
    if not dependency_paths:
        raise ValueError("at least one code/config fingerprint path is required")
    dependencies = _dependencies(dependency_paths)
    created = datetime.now(timezone.utc)
    identity = {
        "created_at": created.isoformat(), "domain": domain_id, "consumer": consumer_id,
        "contract": consumer["contract_version"], "type": evidence_type,
        "outcome": outcome, "scope": scope, "as_of": as_of.isoformat(),
        "dependencies": dependencies, "result": result_summary,
    }
    event_id = hashlib.sha256(json.dumps(identity, sort_keys=True,
                                         default=str).encode()).hexdigest()[:32]
    with _connect(db_path or platform_data_db_path(), writable=True) as conn:
        conn.execute(
            "INSERT INTO dataflow_assurance_events (event_id,event_kind,domain_id,consumer_id,"
            "contract_version,evidence_type,outcome,scope_json,manifest_hash,manifest_version,"
            "dependencies_json,as_of,created_at,max_age_days,prerequisites_json,command_summary,"
            "environment_names_json,result_summary,reason,revokes_event_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, "evidence", domain_id, consumer_id, consumer["contract_version"],
             evidence_type, outcome, json.dumps(scope, sort_keys=True), manifest_hash,
             manifest_version, json.dumps(dependencies, sort_keys=True),
             as_of.astimezone(timezone.utc).isoformat(),
             created.isoformat(), ttl,
             json.dumps(prerequisite_event_ids or []), _clean_summary(command_summary),
             json.dumps(sorted(set(environment_names or []))),
             _clean_summary(result_summary), "", ""))
    return event_id


def revoke_evidence(event_id: str, *, reason: str, db_path: str | Path | None = None,
                    coverage_path: str | Path = _COVERAGE) -> str:
    """Append a revocation event; original evidence remains available for audit."""
    with _connect(db_path or platform_data_db_path(), writable=False) as conn:
        row = conn.execute("SELECT * FROM dataflow_assurance_events WHERE event_id=?",
                           (event_id,)).fetchone()
    if row is None or row["event_kind"] != "evidence":
        raise ValueError("evidence event not found")
    created = datetime.now(timezone.utc)
    revocation_id = hashlib.sha256(
        f"revoke|{event_id}|{created.isoformat()}|{reason}".encode()).hexdigest()[:32]
    with _connect(db_path or platform_data_db_path(), writable=True) as conn:
        conn.execute(
            "INSERT INTO dataflow_assurance_events (event_id,event_kind,domain_id,consumer_id,"
            "contract_version,evidence_type,outcome,scope_json,manifest_hash,manifest_version,"
            "dependencies_json,as_of,created_at,max_age_days,prerequisites_json,command_summary,"
            "environment_names_json,result_summary,reason,revokes_event_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (revocation_id, "revoke", row["domain_id"], row["consumer_id"],
             row["contract_version"], row["evidence_type"], "revoked", row["scope_json"],
             row["manifest_hash"], row["manifest_version"], row["dependencies_json"],
             row["as_of"], created.isoformat(),
             row["max_age_days"], "[]", "", "[]", "", _clean_summary(reason), event_id))
    return revocation_id


def qualification(*, domain_id: str, consumer_id: str, contract_version: str,
                  scope: dict[str, Any], now: datetime | None = None,
                  db_path: str | Path | None = None,
                  coverage_path: str | Path = _COVERAGE) -> dict[str, Any]:
    """Read-only qualification. Any missing, stale, revoked or drifted evidence fails closed."""
    consumer, manifest_hash, manifest_version = _consumer(consumer_id, coverage_path)
    if domain_id != str(consumer.get("domain")):
        raise ValueError("domain_id does not match the target consumer contract")
    if contract_version != str(consumer["contract_version"]):
        raise ValueError("contract_version does not match the target consumer contract")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expected_scope = json.dumps(scope, sort_keys=True)
    path = db_path or platform_data_db_path()
    try:
        conn = _connect(path, writable=False)
    except FileNotFoundError:
        return {"status": "ineligible", "consumer_id": consumer_id,
                "domain_id": domain_id, "missing": list(consumer["required_evidence"]),
                "reasons": ["assurance_ledger_missing"]}
    columns = conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                           "AND name='dataflow_assurance_events'").fetchone()
    if not columns:
        conn.close()
        return {"status": "ineligible", "consumer_id": consumer_id,
                "domain_id": domain_id, "missing": list(consumer["required_evidence"]),
                "reasons": ["assurance_ledger_missing"]}
    rows = conn.execute(
        "SELECT * FROM dataflow_assurance_events WHERE event_kind IN ('evidence','revoke') "
        "AND domain_id=? AND consumer_id=? AND contract_version=? AND scope_json=? "
        "ORDER BY created_at,event_id",
        (domain_id, consumer_id, contract_version, expected_scope)).fetchall()
    conn.close()
    events = [dict(row) for row in rows]
    revoked = {row["revokes_event_id"] for row in events if row["event_kind"] == "revoke"}
    reasons: list[str] = []
    accepted: dict[str, dict] = {}
    for evidence_type in consumer["required_evidence"]:
        latest = next((row for row in reversed(events)
                       if row["event_kind"] == "evidence" and
                       row["evidence_type"] == evidence_type), None)
        if latest is None:
            continue
        if latest["event_id"] in revoked:
            reasons.append(f"revoked:{evidence_type}")
            continue
        if latest["outcome"] != "passed":
            reasons.append(f"failed:{evidence_type}")
            continue
        age = now - datetime.fromisoformat(latest["as_of"])
        if age < timedelta(0) or age > timedelta(days=int(latest["max_age_days"])):
            reasons.append(f"stale:{evidence_type}")
            continue
        if latest["manifest_hash"] != manifest_hash:
            reasons.append(f"manifest_drift:{evidence_type}")
            continue
        dependencies = json.loads(latest["dependencies_json"] or "{}")
        from ..config import REPO_ROOT
        drift = []
        for relative, digest in dependencies.items():
            target = (REPO_ROOT / relative).resolve()
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                drift.append(relative)
        if drift:
            reasons.extend(f"dependency_drift:{item}" for item in drift)
            continue
        accepted[evidence_type] = latest
    missing = [kind for kind in consumer["required_evidence"] if kind not in accepted]
    if missing and not reasons:
        reasons.append("required_evidence_missing")
    return {
        "status": "eligible" if not missing and not reasons else "ineligible",
        "domain_id": domain_id, "consumer_id": consumer_id,
        "contract_version": contract_version, "manifest_version": manifest_version,
        "manifest_hash": manifest_hash, "scope": scope, "missing": missing,
        "evidence": {key: {"event_id": row["event_id"], "as_of": row["as_of"]}
                     for key, row in accepted.items()},
        "reasons": sorted(set(reasons)), "read_only": True,
    }


def evidence_history(*, domain_id: str, consumer_id: str,
                    scope: dict[str, Any] | None = None,
                    db_path: str | Path | None = None,
                    limit: int = 100) -> list[dict[str, Any]]:
    """Return a bounded sanitized event view suitable for versioned reports."""
    path = db_path or platform_data_db_path()
    try:
        conn = _connect(path, writable=False)
    except FileNotFoundError:
        return []
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                          "AND name='dataflow_assurance_events'").fetchone()
    if not exists:
        conn.close()
        return []
    sql = ("SELECT event_id,event_kind,domain_id,consumer_id,contract_version,evidence_type,"
           "outcome,scope_json,manifest_hash,manifest_version,dependencies_json,as_of,created_at,max_age_days,"
           "prerequisites_json,command_summary,environment_names_json,result_summary,reason,"
           "revokes_event_id FROM dataflow_assurance_events WHERE domain_id=? AND consumer_id=?")
    args: list[Any] = [domain_id, consumer_id]
    if scope is not None:
        sql += " AND scope_json=?"
        args.append(json.dumps(scope, sort_keys=True))
    sql += " ORDER BY created_at DESC,event_id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 1000)))
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(row) for row in rows]


__all__ = ["evidence_history", "qualification", "record_evidence", "revoke_evidence"]
