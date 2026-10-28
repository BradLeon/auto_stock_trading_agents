"""Explicit deployment-to-invocation mappings; no grants or fuzzy scope lookup."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .cutover_routing import RouteUnavailable
from .scoped_routes import RouteIdentity, canonical_json


def validate_policy(scope, consumer=None):
    """Validate declarations before they are hash-bound into a deployment."""
    purposes = {"layer": {"layer-review"}, "information": {"information-brief"},
        "sector": {"sector-review"}, "fundamental": {"fundamental-routine", "fundamental-event"},
        "macro": {"macro-review"}, "technical": {"technical-review"},
        "chief": {"chief"}, "risk": {"risk"}, "trader": {"trader"}, "clerk": {"clerk"}}
    if consumer and "deployment_purpose" in scope and scope["deployment_purpose"] not in purposes.get(consumer, set()):
        raise ValueError("deployment purpose does not belong to consumer")
    rule = scope.get("invocation_rule")
    if rule is not None and rule != {"kind": "decision", "id_source": "cycle_id", "entities": "subset"}:
        raise ValueError("unsupported invocation rule")
    if rule is not None and scope.get("deployment_purpose") not in {"risk", "trader"}:
        raise ValueError("decision invocation rule is limited to Risk/Trader")
    event = scope.get("event_rule")
    if event is not None and (not isinstance(event, dict) or set(event) != {"route_kind", "config_sha256"}
            or not isinstance(event["route_kind"], str) or not event["route_kind"]
            or not isinstance(event["config_sha256"], str) or len(event["config_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in event["config_sha256"])):
        raise ValueError("explicit versioned Event rule required")
    if event is not None and (scope.get("event_id") or scope.get("event_version")):
        raise ValueError("fixed Event/version cannot be reinterpreted as an Event rule")
    if (rule is not None or event is not None) and scope.get("time_policy") != "per_invocation":
        raise ValueError("invocation rules require a stable per-invocation scope")
    if "deployment_purpose" in scope and (not isinstance(scope["deployment_purpose"], str) or not scope["deployment_purpose"].strip()):
        raise ValueError("deployment purpose required")


def matches(actual, deployment, *, purpose):
    if any(getattr(actual, k) != getattr(deployment, k) for k in
           ("domain_id", "consumer_id", "contract_version")):
        return False
    a, d = actual.scope, deployment.scope
    if "deployment_purpose" in d and d["deployment_purpose"] != purpose:
        return False
    # Only declared transformations are removed. Every remaining field is exact.
    for s in (a, d):
        s.pop("time_range", None); s.pop("time_policy", None); s.pop("deployment_purpose", None)
    dynamic = d.pop("invocation_rule", None)
    if dynamic:
        if a.get("kind") != "decision" or not a.get("id") or not set(a["entities"]) <= set(d["entities"]):
            return False
        a.update(kind=d["kind"], id=d["id"], entities=d["entities"])
    if d.pop("event_rule", None):
        if not a.get("event_id") or not a.get("event_version"):
            return False
        a.pop("event_id"); a.pop("event_version")
    return a == d


def event_proof(identity, actual, *, workflow, as_of, config_dir=None):
    rule = identity.scope.get("event_rule")
    if not rule:
        return None
    from ats.config import REPO_ROOT
    from ats.data.runtime.repository import platform_data_db_path
    from ats.data.stores.schedule_calendar import ScheduleCalendarStore
    from .routing import resolve_event_route
    from .read_windows import invocation_time
    root = Path(config_dir or os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))
    digest = hashlib.sha256((root / "workflow/event_routes.yaml").read_bytes()).hexdigest()
    if digest != rule["config_sha256"]:
        raise RouteUnavailable("approved Event rule configuration drifted")
    with closing(sqlite3.connect(Path(platform_data_db_path()).resolve().as_uri()+"?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM schedule_events WHERE event_id=? AND valid_until=''",
                           (actual.scope.get("event_id"),)).fetchall()
        if len(row) != 1 or str(row[0]["event_version"]) != str(actual.scope.get("event_version")):
            raise RouteUnavailable("Event version is not current")
        if invocation_time(row[0]["valid_from"]) > invocation_time(as_of):
            raise RouteUnavailable("Event version not visible at invocation cutoff")
        event = ScheduleCalendarStore._event_dict(conn, row[0])
    if event.get("quality_status") != "ok":
        raise RouteUnavailable("Event source quality is not admitted")
    materials = [{**item, "status": "admitted"} for confirmation in event.get("release_confirmations", [])
                 for item in confirmation.get("materials", [])]
    route = resolve_event_route(event, materials=materials, config_dir=root)
    if route.route_kind != rule["route_kind"] or workflow not in route.workflow_ids:
        raise RouteUnavailable("Event is outside approved route/workflow")
    entity = str(event.get("payload", {}).get("entity") or event.get("entity") or "").upper()
    if route.scope_resolver == "event_entity" and (not entity or entity not in actual.scope["entities"]):
        raise RouteUnavailable("Event entity escapes invocation scope")
    return {"event_id": route.event_id, "event_version": route.event_version,
            "route_kind": route.route_kind, "source_refs": event.get("sources", []),
            "publication_at": event["valid_from"], "config_sha256": digest}


def invocation(identity, actual, *, workflow, as_of, purpose, run_id="", plan_hash="", config_dir=None, query_window=None):
    from .read_windows import check_time
    cutoff = check_time(identity, as_of).isoformat()
    if not matches(actual, identity, purpose=purpose):
        raise RouteUnavailable("invocation escapes explicit deployment mapping")
    window = query_window or actual.scope["time_range"]
    if set(window) != {"start", "end"}:
        raise RouteUnavailable("query window requires start/end")
    from .read_windows import invocation_time
    if not invocation_time(window["start"]) <= invocation_time(window["end"]) <= invocation_time(as_of):
        raise RouteUnavailable("query window escapes invocation cutoff")
    return {"version": "phase-f-invocation-v1", "deployment_identity": identity.as_row(),
            "actual_identity": actual.as_row(), "workflow": workflow, "purpose": purpose,
            "as_of": cutoff, "query_window": window, "run_id": run_id,
            "plan_hash": plan_hash, "config_dir": str(config_dir or ""),
            "event": event_proof(identity, actual, workflow=workflow, as_of=as_of, config_dir=config_dir)}


def recheck(identity, binding):
    if not binding or binding.get("version") != "phase-f-invocation-v1" or binding.get("deployment_identity") != identity.as_row():
        raise RouteUnavailable("exact invocation binding missing")
    actual = RouteIdentity(**binding["actual_identity"])
    expected = invocation(identity, actual, workflow=binding["workflow"], as_of=binding["as_of"],
        purpose=binding["purpose"], run_id=binding["run_id"], plan_hash=binding["plan_hash"],
        config_dir=binding.get("config_dir") or None, query_window=binding.get("query_window"))
    if expected != binding:
        raise RouteUnavailable("invocation binding or Event proof changed")
    return actual


def append_receipt(context, rows, *, started_at, status):
    """Append actual receipts in control state, including physically isolated runs."""
    from . import cutover as plane
    body = {"binding": context.invocation, "identity": context.identity.as_row(),
            "deployment": context.deployment, "execution_plan_hash": context.execution_plan_hash,
            "mode": context.mode, "route": context.route, "generation": context.read_generation,
            "joint_epoch": context.joint_epoch, "recovery_generation": context.recovery_generation,
            "publication_generations": context.publication_generations,
            "started_at": started_at, "ended_at": datetime.now(UTC).isoformat(), "status": status,
            "reads": rows, "read_status": "observed" if rows else "untested", "input_hash": hashlib.sha256(canonical_json(rows).encode()).hexdigest()}
    with closing(sqlite3.connect(Path(plane.default_cutover_db_path()).resolve().as_uri()+"?mode=rw", uri=True)) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS read_invocation_receipts(
              seq INTEGER PRIMARY KEY AUTOINCREMENT, receipt_json TEXT NOT NULL, receipt_hash TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS invocation_receipt_no_update BEFORE UPDATE ON read_invocation_receipts
            BEGIN SELECT RAISE(ABORT,'invocation receipts are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS invocation_receipt_no_delete BEFORE DELETE ON read_invocation_receipts
            BEGIN SELECT RAISE(ABORT,'invocation receipts are append-only'); END;
        """)
        raw = canonical_json(body)
        conn.execute("INSERT INTO read_invocation_receipts(receipt_json,receipt_hash) VALUES(?,?)",
                     (raw, hashlib.sha256(raw.encode()).hexdigest()))
        conn.commit()
