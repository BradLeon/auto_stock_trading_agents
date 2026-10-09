"""Read-only Phase F plan A audit; writes only this session's verification artifact."""
from __future__ import annotations

import hashlib
import json
import re
import runpy
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
CHANGE = "implement-phase-f-shadow-run-and-cutover"
SELECTED = {"7.14", "7.15", "7.12", "7.16"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def junit(name):
    tree = ET.parse(OUT / name)
    suites = list(tree.iter("testsuite"))
    counts = {key: sum(int(s.get(key, "0")) for s in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    assert counts["tests"] and not any(counts[k] for k in ("failures", "errors", "skipped")), counts
    return {**counts, "sha256": digest(OUT / name)}


def main():
    rows = re.findall(r"^- \[([ x])\] (\d+\.\d+) (.*)$",
                      (ROOT / "openspec/changes" / CHANGE / "tasks.md").read_text(), re.MULTILINE)
    checked = {tid for mark, tid, _ in rows if mark == "x"}
    graph = {}
    for _, tid, description in rows:
        match = re.search(r"（依赖：([^）]*)）", description)
        graph[tid] = re.findall(r"\d+\.\d+", match[1]) if match else []
    active, done = set(), set()
    def visit(tid):
        assert tid in graph and tid not in active
        if tid in done:
            return
        active.add(tid)
        for dependency in graph[tid]:
            visit(dependency)
        active.remove(tid)
        done.add(tid)
    for tid in graph:
        visit(tid)
    assert SELECTED <= checked and all(set(graph[t]) <= checked for t in SELECTED)
    assert len(rows) == 116 and len(checked) == 52
    assert not {"7.5", "7.17", "11.2"} & checked

    previous = ROOT / "docs/validation/phase_f_recovery_20261007/preflight-evidence.json"
    inventory = runpy.run_path(str(previous.with_name("preflight.py")))["read_databases"]()
    assert inventory == json.loads(previous.read_text())["production_readonly"]
    regression = {name: junit(name) for name in (
        "final-business-v2.junit.xml", "control-regression.junit.xml", "entry-regression.junit.xml",
        "final-price.junit.xml", "final-approval.junit.xml", "record-guards.junit.xml")}
    strict = []
    for name in (CHANGE, "refactor-workflow-dataflow-architecture"):
        result = subprocess.run(["openspec", "validate", name, "--strict", "--json"],
                                cwd=ROOT, capture_output=True, text=True, check=True)
        strict.append(json.loads(result.stdout))
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    from ats.data.contract_validation import validate_target_contract
    from ats.trader.execute import AUTO_EXECUTION_ENABLED
    from ats.workflow.assurance_surface import affected_consumers, load_surface

    assert validate_target_contract()["valid"] and AUTO_EXECUTION_ENABLED is False
    before = json.loads((OUT / "before.json").read_text())
    drift = {p: {"before": old, "after": digest(ROOT / p)} for p, old in before.items()
             if old != digest(ROOT / p)}
    surface = load_surface()
    assert len(surface.consumers) == 10
    assert affected_consumers(surface, [surface.manifest])[surface.manifest] == ("*",)
    paths = set(before) | {
        "src/ats/data/execution_prices.py", "src/ats/data/runtime/execution_prices.py",
        "src/ats/execution/simulation.py", "src/ats/schemas/memory.py",
        "tests/test_phase_f_trader_a.py", "tests/test_dataflow_assurance.py",
        "tests/phase_f_price_harness.py", "tests/conftest.py", "tests/test_execution_authorization.py",
        "tests/test_chief_loop.py", "tests/test_trader.py", "tests/test_overnight_limits.py",
        "tests/test_broker_risk.py", "tests/test_e2e_approval_chain.py"}
    result = {
        "generated_at": datetime.now(UTC).isoformat(), "completed_this_run": sorted(SELECTED),
        "total": 116, "completed": 52, "pending": 64, "dependency_graph_acyclic": True,
        "selected_dependencies_complete": True, "regression": regression, "strict": strict,
        "protected_drift": drift, "affected_consumers": sorted(surface.consumers),
        "source_sha256": {p: digest(ROOT / p) for p in sorted(paths)},
        "production_readonly_unchanged": True, "production_inventory": inventory,
        "qualification_registered": False, "real_broker_network": False,
        "production_C3_enabled": False, "simulation_transport": "explicit isolated no-network FakeBroker",
        "test_limit": "Local synthetic quotes/approval/transport; not full workflow, production qualification or live TWS evidence",
        "followup": ["5.7", "7.1", "7.2", "7.4", "7.11", "7.5", "7.17", "7.13", "11.2", "6.1", "6.4"],
    }
    target = OUT / "verification.json"
    if target.exists():
        frozen = json.loads(target.read_text())
        assert frozen["source_sha256"] == result["source_sha256"], "source changed after verification"
        assert frozen["regression"] == regression, "frozen test artifacts changed"
    else:
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"completed": 52, "pending": 64, "regression": regression}, ensure_ascii=False))


if __name__ == "__main__":
    main()
