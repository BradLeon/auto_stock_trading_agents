from datetime import datetime, timezone
import json

import pytest

from ats.data.structured import (
    ArtifactDescriptor,
    ObservationInput,
    QualityStatus,
    SeriesIdentity,
    SQLiteStructuredRepository,
    build_quality_report,
    render_quality_markdown,
)


NOW = datetime(2026, 8, 25, 8, tzinfo=timezone.utc)


def _repository(tmp_path) -> SQLiteStructuredRepository:
    repo = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog()
    return repo


def _artifact(repo, *, query_scope=None):
    return repo.put_artifact(
        {"value": 100},
        ArtifactDescriptor(
            source_id="tw_mof_exports", dataset_id="regional_tw_exports",
            fetched_at=NOW, query_scope=query_scope or {"period": "2026-07"},
            source_url="https://example.test/tw", media_type="application/json",
            retention="full_response"))


def _observation(repo, artifact_id: str):
    return repo.save_observation(ObservationInput(
        series=SeriesIdentity(
            source_id="tw_mof_exports", dataset_id="regional_tw_exports",
            entity_id="TW_IC_EXPORT", metric_id="regional.tw_ic_exports.value",
            unit="USD", currency="USD", period_basis="month"),
        period="2026-07", value=100.0, published_at=NOW, known_at=NOW,
        fetched_at=NOW, artifact_id=artifact_id,
        quality_status=QualityStatus.ACCEPTED))


def _finish(repo, status: str) -> str:
    run_id = repo.begin_ingestion(
        source_id="tw_mof_exports", dataset_id="regional_tw_exports",
        query_scope={"period": "2026-07"}, at=NOW)
    repo.finish_ingestion(run_id, status=status, at=NOW)
    return run_id


def _tw(report: dict) -> dict:
    return next(row for row in report["datasets"]
                if row["dataset_id"] == "regional_tw_exports")


def test_empty_database_keeps_missing_states_explicit(tmp_path) -> None:
    report = build_quality_report(_repository(tmp_path), now=NOW)
    row = _tw(report)
    assert row["dimensions"]["coverage"]["status"] == "no_coverage"
    assert row["dimensions"]["freshness"]["status"] == "no_data"
    assert row["dimensions"]["availability"]["status"] == "no_run"


def test_partial_success_is_preserved_as_availability_warning(tmp_path) -> None:
    repo = _repository(tmp_path)
    artifact = _artifact(repo)
    _observation(repo, artifact.id)
    _finish(repo, "partial")

    row = _tw(build_quality_report(repo, dataset_id="regional_tw_exports", now=NOW))
    assert row["dimensions"]["coverage"]["status"] == "passed"
    assert row["dimensions"]["availability"]["status"] == "warning"
    assert row["dimensions"]["availability"]["status_counts"] == {"partial": 1}


@pytest.mark.parametrize("status", [
    "zero_match", "not_yet_published", "no_coverage", "stale", "unreachable",
    "unauthorized", "parse_failed", "validation_failed",
])
def test_provider_end_states_are_not_collapsed(tmp_path, status) -> None:
    repo = _repository(tmp_path)
    _finish(repo, status)
    availability = _tw(build_quality_report(
        repo, dataset_id="regional_tw_exports", now=NOW))["dimensions"]["availability"]
    assert availability["status_counts"] == {status: 1}
    expected = "failed" if status in {
        "stale", "unreachable", "unauthorized", "parse_failed", "validation_failed"
    } else "passed"
    assert availability["status"] == expected


def test_conflict_pending_mapping_and_quarantine_are_reported(tmp_path) -> None:
    repo = _repository(tmp_path)
    artifact = _artifact(repo)
    observation = _observation(repo, artifact.id)
    run_id = _finish(repo, "validation_failed")
    repo.save_candidate(
        candidate_id="candidate-1", run_id=run_id, source_id="tw_mof_exports",
        dataset_id="regional_tw_exports", entity_id="TW_IC_EXPORT",
        provider_field="mystery", metric_id="", period="2026-07", value=1,
        unit="", currency="", status="quarantined",
        reason_codes=["metric_unmapped"], artifact_id=artifact.id, raw={})
    repo.record_pending_mapping(
        provider="tw_mof", dataset_id="regional_tw_exports",
        provider_field="mystery", sample={"value": 1}, at=NOW)
    repo.record_conflict(
        dataset_id="regional_tw_exports", entity_id="TW_IC_EXPORT",
        metric_id="regional.tw_ic_exports.value", period="2026-07",
        left_observation_id=observation.id, right_source_id="other_source",
        right_value=90, absolute_difference=10, relative_difference=0.1, at=NOW)

    row = _tw(build_quality_report(repo, dataset_id="regional_tw_exports", now=NOW))
    assert row["dimensions"]["accuracy_reconciliation"]["status"] == "failed"
    assert row["dimensions"]["accuracy_reconciliation"]["open_conflicts"] == 1
    assert row["dimensions"]["completeness"]["pending_mappings"] == 1
    assert row["dimensions"]["completeness"]["status"] == "warning"


def test_artifact_usage_reports_content_hash_deduplication(tmp_path) -> None:
    repo = _repository(tmp_path)
    first = _artifact(repo, query_scope={"run": 1})
    second = _artifact(repo, query_scope={"run": 2})
    usage = repo.artifact_usage()

    assert first.blob_id == second.blob_id
    assert usage["artifacts"] == 2
    assert usage["unique_blobs"] == 1
    assert usage["deduplicated_references"] == 1
    assert usage["deduplication_rate"] == 0.5
    assert usage["by_source"][0]["retention"] == "full_response"


def test_report_exposes_runtime_excluded_sources_without_datasets(tmp_path) -> None:
    report = build_quality_report(_repository(tmp_path), now=NOW)
    assert report["catalog"]["runtime_excluded"] == [
        "ibkr_market", "thetadata_options", "yfinance_market", "yfinance_options"]
    dataset_ids = {row["dataset_id"] for row in report["datasets"]}
    assert not dataset_ids & {"ticker_price", "ohlcv", "option_chain"}


def test_markdown_and_machine_report_have_the_same_dimensions(tmp_path) -> None:
    report = build_quality_report(_repository(tmp_path), now=NOW)
    markdown = render_quality_markdown(report)
    assert "| Dataset | Overall | Coverage | Accuracy | Freshness" in markdown
    assert "`regional_tw_exports`" in markdown
    assert "Deduplication rate" in markdown


def test_freshness_can_gate_on_underlying_source_period_lag(tmp_path) -> None:
    repo = _repository(tmp_path)
    repo.conn.execute(
        "UPDATE structured_datasets SET quality_json=? WHERE dataset_id=?",
        (json.dumps({"freshness_hours_max": 240, "source_period_lag_days_max": 30}),
         "regional_tw_exports"))
    artifact = _artifact(repo)
    observation = _observation(repo, artifact.id)
    repo.conn.execute(
        "UPDATE structured_observations SET period_end=? WHERE observation_id=?",
        ("2026-06-30", observation.id))
    _finish(repo, "succeeded")

    freshness = _tw(build_quality_report(
        repo, dataset_id="regional_tw_exports", now=NOW))["dimensions"]["freshness"]

    assert freshness["status"] == "stale"
    assert freshness["latest_period_end"] == "2026-06-30"
    assert freshness["source_period_lag_days"] > 30


def test_cli_structured_quality_and_inventory(monkeypatch, capsys, tmp_path) -> None:
    from ats.data.products.products import DataProducts
    from ats.runtime import cli

    class EmptyDocumentStore:
        pass

    products = DataProducts(
        store=EmptyDocumentStore(), structured_repository=_repository(tmp_path))
    monkeypatch.setattr("ats.data.products.get_data_products", lambda: products)

    assert cli.run_data("sources") == 0
    sources = json.loads(capsys.readouterr().out)
    assert any(row["source_id"] == "sec_companyfacts" for row in sources)

    assert cli.run_data(
        "quality", dataset="regional_tw_exports", output_format="markdown") == 0
    assert "Structured Data Quality Report" in capsys.readouterr().out

    assert cli.run_data("pending-mappings", status="pending") == 0
    assert json.loads(capsys.readouterr().out) == []
