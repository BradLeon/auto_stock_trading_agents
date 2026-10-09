"""Declarations are inventories; enforcement requires scoped execution evidence.

An evidence bundle is an audit artifact, not a security credential. Registration
checks the actual caller's guard, pinned source/test hashes, and passing positive
and negative business-entry tests in JUnit. It never infers enforcement from a
guard's existence or a bootstrap flag. Future integration tasks supply missing
business-entry tests; absent or changed evidence closes the scope.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import xml.etree.ElementTree as ET
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from ..config import REPO_ROOT
from . import cutover as plane
from .scoped_routes import RouteIdentity, canonical_json


@dataclass(frozen=True)
class CallPoint:
    site: str
    guard: str
    writer: str
    modes: tuple[str, ...]
    scope: str
    implementation_task: str
    consumers: tuple[str, ...] = ("*",)


CALL_POINTS = {
    plane.PROJECTION_READ: (
        CallPoint("ats.runtime.cli.run_chief", "scoped_read", "Chief", ("production", "isolated"),
                  "portfolio/entities/time + requested category/task snapshot", "9.3"),
        CallPoint("ats.runtime.cli.run_sector_html", "scoped_read", "Sector CLI", ("production", "isolated"),
                  "sector/layers/entities/time", "9.6"),
        CallPoint("ats.workflow.dispatcher.Dispatcher.dispatch", "check_plan_reads", "Dispatcher",
                  ("production", "shadow", "isolated"), "task kind/id/entities/time/event version", "5.6"),
    ),
    plane.ANALYST_OUTPUT: (
        CallPoint("ats.memory.store.TradingMemory.save_task_projection_envelope", "guard_analyst_output",
                  "Analyst publisher", ("production", "shadow", "isolated"),
                  "task projection + claim owner/generation", "5.3"),
    ),
    plane.DISPATCHER_SCHEDULE: (
        CallPoint("ats.workflow.ownership.run_owned_workflow", "claim", "Workflow owner",
                  ("production", "shadow", "isolated"), "workflow/scope/planned instant or event/version", "4.2"),
        CallPoint("ats.runtime.scheduler._daily", "claim", "Legacy scheduler",
                  ("production",), "workflow/scope/planned instant", "4.2"),
    ),
    plane.APPROVAL_LIFECYCLE: tuple(
        CallPoint(f"ats.decision.repository.DecisionAuditRepository.{method}", "guard_approval_write",
                  "DecisionAuditRepository", ("production", "isolated"),
                  "cycle/revision/snapshot/approval/account", "5.3")
        for method in ("record_review", "record_approval")),
    plane.CLERK_PUBLICATION: (
        CallPoint("ats.execution.clerk.clerk_run", "guard_clerk_publication", "Clerk",
                  ("production", "isolated"), "ledger/reconciliation window/account", "5.3"),
    ),
    plane.LIVE_TRADER: (
        CallPoint("ats.broker.ibkr.IBKRBroker._submit", "_submission_gate", "IBKRBroker",
                  ("production", "shadow", "isolated"), "intent/route/generation/account/environment", "2.2/2.3/2.8"),
        CallPoint("ats.broker.ibkr.IBKRBroker.cancel_all", "_submission_gate", "IBKRBroker",
                  ("production", "shadow", "isolated"), "order/route/generation/account/environment", "2.2/2.3/2.8"),
    ),
}

# Enumerate legacy/manual counterparts too. They must not disappear from the
# proof requirement merely because a new Dispatcher helper exists.
CALL_POINTS[plane.PROJECTION_READ] += tuple(
    CallPoint(f"ats.runtime.cli.{site}", "scoped_read", "Analyst CLI",
              ("production", "isolated"), "consumer kind/id/entities/time/event version", "5.6", (consumer,))
    for site, consumer in (("run_layer_review", "layer"), ("run_information_pass", "information"),
                          ("run_sector_review", "sector"), ("run_macro_review", "macro"),
                          ("run_technical_review", "technical"), ("run_pead", "fundamental"),
                          ("_run_analyst_fundamental", "fundamental")))
CALL_POINTS[plane.PROJECTION_READ] = tuple(
    CallPoint(p.site, p.guard, p.writer, p.modes, p.scope, p.implementation_task,
              ("chief",) if p.site.endswith("run_chief") else
              ("sector",) if p.site.endswith("run_sector_html") else p.consumers)
    for p in CALL_POINTS[plane.PROJECTION_READ])
CALL_POINTS[plane.ANALYST_OUTPUT] += (
    CallPoint("ats.memory.store.TradingMemory.save_task_projection", "guard_analyst_output",
              "Legacy analyst publisher", ("production", "isolated"),
              "legacy task projection + claim owner/generation", "5.3"),)
CALL_POINTS[plane.DISPATCHER_SCHEDULE] += tuple(
    CallPoint(f"ats.runtime.scheduler.{site}", "claim", "Legacy scheduler",
              ("production", "isolated"), "workflow/scope/planned instant or event/version", "4.2")
    for site in ("_technical_daily", "_macro_weekly", "_sector_weekly", "_weekly_review",
                 "_chief_daily", "pead_daily", "pead_score_window", "_event_triggers"))

CALL_POINTS[plane.PROJECTION_READ] += (
    CallPoint("ats.graph.chief.assemble_context", "scoped_read", "Chief graph",
              ("production", "isolated"), "portfolio/entities/time", "5.6", ("chief",)),
    CallPoint("ats.workflow.dispatcher.Dispatcher._chief_snapshot_gate", "bind_read", "Chief snapshot",
              ("production", "shadow", "isolated"), "portfolio/entities/time", "5.6", ("chief",)),
    CallPoint("ats.data.consumer_api.read_input", "guard_input", "Governed native input",
              ("production", "shadow", "isolated"), "bound kind/id/entities/time", "5.6"),
    CallPoint("ats.workflow.ownership.run_owned_workflow", "check_plan_reads", "Workflow owner",
              ("production", "shadow", "isolated"), "task kind/id/entities/time/event version", "5.6"),)

MODE_SEMANTICS = {
    "production": "authoritative exact-scope route; disabled refuses; target requires qualified evidence",
    "shadow": "isolated publication and execution records; broker writes prohibited",
    "isolated": "isolated state and fake transport; production authority and broker writes prohibited",
}


def resolve_site(site: str):
    """Resolve source AST without importing a module or initializing its stores."""
    parts = site.split(".")
    for split in range(len(parts) - 1, 0, -1):
        candidate = REPO_ROOT / "src" / Path(*parts[:split]).with_suffix(".py")
        if not candidate.is_file():
            continue
        node = ast.parse(candidate.read_text())
        for name in parts[split:]:
            node = next((child for child in node.body if isinstance(
                child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == name), None)
            if node is None:
                raise ValueError(f"call point does not exist: {site}")
        return candidate, node
    raise ValueError(f"call point does not exist: {site}")


def _calls_guard(point: CallPoint) -> bool:
    _, node = resolve_site(point.site)
    return any(isinstance(item, ast.Call) and (
        getattr(item.func, "id", "") == point.guard or getattr(item.func, "attr", "") == point.guard)
        for item in ast.walk(node))


def declaration_report() -> dict:
    """Every real call point, its missing guard and responsible integration task."""
    return {boundary: [{**point.__dict__, "mode_semantics": {
        mode: MODE_SEMANTICS[mode] for mode in point.modes}, "declared": True,
        "guard_present": _calls_guard(point), "enforced": False,
        "reason": "requires exact-scope business-entry execution evidence"} for point in points]
        for boundary, points in CALL_POINTS.items()}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS boundary_enforcement_evidence (
    evidence_id TEXT PRIMARY KEY, boundary TEXT NOT NULL, identity_key TEXT NOT NULL,
    mode TEXT NOT NULL, bundle_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS boundary_evidence_no_update BEFORE UPDATE ON boundary_enforcement_evidence
BEGIN SELECT RAISE(ABORT, 'boundary evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS boundary_evidence_no_delete BEFORE DELETE ON boundary_enforcement_evidence
BEGIN SELECT RAISE(ABORT, 'boundary evidence is append-only'); END;
"""


def _validate_bundle(boundary: str, identity: RouteIdentity, mode: str, bundle: dict):
    if boundary not in CALL_POINTS or mode not in MODE_SEMANTICS:
        raise plane.CutoverError("unknown boundary or mode")
    if bundle.get("identity") != identity.as_row() or bundle.get("mode") != mode:
        raise plane.CutoverError("enforcement proof scope/mode mismatch")
    required = {p.site: p for p in CALL_POINTS[boundary] if mode in p.modes
                and ("*" in p.consumers or identity.consumer_id in p.consumers)}
    proofs = bundle.get("call_points", {})
    if not required or set(proofs) != set(required):
        raise plane.CutoverError("enforcement proof must cover every applicable business call point")
    for site, point in required.items():
        proof = proofs[site]
        if proof.get("writer") != point.writer or not _calls_guard(point):
            raise plane.CutoverError(f"actual guard missing or writer mismatch: {site}")
        positive, negative = proof.get("positive_test"), proof.get("negative_test")
        if not positive or not negative or positive == negative:
            raise plane.CutoverError("distinct positive/negative business-entry tests required")
        junit = Path(proof.get("junit_path", ""))
        try:
            data = junit.read_bytes()
            if hashlib.sha256(data).hexdigest() != proof.get("junit_sha256"):
                raise ValueError("JUnit hash drift")
            cases = list(ET.fromstring(data).iter("testcase"))
            for test in (positive, negative):
                matches = [case for case in cases if f"{case.get('classname')}.{case.get('name')}" == test]
                if len(matches) != 1 or any(matches[0].find(tag) is not None
                                            for tag in ("failure", "error", "skipped")):
                    raise ValueError(f"business-entry test not passing: {test}")
                properties = {p.get("name"): p.get("value")
                              for p in matches[0].iter("property")}
                expected = {"entry_exercised": site, "mode": mode,
                            "identity_json": canonical_json(identity.as_row()),
                            "path_kind": "positive" if test == positive else "negative"}
                if any(properties.get(k) != v for k, v in expected.items()):
                    raise ValueError("executed test entry/scope/mode does not match evidence")
                if test == negative and properties.get("negative_side_effect_count") != "0":
                    raise ValueError("negative test did not prove zero side effects")
            hashes = proof.get("source_hashes", {})
            source, _ = resolve_site(site)
            guard_source = REPO_ROOT / "src/ats/workflow/cutover_wiring.py"
            if boundary == plane.LIVE_TRADER:
                guard_source = REPO_ROOT / "src/ats/execution/broker_write_guard.py"
            test_sources = {REPO_ROOT / "tests" / (test.removeprefix("tests.").split(".")[0] + ".py")
                            for test in (positive, negative)}
            needed = {source, guard_source, Path(__file__),
                      REPO_ROOT / "src/ats/workflow/scoped_routes.py", *test_sources}
            if boundary == plane.LIVE_TRADER:
                needed |= {REPO_ROOT / "src/ats/execution/route_registry.py",
                           REPO_ROOT / "src/ats/execution/route_arbitration.py",
                           REPO_ROOT / "tests/phase_f_broker_harness.py"}
            if not needed.issubset({Path(p).resolve() for p in hashes}):
                raise ValueError("source/test dependency fingerprints missing")
            for name, digest in hashes.items():
                if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest:
                    raise ValueError(f"enforcement dependency drift: {name}")
            if proof.get("negative_side_effect_count") != 0 or proof.get("entry_exercised") != site:
                raise ValueError("business-entry refusal without zero-side-effect proof")
        except (OSError, ValueError, ET.ParseError, TypeError) as exc:
            raise plane.CutoverError(f"invalid enforcement evidence: {exc}") from exc


def record_enforcement(boundary: str, identity: RouteIdentity, *, mode: str, bundle: dict, path=None):
    _validate_bundle(boundary, identity, mode, bundle)
    payload = canonical_json(bundle)
    key = hashlib.sha256(payload.encode()).hexdigest()
    with closing(sqlite3.connect(path or plane.default_cutover_db_path(), isolation_level=None)) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO boundary_enforcement_evidence VALUES (?,?,?,?,?)",
                     (key, boundary, identity.key, mode, payload))
    return key


def assert_enforced(boundary: str, *, identity: RouteIdentity | None, mode="production", path=None):
    if identity is None:
        raise plane.CutoverError("declared wiring is not enforced: exact business identity required")
    target = Path(path or plane.default_cutover_db_path())
    try:
        with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            table = conn.execute("SELECT 1 FROM sqlite_master WHERE name='boundary_enforcement_evidence'").fetchone()
            rows = conn.execute("SELECT bundle_json FROM boundary_enforcement_evidence "
                                "WHERE boundary=? AND identity_key=? AND mode=?",
                                (boundary, identity.key, mode)).fetchall() if table else []
        for (payload,) in rows:
            try:
                _validate_bundle(boundary, identity, mode, json.loads(payload))
            except (plane.CutoverError, ValueError):
                continue
            return
    except sqlite3.Error as exc:
        raise plane.CutoverError(f"enforcement authority unreadable: {exc}") from exc
    raise plane.CutoverError(f"boundary {boundary!r} declared but not enforced for this scope/mode")


def enforcement_report(identity: RouteIdentity, *, mode="production", path=None) -> dict:
    result = {}
    for boundary in CALL_POINTS:
        try:
            assert_enforced(boundary, identity=identity, mode=mode, path=path)
        except plane.CutoverError as exc:
            result[boundary] = {"enforced": False, "reason": str(exc)}
        else:
            result[boundary] = {"enforced": True, "reason": "verified business-entry evidence"}
    return {"identity": identity.as_row(), "mode": mode, "boundaries": result}
