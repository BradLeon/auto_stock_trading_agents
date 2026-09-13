"""Unified ingestion, quality, comparability and trend tests for the revenue dataset.

Covers tasks 4.1-4.5 and 5.1-5.5: both sources land in the same governed store,
revisions keep their vintages, and every derived number names the observations
it came from.
"""

from datetime import datetime, timezone
from pathlib import Path

from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import (
    AdapterArtifact, AdapterBatch, FetchRequest, IngestionStatus, NativeRecord)
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.products import DataProducts
from ats.data.products.frontier_ai_labs_revenue import (
    COMPARABILITY_VERSION, DERIVATION_VERSION, METRIC_ANNUALIZED_RUN_RATE,
    METRIC_FORWARD_PROJECTION, METRIC_TRAILING_REVENUE, TREND_RULE_VERSION,
    comparability, frontier_labs_revenue_evidence_bundle, frontier_labs_revenue_series,
    revenue_trend, rows_hash, select_headline, source_conflicts)
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.data.sources.frontier_ai_labs_revenue import (
    SACRA_PAGES, SacraPublicCompanyProfilesAdapter, TickerTrendsPublicResearchAdapter)

FIXTURES = Path(__file__).parent / "fixtures" / "frontier_ai_labs_revenue"
NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)
QUALITY = {"headline_source_priority": ["company_reported", "media_reported",
                                        "third_party_estimate"],
           "source_conflict_relative_tolerance": 0.10,
           "minimum_comparable_points": 3, "minimum_history_days": 60,
           "direction_net_change_threshold": 0.10}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _store(tmp_path, name="revenue"):
    repository = SQLiteStructuredRepository(tmp_path / f"{name}.sqlite",
                                            artifact_root=tmp_path / f"{name}-artifacts")
    repository.bootstrap_catalog(StructuredCatalog.load())
    return repository


def _products(repository):
    return DataProducts(structured_repository=repository)


def _sacra_request(**scope):
    return FetchRequest(source_id="sacra_public_company_profiles",
                        dataset_id="frontier_ai_labs_revenue", query_scope=dict(scope))


def _tt_request():
    return FetchRequest(source_id="tickertrends_public_research",
                        dataset_id="frontier_ai_labs_revenue")


def _seed_history(repository, *extra):
    """Ingest the frozen TickerTrends seed plus the current Sacra pages."""
    pipeline = IngestionPipeline(repository)
    sacra = SacraPublicCompanyProfilesAdapter(fixture_dir=FIXTURES, clock=lambda: NOW)
    ticker = TickerTrendsPublicResearchAdapter(
        seed_path=FIXTURES / "tickertrends_anthropic_vs_openai_arr_tracking.json",
        clock=lambda: NOW)
    runs = [pipeline.run(sacra, _sacra_request()),
            pipeline.run(ticker, _tt_request())]
    for batch in extra:
        runs.append(pipeline.run(batch["adapter"], batch["request"]))
    return runs


def _record(entity="OPENAI", metric=METRIC_ANNUALIZED_RUN_RATE, value=40e9, period="2026-07",
            period_start="2026-07-01", period_end="2026-07-31", identity="third_party_estimate",
            regime="third_party_annualized_run_rate/v1", period_basis="month", source="a",
            known_at="2026-08-01T00:00:00+00:00"):
    """One revenue record in the projected shape every DataProduct consumes."""
    return {"observation_id": f"{source}:{entity}:{period}", "artifact_id": f"art-{source}",
            "source_id": source, "source_label": source,
            "dataset_id": "frontier_ai_labs_revenue",
            "entity_id": entity, "company": entity.title(), "metric_id": metric,
            "metric_label": metric, "observation_identity": identity,
            "observation_identity_label": identity,
            "raw_metric_label": "annualized revenue", "methodology_regime": regime,
            "period": period, "period_start": period_start, "period_end": period_end,
            "period_basis": period_basis, "value": value, "value_usd": value,
            "value_usd_bn": round(value / 1e9, 6), "currency": "USD", "unit": "USD",
            "published_at": known_at, "known_at": known_at, "quality_status": "accepted",
            "vintage_count": 1, "is_revision": False,
            "citation": {"origin_publisher": "Sacra", "origin_title": "",
                         "origin_url": "https://x.test", "publisher_url": "https://x.test",
                         "raw_quote": "quote"}}


class _Repo:
    """Minimal repository double for the pure-function DataProduct tests."""

    def __init__(self, rows):
        self._rows = list(rows)

    def observations(self, **kwargs):
        rows = self._rows
        if kwargs.get("entity_id"):
            rows = [row for row in rows if row["entity_id"] == kwargs["entity_id"]]
        if kwargs.get("metric_id"):
            rows = [row for row in rows if row["metric_id"] == kwargs["metric_id"]]
        return list(rows)

    def datasets(self):
        return [{"dataset_id": "frontier_ai_labs_revenue", "quality": QUALITY}]


# --------------------------------------------------------------------------- #
# 4.1 / 4.2 — unified store, idempotency, revisions
# --------------------------------------------------------------------------- #

def test_both_sources_land_in_the_same_repository(tmp_path):
    repository = _store(tmp_path)
    runs = _seed_history(repository)
    assert all(run["status"] in {"succeeded", "partial"} for run in runs)
    rows = repository.observations(dataset_id="frontier_ai_labs_revenue",
                                   accepted_only=True, latest_only=True)
    sources = {row["source_id"] for row in rows}
    assert sources == {"sacra_public_company_profiles", "tickertrends_public_research"}
    assert {row["entity_id"] for row in rows} == {"OPENAI", "ANTHROPIC"}
    # A projection is stored, but as its own metric — never as a historical actual.
    metrics = {row["metric_id"] for row in rows}
    assert METRIC_FORWARD_PROJECTION in metrics
    assert METRIC_ANNUALIZED_RUN_RATE in metrics


def test_reingesting_the_identical_artifact_is_a_no_change(tmp_path):
    repository = _store(tmp_path)
    _seed_history(repository)
    before = len(repository.observations(dataset_id="frontier_ai_labs_revenue",
                                         accepted_only=True, latest_only=True))
    pipeline = IngestionPipeline(repository)
    again = pipeline.run(
        SacraPublicCompanyProfilesAdapter(fixture_dir=FIXTURES, clock=lambda: NOW),
        _sacra_request())
    after = len(repository.observations(dataset_id="frontier_ai_labs_revenue",
                                        accepted_only=True, latest_only=True))
    assert again["status"] == "no_change"
    assert before == after


def test_same_period_revision_keeps_the_previous_vintage(tmp_path):
    repository = _store(tmp_path)
    _seed_history(repository)
    original = FIXTURES / "sacra_openai_public_profile.html"
    edited = original.read_text().replace(
        "$40B in annualized revenue in July 2026",
        "$44B in annualized revenue in July 2026")
    assert edited != original.read_text(), "the fixture wording moved; update the edit"

    revision_at = datetime(2026, 9, 14, tzinfo=timezone.utc)

    class Clocked:
        def fetch(self, request):
            adapter = SacraPublicCompanyProfilesAdapter(
                fixture_dir=FIXTURES, clock=lambda: revision_at)
            return adapter.fetch(FetchRequest(
                source_id=request.source_id, dataset_id=request.dataset_id,
                query_scope={"payloads": {"openai": edited}}))

    IngestionPipeline(repository).run(Clocked(), _sacra_request())
    rows = [row for row in repository.observations(
        dataset_id="frontier_ai_labs_revenue", entity_id="OPENAI",
        metric_id=METRIC_ANNUALIZED_RUN_RATE, accepted_only=True, latest_only=False)
        if row["period"] == "2026-07"]
    values = {row["value"] for row in rows}
    assert 44_000_000_000.0 in values and 40_000_000_000.0 in values
    as_of_before = repository.observations(
        dataset_id="frontier_ai_labs_revenue", entity_id="OPENAI",
        metric_id=METRIC_ANNUALIZED_RUN_RATE, as_of=NOW, accepted_only=True,
        latest_only=True)
    assert {row["value"] for row in as_of_before if row["period"] == "2026-07"} == \
        {40_000_000_000.0}
    latest = [row for row in repository.observations(
        dataset_id="frontier_ai_labs_revenue", entity_id="OPENAI",
        metric_id=METRIC_ANNUALIZED_RUN_RATE, accepted_only=True, latest_only=True)
        if row["period"] == "2026-07"]
    assert {row["value"] for row in latest} == {44_000_000_000.0}


def test_quarantined_rows_never_replace_the_last_good_observation(tmp_path):
    repository = _store(tmp_path)
    _seed_history(repository)

    class Broken:
        def fetch(self, request):
            record = NativeRecord(
                entity_id="OPENAI", provider_field="annualized_revenue_run_rate",
                period="2026-07", period_start="2026-07-01", period_end="2026-07-31",
                period_basis="month", value=0.0, unit="USD", currency="USD",
                dimensions={"observation_identity": "third_party_estimate",
                            "methodology_regime": "third_party_annualized_run_rate/v1",
                            "raw_metric_label": "annualized revenue"},
                raw={"raw_quote": "broken", "publisher_url": "https://x.test",
                     "origin_citation": {"origin_publisher": "Sacra"}})
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                status=IngestionStatus.SUCCEEDED, fetched_at=NOW,
                                records=[record],
                                artifacts=[AdapterArtifact(artifact_key="broken",
                                                           payload="broken")])

    IngestionPipeline(repository).run(Broken(), _sacra_request())
    rows = repository.observations(dataset_id="frontier_ai_labs_revenue", entity_id="OPENAI",
                                   metric_id=METRIC_ANNUALIZED_RUN_RATE,
                                   accepted_only=True, latest_only=True)
    assert all(row["value"] > 0 for row in rows)
    assert 40_000_000_000.0 in {row["value"] for row in rows}


# --------------------------------------------------------------------------- #
# 5.1 / 5.2 — query surface
# --------------------------------------------------------------------------- #

def test_series_query_returns_lineage_and_identity_per_observation(tmp_path):
    repository = _store(tmp_path)
    _seed_history(repository)
    products = _products(repository)
    result = products.frontier_labs_revenue_series(company="OPENAI")
    records = result["rows"]
    assert records and result["derivation_version"] == DERIVATION_VERSION
    first = records[0]
    for key in ("observation_id", "artifact_id", "source_id", "entity_id", "metric_id",
                "period", "period_start", "period_end", "value_usd", "currency",
                "observation_identity", "methodology_regime", "raw_metric_label",
                "citation", "known_at"):
        assert key in first, key
    assert first["citation"]["origin_publisher"] or first["citation"]["origin_title"]


def test_bundle_reports_each_company_headline_trend_and_lineage(tmp_path):
    repository = _store(tmp_path)
    _seed_history(repository)
    bundle = frontier_labs_revenue_evidence_bundle(_products(repository), as_of=NOW)
    assert bundle["status"] == "ok" and bundle["derivation_version"] == DERIVATION_VERSION
    companies = {item["entity_id"]: item for item in bundle["companies"]}
    assert set(companies) == {"OPENAI", "ANTHROPIC"}
    for view in companies.values():
        assert view["headline"]["status"] == "ok"
        assert view["trend"]["rule_version"] == TREND_RULE_VERSION
        assert view["lineage"]["input_observation_ids"]
    assert bundle["rows_hash"] == rows_hash(bundle["history_rows"])
    assert bundle["section"]["cross_company_average_growth_computed"] is False


# --------------------------------------------------------------------------- #
# 5.3 — trend derivation
# --------------------------------------------------------------------------- #

def _points(*pairs):
    """(YYYY-MM-DD anchor, value) pairs; one disclosed point each."""
    return [_record(period=start[:7], period_start=start, period_end=start,
                    value=value, source="sacra") for start, value in pairs]


def test_trend_refuses_fewer_than_three_points_or_sixty_days():
    trend = revenue_trend(_points(("2026-05-01", 30e9), ("2026-06-01", 33e9)),
                          quality=QUALITY)
    assert trend["status"] == "insufficient_history"
    assert "comparable_point_count" in trend["reason"]
    assert "input_observation_ids" not in trend

    short_span = _points(("2026-07-01", 30e9), ("2026-07-11", 31e9), ("2026-07-21", 32e9))
    trend = revenue_trend(short_span, quality=QUALITY)
    assert trend["status"] == "insufficient_history" and "span_days" in trend["reason"]


def test_trend_calls_a_steady_climb_expanding_and_a_reversal_mixed():
    expanding = revenue_trend(
        _points(("2026-01-01", 10e9), ("2026-03-01", 20e9), ("2026-06-01", 40e9)),
        quality=QUALITY)
    assert expanding["status"] == "expanding"
    assert expanding["net_change_rate"] > 0 and expanding["linear_slope_usd_per_day"] > 0
    assert expanding["span_days"] >= 60
    assert len(expanding["input_observation_ids"]) == 3

    contracting = revenue_trend(
        _points(("2026-01-01", 40e9), ("2026-03-01", 20e9), ("2026-06-01", 10e9)),
        quality=QUALITY)
    assert contracting["status"] == "contracting"

    reversal = revenue_trend(
        _points(("2026-01-01", 10e9), ("2026-03-01", 40e9), ("2026-06-01", 20e9)),
        quality=QUALITY)
    assert reversal["status"] == "mixed"
    assert reversal["reverse_endpoint_move"] is True


def test_trend_never_interpolates_missing_months():
    trend = revenue_trend(
        _points(("2026-01-01", 10e9), ("2026-04-01", 20e9), ("2026-06-01", 30e9)),
        quality=QUALITY)
    assert trend["observed_periods"] == ["2026-01", "2026-04", "2026-06"]
    assert trend["period_gap"]["status"] == "gap"
    assert trend["period_gap"]["missing_periods"] == ["2026-02", "2026-03", "2026-05"]


def test_trend_keeps_projection_out_of_the_comparable_cell():
    rows = _points(("2026-01-01", 10e9), ("2026-03-01", 20e9), ("2026-06-01", 30e9))
    rows.append(_record(metric=METRIC_FORWARD_PROJECTION, value=200e9, period="2027",
                        period_start="2027-01-01", period_end="2027-12-31",
                        identity="projection", regime="forward_revenue_projection/v1",
                        period_basis="annual"))
    trend = revenue_trend(rows, quality=QUALITY)
    assert trend["observed_periods"] == ["2026-01", "2026-03", "2026-06", "2027"]
    # The projection is admitted as its own metric, so the historical cell is intact.
    assert trend["comparable_point_count"] == 4


# --------------------------------------------------------------------------- #
# 4.4 / 4.5 — comparability, conflicts, headline
# --------------------------------------------------------------------------- #

def test_comparability_requires_the_same_measurement_identity():
    left = _record()
    same = _record(source="b")
    assert comparability(left, same)["comparable"] is True
    assert comparability(left, same)["version"] == COMPARABILITY_VERSION

    other_metric = _record(metric=METRIC_TRAILING_REVENUE, source="b")
    assert comparability(left, other_metric)["allowed_use"] == "side_by_side_only"
    other_identity = _record(identity="company_reported", source="b")
    assert comparability(left, other_identity)["comparable"] is False
    other_regime = _record(regime="company_reported_run_rate/v1", source="b")
    assert comparability(left, other_regime)["comparable"] is False
    other_company = _record(entity="ANTHROPIC", source="b")
    assert comparability(left, other_company)["comparable"] is False
    assert comparability(left, other_company)["directionally_comparable"] is True


def test_source_conflict_is_flagged_above_ten_percent_only():
    close = [_record(value=40e9, source="sacra_public_company_profiles"),
             _record(value=41e9, source="tickertrends_public_research")]
    assert source_conflicts(close, tolerance=0.10) == []

    far = [_record(value=40e9, source="sacra_public_company_profiles"),
           _record(value=60e9, source="tickertrends_public_research")]
    conflicts = source_conflicts(far, tolerance=0.10)
    assert len(conflicts) == 1
    assert conflicts[0]["status"] == "source_conflict"
    assert conflicts[0]["relative_difference"] == round(20e9 / 60e9, 6)
    assert {item["source_id"] for item in conflicts[0]["values"]} == {
        "sacra_public_company_profiles", "tickertrends_public_research"}


def test_headline_keeps_alternatives_and_never_promotes_an_estimate():
    company = _record(identity="company_reported", source="tickertrends_public_research")
    estimate = _record(identity="third_party_estimate",
                       source="sacra_public_company_profiles")
    headline = select_headline([estimate, company], quality=QUALITY)
    assert headline["selected_observation_id"] == company["observation_id"]
    assert [item["observation_id"] for item in headline["alternatives"]] == \
        [estimate["observation_id"]]
    assert headline["record"]["observation_identity"] == "company_reported"

    # With no company disclosure, the estimate leads but is still labelled one.
    only_estimate = select_headline([estimate], quality=QUALITY)
    assert only_estimate["record"]["observation_identity"] == "third_party_estimate"
    assert select_headline([], quality=QUALITY)["status"] == "unavailable"


def test_bundle_does_not_sum_company_and_product_revenue(tmp_path):
    """The product-scope figures never reach the store, so they cannot be summed."""
    repository = _store(tmp_path)
    _seed_history(repository)
    rows = repository.observations(dataset_id="frontier_ai_labs_revenue",
                                   accepted_only=True, latest_only=True)
    openai_run_rate = [row for row in rows
                       if row["entity_id"] == "OPENAI"
                       and row["metric_id"] == METRIC_ANNUALIZED_RUN_RATE]
    assert {row["period"] for row in openai_run_rate} == {"2026-07", "2025-12",
                                                          "2026-01", "2026-05"}
    assert 1_000_000_000.0 not in {row["value"] for row in openai_run_rate}


def test_bundle_flags_cross_company_incomparability_when_cells_differ(tmp_path):
    repository = _store(tmp_path)

    class Split:
        """Anthropic reports contract ARR; OpenAI only has a third-party run rate."""

        def fetch(self, request):
            records = [
                NativeRecord(entity_id="ANTHROPIC", provider_field="reported_arr",
                             period="2026-07", period_start="2026-07-01",
                             period_end="2026-07-31", period_basis="month", value=50e9,
                             unit="USD", currency="USD", published_at=NOW,
                             dimensions={"observation_identity": "company_reported",
                                         "methodology_regime": "company_reported_run_rate/v1",
                                         "raw_metric_label": "ARR"},
                             slice_key="anthropic:2026-07",
                             raw={"raw_quote": "q", "publisher_url": "https://x.test",
                                  "origin_citation": {"origin_publisher": "Sacra"}}),
                NativeRecord(entity_id="OPENAI", provider_field="annualized_revenue_run_rate",
                             period="2026-07", period_start="2026-07-01",
                             period_end="2026-07-31", period_basis="month", value=40e9,
                             unit="USD", currency="USD", published_at=NOW,
                             dimensions={"observation_identity": "third_party_estimate",
                                         "methodology_regime":
                                             "third_party_annualized_run_rate/v1",
                                         "raw_metric_label": "annualized revenue"},
                             slice_key="openai:2026-07",
                             raw={"raw_quote": "q", "publisher_url": "https://x.test",
                                  "origin_citation": {"origin_publisher": "Sacra"}})]
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                status=IngestionStatus.SUCCEEDED, fetched_at=NOW,
                                records=records,
                                artifacts=[AdapterArtifact(
                                    artifact_key=key, payload="split", slice_key=key)
                                    for key in ("openai:2026-07", "anthropic:2026-07")])

    outcome = IngestionPipeline(repository).run(Split(), _sacra_request())
    assert outcome["accepted"] == 2
    bundle = frontier_labs_revenue_evidence_bundle(_products(repository), as_of=NOW)
    assert bundle["comparability"]["across_companies"] is False
    assert bundle["comparability"]["note"]
    for view in bundle["companies"]:
        assert view["trend"]["status"] == "insufficient_history"


# --------------------------------------------------------------------------- #
# 5.5 — exports agree with the query
# --------------------------------------------------------------------------- #

def test_query_frame_and_bundle_share_one_rows_hash(tmp_path):
    """SQL rows, the Pandas frame and the bundle must be the same selection."""
    repository = _store(tmp_path)
    _seed_history(repository)
    products = _products(repository)
    bundle = products.frontier_labs_revenue_evidence_bundle(as_of=NOW)
    openai_rows = [row for row in bundle["history_rows"] if row["entity_id"] == "OPENAI"]
    frame = products.frontier_labs_revenue_series(
        company="OPENAI", metric=METRIC_ANNUALIZED_RUN_RATE, as_of=NOW, as_frame=True)
    assert list(frame["observation_id"]) == [row["observation_id"] for row in openai_rows]
    assert frame.attrs["rows_hash"] == rows_hash(openai_rows)
    assert frame.attrs["derivation_version"] == DERIVATION_VERSION
    assert bundle["rows_hash"] == rows_hash(bundle["history_rows"])
    # The same filter twice returns the identical selection: no hidden ordering drift.
    again = products.frontier_labs_revenue_evidence_bundle(as_of=NOW)
    assert again["rows_hash"] == bundle["rows_hash"]


def test_manifest_is_replayable_offline(tmp_path):
    repository = _store(tmp_path)
    _seed_history(repository)
    products = _products(repository)
    bundle = products.frontier_labs_revenue_evidence_bundle(
        as_of=NOW, snapshot_consumer="evidence_observer",
        snapshot_purpose="ai_frontier_labs_commercialization:v1")
    snapshot_id = (bundle.get("manifest") or {}).get("snapshot_id")
    assert snapshot_id, "an evidence bundle must leave a replayable lineage record"
    replay = products.replay_frontier_labs_revenue_bundle(snapshot_id)
    assert replay is not None and replay["snapshot_id"] == snapshot_id
    assert {row["observation_id"] for row in replay["rows"]} == \
        {row["observation_id"] for row in bundle["history_rows"]}
    assert replay["metadata"]["derivation_version"] == DERIVATION_VERSION
