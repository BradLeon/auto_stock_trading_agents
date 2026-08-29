"""Phase 10.5 release classification is evidence-based and non-mutating."""

from __future__ import annotations

from ats.data.cutover import (
    record_consumer_comparison,
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
