"""Governed DataProducts queries, derivations, SQL and snapshot replay."""

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from ats.data.products import DataProducts
from ats.data.structured import (
    ArtifactDescriptor,
    ObservationInput,
    QualityStatus,
    SeriesIdentity,
    SQLiteStructuredRepository,
    StructuredCatalog,
)


T0 = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


class _LegacyStore:
    def projection_lineage(self, identifier):
        return {"legacy": identifier}


def _repo(tmp_path):
    repo = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    return repo


def _save(repo, *, entity="MSFT", metric="financial.revenue.gaap",
          source="sec_companyfacts", dataset="company_financials",
          period="FY2025Q1", value=100.0, currency="USD", unit="USD",
          period_start="2024-07-01", period_end="2024-09-30",
          known_at=T0, quality=QualityStatus.ACCEPTED):
    artifact = repo.put_artifact(
        {"entity": entity, "period": period, "value": value},
        ArtifactDescriptor(
            source_id=source, dataset_id=dataset, fetched_at=known_at,
            query_scope={"entity": entity, "period": period},
            source_url="https://example.test/data", source_version=known_at.isoformat(),
            media_type="application/json"))
    return repo.save_observation(ObservationInput(
        series=SeriesIdentity(
            source_id=source, dataset_id=dataset, entity_id=entity,
            metric_id=metric, unit=unit, currency=currency,
            period_basis="quarter", adjustment="gaap"),
        period=period, period_start=period_start, period_end=period_end,
        value=value, published_at=known_at - timedelta(hours=1),
        known_at=known_at, fetched_at=known_at, artifact_id=artifact.id,
        quality_status=quality, raw={"value": value})).id


def _products(repo):
    return DataProducts(store=_LegacyStore(), structured_repository=repo)


def test_metric_series_latest_all_vintages_source_and_strict_as_of(tmp_path):
    repo = _repo(tmp_path)
    first = _save(repo, value=100, known_at=T0)
    second = _save(repo, value=102, known_at=T0 + timedelta(days=5))
    products = _products(repo)

    latest = products.metric_series(
        metric="financial.revenue.gaap", entity="MSFT", dataset="company_financials")
    vintages = products.metric_series(
        metric="financial.revenue.gaap", entity="MSFT", dataset="company_financials",
        include_vintages=True)
    historical = products.metric_series(
        metric="financial.revenue.gaap", entity="MSFT", dataset="company_financials",
        as_of=T0 + timedelta(days=1))
    before = products.metric_series(
        metric="financial.revenue.gaap", entity="MSFT", dataset="company_financials",
        as_of=T0 - timedelta(days=1))

    assert [(row["observation_id"], row["value"]) for row in latest["rows"]] == [(second, 102)]
    assert [(row["observation_id"], row["value"]) for row in vintages["rows"]] == [
        (first, 100), (second, 102)]
    assert historical["rows"][0]["observation_id"] == first
    assert before["status"] == "not_yet_known" and before["rows"] == []


def test_source_selection_conflict_strict_loose_fallback_and_gap_state(tmp_path):
    repo = _repo(tmp_path)
    _save(repo, value=100, source="sec_companyfacts")
    _save(repo, value=99, source="defeatbeta_stock_statement",
          quality=QualityStatus.CONFLICT)
    _save(repo, entity="AMZN", value=80, source="defeatbeta_stock_statement")
    run = repo.begin_ingestion(
        source_id="sec_companyfacts", dataset_id="company_financials", query_scope={})
    repo.finish_ingestion(run, status="unreachable")
    products = _products(repo)

    strict = products.metric_series(
        metric="financial.revenue.gaap", entity="MSFT", dataset="company_financials")
    loose = products.metric_series(
        metric="financial.revenue.gaap", entity="MSFT", dataset="company_financials",
        quality="loose")
    fallback = products.metric_series(
        metric="financial.revenue.gaap", entity="AMZN", dataset="company_financials")
    missing = products.metric_series(
        metric="financial.revenue.gaap", entity="KLAC", dataset="company_financials",
        source_id="sec_companyfacts")

    assert strict["status"] == "quality_rejected" and strict["rows"] == []
    assert strict["rejected"][0]["strict_reasons"] == ["source_conflict"]
    assert loose["rows"][0]["selected_source"] == "sec_companyfacts"
    assert loose["rows"][0]["conflict"] is True
    assert set(loose["conflicts"][0]["sources"]) == {
        "sec_companyfacts", "defeatbeta_stock_statement"}
    assert fallback["rows"][0]["selection_reason"] == "fallback_source_used"
    assert missing["status"] == "unreachable"


def test_cross_section_preserves_partial_coverage_and_marks_incomparable(tmp_path):
    repo = _repo(tmp_path)
    _save(repo, entity="MSFT", currency="USD", unit="USD")
    _save(repo, entity="TSM", currency="TWD", unit="TWD",
          period_start="2024-10-01", period_end="2024-12-31")

    result = _products(repo).cross_section(
        metric="financial.revenue.gaap", entities=["MSFT", "TSM", "KLAC"],
        dataset="company_financials", period="FY2025Q1")

    assert len(result["rows"]) == 2 and len(result["missing"]) == 1
    assert result["missing"][0]["entity_id"] == "KLAC"
    assert result["rows"][0]["comparability"] == "comparable"
    assert result["rows"][1]["comparability"] == "incomparable"
    assert {"period_start_differs", "period_end_differs", "unit_differs", "currency_differs"} \
        <= set(result["rows"][1]["comparability_reasons"])


def test_versioned_yoy_mom_rolling_and_explicit_fx_never_use_zero_for_missing(tmp_path):
    repo = _repo(tmp_path)
    products = _products(repo)
    rows = []
    for month, value in (("2025-01", 100), ("2025-02", 110), ("2026-01", 120)):
        oid = _save(
            repo, entity="TW_IC_EXPORT", metric="regional.tw_ic_exports.value",
            source="tw_mof_exports", dataset="regional_tw_exports", period=month,
            value=value, currency="USD", unit="USD M", period_start=f"{month}-01",
            period_end=f"{month}-28")
        rows.append(repo.observation(oid))
    query = {"metric_id": "regional.tw_ic_exports.value", "rows": rows}

    yoy = products.derive(operation="yoy", query_result=query, version="v1")
    mom = products.derive(operation="mom", query_result=query, version="v1")
    rolling = products.derive(
        operation="rolling", query_result=query, window=2, min_periods=2)
    fx = products.derive(
        operation="fx_convert", query_result={"metric_id": "revenue", "rows": [rows[0]]},
        fx_result={"rows": [dict(rows[0], value=0.032,
                                 observation_id="explicit-fx", currency="USD/TWD")]},
        target_currency="USD")

    assert yoy["rows"][0]["value"] is None
    assert yoy["rows"][0]["derivation_status"] == "insufficient_history"
    assert yoy["rows"][-1]["value"] == pytest.approx(0.2)
    assert mom["rows"][1]["value"] == pytest.approx(0.1)
    assert rolling["rows"][0]["value"] is None
    assert rolling["rows"][1]["value"] == 105
    assert fx["rows"][0]["value"] == pytest.approx(3.2)
    assert fx["rows"][0]["original_currency"] == "USD"
    assert fx["rows"][0]["fx_observation_id"] == "explicit-fx"
    assert {row["definition_version"] for row in repo.derivations()} == {"v1"}


def test_dataframe_and_read_only_sql_share_accepted_rows(tmp_path):
    repo = _repo(tmp_path)
    _save(repo)
    products = _products(repo)
    frame = products.metric_series(
        metric="financial.revenue.gaap", entity="MSFT", dataset="company_financials",
        source_id="sec_companyfacts", as_frame=True)
    conn = products.read_only_sql()
    try:
        sql_rows = conn.execute(
            "SELECT entity_id,metric_id,period,value,source_id,known_at "
            "FROM structured_observations_accepted WHERE entity_id='MSFT' "
            "AND metric_id='financial.revenue.gaap'").fetchall()
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO structured_metrics(metric_id) VALUES ('forbidden')")
    finally:
        conn.close()

    columns = ["entity_id", "metric_id", "period", "value", "source_id", "known_at"]
    assert frame[columns].to_dict("records") == [dict(row) for row in sql_rows]
    assert {"quality_status", "artifact_id", "observation_id", "lineage"} <= set(frame.columns)


def test_discovery_health_lineage_and_snapshot_replay_are_stable(tmp_path):
    repo = _repo(tmp_path)
    first = _save(repo, value=100, known_at=T0)
    products = _products(repo)
    result = products.metric_series(
        metric="financial.revenue.gaap", entity="MSFT", dataset="company_financials")
    manifest = products.snapshot_manifest(
        consumer="pead", purpose="test-replay", as_of=T0, rows=[
            *result["rows"],
            {"observation_id": "runtime-price", "input_mode": "runtime"},
        ])

    _save(repo, value=150, known_at=T0 + timedelta(days=30))
    replay = products.replay_snapshot(manifest["snapshot_id"])

    assert len(products.sources()) == 14
    assert len(products.datasets()) == 7
    assert len(products.metrics()) == 76
    assert any(row["source_id"] == "sec_companyfacts" for row in products.structured_health())
    assert products.lineage(first)["artifact"]["source_url"] == "https://example.test/data"
    assert [row["value"] for row in replay["rows"]] == [100]
    assert [item["observation_id"] for item in replay["items"]] == [first]
    assert products.lineage("old-projection") == {"legacy": "old-projection"}
