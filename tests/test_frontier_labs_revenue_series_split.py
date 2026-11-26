"""Revenue trends stay inside one source; a company's own filing figures form their own series beside the others."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ats.agents.evidence.commercialization import (
    observe_ai_commercialization,
    render_ai_commercialization_markdown,
)
from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import (
    AdapterArtifact,
    AdapterBatch,
    FetchRequest,
    IngestionStatus,
    NativeRecord,
)
from ats.data.pipelines.structured import disclosure_metrics as pipeline
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.products import DataProducts
from ats.data.products.frontier_ai_labs_revenue import (
    SOURCE_COMPANY_FILING,
    SOURCE_SACRA,
    SOURCE_TICKERTRENDS,
)
from ats.data.sources.governed_observer import commercialization_points
from ats.data.stores.structured import disclosure_review as review
from ats.data.stores.structured.repository import SQLiteStructuredRepository

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
BN = 1_000_000_000.0


@pytest.fixture
def repo(tmp_path):
    repository = SQLiteStructuredRepository(tmp_path / "revenue.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog(StructuredCatalog.load())
    yield repository
    repository.close()


def _ingest(repo, source, points):
    """One third-party source's monthly run-rate readings for Anthropic."""

    class Monthly:
        def fetch(self, request):
            records, artifacts = [], []
            for month, value in points:
                key = f"{source}:{month}"
                records.append(NativeRecord(
                    entity_id="ANTHROPIC", provider_field="annualized_revenue_run_rate", period=month,
                    period_start=f"{month}-01", period_end=f"{month}-28", period_basis="calendar_month",
                    value=value * BN, unit="USD", currency="USD", published_at=NOW,
                    dimensions={"observation_identity": "third_party_estimate",
                                "methodology_regime": "third_party_annualized_run_rate/v1",
                                "raw_metric_label": "annualized revenue"},
                    slice_key=key, raw={"raw_quote": "q", "publisher_url": "https://x.test",
                                        "origin_citation": {"origin_publisher": source}}))
                artifacts.append(AdapterArtifact(artifact_key=key, payload=key, slice_key=key))
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                status=IngestionStatus.SUCCEEDED, fetched_at=NOW, records=records, artifacts=artifacts)

    outcome = IngestionPipeline(repo).run(Monthly(), FetchRequest(source_id=source,
                                                                  dataset_id="frontier_ai_labs_revenue"))
    assert outcome["accepted"] == len(points), outcome


def _file(repo, key, period, value, *, basis, at=NOW):
    metric = pipeline.issuer_metrics("ANT", [key])[1][0]
    document = {"entity": "ANT", "document_id": "doc-424B4", "version_id": "v1", "filing_date": "2026-11-19",
                "form_type": "424B4", "accession_number": "0000000000-26-000001"}
    checked = {"period_end": period, "period_basis": basis, "bucket": "", "value": value * BN,
               "quote": f"{key} {period} {value}", "char_start": 0, "char_end": 10, "reasons": [],
               "qualifier": "approximately"}
    candidate = pipeline.save_candidate(repo, document, metric, checked, extraction_id="e", method="test", now=at)
    review.confirm(repo, candidate, reviewer="owner", at=at)


def _anthropic(repo, as_of=NOW):
    bundle = DataProducts(structured_repository=repo).frontier_labs_revenue_evidence_bundle(
        companies=["ANTHROPIC"], as_of=as_of)
    return bundle, bundle["companies"][0]


def test_a_source_switch_is_not_reported_as_a_decline(repo):
    _ingest(repo, SOURCE_TICKERTRENDS, [("2026-06", 69.6)])
    _ingest(repo, SOURCE_SACRA, [("2026-07", 65.0)])
    bundle, view = _anthropic(repo)
    assert [[point["period"] for point in item["points"]] for item in view["series"]] == [["2026-07"], ["2026-06"]]
    assert view["trend"]["status"] == "insufficient_history" and view["trend"]["comparable_point_count"] == 1
    assert "net_change_rate" not in view["trend"] and "last_comparable_change_rate" not in view["trend"]
    assert len({row["source_id"] for row in bundle["history_rows"]}) == 1


def test_each_source_keeps_its_own_trend(repo):
    _ingest(repo, SOURCE_TICKERTRENDS, [("2026-01", 10.2), ("2026-04", 35.6), ("2026-06", 69.6)])
    _ingest(repo, SOURCE_SACRA, [("2025-12", 9.0), ("2026-05", 47.0), ("2026-07", 65.0), ("2026-10", 80.0)])
    _, view = _anthropic(repo)
    assert view["comparison_cell"]["source_id"] == SOURCE_SACRA
    assert view["trend"]["observed_periods"] == ["2025-12", "2026-05", "2026-07", "2026-10"]
    assert {item["source_id"]: item["trend_status"] for item in view["series"]} == {
        SOURCE_SACRA: "expanding", SOURCE_TICKERTRENDS: "expanding"}
    assert view["trend"]["last_comparable_change_rate"] > 0


def test_filing_figures_form_a_company_series_beside_third_party_ones(repo):
    _ingest(repo, SOURCE_SACRA, [("2026-05", 47.0), ("2026-07", 65.0), ("2026-10", 80.0)])
    for period, value in (("2026-03-31", 4.731), ("2026-06-30", 11.555), ("2026-09-30", 16.956)):
        _file(repo, "revenue.total", period, value, basis="quarter")
    _file(repo, "revenue.total", "2026-09-30", 33.242, basis="ytd_9m")
    _file(repo, "revenue.run_rate", "2026-10-31", 81.0, basis="instant")
    bundle, view = _anthropic(repo, as_of=NOW + timedelta(hours=1))
    filed = {(item["metric_id"].split(".")[-1], item["period_basis"]): item for item in view["series"]
             if item["source_id"] == SOURCE_COMPANY_FILING}
    assert set(filed) == {("trailing_revenue", "quarter"), ("annualized_revenue_run_rate", "instant")}
    assert all(item["observation_identity"] == "company_reported" and not item["is_comparison"]
               for item in filed.values())
    assert [point["period"] for point in filed["trailing_revenue", "quarter"]["points"]] == [
        "2026-03-31", "2026-06-30", "2026-09-30"]                              # year-to-date stays out
    assert filed["trailing_revenue", "quarter"]["trend_status"] == "expanding"
    assert view["comparison_cell"]["source_id"] == SOURCE_SACRA and view["trend"]["comparable_point_count"] == 3
    assert bundle["source_conflicts"] == []
    rows = DataProducts(structured_repository=repo).frontier_labs_revenue_series(
        company="ANTHROPIC", source_id=SOURCE_COMPANY_FILING)["rows"]
    assert len(rows) == 4 and {row["source_label"] for row in rows} == {"公司招股书与定期报告自报"}
    assert all(row["observation_id"] and row["citation"]["raw_quote"] for row in rows)
    _, before = _anthropic(repo, as_of=NOW - timedelta(hours=1))
    assert {item["source_id"] for item in before["series"]} <= {SOURCE_SACRA}


def test_filing_series_alone_still_leaves_a_replayable_snapshot(repo):
    for period, value in (("2026-03-31", 4.731), ("2026-06-30", 11.555), ("2026-09-30", 16.956)):
        _file(repo, "revenue.total", period, value, basis="quarter")
    products = DataProducts(structured_repository=repo)
    bundle = products.frontier_labs_revenue_evidence_bundle(
        companies=["ANTHROPIC"], as_of=NOW + timedelta(hours=1), snapshot_consumer="evidence_observer",
        snapshot_purpose="ai_frontier_labs_commercialization:v1")
    assert bundle["companies"][0]["comparison_cell"]["source_id"] == SOURCE_COMPANY_FILING
    replay = products.replay_frontier_labs_revenue_bundle(bundle["manifest"]["snapshot_id"])
    assert {row["observation_id"] for row in replay["rows"]} == {
        row["observation_id"] for row in bundle["history_rows"]}


def test_report_lists_the_series_apart_and_the_observer_point_stays_in_one_source(repo):
    _ingest(repo, SOURCE_TICKERTRENDS, [("2026-06", 69.6)])
    _ingest(repo, SOURCE_SACRA, [("2026-05", 47.0), ("2026-07", 65.0), ("2026-10", 80.0)])
    _file(repo, "revenue.run_rate", "2026-10-31", 81.0, basis="instant")
    packet = observe_ai_commercialization(
        products=DataProducts(structured_repository=repo), as_of=NOW + timedelta(hours=1),
        workflow_scope={"sector": "ai_hardware", "layer": "L1_app",
                        "evidence_sections": ["frontier_labs_revenue_scale_and_trend"]})
    text = render_ai_commercialization_markdown(packet)
    assert "并列序列（按来源与口径分开，不拼接）" in text
    assert "| Anthropic | 公司招股书与定期报告自报 | 年化收入运行率 | 公司披露 | `instant` | 2026-10-31 81.00 |" in text
    assert any("趋势只在同一来源内计算" in item for item in packet["warnings"])
    [point] = [item for item in commercialization_points(packet) if item.series == "revenue_anthropic"]
    assert point.value == 80.0 and "可比序列 3 个披露点（2026-05→2026-10）" in point.evidence_span
