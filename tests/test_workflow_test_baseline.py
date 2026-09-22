"""Baseline attribution: environmental failures must be reported as one cause with an
affected scope, business failures as clusters with a criterion, and whatever remains
as an explicit unattributed bucket. This is what keeps a green baseline meaningful
across Phases B–E.
"""

from __future__ import annotations

from ats.workflow.test_baseline import (
    BaselineRecord,
    batched_evidence_note,
    iter_batches,
    parse_junit_xml,
    parse_pytest_text,
    render_summary,
    summarize,
)


def _guard_interrupted_log(error_count: int = 1511, passed: int = 22) -> str:
    lines = [
        "SAFE_DELETE_BULK_CONFIRM_REQUIRED "
        '{"count":50,"threshold":50,"scope":"turn"}',
    ]
    for i in range(error_count):
        lines.append(f"ERROR tests/test_mod_{i % 40}.py::test_case_{i} - SystemExit: 1")
    for i in range(passed):
        lines.append(f"PASSED tests/test_ok.py::test_case_{i}")
    lines.append(f"{passed} passed, {error_count} errors in 13.00s")
    return "\n".join(lines)


def test_parse_pytest_text_extracts_counts_and_outcomes():
    text = "\n".join(
        [
            "FAILED tests/test_chain_evidence.py::test_x - sqlite3.OperationalError: "
            "no such table: evidence_observations",
            "PASSED tests/test_chain_evidence.py::test_y",
            "1 failed, 1 passed in 3.21s",
        ]
    )
    report = parse_pytest_text(text)
    assert report.totals["failed"] == 1
    assert report.totals["passed"] == 1
    assert len(report.failures) == 1
    assert report.failures[0].message.endswith("no such table: evidence_observations")


def test_missing_table_failures_cluster_with_a_criterion():
    text = "\n".join(
        [
            f"FAILED tests/test_chain_{i}.py::test_case - sqlite3.OperationalError: "
            "no such table: evidence_observations"
            for i in range(12)
        ]
        + ["12 failed in 9.10s"]
    )
    summary = summarize(parse_pytest_text(text))
    cluster = next(c for c in summary.clusters if c.label.startswith("missing table"))
    assert cluster.size == 12
    assert cluster.kind == "business"
    assert "evidence_observations" in cluster.label
    assert cluster.criterion  # 判据不得为空


def test_signature_drift_failures_cluster_by_argument():
    text = "\n".join(
        [
            "ERROR tests/test_monitor.py::test_a - TypeError: lambda() got an "
            "unexpected keyword argument 'consumer'",
            "ERROR tests/test_triage.py::test_b - TypeError: lambda() got an "
            "unexpected keyword argument 'consumer'",
            "2 errors in 1.00s",
        ]
    )
    summary = summarize(parse_pytest_text(text))
    cluster = next(c for c in summary.clusters if "signature drift" in c.label)
    assert cluster.size == 2
    assert "consumer" in cluster.label


def test_whole_run_setup_interruption_is_one_environmental_cause():
    """1.7: a suite-wide setup interruption is a single environmental cause, not N
    business failures — with the amplification mechanism and affected scope stated."""
    summary = summarize(parse_pytest_text(_guard_interrupted_log()))
    assert summary.whole_run_environmental is True
    assert summary.environmental == 1511
    assert summary.business == 0, "setup interruptions must not count as business failures"
    assert "放大" in summary.amplification
    assert "_isolate_db" in summary.amplification
    assert summary.affected_scope == 1511


def test_missing_optional_dependency_is_environmental_not_a_regression():
    text = "\n".join(
        [
            "ERROR tests/test_scheduler_jobs.py::test_a - ModuleNotFoundError: "
            "No module named 'apscheduler'",
            "1 error in 0.50s",
        ]
    )
    summary = summarize(parse_pytest_text(text))
    assert summary.environmental == 1
    assert summary.business == 0
    cluster = summary.clusters[0]
    assert cluster.kind == "environmental"
    assert "apscheduler" in cluster.criterion


def test_unattributed_failures_are_reported_separately():
    text = "\n".join(
        [
            "FAILED tests/test_a.py::test_one - AssertionError: assert 1 == 2",
            "FAILED tests/test_b.py::test_two - AssertionError: assert 'a' == 'b'",
            "2 failed in 0.30s",
        ]
    )
    summary = summarize(parse_pytest_text(text))
    assert summary.unattributed == 2
    rendered = render_summary(summary)
    assert "未归因" in rendered


def test_render_summary_lists_clusters_and_disposition():
    text = "\n".join(
        [
            "FAILED tests/test_a.py::test_one - sqlite3.OperationalError: "
            "no such table: evidence_facts",
            "ERROR tests/test_b.py::test_two - SystemExit: 1",
            "1 failed, 1 error in 0.40s",
        ]
    )
    rendered = render_summary(summarize(parse_pytest_text(text)))
    assert "失败簇" in rendered
    assert "missing table: evidence_facts" in rendered
    assert "environmental" in rendered


def test_render_summary_includes_measurement_record():
    record = BaselineRecord(
        command="uv run pytest",
        dependency_scope="uv sync --all-extras",
        environment_conditions=["沙箱对临时目录 mkdir 设限"],
        limitations=["分批运行不覆盖跨批次顺序依赖"],
    )
    rendered = render_summary(summarize(parse_pytest_text("1 passed in 0.1s")), record=record)
    assert "uv run pytest" in rendered
    assert "uv sync --all-extras" in rendered
    assert "沙箱对临时目录 mkdir 设限" in rendered


def test_parse_junit_xml_separates_setup_errors_from_assertion_failures():
    xml = (
        "<testsuites><testsuite name='pytest' tests='2' failures='1' errors='1'>"
        "<testcase classname='tests.test_x' name='test_ok'/>"
        "<testcase classname='tests.test_x' name='test_assert'>"
        "<failure message='assert 20000.0 == 10000'/></testcase>"
        "<testcase classname='tests.test_x' name='test_setup'>"
        "<error message='SystemExit: 1'/></testcase>"
        "</testsuite></testsuites>"
    )
    report = parse_junit_xml(xml)
    summary = summarize(report)
    assert summary.passed == 1
    assert summary.business == 1
    assert summary.environmental == 1


def test_batched_evidence_note_states_its_limitations():
    note = batched_evidence_note(batch_count=14, per_batch=10)
    assert "分批" in note
    assert "不得" in note  # 必须写明不能替代全量基线


def test_iter_batches_splits_consecutively():
    batches = list(iter_batches([str(i) for i in range(7)], 3))
    assert batches == [["0", "1", "2"], ["3", "4", "5"], ["6"]]
