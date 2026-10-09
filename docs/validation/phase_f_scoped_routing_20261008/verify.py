"""Read-only recovery verification; outputs evidence under this directory only.

Run with uv run --offline --no-sync python <this file>. Tests are run separately;
their JUnit files are read here so verification never launches a trading path.
"""
from __future__ import annotations

import hashlib
import json
import re
import runpy
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
CHANGE = "implement-phase-f-shadow-run-and-cutover"
BASE = ROOT / "docs/validation/phase_f_recovery_20261007/preflight-evidence.json"
CODE = [
    "src/ats/workflow/cutover.py", "src/ats/workflow/cutover_wiring.py",
    "src/ats/workflow/cutover_routing.py", "src/ats/workflow/read_cutover.py",
    "src/ats/workflow/boundary_evidence.py", "src/ats/workflow/scoped_routes.py",
    "src/ats/runtime/cli.py",
]


def run(*args):
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"check failed: {args}: {result.stdout} {result.stderr}")
    return result.stdout


def junit(name):
    tree = ElementTree.parse(OUT / name)
    suites = list(tree.iter("testsuite"))
    return {key: sum(int(s.get(key, "0")) for s in suites)
            for key in ("tests", "failures", "errors", "skipped")}


def main():
    baseline = json.loads(BASE.read_text())
    hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
              for p in baseline["baseline_hashes"]}
    assert hashes == baseline["baseline_hashes"], "protected/config/uv baseline changed"
    preflight = runpy.run_path(str(BASE.with_name("preflight.py")))
    current = preflight["read_databases"]()
    assert current == baseline["production_readonly"], "production inventories changed"
    # The older inventory didn't know the new receipt table. Independently prove
    # that isolated tests haven't even installed that schema in production.
    production_route = ROOT / "var/phase_f_routes.sqlite"
    with closing(sqlite3.connect(production_route.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        receipt_schema_absent = not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='trade_submit_receipts'").fetchone()
    assert receipt_schema_absent

    tasks = (ROOT / "openspec/changes" / CHANGE / "tasks.md").read_text()
    rows = re.findall(r"^- \[([ x])\] (\d+\.\d+) (.*)$", tasks, re.M)
    graph = {}
    checked = {tid for mark, tid, _ in rows if mark == "x"}
    for _, tid, description in rows:
        match = re.search(r"（依赖：([^）]*)）", description)
        graph[tid] = re.findall(r"\d+\.\d+", match[1]) if match else []
    done, active = set(), set()
    def visit(tid):
        assert tid in graph, f"missing dependency {tid}"
        assert tid not in active, f"dependency cycle at {tid}"
        if tid in done:
            return
        active.add(tid)
        for dep in graph[tid]:
            visit(dep)
        active.remove(tid)
        done.add(tid)
    for tid in graph:
        visit(tid)
    selected = {"5.2", "5.5", "5.14"}
    assert selected <= checked
    assert all(set(graph[tid]) <= checked for tid in selected)
    assert len(rows) == 116 and len(checked) == 46
    regression, records = junit("regression.junit.xml"), junit("record-guards.junit.xml")
    assert regression == {"tests": 324, "failures": 0, "errors": 0, "skipped": 0}
    assert records == {"tests": 20, "failures": 0, "errors": 0, "skipped": 0}
    strict = [json.loads(run("openspec", "validate", name, "--strict", "--json"))
              for name in (CHANGE, "refactor-workflow-dataflow-architecture")]
    run("git", "diff", "--check")
    new_files = ["src/ats/workflow/boundary_evidence.py", "src/ats/workflow/scoped_routes.py",
                 "src/ats/workflow/cutover_wiring.py", "tests/test_phase_f_scoped_routes.py",
                 "tests/phase_f_broker_harness.py", "tests/test_phase_f_broker_enforcement.py"]
    run("uv", "run", "--offline", "--no-sync", "ruff", "check", *new_files)

    from ats.workflow import boundary_evidence as be
    from ats.workflow import cutover_wiring as cw
    from ats.workflow.scoped_routes import RouteIdentity
    inventory = be.declaration_report()
    assert sum(len(points) for points in inventory.values()) == 26
    assert all(cw.valid_owner_mapping(owner) for owner in cw.OWNER_BOUNDARIES)
    with closing(sqlite3.connect((ROOT / "var/phase_f_cutover.sqlite").as_uri() + "?mode=ro", uri=True)) as conn:
        absent_tables = [name for name in ("scoped_cutover_routes", "scoped_cutover_history", "boundary_enforcement_evidence")
                         if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone()]
    assert len(absent_tables) == 3, "isolated schema leaked into production control database"
    who = RouteIdentity("ai_hardware", "trader", "v1", {
        "kind": "sector", "id": "ai_hardware", "entities": ["NVDA", "AMD"],
        "time_range": {"start": "2026-10-07T00:00:00Z", "end": "2026-10-08T00:00:00Z"}})
    dependency_files = ["src/ats/broker/ibkr.py", "src/ats/execution/broker_write_guard.py",
        "src/ats/execution/route_registry.py", "src/ats/execution/route_arbitration.py",
        "src/ats/workflow/boundary_evidence.py", "src/ats/workflow/scoped_routes.py",
        "tests/test_phase_f_broker_enforcement.py", "tests/test_phase_f_scoped_routes.py",
        "tests/phase_f_broker_harness.py"]
    fingerprints = {str(ROOT / name): hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                    for name in dependency_files}
    tests = {
        "_submit": ("tests.test_phase_f_broker_enforcement.test_valid_session_sets_explicit_order_account_and_preserves_reads",
                    "tests.test_phase_f_broker_enforcement.test_freeze_between_batch_check_and_actual_write_is_rechecked"),
        "cancel_all": ("tests.test_phase_f_scoped_routes.test_business_cancel_positive",
                       "tests.test_phase_f_scoped_routes.test_business_cancel_wrong_account_negative")}
    bundle = {"identity": who.as_row(), "mode": "isolated", "call_points": {
        p.site: {"writer": p.writer, "entry_exercised": p.site, "negative_side_effect_count": 0,
                 "positive_test": tests[p.site.split(".")[-1]][0],
                 "negative_test": tests[p.site.split(".")[-1]][1],
                 "junit_path": str(OUT / "regression.junit.xml"),
                 "junit_sha256": hashlib.sha256((OUT / "regression.junit.xml").read_bytes()).hexdigest(),
                 "source_hashes": fingerprints} for p in be.CALL_POINTS["live_trader"]}}
    be._validate_bundle("live_trader", who, "isolated", bundle)
    (OUT / "isolated-entry-proof.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")
    evidence = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "branch": run("git", "branch", "--show-current").strip(),
        "head": run("git", "rev-parse", "HEAD").strip(),
        "completed_this_run": sorted(selected), "total": len(rows),
        "completed": len(checked), "pending": len(rows) - len(checked),
        "dependency_graph_acyclic": True, "selected_dependencies_complete": True,
        "regression": regression, "record_guards": records,
        "strict": strict, "git_diff_check": "passed", "new_files_ruff": "passed",
        "baseline_hashes_unchanged": True, "baseline_hashes": hashes,
        "production_inventory_unchanged": True, "production_readonly": current,
        "production_submit_receipt_schema_absent": receipt_schema_absent,
        "production_new_scope_tables_absent": absent_tables,
        "declarations": inventory, "owners": [o.as_row() for o in cw.OWNER_BOUNDARIES],
        "isolated_entry_proof_validated": True,
        "implementation_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
                                   for p in CODE},
        "full_suite_rerun": False, "broker_transport": "FakeIB only",
        "real_broker_network_writes": False, "production_qualification_written": False,
        "production_authorizations_or_routes_changed": False, "C3_enabled": False,
    }
    (OUT / "verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"completed": len(checked), "total": len(rows),
                      "regression": regression, "record_guards": records,
                      "baseline_and_production_inventory_unchanged": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
