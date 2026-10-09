"""Read-only verification and explicit protected drift; no qualification registration."""
import hashlib
import json
import re
import runpy
import sqlite3
import subprocess
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
CHANGE = "implement-phase-f-shadow-run-and-cutover"
BASE = ROOT / "docs/validation/phase_f_recovery_20261007/preflight-evidence.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True)
    return result.stdout


def junit(name):
    tree = ET.parse(OUT / name)
    suites = list(tree.iter("testsuite"))
    counts = {k: sum(int(s.get(k, "0")) for s in suites)
              for k in ("tests", "failures", "errors", "skipped")}
    failures = sorted(t.get("name") for t in tree.iter("testcase") if t.find("failure") is not None)
    return {**counts, "failed_cases": failures, "sha256": digest(OUT / name)}


def main():
    baseline = json.loads(BASE.read_text())
    current_hashes = {p: digest(ROOT / p) for p in baseline["baseline_hashes"]}
    drift = {p: {"before": old, "after": current_hashes[p]} for p, old in
             baseline["baseline_hashes"].items() if old != current_hashes[p]}
    assert set(drift) == {"src/ats/data/consumer_api.py", "src/ats/decision/repository.py",
                          "src/ats/execution/clerk.py"}
    impacts = {}
    for item in baseline["protected_surface"]:
        if item["path"] in drift:
            impacts.setdefault(item["path"], set()).update(item["invalidates"].split(","))
    impacts = {p: sorted(values) for p, values in impacts.items()}
    assert len(impacts["src/ats/data/consumer_api.py"]) == 10
    preflight = runpy.run_path(str(BASE.with_name("preflight.py")))
    production = preflight["read_databases"]()
    assert production == baseline["production_readonly"], "production inventory changed"
    absent = {}
    for filename, tables in {
        "var/phase_f_cutover.sqlite": ["scoped_cutover_routes", "scoped_cutover_history", "boundary_enforcement_evidence"],
        "var/phase_f_routes.sqlite": ["trade_submit_receipts"],
    }.items():
        with closing(sqlite3.connect((ROOT / filename).as_uri() + "?mode=ro", uri=True)) as conn:
            absent[filename] = [t for t in tables if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()]
        assert absent[filename] == tables

    tasks = (ROOT / "openspec/changes" / CHANGE / "tasks.md").read_text()
    rows = re.findall(r"^- \[([ x])\] (\d+\.\d+) (.*)$", tasks, re.MULTILINE)
    checked = {tid for mark, tid, _ in rows if mark == "x"}
    graph = {}
    for _, tid, description in rows:
        match = re.search(r"（依赖：([^）]*)）", description)
        graph[tid] = re.findall(r"\d+\.\d+", match[1]) if match else []
    done, active = set(), set()
    def visit(tid):
        assert tid in graph and tid not in active
        if tid in done:
            return
        active.add(tid)
        for parent in graph[tid]:
            visit(parent)
        active.remove(tid)
        done.add(tid)
    for tid in graph:
        visit(tid)
    assert {"5.3", "5.6"} <= checked
    assert all(set(graph[t]) <= checked for t in ("5.3", "5.6"))
    assert len(rows) == 116 and len(checked) == 48

    regression, records = junit("final-regression.junit.xml"), junit("record-guards.junit.xml")
    assert regression["tests"] >= 560 and regression["failures"] == regression["errors"] == regression["skipped"] == 0
    assert records["tests"] == 20 and records["failures"] == records["errors"] == records["skipped"] == 0
    head_chief, current_chief = junit("head-chief.junit.xml"), junit("current-chief.junit.xml")
    assert head_chief["failed_cases"] == current_chief["failed_cases"] and len(head_chief["failed_cases"]) == 3
    head_facts, expanded = junit("head-facts.junit.xml"), junit("regression.junit.xml")
    assert head_facts["failed_cases"] == expanded["failed_cases"] and len(head_facts["failed_cases"]) == 1

    strict = [json.loads(run("openspec", "validate", name, "--strict", "--json"))
              for name in (CHANGE, "refactor-workflow-dataflow-architecture")]
    run("git", "diff", "--check")
    from ats.trader.execute import AUTO_EXECUTION_ENABLED
    from ats.workflow.boundary_evidence import declaration_report

    assert AUTO_EXECUTION_ENABLED is False
    inventory = declaration_report()
    assert sum(len(points) for points in inventory.values()) == 31
    for boundary in ("projection_read", "analyst_output", "approval_lifecycle", "clerk_publication"):
        assert all(p["guard_present"] for p in inventory[boundary])
        assert all(not p["enforced"] for p in inventory[boundary])
    code = ["src/ats/workflow/runtime_reads.py", "src/ats/workflow/cutover_wiring.py",
            "src/ats/workflow/boundary_evidence.py", "src/ats/workflow/cutover_routing.py",
            "src/ats/workflow/dispatcher.py", "src/ats/workflow/phase_e.py",
            "src/ats/workflow/ownership.py", "src/ats/workflow/isolation.py",
            "src/ats/runtime/cli.py", "src/ats/memory/store.py", "src/ats/memory/__init__.py",
            "src/ats/graph/chief.py", *drift, "tests/test_phase_f_business_wiring.py"]
    result = {
        "generated_at": datetime.now(UTC).isoformat(), "head": run("git", "rev-parse", "HEAD").strip(),
        "completed_this_run": ["5.3", "5.6"], "total": 116, "completed": 48, "pending": 68,
        "dependency_graph_acyclic": True, "selected_dependencies_complete": True,
        "regression": regression, "record_guards": records, "strict": strict,
        "limited_head_chief_comparison": {"head": head_chief, "current": current_chief},
        "limited_head_store_comparison": {"head": head_facts, "expanded_current": expanded},
        "baseline_limit": "Only named HEAD modules are restored in subprocesses; other current code/tests remain. Not a full or pre-Phase-F baseline.",
        "protected_drift": drift, "affected_consumers": impacts,
        "old_evidence_retained": True, "qualification_registered": False,
        "production_readonly_unchanged": True, "production_inventory": production,
        "production_schema_absent": absent, "C3_enabled": False,
        "boundary_inventory": inventory, "source_sha256": {p: digest(ROOT / p) for p in code},
        "followup": ["5.7", "5.8", "5.4", "5.15", "7.11", "7.14", "7.17", "6.1", "6.4", "9.3", "9.6", "13.6"],
    }
    target = OUT / "verification.json"
    if target.exists():
        frozen = json.loads(target.read_text())
        assert frozen["source_sha256"] == result["source_sha256"], "source changed after recorded test run"
        assert frozen["regression"] == regression and frozen["record_guards"] == records, "frozen JUnit changed"
    else:
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"completed": 48, "regression": regression, "protected_drift": sorted(drift)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
