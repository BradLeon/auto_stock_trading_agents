"""Sacra / TickerTrends parsing, discovery and quality-gate tests.

Covers tasks 2.2-2.4, 3.1-3.4, 3.6 and 4.3: frozen fixtures only, never a live
fetch, and every refusal must be visible as a reason code or a diagnostic.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ats.data.core.structured_models import FetchRequest, IngestionStatus
from ats.data.sources.frontier_ai_labs_revenue import (
    MINIMUM_COMPANY_REVENUE_USD,
    SACRA_PAGES,
    METRIC_ANNUALIZED_RUN_RATE,
    METRIC_FORWARD_PROJECTION,
    METRIC_TRAILING_REVENUE,
    SacraPublicCompanyProfilesAdapter,
    TickerTrendsPublicResearchAdapter,
    load_frozen_article,
    parse_sacra_page,
    parse_tickertrends_article,
    semantic_fingerprint,
    validate_frontier_labs_revenue_records,
)

FIXTURES = Path(__file__).parent / "fixtures" / "frontier_ai_labs_revenue"
OPENAI_PAGE = {"slug": "openai", "entity_id": "OPENAI", "company": "OpenAI",
               "url": "https://sacra.com/c/openai/"}
ANTHROPIC_PAGE = {"slug": "anthropic", "entity_id": "ANTHROPIC", "company": "Anthropic",
                  "url": "https://sacra.com/c/anthropic/"}
NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _cells(candidates):
    return {(item.entity_id, item.metric_id, item.observation_identity, item.period):
            item.value for item in candidates if item.accepted}


# --------------------------------------------------------------------------- #
# Sacra: what is admitted
# --------------------------------------------------------------------------- #

def test_sacra_openai_fixture_admits_run_rate_and_trailing_revenue():
    candidates, report = parse_sacra_page(_fixture("sacra_openai_public_profile.html"),
                                          page=OPENAI_PAGE)
    cells = _cells(candidates)
    assert cells[("OPENAI", METRIC_ANNUALIZED_RUN_RATE, "third_party_estimate",
                  "2026-07")] == 40_000_000_000.0
    assert cells[("OPENAI", METRIC_ANNUALIZED_RUN_RATE, "third_party_estimate",
                  "2025-12")] == 20_000_000_000.0
    assert cells[("OPENAI", METRIC_TRAILING_REVENUE, "third_party_estimate",
                  "2026-Q2")] == 6_700_000_000.0
    assert cells[("OPENAI", METRIC_TRAILING_REVENUE, "third_party_estimate",
                  "2026-Q1")] == 5_700_000_000.0
    # Every admitted figure carries a quote, a publisher URL and an origin citation.
    for candidate in candidates:
        if candidate.accepted:
            assert candidate.raw_quote and candidate.publisher_url
            assert candidate.origin_citation.get("origin_publisher") or \
                candidate.origin_citation.get("origin_title")
    assert report["per_page"][0]["parser_version"].endswith("/v1")


def test_sacra_anthropic_fixture_admits_two_comparable_run_rate_points():
    candidates, _ = parse_sacra_page(_fixture("sacra_anthropic_public_profile.html"),
                                     page=ANTHROPIC_PAGE)
    cells = _cells(candidates)
    assert cells[("ANTHROPIC", METRIC_ANNUALIZED_RUN_RATE, "third_party_estimate",
                  "2026-07")] == 65_000_000_000.0
    assert cells[("ANTHROPIC", METRIC_ANNUALIZED_RUN_RATE, "third_party_estimate",
                  "2026-05")] == 47_000_000_000.0


# --------------------------------------------------------------------------- #
# Sacra: what is refused, and why
# --------------------------------------------------------------------------- #

def test_paywalled_table_rows_are_diagnostics_not_zero_values():
    _, report = parse_sacra_page(_fixture("sacra_openai_public_profile.html"),
                                 page=OPENAI_PAGE)
    redactions = [item for item in report["diagnostics"]
                  if item["code"] == "paywalled_measurement_redacted"]
    assert redactions, "the frozen page keeps redacted rows specifically to test this"
    assert all(item["detail"].startswith("upstream redaction") for item in redactions)
    assert report["per_page"][0]["paywalled_row_count"] == len(redactions)


def test_product_scope_revenue_is_recorded_but_never_summed_into_company_series():
    """OpenAI ads and Claude Code are real figures belonging to another series."""
    candidates, _ = parse_sacra_page(_fixture("sacra_openai_public_profile.html"),
                                     page=OPENAI_PAGE)
    excluded = [item for item in candidates if "product_scope_excluded" in item.diagnostics]
    assert excluded, "the frozen page contains the ads-business sentence"
    assert all(not item.accepted for item in excluded)
    assert all(item.product_scope for item in excluded)
    admitted = _cells(candidates)
    assert 1_000_000_000.0 not in admitted.values() or \
        ("OPENAI", METRIC_ANNUALIZED_RUN_RATE, "third_party_estimate", "2026-08") \
        not in admitted

    anthropic, _ = parse_sacra_page(_fixture("sacra_anthropic_public_profile.html"),
                                    page=ANTHROPIC_PAGE)
    claude_code = [item for item in anthropic
                   if item.product_scope == "Claude Code"]
    assert claude_code and all(not item.accepted for item in claude_code)


def test_projection_is_labelled_and_never_treated_as_historical_actual():
    candidates, _ = parse_sacra_page(_fixture("sacra_anthropic_public_profile.html"),
                                     page=ANTHROPIC_PAGE)
    projections = [item for item in candidates
                   if item.metric_id == METRIC_FORWARD_PROJECTION and item.accepted]
    assert projections, "Anthropic's 2028 projection sentence must be captured"
    assert all(item.observation_identity == "projection" for item in projections)
    assert all(item.period.startswith("20") for item in projections)
    # It is admitted as its own metric, never fused into the run-rate cell.
    assert all(item.metric_id != METRIC_ANNUALIZED_RUN_RATE for item in projections)


def test_cost_and_financing_figures_are_not_revenue():
    candidates, _ = parse_sacra_page(_fixture("sacra_openai_public_profile.html"),
                                     page=OPENAI_PAGE)
    refused = [item for item in candidates
               if "not_company_revenue_context" in item.diagnostics]
    assert refused and all(not item.accepted for item in refused)
    assert 27_000_000_000.0 in {item.value for item in refused}  # projected cash burn


def test_chart_caption_values_are_never_read_numerically():
    page = (
        '<h2 id="revenue">Revenue</h2>'
        '<p><figure><img src="chart.png"><figcaption>Annualized revenue reached '
        '$999 billion in June 2026</figcaption></figure></p>'
        '<p>Sacra estimates that OpenAI hit $40B in annualized revenue in July 2026, '
        'up from $20B at the end of 2025.</p>'
    )
    candidates, report = parse_sacra_page(page, page=OPENAI_PAGE)
    assert 999_000_000_000.0 not in {item.value for item in candidates}
    assert "chart" in report["per_page"][0]["chart_reading_note"]


def test_revenue_section_missing_is_a_parse_failure_not_silence():
    _, report = parse_sacra_page("<html><body><h2>Overview</h2></body></html>",
                                 page=OPENAI_PAGE)
    assert report["diagnostics"][0]["code"] == "revenue_section_missing"


# --------------------------------------------------------------------------- #
# TickerTrends frozen 2026H1 seed
# --------------------------------------------------------------------------- #

def _tickertrends():
    article = load_frozen_article(
        FIXTURES / "tickertrends_anthropic_vs_openai_arr_tracking.json")
    published = datetime.fromisoformat(
        str(article["post_date"]).replace("Z", "+00:00"))
    return parse_tickertrends_article(
        body_html=str(article["body_html"]), url=str(article["canonical_url"]),
        published_at=published)


def test_tickertrends_seed_accepts_only_first_half_2026_points():
    candidates, report = _tickertrends()
    cells = _cells(candidates)
    assert cells[("ANTHROPIC", METRIC_ANNUALIZED_RUN_RATE, "third_party_estimate",
                  "2026-01")] == 10_200_000_000.0
    assert cells[("ANTHROPIC", METRIC_ANNUALIZED_RUN_RATE, "third_party_estimate",
                  "2026-04")] == 35_600_000_000.0
    assert cells[("ANTHROPIC", METRIC_ANNUALIZED_RUN_RATE, "third_party_estimate",
                  "2026-06")] == 69_600_000_000.0
    assert cells[("OPENAI", METRIC_ANNUALIZED_RUN_RATE, "third_party_estimate",
                  "2026-01")] == 21_400_000_000.0
    assert cells[("OPENAI", METRIC_ANNUALIZED_RUN_RATE, "third_party_estimate",
                  "2026-05")] == 33_000_000_000.0
    # July belongs to the seed's narrative but not to its governed window.
    july = [item for item in candidates if item.period == "2026-07"]
    assert july and all("outside_accepted_reference_window" in item.diagnostics
                        for item in july)
    assert report["per_page"][0]["accepted_window"] == ["2026-01-01", "2026-06-30"]


def test_tickertrends_seed_has_no_interpolated_or_duplicated_months():
    candidates, _ = _tickertrends()
    accepted = [item for item in candidates if item.accepted]
    keys = [(item.entity_id, item.period) for item in accepted]
    assert len(keys) == len(set(keys)), "a duplicated cell means double parsing"
    # February, March: the article simply does not state them; nothing is filled in.
    assert {item.period for item in accepted if item.entity_id == "OPENAI"} == \
        {"2026-01", "2026-05"}


def test_parsing_the_same_fixture_twice_is_identical():
    first, _ = parse_sacra_page(_fixture("sacra_openai_public_profile.html"),
                                page=OPENAI_PAGE)
    second, _ = parse_sacra_page(_fixture("sacra_openai_public_profile.html"),
                                 page=OPENAI_PAGE)
    assert [(item.entity_id, item.metric_id, item.period, item.value, item.diagnostics)
            for item in first] == [
        (item.entity_id, item.metric_id, item.period, item.value, item.diagnostics)
        for item in second]


# --------------------------------------------------------------------------- #
# Sacra adapter: discovery, single fetch, browser fallback, unavailability
# --------------------------------------------------------------------------- #

def test_discovery_touches_each_page_once_and_reports_identities():
    adapter = SacraPublicCompanyProfilesAdapter(fixture_dir=FIXTURES, clock=lambda: NOW)
    request = FetchRequest(source_id="sacra_public_company_profiles",
                           dataset_id="frontier_ai_labs_revenue")
    result = adapter.discover(request)
    assert result.status.value != "validation_failed"
    assert {candidate.metadata["scope"] for candidate in result.candidates} == \
        {page["slug"] for page in SACRA_PAGES}
    assert adapter.browser_snapshots_used == 0
    assert result.latest_available_period
    # A second discover re-uses the payload already read this run: no page is opened
    # twice, and the identity is stable.
    again = adapter.discover(request)
    assert again.latest_upstream_identity == result.latest_upstream_identity


def test_browser_fallback_runs_at_most_once_per_page_and_only_when_static_is_empty():
    class Browser:
        calls = 0

        def snapshot(self, url):
            Browser.calls += 1
            return '<h2 id="revenue">Revenue</h2><p>Sacra estimates that OpenAI hit ' \
                   '$40B in annualized revenue in July 2026.</p>'

    class EmptyClient:
        def get(self, *args, **kwargs):
            raise ConnectionError("static fetch blocked")

    adapter = SacraPublicCompanyProfilesAdapter(
        client=EmptyClient(), browser=Browser(), pages=[OPENAI_PAGE], clock=lambda: NOW)
    with pytest.raises(ConnectionError):
        adapter.discover(FetchRequest(source_id="sacra_public_company_profiles",
                                      dataset_id="frontier_ai_labs_revenue"))

    class StaticClient:
        def get(self, *args, **kwargs):
            class Response:
                text = "<html><body><h2>Overview</h2></body></html>"

                def raise_for_status(self):
                    return None
            return Response()

    adapter = SacraPublicCompanyProfilesAdapter(
        client=StaticClient(), browser=Browser(), pages=[OPENAI_PAGE], clock=lambda: NOW)
    adapter.discover(FetchRequest(source_id="sacra_public_company_profiles",
                                  dataset_id="frontier_ai_labs_revenue"))
    assert Browser.calls <= len(adapter.pages)


def test_static_page_without_revenue_section_is_reported_not_guessed():
    class Client:
        def get(self, *args, **kwargs):
            class Response:
                text = "<html><body><h2>Overview</h2><p>Nothing here.</p></body></html>"

                def raise_for_status(self):
                    return None
            return Response()

    adapter = SacraPublicCompanyProfilesAdapter(client=Client(), pages=[OPENAI_PAGE],
                                                clock=lambda: NOW)
    batch = adapter.fetch(FetchRequest(source_id="sacra_public_company_profiles",
                                       dataset_id="frontier_ai_labs_revenue"))
    assert batch.status is IngestionStatus.PARTIAL
    assert batch.records == []
    assert any("no_admissible_revenue" in failure.message for failure in batch.failures)


def test_fetch_keeps_rejected_candidates_out_of_records_but_in_diagnostics():
    adapter = SacraPublicCompanyProfilesAdapter(fixture_dir=FIXTURES, clock=lambda: NOW)
    batch = adapter.fetch(FetchRequest(source_id="sacra_public_company_profiles",
                                       dataset_id="frontier_ai_labs_revenue"))
    assert batch.status is IngestionStatus.SUCCEEDED
    rejected = batch.provider_metadata["rejected_candidates"]
    assert rejected and all(item["reason_codes"] for item in rejected)
    assert all(record.value > 0 for record in batch.records)
    assert len(batch.artifacts) == len(SACRA_PAGES)
    assert all(artifact.retention == "constrained_snapshot" for artifact in batch.artifacts)


def test_semantic_fingerprint_ignores_styling_noise_and_sees_layout_drift():
    """A restyle must not look like a new release; a changed table schema must."""
    base = '<h2 id="revenue">Revenue</h2><p>OpenAI hit $40B in annualized revenue.</p>' \
        '<table><tr><th>Time</th><th>Metric</th><th>Value</th></tr>' \
        '<tr><td>2026</td><td>Revenue</td><td>$40B</td></tr></table>'
    restyled = base.replace("<p>", "<p class='x'>").replace("<table>", "<table id='t1'>")
    new_column = base.replace("<th>Value</th>", "<th>Value</th><th>Type</th>")
    assert semantic_fingerprint(base) == semantic_fingerprint(restyled)
    assert semantic_fingerprint(base) != semantic_fingerprint(new_column)
    # Dropping the revenue heading is the strongest possible drift signal.
    assert semantic_fingerprint(base) != semantic_fingerprint(base.replace('id="revenue"', ""))


# --------------------------------------------------------------------------- #
# TickerTrends adapter
# --------------------------------------------------------------------------- #

def test_tickertrends_adapter_reads_the_versioned_seed_offline():
    seed = FIXTURES / "tickertrends_anthropic_vs_openai_arr_tracking.json"
    adapter = TickerTrendsPublicResearchAdapter(seed_path=seed, clock=lambda: NOW)
    batch = adapter.fetch(FetchRequest(source_id="tickertrends_public_research",
                                       dataset_id="frontier_ai_labs_revenue"))
    assert batch.status is IngestionStatus.SUCCEEDED
    assert batch.provider_metadata["scheduled_discovery"] is False
    assert batch.provider_metadata["accepted_reference_period"] == \
        ["2026-01-01", "2026-06-30"]
    assert {record.entity_id for record in batch.records} == {"OPENAI", "ANTHROPIC"}


def test_tickertrends_adapter_refuses_to_guess_without_its_seed(tmp_path):
    adapter = TickerTrendsPublicResearchAdapter(seed_path=tmp_path / "missing.json",
                                                clock=lambda: NOW)
    with pytest.raises(FileNotFoundError, match="tickertrends_seed_missing"):
        adapter.fetch(FetchRequest(source_id="tickertrends_public_research",
                                   dataset_id="frontier_ai_labs_revenue"))


# --------------------------------------------------------------------------- #
# Quality gate
# --------------------------------------------------------------------------- #

def _record(**overrides):
    from ats.data.core.structured_models import NativeRecord

    payload = dict(entity_id="OPENAI", provider_field="annualized_revenue_run_rate",
                   period="2026-07", period_start="2026-07-01", period_end="2026-07-31",
                   period_basis="month", value=40_000_000_000.0, unit="USD",
                   currency="USD",
                   dimensions={"observation_identity": "third_party_estimate",
                               "methodology_regime": "third_party_annualized_run_rate/v1",
                               "raw_metric_label": "annualized revenue"},
                   raw={"raw_quote": "OpenAI hit $40B in annualized revenue in July 2026",
                        "publisher_url": "https://sacra.com/c/openai/",
                        "origin_citation": {"origin_publisher": "Sacra"}})
    payload.update(overrides)
    return NativeRecord(**payload)


def test_quality_gate_passes_a_complete_company_revenue_observation():
    assert validate_frontier_labs_revenue_records([_record()]) == []


@pytest.mark.parametrize("overrides,expected", [
    ({"value": 0.0}, "non_positive_revenue"),
    ({"value": -1.0}, "non_positive_revenue"),
    ({"value": float(MINIMUM_COMPANY_REVENUE_USD) - 1}, "below_company_revenue_scale"),
    ({"currency": "EUR"}, "currency_unsupported"),
    ({"period_start": ""}, "reference_period_incomplete"),
    ({"dimensions": {"observation_identity": "third_party_estimate"}},
     "methodology_regime_missing"),
    ({"raw": {"raw_quote": "quote"}}, "publisher_url_missing"),
    ({"raw": {"raw_quote": "", "publisher_url": "u"}}, "raw_quote_missing"),
])
def test_quality_gate_rejects_off_scale_and_unattributable_rows(overrides, expected):
    issues = validate_frontier_labs_revenue_records([_record(**overrides)])
    assert any(expected in issue for issue in issues)


def test_quality_gate_flags_duplicate_cells():
    issues = validate_frontier_labs_revenue_records([_record(), _record()])
    assert any(issue.startswith("duplicate_cell") for issue in issues)


def test_partition_keeps_only_admissible_records_and_names_the_refusals():
    from ats.data.sources.frontier_ai_labs_revenue import partition_admissible_records

    admitted, quarantined = partition_admissible_records(
        [_record(), _record(period="2026-08", period_start="2026-08-01",
                            period_end="2026-08-31", currency="EUR")])
    assert len(admitted) == 1 and admitted[0].currency == "USD"
    assert quarantined and quarantined[0]["reason_codes"] == ["currency_unsupported:EUR"]


def test_frozen_fixtures_produce_no_quarantined_observations():
    """The two governed sources are clean end to end; nothing is silently dropped."""
    from ats.data.sources.frontier_ai_labs_revenue import TICKERTRENDS_DEFAULT_SEED_FILENAME

    adapter = SacraPublicCompanyProfilesAdapter(fixture_dir=FIXTURES, clock=lambda: NOW)
    batch = adapter.fetch(FetchRequest(source_id="sacra_public_company_profiles",
                                       dataset_id="frontier_ai_labs_revenue"))
    assert batch.status is IngestionStatus.SUCCEEDED
    assert not any("quality_gate" in failure.message for failure in batch.failures)
    seed_batch = TickerTrendsPublicResearchAdapter(
        seed_path=FIXTURES / TICKERTRENDS_DEFAULT_SEED_FILENAME,
        clock=lambda: NOW).fetch(
            FetchRequest(source_id="tickertrends_public_research",
                         dataset_id="frontier_ai_labs_revenue"))
    assert seed_batch.status is IngestionStatus.SUCCEEDED
