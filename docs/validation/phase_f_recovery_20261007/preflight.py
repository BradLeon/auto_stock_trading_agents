"""Read-only Phase F recovery inventory; no workflow, broker or evidence writes.

Run through uv from the repository root. Output goes only to this validation
directory. Counterexamples construct projections in memory, never decision cycles.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.decision.snapshot import build_research_snapshot
from ats.workflow.assurance_surface import load_surface
from ats.workflow.phase_e import phase_e_registry
from ats.workflow.run_contracts import (
    TaskResult, TriggerContext, WorkflowRunRequest, build_run_result,
)

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).with_name("preflight-evidence.json")
PRE_PHASE_F = "6b72d05"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_databases() -> dict:
    result = {}
    for relative in (
        "var/phase_f_cutover.sqlite", "var/phase_f_batches.sqlite",
        "var/phase_f_dispatch.sqlite", "var/phase_f_routes.sqlite",
        "var/phase_f_schedule_switch.sqlite", "var/phase_f_live_authorizations.sqlite",
        "var/shadow/orders.sqlite", "var/shadow/reports.sqlite", "var/data.sqlite",
    ):
        path = ROOT / relative
        if not path.exists():
            result[relative] = {"exists": False}
            continue
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.row_factory = sqlite3.Row
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            item = {"exists": True, "assurance_table_present":
                    "dataflow_assurance_events" in tables, "counts": {},
                    "authorization_inventory": {}}
            for table in sorted(tables):
                if table.startswith(("cutover_", "dispatch_", "trade_route_",
                                     "schedule_", "shadow_", "live_", "read_cutover_",
                                     "dataflow_assurance")):
                    quoted = '"' + table.replace('"', '""') + '"'
                    item["counts"][table] = conn.execute(
                        f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
                if "authoriz" in table or "authoris" in table:
                    quoted = '"' + table.replace('"', '""') + '"'
                    rows = [dict(r) for r in conn.execute(f"SELECT * FROM {quoted}")]
                    safe = []
                    for row in rows:
                        fields = {key: row[key] for key in (
                            "reference", "scope", "scope_json", "environment",
                            "valid_until", "valid_from", "issued_at", "created_at", "issuer",
                        ) if key in row}
                        # Names/accounts/free-text are not published. Presence and
                        # digests permit subsequent read-only comparisons.
                        for key in ("authorised_by", "issued_by", "source", "note", "account"):
                            if key in row:
                                value = str(row[key] or "")
                                fields[key + "_present"] = bool(value)
                                fields[key + "_sha256"] = digest(value.encode())
                        fields["record_sha256"] = digest(json.dumps(
                            row, sort_keys=True, default=str).encode())
                        fields["provenance_verified"] = False
                        safe.append(fields)
                    item["authorization_inventory"][table] = safe
            if "cutover_boundary_state" in tables:
                item["boundaries"] = [dict(r) for r in conn.execute(
                    "SELECT boundary,route,wired FROM cutover_boundary_state ORDER BY boundary")]
            if "cutover_batches" in tables:
                columns = {r[1] for r in conn.execute("PRAGMA table_info(cutover_batches)")}
                selected = sorted(columns & {"batch_id", "batch_class", "consumers_json",
                                             "scope_json", "shadow_report_id"})
                item["batches"] = [dict(r) for r in conn.execute(
                    "SELECT " + ",".join(selected) + " FROM cutover_batches ORDER BY batch_id")]
            result[relative] = item
    return result


def counterexamples() -> dict:
    registry = phase_e_registry()
    now = datetime.now(timezone.utc)
    scope = ProjectionScope(kind="entity", id="NVDA")
    payload = dict(entity="NVDA", metric="revenue", period="FY2026",
                   previous_value=1, new_value=2, driver="probe")
    # Use trusted, already validated envelope constructors. Only Fundamental is
    # needed to expose the CATEGORY vs TASK difference; the custom registry below
    # deliberately contains its two modes. It proves the logical divergence, not
    # six-category business acceptance.
    from ats.workflow.run_contracts import TaskRegistry
    small = TaskRegistry([registry.spec("fundamental-routine").model_copy(
        update={"depends_on": ()}), registry.spec("fundamental-event").model_copy(
        update={"depends_on": ()})])
    env = build_envelope(
        role="fundamental_expectation_update", payload=payload,
        scope=scope, as_of=now.isoformat(), valid_until=(now + timedelta(hours=1)).isoformat(),
        input_refs=["probe-only"], data_vintage_refs=["probe-only@2026-10-07"])
    snapshot = build_research_snapshot(
        registry=small, projections={"fundamental-routine": env}, scope=scope, at=now)
    results = {}
    for label, requested in (
        ("all_registered_modes", small.task_ids()),
        ("selected_routine_only", ("fundamental-routine",)),
        ("selected_event_but_only_routine_available", ("fundamental-event",)),
    ):
        request = WorkflowRunRequest(
            run_id=label, trigger=TriggerContext(kind="manual", trigger_id=label),
            tasks=requested, scope=scope, as_of=now.isoformat(), enter_decision_cycle=True)
        run = build_run_result(request, small, {
            "fundamental-routine": TaskResult(task_id="fundamental-routine", status="succeeded",
                                             projection_refs=(env.projection_id,))})
        results[label] = {"snapshot_complete": snapshot.complete,
                          "run_complete": run.complete,
                          "run_allows_cycle": run.decision_cycle_entered,
                          "missing_tasks": [r.task_id for r in run.missing_requirements]}
    assert results["all_registered_modes"]["snapshot_complete"]
    assert not results["all_registered_modes"]["run_complete"]
    assert results["selected_routine_only"]["run_complete"]
    assert not results["selected_event_but_only_routine_available"]["run_complete"]
    return {"logical_probes_only": True, "registry": {
        task: registry.spec(task).model_dump(mode="json") for task in registry.task_ids()},
        "cases": results}


def main() -> None:
    surface = load_surface()
    paths = list(surface.all_paths())
    configs = ["pyproject.toml", "uv.lock", "config/workflow/workflow_owners.yaml",
               "config/workflow/phase_e_schedules.yaml", "config/workflow/legacy_retirement.yaml",
               "config/settings.yaml", *paths]
    pre = git("rev-parse", PRE_PHASE_F)
    full_tree = git("ls-tree", "-r", "--name-only", pre).splitlines()
    evidence = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git": {"branch": git("branch", "--show-current"), "head": git("rev-parse", "HEAD"),
                "pre_phase_f_commit": pre,
                "pre_phase_f_subject": git("show", "-s", "--format=%s", pre),
                "pre_phase_f_tests_tracked": sum(p.startswith("tests/") for p in full_tree),
                "first_tracked_test_commit": git("rev-parse", "374bcf1"),
                "pre_phase_f_has_broker_guard": "src/ats/execution/broker_write_guard.py" in full_tree,
                "baseline_caveat": "Pre-Phase-F tests were untracked. Historical test corpus "
                "cannot be reconstructed from that tree. Task 13.6 must identify its fixed "
                "compatible baseline test corpus and newly added tests separately."},
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "uv": subprocess.check_output(["uv", "--version"], text=True).strip(),
                        "packages": dict(sorted((d.metadata["Name"], d.version)
                                                for d in importlib.metadata.distributions()))},
        "protected_surface": surface.as_rows(),
        "baseline_hashes": {p: digest((ROOT / p).read_bytes()) for p in sorted(set(configs))},
        "pre_phase_f_lock_hash": digest(subprocess.check_output(["git", "show", f"{pre}:uv.lock"], cwd=ROOT)),
        "phase_e_owners": yaml.safe_load((ROOT / configs[2]).read_text()),
        "phase_e_schedules": yaml.safe_load((ROOT / configs[3]).read_text()),
        "production_readonly": read_databases(), "completeness_probes": counterexamples(),
        "actions": {"broker_network": False, "production_workflow_run": False,
                    "production_evidence_written": False, "authorization_modified": False},
    }
    OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(OUTPUT.relative_to(ROOT)), "surface_paths": len(paths),
                      "consumers": len(surface.consumers), "head": evidence["git"]["head"],
                      "baseline": pre, "counterexamples": evidence["completeness_probes"]["cases"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
