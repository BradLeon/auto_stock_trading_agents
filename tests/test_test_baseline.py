"""The test-baseline contract itself: measurement conditions, environmental vs business
attribution, and cluster-level (never just total-level) reporting.

This file exists because a baseline that cannot be reproduced or attributed is not
evidence — it is a number. The scenarios here are the ones `workflow/test-baseline`
requires, and each pins the half that is easy to lose: the condition record, the
distinction between "the test never ran" and "the assertion failed", and the presence of
an explicit criterion for every cluster.
"""

from __future__ import annotations

from pathlib import Path

# Imported as a module, not by name: pytest tries to collect `tb.TestOutcome` /
# `tb.TestRunReport` as test classes and emits collection warnings for them.
from ats.workflow import test_baseline as tb

REPO_ROOT = Path(__file__).resolve().parents[1]

_BaselineRecord = tb.BaselineRecord

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="4" failures="1" errors="1" skipped="0">
    <testcase classname="tests.test_a" name="test_passes"/>
    <testcase classname="tests.test_a" name="test_assertion_failed">
      <failure message="assert 1 == 2">traceback</failure>
    </testcase>
    <testcase classname="tests.test_b" name="test_setup_broke">
      <error message="[safe-delete] SAFE_DELETE_BULK_CONFIRM_REQUIRED">traceback</error>
    </testcase>
    <testcase classname="tests.test_b" name="test_missing_dep">
      <failure message="No module named 'apscheduler'">traceback</failure>
    </testcase>
  </testsuite>
</testsuites>
"""

TEXT = """
PASSED tests/test_a.py::test_passes
FAILED tests/test_a.py::test_gone - AssertionError: no such table: evidence_observations
ERROR tests/test_b.py::test_setup - [safe-delete] SAFE_DELETE_BULK_CONFIRM_REQUIRED
1 passed, 1 failed, 1 error in 0.50s
"""


# --- 环境依赖入口 ----------------------------------------------------------- #

def test_the_documented_entry_point_is_the_project_uv_entry():
    """Scenario: 在新环境重建测试能力."""
    from ats.runtime.optional_deps import INSTALL_COMMAND

    assert INSTALL_COMMAND == "uv sync --all-extras"
    script = (REPO_ROOT / "scripts" / "run_tests.sh").read_text(encoding="utf-8")
    assert "uv sync --all-extras" in script
    assert "uv run pytest" in script


def test_a_missing_optional_dependency_is_reported_with_its_group():
    """Scenario: 缺失可选依赖被显式报告."""
    from ats.runtime.optional_deps import describe_missing_optional_dependency

    described = describe_missing_optional_dependency("No module named 'apscheduler'")
    assert described is not None
    assert "apscheduler" in described
    assert "uv sync --all-extras" in described


# --- 测量条件 --------------------------------------------------------------- #

def test_a_baseline_record_carries_its_measurement_conditions():
    """Scenario: 复核一次基线测量."""
    record = _BaselineRecord(
        command="./scripts/run_tests.sh",
        dependency_scope="uv sync --all-extras（含全部 extras）",
        environment_conditions=["CPython 3.12.12", "uv 0.9.25"],
        counts="135 failed / 1398 passed",
    )
    rendered = record.render()
    assert "./scripts/run_tests.sh" in rendered
    assert "uv sync --all-extras" in rendered
    assert "135 failed / 1398 passed" in rendered


def test_a_restricted_environment_declares_its_limitations():
    """Scenario: 执行环境限制了测试运行."""
    note = tb.batched_evidence_note(batch_count=18, per_batch=8)
    assert "不得" in note and "全量基线" in note
    record = _BaselineRecord(command="x", dependency_scope="y", limitations=[note])
    assert note in record.render()


# --- 环境性 vs 业务性 -------------------------------------------------------- #

def test_a_setup_error_is_environmental_and_an_assertion_is_business():
    """Scenario: 依赖缺失导致的失败 / 区分后的基线摘要."""
    report = tb.parse_junit_xml(JUNIT)
    summary = tb.summarize(report)
    kinds = {o.nodeid: o.kind for o in report.outcomes}
    assert kinds["tests/test_b.py::test_setup_broke"] == "ERROR"
    assert kinds["tests/test_a.py::test_assertion_failed"] == "FAILED"
    # The missing dependency is environmental even though pytest calls it a failure:
    # the module was never imported, so no assertion was ever evaluated.
    assert summary.environmental == 2
    assert summary.business == 1


def test_a_whole_run_setup_interruption_is_called_out_with_its_scope():
    """Scenario: 整库级的 setup 中断."""
    outcomes = [tb.TestOutcome("PASSED", f"tests/x.py::t{i}") for i in range(6)]
    outcomes += [tb.TestOutcome("ERROR", f"tests/y.py::t{i}",
                             "[safe-delete] SAFE_DELETE_BULK_CONFIRM_REQUIRED")
                 for i in range(10)]
    summary = tb.summarize(tb.TestRunReport(outcomes=outcomes,
                                      raw_notes=["SAFE_DELETE_BULK_CONFIRM_REQUIRED"]))
    assert summary.whole_run_environmental is True
    assert summary.affected_scope == 10
    assert "放大机制" in summary.amplification
    rendered = tb.render_summary(summary)
    assert "整库级环境性中断" in rendered


def test_the_summary_reports_clusters_not_only_totals():
    """Scenario: 多数失败同源."""
    summary = tb.summarize(tb.parse_pytest_text(TEXT))
    table_missing = [c for c in summary.clusters if "evidence_observations" in c.label]
    assert table_missing, summary.clusters
    cluster = table_missing[0]
    assert cluster.size == 1
    assert cluster.criterion, "every cluster must state why its members belong together"
    assert "不计入业务回归" in cluster.disposition or "需逐簇修复" in cluster.disposition


def test_unattributed_failures_are_listed_rather_than_dropped():
    """Scenario: 存在尚未归因的失败."""
    outcomes = [tb.TestOutcome("FAILED", f"tests/z.py::t{i}", "some brand new explosion")
                for i in range(3)]
    summary = tb.summarize(tb.TestRunReport(outcomes=outcomes))
    assert summary.unattributed == 3
    assert any(c.label == "unattributed" for c in summary.clusters)
    assert "未归因" in tb.render_summary(summary)


def test_every_cluster_carries_a_criterion():
    """A cluster with no stated criterion is just a count with a name."""
    report = tb.parse_junit_xml(JUNIT)
    for cluster in tb.summarize(report).clusters:
        assert cluster.criterion.strip(), cluster.label


def test_batched_reports_merge_and_split_deterministically():
    reports = [tb.parse_junit_xml(JUNIT), tb.parse_junit_xml(JUNIT)]
    merged = tb.merge_reports(reports)
    assert len(merged.outcomes) == 8
    assert merged.totals["failures"] == 2
    assert list(tb.iter_batches(list(range(5)), 2)) == [[0, 1], [2, 3], [4]]


# --- 文档可复核 ------------------------------------------------------------- #

def test_the_design_document_points_at_the_baseline_record():
    """Scenario: 文档数字与实测不一致 — the cited numbers must have a place to be
    re-measured from, and that place must be reachable from the design document."""
    baseline_doc = REPO_ROOT / "docs" / "TEST_BASELINE.md"
    design_doc = REPO_ROOT / "docs" / "TARGET_WORKFLOW_DATAFLOW.md"
    assert baseline_doc.exists()
    design = design_doc.read_text(encoding="utf-8")
    assert "docs/TEST_BASELINE.md" in design
    text = baseline_doc.read_text(encoding="utf-8")
    assert "uv sync --all-extras" in text
