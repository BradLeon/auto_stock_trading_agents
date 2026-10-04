"""Append-only dataflow verification evidence and fail-closed read qualification."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
  revokes_event_id TEXT NOT NULL DEFAULT '', details_json TEXT NOT NULL DEFAULT '{}'
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
    row = next(
        (item for item in manifest.get("consumers", []) if item.get("id") == consumer_id), None
    )
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
    text = re.sub(
        r"(?i)(api[_-]?key|token|password|secret|account)\s*[=:]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        str(value or ""),
    )
    return text[:limit]


def _evidence_details(details: dict[str, Any]) -> dict[str, Any]:
    """Store only bounded machine proof summaries, never source bodies or accounts."""
    if not isinstance(details, dict) or set(details) - {"inputs", "rollback"}:
        raise ValueError("details supports only inputs and rollback")
    result: dict[str, Any] = {}
    for name in details:
        if not isinstance(details[name], list) or len(details[name]) > 100:
            raise ValueError(f"details.{name} must be a bounded list")
        result[name] = []
        allowed = (
            {
                "input_id",
                "source_status",
                "reason",
                "stage",
                "checked_at",
                "run_id",
                "task_id",
                "input_refs",
                "checks",
            }
            if name == "inputs"
            else {
                "product",
                "route",
                "status",
                "read_only",
                "input_refs",
                "payload_hash",
                "scope_hash",
                "native_packet_equal",
                "reason",
                "as_of",
            }
        )
        for row in details[name]:
            if not isinstance(row, dict) or set(row) - allowed:
                raise ValueError(f"unknown details.{name} fields")
            cleaned = {}
            for key, value in row.items():
                if key in {"reason", "stage"}:
                    cleaned[key] = _clean_summary(str(value))
                elif key == "checks":
                    if not isinstance(value, dict) or any(
                        not re.fullmatch(r"[a-z_]+", str(k)) or type(v) is not bool
                        for k, v in value.items()
                    ):
                        raise ValueError("input checks must be named boolean results")
                    cleaned[key] = value
                elif key == "input_refs":
                    if (
                        not isinstance(value, list)
                        or len(value) > 200
                        or any(
                            not isinstance(v, str)
                            or not re.fullmatch(r"[A-Za-z0-9_.:@/+-]{1,200}", v)
                            for v in value
                        )
                    ):
                        raise ValueError("input_refs must contain bounded opaque references")
                    cleaned[key] = value
                elif isinstance(value, str):
                    if len(value) > 200 or not re.fullmatch(r"[A-Za-z0-9_.:@/+-]*", value):
                        raise ValueError(f"invalid proof identifier: {key}")
                    cleaned[key] = value
                elif type(value) is bool:
                    cleaned[key] = value
                else:
                    raise ValueError(f"invalid proof value: {key}")
            result[name].append(cleaned)
    return result


def _invalid_event(
    row,
    *,
    event_map,
    revoked,
    now,
    manifest_hash,
    stack,
    required_paths=(),
    consumer=None,
    policy=None,
) -> list[str]:
    kind = row["evidence_type"]
    identity = row["event_id"]
    if identity in stack:
        return [f"prerequisite_cycle:{kind}"]
    if identity in revoked:
        return [f"revoked:{kind}"]
    if row["outcome"] != "passed":
        return [f"failed:{kind}"]
    try:
        stamp = datetime.fromisoformat(row["as_of"])
        created = datetime.fromisoformat(row["created_at"])
        if stamp.tzinfo is None or created.tzinfo is None:
            return [f"invalid_timestamp:{kind}"]
        age = now - stamp
        if created > now or age < timedelta(0) or age > timedelta(days=int(row["max_age_days"])):
            return [f"stale:{kind}"]
        if row["manifest_hash"] != manifest_hash:
            return [f"manifest_drift:{kind}"]
        from ..config import REPO_ROOT

        dependencies = json.loads(row["dependencies_json"] or "{}")
        if not dependencies:
            return [f"fingerprints_missing:{kind}"]
        if set(required_paths) - set(dependencies):
            return [f"fingerprints_incomplete:{kind}"]
        reasons = []
        details = _evidence_details(json.loads(row.get("details_json") or "{}"))
        if consumer is not None and policy is not None:
            if kind in {"fallback", "rollback"}:
                reasons.extend(
                    _rollback_failures(
                        consumer, details, policy, now=now, scope=json.loads(row["scope_json"])
                    )
                )
            if kind == "completeness":
                failures, _ = _input_failures(consumer["id"], details, policy, now=now)
                reasons.extend(failures)
        for relative, digest in dependencies.items():
            target = (REPO_ROOT / relative).resolve()
            if (
                not target.is_relative_to(REPO_ROOT.resolve())
                or not target.is_file()
                or hashlib.sha256(target.read_bytes()).hexdigest() != digest
            ):
                reasons.append(f"dependency_drift:{relative}")
        for ref in json.loads(row["prerequisites_json"] or "[]"):
            prerequisite = event_map.get(ref)
            if prerequisite is None or prerequisite["created_at"] > row["created_at"]:
                reasons.append(f"prerequisite_missing:{kind}")
            else:
                reasons.extend(
                    f"prerequisite:{error}"
                    for error in _invalid_event(
                        prerequisite,
                        event_map=event_map,
                        revoked=revoked,
                        now=now,
                        manifest_hash=manifest_hash,
                        stack=stack | {identity},
                        required_paths=required_paths,
                        consumer=consumer,
                        policy=policy,
                    )
                )
        return reasons
    except (TypeError, ValueError, KeyError):
        return [f"invalid_evidence:{kind}"]


def _rollback_failures(consumer, details, policy, *, now, scope) -> list[str]:
    routes = policy.get("rollback_routes", {})
    if not routes:
        return ["rollback_policy_missing"]
    rows = details.get("rollback", [])
    failures = []
    for product in consumer["products"]:
        matches = [row for row in rows if row.get("product") == product]
        if len(matches) != 1:
            failures.append(f"rollback_unverified:{product}")
            continue
        row = matches[0]
        product_scope = (scope.get("products") or {}).get(product)
        if product_scope is None and len(consumer["products"]) == 1:
            product_scope = scope
        expected_scope_hash = hashlib.sha256(
            json.dumps(product_scope, sort_keys=True).encode()
        ).hexdigest()
        try:
            stamp = datetime.fromisoformat(row.get("as_of", ""))
            valid_stamp = stamp.tzinfo is not None and stamp <= now
        except ValueError:
            valid_stamp = False
        if (
            row.get("route") != routes.get(product)
            or row.get("status") != "passed"
            or row.get("read_only") is not True
            or row.get("native_packet_equal") is not True
            or not valid_stamp
            or product_scope is None
            or row.get("scope_hash") != expected_scope_hash
            or not row.get("input_refs")
            or not re.fullmatch(r"[a-f0-9]{64}", row.get("payload_hash", ""))
        ):
            failures.append(f"rollback_unverified:{product}")
    return failures


def _input_failures(consumer_id, details, policy, *, now) -> tuple[list[str], list[dict]]:
    rows = details.get("inputs", [])
    required = set(policy.get("required_inputs", {}).get(consumer_id, []))
    required.update(policy.get("required_status_inputs", {}).get(consumer_id, []))
    failures, gaps = [], []
    ids = [row.get("input_id") for row in rows]
    if len(ids) != len(set(ids)):
        failures.append("duplicate_input_status")
    for missing in required - set(ids):
        failures.append(f"input_status_missing:{missing}")
    for row in rows:
        identity = row.get("input_id", "")
        try:
            stamp = datetime.fromisoformat(row.get("checked_at", ""))
            if stamp.tzinfo is None or stamp > now:
                raise ValueError("naive")
        except ValueError:
            failures.append(f"input_status_timestamp_missing:{identity}")
        if not row.get("run_id") and not row.get("task_id"):
            failures.append(f"input_status_run_missing:{identity}")
        if row.get("source_status") in {"complete", "succeeded", "no_change"}:
            if not row.get("input_refs"):
                failures.append(f"input_lineage_missing:{identity}")
            continue
        exception = policy.get("optional_inputs", {}).get(identity, {})
        if (
            identity == "sec_edgar_filing_body"
            and exception.get("optional") is True
            and exception.get("blocking") is False
            and row.get("source_status")
            in {"failed", "unavailable", "no_coverage", "quarantined", "partial"}
            and row.get("reason")
            and row.get("stage")
            and all(
                row.get("checks", {}).get(check) is True
                for check in exception.get("required_checks", [])
            )
        ):
            gaps.append(
                {
                    **row,
                    "optional": True,
                    "blocking": False,
                    "policy_version": exception["policy_version"],
                    "source_verified": False,
                }
            )
        else:
            failures.append(f"input_failed:{identity}")
    return failures, gaps


def _connect(path: str | Path, *, writable: bool) -> sqlite3.Connection:
    target = Path(path)
    if writable:
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(target, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript(_SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(dataflow_assurance_events)")}
        if "manifest_version" not in columns:
            conn.execute(
                "ALTER TABLE dataflow_assurance_events ADD COLUMN "
                "manifest_version TEXT NOT NULL DEFAULT ''"
            )
        if "details_json" not in columns:
            conn.execute(
                "ALTER TABLE dataflow_assurance_events ADD COLUMN "
                "details_json TEXT NOT NULL DEFAULT '{}'"
            )
    else:
        if not target.is_file():
            raise FileNotFoundError(target)
        conn = sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def record_evidence(
    *,
    domain_id: str,
    consumer_id: str,
    evidence_type: str,
    outcome: str,
    scope: dict[str, Any],
    as_of: datetime,
    command_summary: str,
    result_summary: str,
    environment_names: list[str] | None = None,
    prerequisite_event_ids: list[str] | None = None,
    dependency_paths: list[str] | None = None,
    max_age_days: int | None = None,
    details: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
    coverage_path: str | Path = _COVERAGE,
) -> str:
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
    ttl = int(max_age_days if max_age_days is not None else consumer.get("evidence_ttl_days") or 90)
    if ttl <= 0:
        raise ValueError("max_age_days must be positive")
    if not dependency_paths:
        raise ValueError("at least one code/config fingerprint path is required")
    dependencies = _dependencies(dependency_paths)
    details = _evidence_details(details or {})
    names = sorted(set(environment_names or []))
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names):
        raise ValueError("environment_names must contain names, never values")
    created = datetime.now(timezone.utc)
    identity = {
        "created_at": created.isoformat(),
        "domain": domain_id,
        "consumer": consumer_id,
        "contract": consumer["contract_version"],
        "type": evidence_type,
        "outcome": outcome,
        "scope": scope,
        "as_of": as_of.isoformat(),
        "dependencies": dependencies,
        "result": result_summary,
        "details": details,
    }
    event_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, default=str).encode()
    ).hexdigest()[:32]
    with _connect(db_path or platform_data_db_path(), writable=True) as conn:
        for prerequisite in prerequisite_event_ids or []:
            row = conn.execute(
                "SELECT * FROM dataflow_assurance_events WHERE event_id=?", (prerequisite,)
            ).fetchone()
            if (
                row is None
                or row["event_kind"] != "evidence"
                or any(
                    row[key] != value
                    for key, value in (
                        ("domain_id", domain_id),
                        ("consumer_id", consumer_id),
                        ("contract_version", consumer["contract_version"]),
                        ("scope_json", json.dumps(scope, sort_keys=True)),
                    )
                )
            ):
                raise ValueError("prerequisite must reference evidence in the exact consumer scope")
        conn.execute(
            "INSERT INTO dataflow_assurance_events (event_id,event_kind,domain_id,consumer_id,"
            "contract_version,evidence_type,outcome,scope_json,manifest_hash,manifest_version,"
            "dependencies_json,as_of,created_at,max_age_days,prerequisites_json,command_summary,"
            "environment_names_json,result_summary,reason,revokes_event_id,details_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                "evidence",
                domain_id,
                consumer_id,
                consumer["contract_version"],
                evidence_type,
                outcome,
                json.dumps(scope, sort_keys=True),
                manifest_hash,
                manifest_version,
                json.dumps(dependencies, sort_keys=True),
                as_of.astimezone(timezone.utc).isoformat(),
                created.isoformat(),
                ttl,
                json.dumps(prerequisite_event_ids or []),
                _clean_summary(command_summary),
                json.dumps(names),
                _clean_summary(result_summary),
                "",
                "",
                json.dumps(details, sort_keys=True),
            ),
        )
    return event_id


def revoke_evidence(
    event_id: str,
    *,
    reason: str,
    db_path: str | Path | None = None,
    coverage_path: str | Path = _COVERAGE,
) -> str:
    """Append a revocation event; original evidence remains available for audit."""
    if not reason.strip():
        raise ValueError("revocation requires a reason")
    with _connect(db_path or platform_data_db_path(), writable=False) as conn:
        row = conn.execute(
            "SELECT * FROM dataflow_assurance_events WHERE event_id=?", (event_id,)
        ).fetchone()
    if row is None or row["event_kind"] != "evidence":
        raise ValueError("evidence event not found")
    created = datetime.now(timezone.utc)
    revocation_id = hashlib.sha256(
        f"revoke|{event_id}|{created.isoformat()}|{reason}".encode()
    ).hexdigest()[:32]
    with _connect(db_path or platform_data_db_path(), writable=True) as conn:
        conn.execute(
            "INSERT INTO dataflow_assurance_events (event_id,event_kind,domain_id,consumer_id,"
            "contract_version,evidence_type,outcome,scope_json,manifest_hash,manifest_version,"
            "dependencies_json,as_of,created_at,max_age_days,prerequisites_json,command_summary,"
            "environment_names_json,result_summary,reason,revokes_event_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                revocation_id,
                "revoke",
                row["domain_id"],
                row["consumer_id"],
                row["contract_version"],
                row["evidence_type"],
                "revoked",
                row["scope_json"],
                row["manifest_hash"],
                row["manifest_version"],
                row["dependencies_json"],
                row["as_of"],
                created.isoformat(),
                row["max_age_days"],
                "[]",
                "",
                "[]",
                "",
                _clean_summary(reason),
                event_id,
            ),
        )
    return revocation_id


def qualification(
    *,
    domain_id: str,
    consumer_id: str,
    contract_version: str,
    scope: dict[str, Any],
    now: datetime | None = None,
    db_path: str | Path | None = None,
    coverage_path: str | Path = _COVERAGE,
) -> dict[str, Any]:
    """Read-only qualification. Any missing, stale, revoked or drifted evidence fails closed."""
    consumer, manifest_hash, manifest_version = _consumer(consumer_id, coverage_path)
    if domain_id != str(consumer.get("domain")):
        raise ValueError("domain_id does not match the target consumer contract")
    if contract_version != str(consumer["contract_version"]):
        raise ValueError("contract_version does not match the target consumer contract")
    if not isinstance(scope, dict) or not scope:
        raise ValueError("scope must be a non-empty object")
    if now is not None and now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expected_scope = json.dumps(scope, sort_keys=True)
    path = db_path or platform_data_db_path()
    try:
        conn = _connect(path, writable=False)
    except FileNotFoundError:
        return {
            "status": "ineligible",
            "consumer_id": consumer_id,
            "domain_id": domain_id,
            "missing": list(consumer["required_evidence"]),
            "reasons": ["assurance_ledger_missing"],
        }
    columns = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='dataflow_assurance_events'"
    ).fetchone()
    if not columns:
        conn.close()
        return {
            "status": "ineligible",
            "consumer_id": consumer_id,
            "domain_id": domain_id,
            "missing": list(consumer["required_evidence"]),
            "reasons": ["assurance_ledger_missing"],
        }
    rows = conn.execute(
        "SELECT * FROM dataflow_assurance_events WHERE event_kind IN ('evidence','revoke') "
        "AND domain_id=? AND consumer_id=? AND contract_version=? AND scope_json=? "
        "ORDER BY created_at,event_id",
        (domain_id, consumer_id, contract_version, expected_scope),
    ).fetchall()
    conn.close()
    events = [dict(row) for row in rows]
    revoked = {row["revokes_event_id"] for row in events if row["event_kind"] == "revoke"}
    reasons: list[str] = []
    accepted: dict[str, dict] = {}
    event_map = {row["event_id"]: row for row in events if row["event_kind"] == "evidence"}
    manifest, current_hash = _manifest(coverage_path)
    policy = manifest.get("qualification_policy", {})
    if current_hash != manifest_hash:
        reasons.append("manifest_changed_during_query")
    optional = policy.get("optional_inputs", {})
    sec_policy = optional.get("sec_edgar_filing_body", {})
    if (
        set(optional) != {"sec_edgar_filing_body"}
        or set(sec_policy.get("required_checks", []))
        != {"empty_input_safe", "error_visible", "no_risk_inference", "invalid_material_rejected"}
        or set(policy.get("required_inputs", {}).get("fundamental", []))
        != {"company_financials", "defeatbeta_sec_filing_index", "defeatbeta_earnings_transcript"}
        or policy.get("required_status_inputs", {}).get("fundamental") != ["sec_edgar_filing_body"]
    ):
        reasons.append("qualification_policy_invalid")
    gaps: list[dict] = []
    for evidence_type in consumer["required_evidence"]:
        latest = next(
            (
                row
                for row in reversed(events)
                if row["event_kind"] == "evidence" and row["evidence_type"] == evidence_type
            ),
            None,
        )
        if latest is None:
            continue
        invalid = _invalid_event(
            latest,
            event_map=event_map,
            revoked=revoked,
            now=now,
            manifest_hash=manifest_hash,
            stack=set(),
            required_paths=(
                policy.get("required_fingerprint_paths", [])
                + policy.get("consumer_fingerprint_paths", {}).get(consumer_id, [])
            ),
            consumer=consumer,
            policy=policy,
        )
        if invalid:
            reasons.extend(invalid)
            continue
        details = json.loads(latest.get("details_json") or "{}")
        if evidence_type in {"rollback", "fallback"}:
            failures = _rollback_failures(consumer, details, policy, now=now, scope=scope)
            if failures:
                reasons.extend(failures)
                continue
        if evidence_type == "completeness":
            failures, input_gaps = _input_failures(consumer_id, details, policy, now=now)
            gaps.extend(input_gaps)
            if failures:
                reasons.extend(failures)
                continue
        accepted[evidence_type] = latest
    missing = [kind for kind in consumer["required_evidence"] if kind not in accepted]
    if missing and not reasons:
        reasons.append("required_evidence_missing")
    return {
        "status": "eligible" if not missing and not reasons else "ineligible",
        "domain_id": domain_id,
        "consumer_id": consumer_id,
        "contract_version": contract_version,
        "manifest_version": manifest_version,
        "manifest_hash": manifest_hash,
        "scope": scope,
        "missing": missing,
        "evidence": {
            key: {"event_id": row["event_id"], "as_of": row["as_of"]}
            for key, row in accepted.items()
        },
        "reasons": sorted(set(reasons)),
        "read_only": True,
        "qualification_policy_version": policy.get("version", "legacy"),
        "accepted_nonblocking_gaps": gaps,
    }


def evidence_history(
    *,
    domain_id: str,
    consumer_id: str,
    scope: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return a bounded sanitized event view suitable for versioned reports."""
    path = db_path or platform_data_db_path()
    try:
        conn = _connect(path, writable=False)
    except FileNotFoundError:
        return []
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dataflow_assurance_events'"
    ).fetchone()
    if not exists:
        conn.close()
        return []
    sql = (
        "SELECT event_id,event_kind,domain_id,consumer_id,contract_version,evidence_type,"
        "outcome,scope_json,manifest_hash,manifest_version,dependencies_json,as_of,created_at,max_age_days,"
        "prerequisites_json,command_summary,environment_names_json,result_summary,reason,"
        "revokes_event_id FROM dataflow_assurance_events WHERE domain_id=? AND consumer_id=?"
    )
    args: list[Any] = [domain_id, consumer_id]
    if scope is not None:
        sql += " AND scope_json=?"
        args.append(json.dumps(scope, sort_keys=True))
    sql += " ORDER BY created_at DESC,event_id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 1000)))
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    result = [dict(row) for row in rows]
    # Older ledgers remain readable; absent v2 details cannot grant new qualification.
    with _connect(path, writable=False) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(dataflow_assurance_events)")}
        if "details_json" in columns:
            for row in result:
                row["details"] = json.loads(
                    conn.execute(
                        "SELECT details_json FROM dataflow_assurance_events WHERE event_id=?",
                        (row["event_id"],),
                    ).fetchone()[0]
                )
    return result


__all__ = ["evidence_history", "qualification", "record_evidence", "revoke_evidence"]
