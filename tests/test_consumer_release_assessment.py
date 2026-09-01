"""Phase 10.5 release classification is evidence-based and non-mutating."""

from __future__ import annotations

from ats.data.cutover import (
    consumer_release_records,
    record_consumer_comparison,
    record_consumer_release_decision,
    record_consumer_release_verification,
)
from ats.data.release_assessment import (
    EQUIVALENT,
    GOVERNED_UPGRADE,
    ORCHESTRATION_BOUNDARY,
    PLATFORM_REGRESSION,
    assess_consumer_release,
)
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.data.pipelines.structured.release import ReleaseManager


def test_equivalent_direct_consumer_can_pass_data_evidence_gate(tmp_path) -> None:
    path = tmp_path / "data.sqlite"
    record_consumer_comparison(
        consumer="sector_agent", entity="REGIONAL:TW", data_db=path, status="reconciled",
        details={"legacy": {"tw": ["2026-07", 1]}, "platform": {"tw": ["2026-07", 1]},
                 "reason": "identical_levels_and_derivations"},
    )

    report = assess_consumer_release(consumer="sector-agent", data_db=path)

    assert report["category"] == EQUIVALENT
    assert report["platform_eligible"] is True


def test_legacy_network_failure_needs_independent_governed_upgrade_verification(tmp_path) -> None:
    path = tmp_path / "data.sqlite"
    record_consumer_comparison(
        consumer="macro_agent", entity="REGIONAL:TW", data_db=path, status="mismatch",
        details={"legacy": {}, "platform": {"tw": ["2026-07", 1]}, "reason": "ConnectError"},
    )

    candidate = assess_consumer_release(consumer="macro_agent", data_db=path)
    assert candidate["category"] == GOVERNED_UPGRADE
    assert candidate["platform_eligible"] is False
    assert next(row for row in candidate["checks"]
                if row["check"] == "independent_governed_upgrade_verification")["passed"] is False

    record_consumer_release_verification(
        consumer="macro_agent", entity="REGIONAL:TW", data_db=path,
        details={
            "lineage": "structured observation and raw official artifact reviewed",
            "period_unit_definition": "2026-07, reported unit, same regional economic series",
            "freshness": "known_at and cadence reviewed",
            "output": "macro report fixture rendered from the reviewed observation",
        },
    )
    verified = assess_consumer_release(consumer="macro_agent", data_db=path)
    assert verified["category"] == GOVERNED_UPGRADE
    assert verified["platform_eligible"] is True


def test_empty_legacy_statement_dto_is_governed_upgrade_not_regression(tmp_path) -> None:
    """An old provider returning no DTO needs review, not an automatic rejection."""
    path = tmp_path / "data.sqlite"
    record_consumer_comparison(
        consumer="pead_fundamentals", entity="MSFT", data_db=path, status="mismatch",
        details={
            "legacy": [],
            "platform": ["2026-06-30", ["Revenue", "EPS", "CapEx", "Total Debt"]],
            "reconciliation": {"reason": "statement_unavailable_on_one_side"},
        },
    )

    candidate = assess_consumer_release(consumer="pead_fundamentals", data_db=path)

    assert candidate["category"] == GOVERNED_UPGRADE
    assert candidate["platform_eligible"] is False
    assert next(row for row in candidate["checks"]
                if row["check"] == "independent_governed_upgrade_verification")["passed"] is False


def test_event_bound_pead_output_upgrade_requires_and_accepts_review(tmp_path) -> None:
    """A correct event package may supersede a stale legacy calendar output."""
    path = tmp_path / "data.sqlite"
    record_consumer_comparison(
        consumer="pead_graph", entity="NVDA", data_db=path, status="reconciled",
        details={
            "legacy": {"event_date": "2026-11-17", "scorecard": 0.0},
            "platform": {
                "event_date": "2026-08-26",
                "documents": ["release@v1", "filing@v1", "transcript@v1"],
                "scorecard": 0.0,
            },
            "reconciliation": {"kind": "governed_event_binding_upgrade"},
        },
    )
    candidate = assess_consumer_release(consumer="pead_graph", data_db=path)
    assert candidate["category"] == GOVERNED_UPGRADE
    assert candidate["platform_eligible"] is False

    record_consumer_release_verification(
        consumer="pead_graph", entity="NVDA", data_db=path,
        details={
            "lineage": "immutable release, filing and transcript versions reviewed",
            "period_unit_definition": "NVDA Q2 FY2027, official event date",
            "freshness": "accepted event package reviewed",
            "output": "same deterministic scorecard and no order",
        },
    )
    assert assess_consumer_release(consumer="pead_graph", data_db=path)["platform_eligible"] is True


def test_nonempty_shadow_difference_is_platform_regression(tmp_path) -> None:
    path = tmp_path / "data.sqlite"
    record_consumer_comparison(
        consumer="pead_fundamentals", entity="TSM", data_db=path, status="mismatch",
        details={"legacy": {"fcf": 1}, "platform": {"fcf": 2},
                 "reconciliation": {"reason": "same_period_core_value_difference"}},
    )

    report = assess_consumer_release(consumer="pead_fundamentals", data_db=path)

    assert report["category"] == PLATFORM_REGRESSION
    assert report["platform_eligible"] is False


def test_table_hash_equivalence_does_not_substitute_for_unstructured_consumer_output(tmp_path) -> None:
    path = tmp_path / "data.sqlite"
    record_consumer_comparison(
        consumer="evidence-chain", entity="NVDA", data_db=path, status="reconciled",
        details={"comparisons": {"documents": {"matched": True}}},
    )

    report = assess_consumer_release(consumer="evidence_chain", data_db=path)

    assert report["category"] == EQUIVALENT
    assert report["platform_eligible"] is False
    assert next(row for row in report["checks"]
                if row["check"] == "coverage_freshness_output_evidence")["passed"] is False


def test_orchestration_boundary_ignores_old_table_reconciliation_and_cannot_publish(tmp_path) -> None:
    path = tmp_path / "data.sqlite"
    record_consumer_comparison(
        consumer="chief-graph", entity="NVDA", data_db=path, status="reconciled",
        details={"legacy": {"memory": "hash"}, "platform": {"memory": "hash"}},
    )
    report = assess_consumer_release(consumer="chief_graph", data_db=path)
    assert report["category"] == ORCHESTRATION_BOUNDARY
    assert report["platform_eligible"] is False
    assert report["legacy_comparison_records_ignored"] == 1

    repository = SQLiteStructuredRepository(path, artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog()
    try:
        check = ReleaseManager(repository, path=tmp_path / "releases.yaml").check_consumer(
            "chief_graph", mode="platform", data_db=path)
    finally:
        repository.close()
    assessment = next(row["assessment"] for row in check["checks"]
                      if row["check"] == "consumer_release_assessment")
    assert assessment["category"] == ORCHESTRATION_BOUNDARY
    assert check["ready"] is False


def test_release_decision_is_durable_but_does_not_change_shadow_eligibility(tmp_path) -> None:
    path = tmp_path / "data.sqlite"
    record_consumer_comparison(
        consumer="sector_agent", entity="REGIONAL:TW", data_db=path, status="reconciled",
        details={"legacy": {"tw": ["2026-07", 1]}, "platform": {"tw": ["2026-07", 1]}},
    )
    assessment = assess_consumer_release(consumer="sector_agent", data_db=path)
    recorded = record_consumer_release_decision(
        consumer="sector_agent", data_db=path, assessment=assessment, decision="published",
        mode_before="shadow", mode_after="platform",
        details={
            "inputs": "regional_tw_exports and regional_kr_exports",
            "output": "sector context fixture",
            "lineage": "observation IDs and raw official artifacts",
            "failure_handling": "shadow/fallback retains legacy on provider failure",
            "rollback": "consumer overlay platform to legacy drill",
        },
    )

    assert recorded["decision"] == "published"
    assert consumer_release_records(consumer="sector_agent", data_db=path) == [recorded]
    assert assess_consumer_release(consumer="sector_agent", data_db=path)["platform_eligible"] is True


def test_release_decision_cannot_bypass_a_failed_assessment(tmp_path) -> None:
    path = tmp_path / "data.sqlite"
    assessment = assess_consumer_release(consumer="pead_monitor", data_db=path)
    try:
        record_consumer_release_decision(
            consumer="pead_monitor", data_db=path, assessment=assessment, decision="published",
            mode_before="shadow", mode_after="platform",
            details={key: "evidence" for key in (
                "inputs", "output", "lineage", "failure_handling", "rollback")},
        )
    except ValueError as exc:
        assert "without a passing" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("failed assessment must not be publishable")


def test_consumer_release_records_cli_is_read_only(monkeypatch, tmp_path, capsys) -> None:
    from ats.runtime.cli import main

    record_consumer_comparison(
        consumer="sector_agent", entity="REGIONAL:TW", data_db=tmp_path / "data.sqlite",
        status="reconciled", details={"legacy": {"tw": 1}, "platform": {"tw": 1}},
    )
    assessment = assess_consumer_release(consumer="sector_agent", data_db=tmp_path / "data.sqlite")
    record_consumer_release_decision(
        consumer="sector_agent", data_db=tmp_path / "data.sqlite", assessment=assessment,
        decision="published", mode_before="shadow", mode_after="platform",
        details={key: "evidence" for key in (
            "inputs", "output", "lineage", "failure_handling", "rollback")},
    )

    assert main(["data", "consumer-release-records", "--consumer", "sector_agent",
                 "--target-db", str(tmp_path / "data.sqlite")]) == 0
    assert '"decision": "published"' in capsys.readouterr().out
