"""TickerTrends research-notes series: parsing, merging, adapter routes and the product separation.

The notes quote other outlets, give product-line ARR and closed-quarter revenue.  They are kept, each labelled
by who stated it, which product it describes and which period basis it was stated on; what they cannot support
(increments, undated or unattributable figures) is refused with a reason code instead of being guessed.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import DiscoveryStatus, FetchRequest
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.products import DataProducts
from ats.data.products.frontier_ai_labs_revenue import (
    frontier_labs_company_view, frontier_labs_revenue_cells, frontier_labs_revenue_series)
from ats.data.sources.frontier_ai_labs_revenue import (
    TICKERTRENDS_ARTICLE_SLUG, TICKERTRENDS_NOTES_PARSER_VERSION, TICKERTRENDS_POST_API_TEMPLATE,
    TickerTrendsPublicResearchAdapter, expand_month_abbreviations, merge_duplicate_candidates,
    note_is_relevant, parse_tickertrends_note, partition_admissible_records)
from ats.data.stores.structured.repository import SQLiteStructuredRepository

FIXTURES = Path(__file__).parent / "fixtures" / "frontier_ai_labs_revenue"
NOTES = FIXTURES / "tickertrends_notes"
NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)


def _note(slug: str) -> dict:
    return json.loads((NOTES / f"{slug}.json").read_text(encoding="utf-8"))


def _accepted(slug: str):
    candidates, _ = parse_tickertrends_note(_note(slug))
    return [item for item in candidates if item.accepted]


def _cell(candidates, *, entity, product="", identity=None, period=None):
    return [item for item in candidates
            if item.entity_id == entity and item.product_scope == product
            and (identity is None or item.observation_identity == identity)
            and (period is None or item.period == period)]


def _request():
    return FetchRequest(source_id="tickertrends_public_research",
                        dataset_id="frontier_ai_labs_revenue")


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #

def test_month_abbreviations_are_expanded_only_where_they_are_dates():
    assert expand_month_abbreviations("as of Aug. 12, up from $42.6B on Jul. 6") \
        == "as of August 12, up from $42.6B on July 6"
    assert expand_month_abbreviations("The market was strong.") == "The market was strong."


def test_media_quoted_quarterly_revenue_is_kept_with_the_outlet_named():
    rows = _cell(_accepted("anthropics-q3-revenue-growth-slowed"), entity="ANTHROPIC")
    assert {item.period: item.value for item in rows} == {
        "2026-Q3": 16.8e9, "2026-Q2": 11.6e9, "2026-Q1": 4.73e9}
    assert all(item.observation_identity == "media_reported" for item in rows)
    assert all(item.metric_id == "ai.frontier_lab.trailing_revenue" for item in rows)
    assert all(item.period_basis == "quarter" for item in rows)
    assert all(item.origin_citation["origin_publisher"] == "Financial Times" for item in rows)
    assert all(item.period_year_source == "publication_year" for item in rows)


def test_increments_are_refused_not_stored_as_levels():
    candidates, _ = parse_tickertrends_note(_note("anthropics-q3-revenue-growth-slowed"))
    deltas = [item for item in candidates if item.value in (5.2e9, 6.87e9)]
    assert deltas and all("delta_not_level" in item.diagnostics for item in deltas)
    codex, _ = parse_tickertrends_note(_note("claude-code-vs-openai-codex-arr"))
    differences = [item for item in codex if round(item.value / 1e9, 2) in (6.30, 8.38, 7.07, 6.73, 6.25, 6.06)
                   and item.product_scope == "Codex" and "delta_not_level" in item.diagnostics]
    assert len(differences) >= 6


def test_product_level_arr_keeps_its_product_and_the_owning_company():
    candidates = _accepted("claude-code-vs-openai-codex-arr")
    claude_code = {item.period: item.value for item in _cell(
        candidates, entity="ANTHROPIC", product="Claude Code", identity="third_party_estimate")
        if item.period_basis == "instant"}
    assert claude_code["2026-07-06"] == 14.26e9
    assert claude_code["2026-08-10"] == 15.12e9
    codex = {item.period: item.value for item in _cell(
        candidates, entity="OPENAI", product="Codex", identity="third_party_estimate")
        if item.period_basis == "instant"}
    assert codex["2026-07-06"] == 5.88e9 and codex["2026-08-10"] == 8.83e9
    assert all(item.metric_id == "ai.frontier_lab.annualized_revenue_run_rate" for item in candidates
               if item.observation_identity == "third_party_estimate")
    # no company-level row is produced from a product sentence
    assert not _cell(candidates, entity="ANTHROPIC", product="")
    assert not _cell(candidates, entity="OPENAI", product="")


def test_a_company_stated_run_rate_is_company_reported_for_the_named_product():
    rows = _cell(_accepted("claude-code-vs-openai-codex-arr"), entity="ANTHROPIC",
                 product="Claude Code", identity="company_reported")
    assert [(item.period, item.value) for item in rows] == [("2026-02-12", 2.5e9)]
    assert rows[0].methodology_regime == "company_reported_run_rate/v1"


def test_month_level_history_is_stored_on_a_month_basis_with_its_qualifier():
    rows = {item.period: item for item in _cell(
        _accepted("claude-code-vs-openai-codex-arr"), entity="ANTHROPIC", product="Claude Code")
        if item.period_basis == "calendar_month"}
    assert rows["2026-03"].value == 6.3e9 and rows["2026-03"].value_qualifier == "approximate"
    assert rows["2026-06"].value == 14.0e9 and rows["2026-06"].value_qualifier == "lower_bound"


def test_unresolvable_period_is_refused_rather_than_dated_to_the_publication_day():
    candidates, _ = parse_tickertrends_note(_note("claude-code-vs-openai-codex-arr"))
    undated = [item for item in candidates if "reference_period_unresolved" in item.diagnostics]
    assert undated and all(not item.accepted for item in undated)


def test_a_date_list_pairs_each_figure_with_its_own_date():
    rows = {item.period: item.value for item in _cell(
        _accepted("openai-arr-growth-accelerated-july-2026"), entity="OPENAI")}
    assert rows == {"2026-05-30": 33.0e9, "2026-06-25": 37.3e9, "2026-07-02": 38.5e9,
                    "2026-07-09": 40.3e9, "2026-07-29": 42.6e9}


def test_other_outlets_figure_is_media_reported_and_never_taints_the_tickertrends_figure():
    candidates = _accepted("openai-arr-tracking-44-3b-bloomberg-40b-run-rate")
    media = _cell(candidates, entity="OPENAI", identity="media_reported")
    assert [(item.period, item.value, item.value_qualifier) for item in media] == [
        ("2026-07-31", 40.0e9, "lower_bound")]
    assert media[0].origin_citation["origin_publisher"] == "Bloomberg"
    own = _cell(candidates, entity="OPENAI", identity="third_party_estimate")
    assert {(item.period, item.value) for item in own} == {
        ("2026-08-12", 44.3e9), ("2026-07-29", 42.6e9)}


def test_latest_reading_without_a_date_takes_the_publication_day_and_says_so():
    rows = {item.entity_id: item for item in _accepted("openai-and-anthropic-arr-tracking")}
    assert rows["OPENAI"].value == 50.44e9 and rows["ANTHROPIC"].value == 76.8e9
    assert all(item.period == "2026-09-29" and item.period_year_source == "publication_date"
               for item in rows.values())


def test_launch_note_figure_goes_to_the_product_named_in_the_title():
    rows = _accepted("chatgpt-ads-arr-weekly-tracking")
    assert [(item.entity_id, item.product_scope, item.value, item.period) for item in rows] == [
        ("OPENAI", "ChatGPT Ads", 1.0e9, "2026-08-31")]
    assert rows[0].origin_citation["subject_inherited"] is True
    assert rows[0].observation_identity == "third_party_estimate"


def test_unattributed_headline_is_media_reported_and_outlet_report_needs_a_revenue_label():
    candidates, _ = parse_tickertrends_note(_note("tickertrends-tracked-openai-revenue"))
    headline = _cell([c for c in candidates if c.accepted], entity="OPENAI", identity="media_reported")
    assert [(item.period, item.value, item.value_qualifier) for item in headline
            if item.value == 70.0e9] == [("2026-09-29", 70.0e9, "approximate")]
    ft = [item for item in candidates if item.origin_citation["origin_publisher"] == "Financial Times"]
    assert ft and all(item.observation_identity == "media_reported" for item in ft)


# --------------------------------------------------------------------------- #
# merging
# --------------------------------------------------------------------------- #

def test_the_same_reading_restated_in_one_note_is_merged_into_one_observation():
    candidates, _ = parse_tickertrends_note(_note("claude-code-vs-openai-codex-arr"))
    merged, superseded = merge_duplicate_candidates(candidates)
    keys = [(item.entity_id, item.provider_field, item.period, item.observation_identity,
             item.product_scope) for item in merged]
    assert len(keys) == len(set(keys))
    assert not superseded
    latest = [item for item in merged if item.entity_id == "ANTHROPIC"
              and item.product_scope == "Claude Code" and item.period == "2026-08-10"
              and item.period_basis == "instant" and item.observation_identity == "third_party_estimate"]
    assert len(latest) == 1 and latest[0].origin_citation["additional_citations"]


def test_a_newer_note_supersedes_an_older_changed_figure_and_says_so():
    older = _note("openai-arr-growth-accelerated-july-2026")
    newer = _note("openai-arr-growth-accelerated-july-2026")
    newer["slug"] = "openai-arr-growth-accelerated-july-2026-revised"
    newer["post_date"] = "2026-08-02T12:00:00.000Z"
    newer["body_html"] = newer["body_html"].replace("$37.3B", "$36.0B")
    first, _ = parse_tickertrends_note(older)
    second, _ = parse_tickertrends_note(newer)
    merged, superseded = merge_duplicate_candidates([*first, *second])
    june = [item for item in merged if item.period == "2026-06-25"]
    assert [item.value for item in june] == [36.0e9]
    assert superseded and all(item["reason_codes"] == ["superseded_by_newer_note"] for item in superseded)
    assert {item["value"] for item in superseded} == {37.3e9}
    # the readings both notes agree on are merged, not superseded
    july = [item for item in merged if item.period == "2026-07-29"]
    assert len(july) == 1 and july[0].origin_citation["additional_citations"]


# --------------------------------------------------------------------------- #
# adapter
# --------------------------------------------------------------------------- #

def _essay_seed() -> Path:
    return FIXTURES / "tickertrends_anthropic_vs_openai_arr_tracking.json"


def _notes_adapter(slugs, **overrides):
    return TickerTrendsPublicResearchAdapter(
        seed_path=_essay_seed(), clock=lambda: NOW, include_notes=True,
        note_articles=[_note(slug) for slug in slugs], **overrides)


ALL_NOTES = ("anthropics-q3-revenue-growth-slowed", "claude-code-vs-openai-codex-arr",
             "openai-arr-growth-accelerated-july-2026",
             "openai-arr-tracking-44-3b-bloomberg-40b-run-rate", "openai-and-anthropic-arr-tracking",
             "chatgpt-ads-arr-weekly-tracking", "tickertrends-tracked-openai-revenue")


def test_batch_with_notes_passes_the_quality_gate_and_keeps_one_artifact_per_note():
    batch = _notes_adapter(ALL_NOTES).fetch(_request())
    assert not [f for f in batch.failures if f.message.startswith("quality_gate_quarantined")]
    assert batch.status.value == "succeeded"
    slices = {artifact.artifact_key for artifact in batch.artifacts}
    assert slices == {f"tickertrends:{TICKERTRENDS_ARTICLE_SLUG}",
                      *(f"tickertrends:{slug}" for slug in ALL_NOTES)}
    assert {record.slice_key for record in batch.records} <= slices
    scopes = {record.dimensions.get("product_scope", "") for record in batch.records}
    assert {"", "Claude Code", "Codex", "ChatGPT Ads"} <= scopes
    assert "superseded_by_newer_note" not in json.dumps(batch.provider_metadata["rejected_candidates"])
    admitted, quarantined = partition_admissible_records(list(batch.records))
    assert len(admitted) == len(batch.records) and not quarantined


def test_existing_rows_keep_their_dimensions_when_notes_are_added():
    plain = TickerTrendsPublicResearchAdapter(seed_path=_essay_seed(), clock=lambda: NOW).fetch(_request())
    with_notes = _notes_adapter(ALL_NOTES).fetch(_request())
    essay_rows = [r for r in with_notes.records if r.slice_key == f"tickertrends:{TICKERTRENDS_ARTICLE_SLUG}"]
    assert [(r.entity_id, r.period, r.value, r.dimensions, r.raw) for r in essay_rows] \
        == [(r.entity_id, r.period, r.value, r.dimensions, r.raw) for r in plain.records]


def test_without_include_notes_the_adapter_still_reads_only_the_essay():
    batch = TickerTrendsPublicResearchAdapter(seed_path=_essay_seed(), clock=lambda: NOW,
                                              note_articles=None).fetch(_request())
    assert {artifact.artifact_key for artifact in batch.artifacts} == {
        f"tickertrends:{TICKERTRENDS_ARTICLE_SLUG}"}


def test_discovery_announces_one_candidate_per_note_with_a_claim_free_methodology_fingerprint():
    adapter = _notes_adapter(ALL_NOTES[:2])
    result = adapter.discover(_request())
    scopes = [item.metadata["scope"] for item in result.candidates]
    assert scopes == [TICKERTRENDS_ARTICLE_SLUG, *ALL_NOTES[:2]]
    assert result.latest_available_period >= "2026-08-10"
    notes = [item for item in result.candidates if item.metadata["scope"] != TICKERTRENDS_ARTICLE_SLUG]
    assert all(item.metadata["parser_version"] == TICKERTRENDS_NOTES_PARSER_VERSION for item in notes)

    # a revised figure changes the release identity but is not methodology drift
    revised = _note("claude-code-vs-openai-codex-arr")
    revised["body_html"] = revised["body_html"].replace("$15.12 billion", "$15.30 billion")
    again = TickerTrendsPublicResearchAdapter(
        seed_path=_essay_seed(), clock=lambda: NOW, include_notes=True,
        note_articles=[_note(ALL_NOTES[0]), revised]).discover(_request())
    before = {item.metadata["scope"]: item for item in result.candidates}
    after = {item.metadata["scope"]: item for item in again.candidates}
    code = "claude-code-vs-openai-codex-arr"
    assert before[code].identity != after[code].identity
    assert before[code].methodology_fingerprint == after[code].methodology_fingerprint
    assert result.latest_upstream_identity != again.latest_upstream_identity


def test_a_note_without_any_usable_figure_is_diagnosed_not_announced():
    barren = _note("anthropics-q3-revenue-growth-slowed")
    barren["slug"], barren["body_html"] = "no-figures-here", "<p>ARR was discussed without any dollar figure.</p>"
    result = TickerTrendsPublicResearchAdapter(
        seed_path=_essay_seed(), clock=lambda: NOW, include_notes=True,
        note_articles=[barren]).discover(_request())
    assert [item.metadata["scope"] for item in result.candidates] == [TICKERTRENDS_ARTICLE_SLUG]
    assert any(item.get("slug") == "no-figures-here" for item in result.diagnostics["per_page"])


# --------------------------------------------------------------------------- #
# live route
# --------------------------------------------------------------------------- #

class _Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class _NotesClient:
    """Serves an archive listing and post bodies; optionally fails the first N calls to a URL."""

    def __init__(self, flaky=None):
        self.calls: list[str] = []
        self.flaky = dict(flaky or {})
        essay = json.loads(_essay_seed().read_text(encoding="utf-8"))
        essay.update(audience="everyone", free_unlock_required=False)
        essay.pop("body_html_sha256", None)
        self.posts = {TICKERTRENDS_ARTICLE_SLUG: essay}
        self.listing = []
        for slug in ("claude-code-vs-openai-codex-arr", "openai-and-anthropic-arr-tracking"):
            self.posts[slug] = _note(slug)
            self.listing.append(self._entry(self.posts[slug]))
        self.listing.append({"slug": "old-revenue-note", "post_date": "2026-06-20T10:00:00.000Z",
                             "title": "Old revenue note", "audience": "everyone"})
        self.listing.append({"slug": "meme-stocks", "post_date": "2026-10-01T10:00:00.000Z",
                             "title": "Meme stocks", "audience": "everyone"})

    @staticmethod
    def _entry(article):
        return {key: article[key] for key in ("slug", "title", "post_date", "audience")}

    def get(self, url, **kwargs):
        self.calls.append(url)
        remaining = self.flaky.get(url, 0)
        if remaining:
            self.flaky[url] = remaining - 1
            raise ConnectionResetError("tls reset")
        if "/archive?" in url:
            offset = int(url.rsplit("offset=", 1)[1])
            return _Response(json.dumps(self.listing if offset == 0 else []))
        slug = url.rsplit("/", 1)[1]
        return _Response(json.dumps(self.posts[slug]))


def test_note_relevance_filter_is_public_dated_and_about_revenue():
    assert note_is_relevant({"slug": "openai-arr-tracking", "post_date": "2026-08-14T00:00:00Z",
                             "title": "OpenAI ARR tracking", "audience": "everyone"})
    assert not note_is_relevant({"slug": "x", "post_date": "2026-06-30T00:00:00Z",
                                 "title": "Revenue", "audience": "everyone"})
    assert not note_is_relevant({"slug": "x", "post_date": "2026-08-14T00:00:00Z",
                                 "title": "Revenue", "audience": "only_paid"})
    assert not note_is_relevant({"slug": "x", "post_date": "2026-08-14T00:00:00Z",
                                 "title": "GitHub mentions", "audience": "everyone"})
    assert not note_is_relevant({"slug": TICKERTRENDS_ARTICLE_SLUG, "post_date": "2026-07-23T00:00:00Z",
                                 "title": "ARR", "audience": "everyone"})


def test_live_route_lists_the_archive_then_reads_each_relevant_note_once():
    client = _NotesClient()
    adapter = TickerTrendsPublicResearchAdapter(client=client, clock=lambda: NOW, include_notes=True,
                                                sleep=lambda seconds: None)
    result = adapter.discover(_request())
    assert result.status is DiscoveryStatus.NEW_RELEASE
    fetched = [call.rsplit("/", 1)[1] for call in client.calls if "/archive?" not in call]
    assert fetched.count("claude-code-vs-openai-codex-arr") == 1
    assert "old-revenue-note" not in fetched and "meme-stocks" not in fetched
    assert sum("/archive?" in call for call in client.calls) == 1
    # ingest reuses the probe's payloads instead of asking the site again
    before = len(client.calls)
    batch = adapter.fetch(FetchRequest(source_id="tickertrends_public_research",
                                       dataset_id="frontier_ai_labs_revenue",
                                       query_scope={"payloads": dict(adapter.discovered_payloads)}))
    assert len(client.calls) == before
    assert batch.provider_metadata["notes_ingested"]


def test_a_transient_reset_is_retried_and_a_dead_note_degrades_to_partial():
    flaky = {TICKERTRENDS_POST_API_TEMPLATE.format(slug="claude-code-vs-openai-codex-arr"): 2}
    client = _NotesClient(flaky=flaky)
    adapter = TickerTrendsPublicResearchAdapter(client=client, clock=lambda: NOW, include_notes=True,
                                                sleep=lambda seconds: None)
    result = adapter.discover(_request())
    assert result.status is DiscoveryStatus.NEW_RELEASE
    assert "claude-code-vs-openai-codex-arr" in {item.metadata["scope"] for item in result.candidates}

    dead = {TICKERTRENDS_POST_API_TEMPLATE.format(slug="claude-code-vs-openai-codex-arr"): 99}
    client = _NotesClient(flaky=dead)
    adapter = TickerTrendsPublicResearchAdapter(client=client, clock=lambda: NOW, include_notes=True,
                                                sleep=lambda seconds: None)
    result = adapter.discover(_request())
    assert result.status is DiscoveryStatus.PARTIAL
    assert result.diagnostics["note_failures"] == [
        "tickertrends_note_unreachable:claude-code-vs-openai-codex-arr:ConnectionResetError"]
    scopes = {item.metadata["scope"] for item in result.candidates}
    assert "openai-and-anthropic-arr-tracking" in scopes
    assert "claude-code-vs-openai-codex-arr" not in scopes


# --------------------------------------------------------------------------- #
# product separation
# --------------------------------------------------------------------------- #

def _ingested(tmp_path):
    repository = SQLiteStructuredRepository(tmp_path / "notes.sqlite",
                                            artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog(StructuredCatalog.load())
    run = IngestionPipeline(repository).run(_notes_adapter(ALL_NOTES), _request())
    assert run["status"] in {"succeeded", "partial"}, run
    return repository


def test_product_and_other_basis_rows_never_reach_the_company_headline_or_trend(tmp_path):
    repository = _ingested(tmp_path)
    products = DataProducts(structured_repository=repository)
    cells = frontier_labs_revenue_cells(products, company="ANTHROPIC")
    scopes = {(cell["product_scope"], cell["period_basis"], cell["observation_identity"]) for cell in cells}
    assert ("Claude Code", "instant", "third_party_estimate") in scopes
    assert ("", "quarter", "media_reported") in scopes

    view = frontier_labs_company_view(products, company="ANTHROPIC")
    comparison = view["comparison_cell"]
    assert comparison["period_basis"] == "calendar_month"
    assert all(not row["product_scope"] for row in view["history_rows"])
    headline = view["headline"]["record"]
    assert headline["product_scope"] == "" and headline["period_basis"] != "quarter"
    supplementary = view["supplementary_rows"]
    assert any(row["product_scope"] == "Claude Code" for row in supplementary)
    assert any(row["observation_identity"] == "media_reported" and row["period_basis"] == "quarter"
               for row in supplementary)
    assert not [item for item in view["source_conflicts"] if item["observation_identity"] == "media_reported"]


def test_series_product_returns_company_rows_unless_a_product_is_requested(tmp_path):
    repository = _ingested(tmp_path)
    products = DataProducts(structured_repository=repository)
    company = frontier_labs_revenue_series(products, company="OPENAI")
    assert company["row_count"] and all(not row["product_scope"] for row in company["rows"])
    codex = frontier_labs_revenue_series(products, company="OPENAI", product_scope="Codex")
    assert codex["row_count"] and all(row["product_scope"] == "Codex" for row in codex["rows"])
    everything = frontier_labs_revenue_series(products, company="OPENAI", product_scope="*")
    assert everything["row_count"] == company["row_count"] + codex["row_count"] + sum(
        1 for row in everything["rows"] if row["product_scope"] not in ("", "Codex"))


def test_reingesting_the_same_notes_adds_no_new_vintages(tmp_path):
    repository = _ingested(tmp_path)
    first = len(repository.observations(dataset_id="frontier_ai_labs_revenue", latest_only=False,
                                        accepted_only=True, limit=100_000))
    IngestionPipeline(repository).run(_notes_adapter(ALL_NOTES), _request())
    second = len(repository.observations(dataset_id="frontier_ai_labs_revenue", latest_only=False,
                                         accepted_only=True, limit=100_000))
    assert first == second


@pytest.mark.parametrize("slug", ALL_NOTES)
def test_parsing_a_note_twice_is_identical(slug):
    first, first_report = parse_tickertrends_note(_note(slug))
    second, second_report = parse_tickertrends_note(_note(slug))
    assert first == second
    assert first_report == second_report
