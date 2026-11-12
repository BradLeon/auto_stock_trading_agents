"""Ramp AI Index v2 (upgrade-ramp-ai-index-v2): FRED first, the page runner only for what FRED lacks."""

from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import FetchRequest
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.pipelines.structured.discovery import discover_source, release_check
from ats.data.adapters.structured.registry import validate_source_registration
from ats.agents.evidence.ramp_visualization import render_ramp_charts
from ats.data.sources.ramp_ai_index import (
    INBOX_MAX_AGE_DAYS,
    OUT_OF_SCOPE,
    REGIME,
    RampAIIndexAdapter,
    RampBrowserAdapter,
    RampExportError,
    cross_check_fred,
    parse_adoption_tsv,
    parse_spend_tsv,
    parse_token_share_tsv,
    split_fred_covered,
    validate_ramp_records,
)
from ats.data.sources.ramp_ai_index_fred import RampAIIndexFredAdapter, fred_coverage, load_fred_series
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.data.products.base import DataProducts


NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)
COVERAGE = fred_coverage(StructuredCatalog.load())

# Shapes copied from the 2026-11-12 official exports (values shortened).
MODELS = ("Date\tSeries\tAdoption rate (%)\tMonthly change (pp)\tYearly change (pp)\n"
          "2026-10-01\tRamp Overall\t56.79\t0.97\t13.26\n"
          "2026-10-01\tAnthropic\t34.10\t1.10\t20.00\n"
          "2026-10-01\tOpenAI\t35.20\t0.10\t2.00\n"
          "2026-10-01\txAI\t2.10\t0.20\t1.00\n"
          "2026-10-01\tMistral AI\t0.80\t0.00\t0.10\n"
          "2026-10-01\tDeepSeek\t0.30\t0.00\t0.05\n")
SECTOR = ("Date\tSector\tAdoption rate (%)\tMonthly change (pp)\n"
          "2026-10-01\tAccommodation and food services\t34.96\t1.98\n"
          "2026-10-01\tInformation\t80.10\t0.50\n"
          "2026-10-01\tWholesale trade\t50.00\t0.40\n"
          "2026-10-01\tUtilities\t41.00\t0.30\n")
SPEND = ("Date\tMedian (USD / employee / month)\tTop 10% (USD / employee / month)\tTop 1% (USD / employee / month)\n"
         "2026-10-01\t16.32\t800.84\t10199.20\n"
         "2026-09-01\t15.38\t793.25\t9346.50\n")
# The official Shares export is fractions: each week sums to 1.0 (2026-11-12 real export).
MAKER_SHARES = ("Date\tAnthropic\tOpenAI\tGoogle\n"
                "2026-10-25\t0.55\t0.40\t0.05\n"
                "2026-11-01\t0.56\t0.39\t0.05\n")
MODEL_SHARES = ("Date\tClaude Opus 5.5 (Anthropic)\tGPT-6 Astra (OpenAI)\tGPT 5.6 TERRA (AUTO) (OpenAI)\n"
                "2026-10-25\t0.60\t0.30\t0.10\n"
                "2026-11-01\t0.61\t\t0.39\n")
MODEL_VOLUMES = ("Date\tClaude Opus 5.5 (Anthropic)\tGPT-6 Astra (OpenAI)\n"
                 "2026-10-25\t95.0\t55.0\n"
                 "2026-11-01\t100.0\t55.9\n")
PAGE = {"adoption_overall_models": MODELS, "adoption_sector": SECTOR, "spend_per_employee_overall": SPEND,
        "token_spend_share_by_maker": MAKER_SHARES, "token_spend_share_by_model": MODEL_SHARES}


def _page_adapter(**kwargs):
    kwargs.setdefault("clock", lambda: NOW)
    kwargs.setdefault("fred_coverage", COVERAGE)
    return RampAIIndexAdapter(**kwargs)


def _repo(tmp_path, name="ramp.sqlite"):
    repo = SQLiteStructuredRepository(tmp_path / name, artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    return repo


# --- FRED part ---------------------------------------------------------------------------------------------

class _Response:
    def __init__(self, *, content=b"", payload=None, status=200):
        self.content, self._payload, self.status_code = content, payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ConnectionError(f"http {self.status_code}")

    def json(self):
        return self._payload


class _FredClient:
    """FRED graph CSV + series metadata for every registered Ramp series."""

    def __init__(self, *, values=None, broken=(), frequency="Monthly"):
        self.values = values or {"2026-09-01": 55.83, "2026-10-01": 56.80}
        self.broken = set(broken)
        self.frequency = frequency
        self.calls = []

    def get(self, url, timeout=60):
        self.calls.append(url)
        series_id = url.rsplit("=", 1)[-1]
        if series_id in self.broken:
            return _Response(status=503)
        if "graph/api/series" in url:
            return _Response(payload={"chart_series": [{"series_objects": {"a": {
                "series_id": series_id,
                "title": f"Artificial Intelligence Adoption by Ramp Businesses: {series_id}",
                "frequency": self.frequency, "season": "Not Seasonally Adjusted", "units": "Percent",
                "notes": "Ramp AI Index measures the adoption rate ... more than 70,000 American businesses.",
                "last_updated": "2026-11-11 10:36:24-06"}}}]})
        rows = "".join(f"{period},{value}\n" for period, value in self.values.items())
        return _Response(content=f"observation_date,{series_id}\n{rows}".encode())


def _fred_request(**scope):
    return FetchRequest(source_id="ramp_ai_index_fred", dataset_id="ramp_ai_adoption", query_scope=scope)


def test_catalog_registers_fred_part_and_v2_scopes():
    catalog = StructuredCatalog.load()
    series = load_fred_series(catalog)
    assert len(series) == 14
    assert {spec["scope"] for spec in series.values()} == {
        "adoption_overall", "adoption_overall_models", "adoption_sector", "adoption_business_size"}
    assert validate_source_registration("ramp_ai_index_fred", catalog=catalog)["valid"]
    assert validate_source_registration("ramp_ai_index", catalog=catalog)["valid"]
    metrics = {item.id for item in catalog.metrics()}
    assert {"ai.ramp.token_spend_share_by_maker", "ai.ramp.token_spend_share_by_model"} <= metrics
    raw = catalog.raw["sources"]
    assert raw["ramp_ai_index"]["approved_methodology_regimes"] == ["ramp_ai_index_v2"]
    assert raw["ramp_ai_index_fred"]["approved_methodology_regimes"] == ["ramp_ai_index_v2"]


def test_fred_adapter_parses_all_series_and_isolates_failures():
    adapter = RampAIIndexFredAdapter(client=_FredClient(broken={"RAMPAIXAI"}), clock=lambda: NOW)
    found = adapter.discover(_fred_request())
    assert found.status.value == "partial"
    assert len(found.candidates) == 13 and "RAMPAIXAI" in found.diagnostics["series_failures"]
    batch = adapter.fetch(_fred_request(discovery_candidates=[item.model_dump(mode="json") for item in found.candidates]))
    assert batch.status.value == "succeeded"
    overall = [row for row in batch.records if row.entity_id == "RAMP_OVERALL"]
    assert [(row.period, row.value) for row in overall] == [("2026-09-01", 55.83), ("2026-10-01", 56.8)]
    assert overall[0].dimensions["methodology_regime"] == REGIME
    vendors = {row.entity_id for row in batch.records if row.provider_field == "vendor_adoption_pct"}
    assert vendors == {"RAMP_VENDOR:ANTHROPIC", "RAMP_VENDOR:OPENAI"}
    sizes = {row.entity_id for row in batch.records if row.dimensions["scope"] == "adoption_business_size"}
    assert sizes == {"RAMP_SIZE:SMALL", "RAMP_SIZE:MEDIUM", "RAMP_SIZE:LARGE"}


def test_fred_frequency_drift_is_refused():
    adapter = RampAIIndexFredAdapter(client=_FredClient(frequency="Quarterly"), clock=lambda: NOW)
    found = adapter.discover(_fred_request())
    assert found.status.value == "unreachable" or not found.candidates
    assert all("fred_metadata_drift" in reason for reason in found.diagnostics["series_failures"].values())


def test_fred_ingest_is_idempotent_and_reruns_report_no_change(tmp_path, monkeypatch):
    import ats.data.pipelines.structured.discovery as discovery

    repo = _repo(tmp_path)
    client = _FredClient()
    monkeypatch.setattr(discovery, "build_ingestion", lambda source_id, **kwargs: (
        RampAIIndexFredAdapter(client=client, clock=lambda: NOW),
        FetchRequest(source_id=source_id, dataset_id="ramp_ai_adoption", query_scope=kwargs.get("query_scope") or {})))
    monkeypatch.setattr(discovery, "ingest_source", lambda repository, source_id, *, query_scope, **kw:
                        IngestionPipeline(repository).run(
                            RampAIIndexFredAdapter(client=client, clock=lambda: NOW),
                            FetchRequest(source_id=source_id, dataset_id="ramp_ai_adoption", query_scope=query_scope)))
    first = release_check(repo, source_ids=["ramp_ai_index_fred"], ingest_new=True, force=True)
    assert first["status"] == "succeeded"
    assert first["sources"][0]["ingestion"]["accepted"] == 28
    second = release_check(repo, source_ids=["ramp_ai_index_fred"], ingest_new=True, force=True)
    assert second["status"] == "no_change"
    repo.close()


# --- page part: v2 parsers ---------------------------------------------------------------------------------

def test_v2_adoption_parser_keeps_vendor_and_sector_columns():
    vendors = parse_adoption_tsv(MODELS, scope="adoption_overall_models", fetched_at=NOW, slice_key="s")
    assert {row.entity_id for row in vendors} >= {"RAMP_VENDOR:MISTRAL_AI", "RAMP_VENDOR:DEEPSEEK", "RAMP_OVERALL"}
    assert all(row.dimensions["methodology_regime"] == REGIME for row in vendors)
    sectors = parse_adoption_tsv(SECTOR, scope="adoption_sector", fetched_at=NOW, slice_key="s")
    assert "NAICS:ACCOMMODATION_AND_FOOD_SERVICES" in {row.entity_id for row in sectors}
    # The v2 sector export has no yearly change column: no yearly record, not a zero.
    assert "yearly_change_pp" not in {row.provider_field for row in sectors}


def test_v2_spend_parser_reads_unit_suffixed_headers_and_keeps_quantile_gate():
    rows = parse_spend_tsv(SPEND, fetched_at=NOW, slice_key="spend")
    october = {row.entity_id: row.value for row in rows if row.period == "2026-10-01"}
    assert october == {"QUANTILE:MEDIAN": 16.32, "QUANTILE:TOP10": 800.84, "QUANTILE:TOP1": 10199.2}
    assert not validate_ramp_records(rows)
    bad = parse_spend_tsv(SPEND.replace("16.32", "900.00"), fetched_at=NOW, slice_key="s")
    assert any(item.startswith("spend_quantile_order_failed") for item in validate_ramp_records(bad))


def test_token_shares_become_weekly_long_rows_and_volumes_are_refused():
    makers, warnings = parse_token_share_tsv(MAKER_SHARES, scope="token_spend_share_by_maker", fetched_at=NOW)
    assert {row.entity_id for row in makers} == {"TOKEN_MAKER:ANTHROPIC", "TOKEN_MAKER:OPENAI", "TOKEN_MAKER:GOOGLE"}
    assert {row.period for row in makers} == {"2026-10-25", "2026-11-01"}      # weeks, not months
    assert all(row.period_basis == "calendar_week" for row in makers) and not warnings
    models, _ = parse_token_share_tsv(MODEL_SHARES, scope="token_spend_share_by_model", fetched_at=NOW)
    entities = {row.entity_id for row in models}
    assert "TOKEN_MODEL:ANTHROPIC:CLAUDE_OPUS_5_5" in entities
    assert "TOKEN_MODEL:OPENAI:GPT_5_6_TERRA_AUTO" in entities                    # maker = last parenthetical
    assert len([row for row in models if row.period == "2026-11-01"]) == 2       # an empty cell is skipped
    assert {row.value for row in makers if row.period == "2026-11-01"} == {56.0, 39.0, 5.0}   # stored as percent
    assert makers[0].raw["provider_value"] == "0.55" and makers[0].raw["provider_value_unit"] == "fraction"
    with pytest.raises(RampExportError, match="methodology_drift:token_export_not_share"):
        parse_token_share_tsv(MODEL_VOLUMES, scope="token_spend_share_by_model", fetched_at=NOW)


def test_token_share_total_off_100_is_a_warning_not_a_block():
    rows, warnings = parse_token_share_tsv("Date\tAnthropic\tOpenAI\n2026-10-25\t60\t40\n2026-11-01\t50\t48\n",
                                           scope="token_spend_share_by_maker", fetched_at=NOW)
    assert rows and warnings == ["token_share_total_off_100:2026-11-01:98.00"]


# --- page part: increments only ----------------------------------------------------------------------------

def test_page_rows_fred_publishes_are_not_stored():
    vendors = parse_adoption_tsv(MODELS, scope="adoption_overall_models", fetched_at=NOW, slice_key="s")
    kept, covered = split_fred_covered(vendors, COVERAGE)
    assert {row.entity_id for row in kept} == {"RAMP_VENDOR:MISTRAL_AI", "RAMP_VENDOR:DEEPSEEK"}
    assert {row.entity_id for row in covered} == {"RAMP_OVERALL", "RAMP_VENDOR:ANTHROPIC",
                                                  "RAMP_VENDOR:OPENAI", "RAMP_VENDOR:XAI"}
    sectors = parse_adoption_tsv(SECTOR, scope="adoption_sector", fetched_at=NOW, slice_key="s")
    kept, covered = split_fred_covered(sectors, COVERAGE)
    assert {row.entity_id for row in kept} == {"NAICS:WHOLESALE_TRADE", "NAICS:UTILITIES"}
    assert {row.entity_id for row in covered} == {"NAICS:ACCOMMODATION_AND_FOOD_SERVICES", "NAICS:INFORMATION"}


def test_cross_check_flags_page_values_that_differ_from_fred():
    vendors = parse_adoption_tsv(MODELS, scope="adoption_overall_models", fetched_at=NOW, slice_key="s")
    _, covered = split_fred_covered(vendors, COVERAGE)
    fred = {("RAMP_OVERALL", "2026-10"): 56.79, ("RAMP_VENDOR:ANTHROPIC", "2026-10"): 33.00}
    warnings = cross_check_fred(covered, fred)
    assert warnings == ["fred_page_mismatch:RAMP_VENDOR:ANTHROPIC:2026-10:page=34.1000:fred=33.0000"]


def test_page_fetch_stores_only_increments_and_records_cross_check(tmp_path):
    adapter = _page_adapter(fred_values=lambda: {("RAMP_VENDOR:ANTHROPIC", "2026-10"): 33.0})
    request = FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_adoption",
                           query_scope={"payloads": PAGE})
    found = adapter.discover(request)
    assert found.status.value == "new_release"
    assert found.diagnostics["fred_covered_rows"] > 0
    assert any(item.startswith("fred_page_mismatch:RAMP_VENDOR:ANTHROPIC") for item in found.diagnostics["warnings"])
    repo = _repo(tmp_path)
    result = IngestionPipeline(repo).run(adapter, request)
    assert result["status"] == "succeeded"
    stored = {row["entity_id"] for row in repo.observations(dataset_id="ramp_ai_adoption", source_id="ramp_ai_index",
                                                            latest_only=True, accepted_only=True, limit=1000)}
    assert stored == {"RAMP_VENDOR:MISTRAL_AI", "RAMP_VENDOR:DEEPSEEK", "NAICS:WHOLESALE_TRADE", "NAICS:UTILITIES"}
    repo.close()


# --- discovery: approved regime change vs drift -------------------------------------------------------------

def test_approved_regime_transition_is_not_drift_but_same_regime_change_is(tmp_path, monkeypatch):
    import ats.data.pipelines.structured.discovery as discovery

    repo = _repo(tmp_path)
    state = {"adapter": _page_adapter()}
    monkeypatch.setattr(discovery, "build_ingestion", lambda source_id, **kwargs: (
        state["adapter"], FetchRequest(source_id=source_id, dataset_id="ramp_ai_adoption",
                                       query_scope={"payloads": state["payloads"], "scope": "adoption_sector"})))
    # A v1 probe recorded the same scope with a different fingerprint and regime.
    repo.save_source_check(
        source_id="ramp_ai_index", dataset_id="ramp_ai_adoption", status="succeeded",
        latest_upstream_identity="RAMP:v1", latest_ingested_identity="RAMP:v1", latest_available_period="2026-08-01",
        candidates=[{"identity": "v1", "period": "2026-08-01", "urls": [], "methodology_fingerprint": "v1-fingerprint",
                     "metadata": {"scope": "adoption_sector", "methodology_regime": "ramp_ai_index_v1"}}])
    state["payloads"] = {"adoption_sector": SECTOR}
    moved = discover_source(repo, "ramp_ai_index", force=True)
    assert moved["status"] == "new_release"
    assert moved["diagnostics"]["approved_regime_transition"]["adoption_sector"] == {
        "previous_regime": "ramp_ai_index_v1", "regime": REGIME}
    repo.save_source_check(
        source_id="ramp_ai_index", dataset_id="ramp_ai_adoption", status="succeeded",
        latest_upstream_identity=moved["latest_upstream_identity"],
        latest_ingested_identity=moved["latest_upstream_identity"],
        latest_available_period=moved["latest_available_period"], candidates=moved["candidates"])
    state["payloads"] = {"adoption_sector": SECTOR.replace("Monthly change (pp)", "Monthly change (pp)\tNew column")
                         .replace("1.98\n", "1.98\tx\n").replace("0.50\n", "0.50\tx\n")
                         .replace("0.40\n", "0.40\tx\n").replace("0.30\n", "0.30\tx\n")}
    drift = discover_source(repo, "ramp_ai_index", force=True)
    assert drift["status"] == "methodology_drift"
    repo.close()


# --- page part: inbox freshness and failure isolation -------------------------------------------------------

def _write_inbox(inbox, scope, text, *, exported_at):
    inbox.mkdir(exist_ok=True)
    (inbox / f"{scope}.tsv").write_text(text, encoding="utf-8")
    (inbox / f"{scope}.json").write_text(json.dumps({"exported_at": exported_at.isoformat()}), encoding="utf-8")


def test_fresh_inbox_is_used_without_a_browser(tmp_path):
    inbox = tmp_path / "ramp_exports"
    _write_inbox(inbox, "adoption_sector", SECTOR, exported_at=NOW - timedelta(days=2))
    adapter = _page_adapter(export_dir=inbox)
    batch = adapter.fetch(FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_adoption",
                                       query_scope={"scope": "adoption_sector"}))
    assert batch.status.value == "succeeded"
    assert batch.artifacts[0].metadata["export_method"] == "official_clipboard"


def test_stale_inbox_is_never_reported_as_no_change(tmp_path):
    inbox = tmp_path / "ramp_exports"
    _write_inbox(inbox, "adoption_sector", SECTOR, exported_at=NOW - timedelta(days=INBOX_MAX_AGE_DAYS + 50))
    adapter = _page_adapter(export_dir=inbox)
    request = FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_adoption",
                           query_scope={"scope": "adoption_sector"})
    found = adapter.discover(request)
    assert found.status.value == "export_unreadable"
    assert "inbox_stale_60d" in found.diagnostics["scope_failures"]["adoption_sector"]
    batch = adapter.fetch(request)
    assert batch.status.value == "export_unreadable"


def test_stale_inbox_with_a_runner_asks_the_browser(tmp_path):
    inbox = tmp_path / "ramp_exports"
    _write_inbox(inbox, "adoption_sector", "Date\tSector\tAdoption rate (%)\n2026-08-01\tUtilities\t1\n",
                 exported_at=NOW - timedelta(days=60))
    calls = []
    adapter = _page_adapter(export_dir=inbox, browser=RampBrowserAdapter(lambda scope: calls.append(scope) or SECTOR))
    batch = adapter.fetch(FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_adoption",
                                       query_scope={"scope": "adoption_sector"}))
    assert calls == ["adoption_sector"] and batch.status.value == "succeeded"
    assert max(row.period for row in batch.records) == "2026-10-01"


def test_missing_browser_fails_the_page_part_explicitly():
    from ats.data.sources.ramp_browser_runner import RampBrowserRunner, playwright_available

    if playwright_available():
        pytest.skip("playwright installed; covered by the runner fixture test")
    adapter = _page_adapter(browser=RampBrowserAdapter(RampBrowserRunner("/nonexistent").export))
    batch = adapter.fetch(FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_spend"))
    assert batch.status.value == "export_unreadable"
    assert "browser_not_installed" in batch.failures[0].message


def test_one_page_scope_failing_does_not_block_others():
    adapter = _page_adapter()
    batch = adapter.fetch(FetchRequest(
        source_id="ramp_ai_index", dataset_id="ramp_ai_spend",
        query_scope={"payloads": {"spend_per_employee_overall": SPEND,
                                  "token_spend_share_by_maker": MAKER_SHARES,
                                  "token_spend_share_by_model": MODEL_VOLUMES}}))
    assert batch.status.value == "partial"
    assert {failure.status.value for failure in batch.failures} == {"methodology_drift"}
    assert {row.dimensions["scope"] for row in batch.records} == {"spend_per_employee_overall",
                                                                  "token_spend_share_by_maker"}


def test_clipboard_and_browser_fail_closed():
    with pytest.raises(RampExportError, match="export_unreadable"):
        RampBrowserAdapter(lambda scope: b"").export("adoption_sector")
    with pytest.raises(RampExportError, match="permission"):
        RampBrowserAdapter(lambda scope: (_ for _ in ()).throw(PermissionError())).export("adoption_sector")
    assert set(OUT_OF_SCOPE) == {"geographies", "token_volume", "token_prices", "ai_share_of_business_spend"}


def test_environment_api_key_does_not_activate_a_fallback(monkeypatch):
    monkeypatch.setenv("RAMP_DATA_API_KEY", "should-not-be-read")
    batch = _page_adapter().fetch(FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_adoption", query_scope={}))
    assert batch.status.value == "export_unreadable"
    assert "official_web_export_required" in batch.failures[0].message


def test_release_check_hands_discovered_payloads_to_ingest_once(tmp_path, monkeypatch):
    """A scheduled probe must not invoke the browser twice for one release."""
    import ats.data.pipelines.structured.discovery as discovery

    calls = {"exports": 0, "payloads": {}}

    def export(scope):
        calls["exports"] += 1
        return PAGE[scope]

    adapter = _page_adapter(browser=RampBrowserAdapter(export))
    request = FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_adoption")
    repo = _repo(tmp_path)
    monkeypatch.setattr(discovery, "build_ingestion", lambda *args, **kwargs: (adapter, request))

    def fake_ingest(repository, source_id, *, query_scope, **kwargs):
        calls["payloads"] = query_scope.get("payloads", {})
        return {"status": "succeeded", "run_id": "run-once"}

    monkeypatch.setattr(discovery, "ingest_source", fake_ingest)
    result = release_check(repo, source_ids=["ramp_ai_index"], dataset_id="ramp_ai_adoption",
                           ingest_new=True, force=True, catalog=StructuredCatalog.load())
    assert result["status"] == "succeeded"
    assert calls["exports"] == 2          # the adoption job's two page charts, once each
    assert calls["payloads"]["adoption_sector"] == SECTOR
    repo.close()


# --- regimes are separate measurements ---------------------------------------------------------------------

def _seed_v1_overall(repo, value=56.13):
    """A v1 page observation as stored before 2026-11 (regime v1, same entity and month)."""
    from ats.data.sources import ramp_ai_index as page

    v1 = parse_adoption_tsv(f"Date\tSeries\tAdoption rate (%)\n2026-08-01\tRamp Overall\t{value}\n",
                            scope="adoption_overall", fetched_at=NOW, slice_key="v1")
    for record in v1:
        record.dimensions["methodology_regime"] = "ramp_ai_index_v1"

    class _V1:
        def fetch(self, request):
            from ats.data.core.structured_models import AdapterArtifact, AdapterBatch, IngestionStatus
            return AdapterBatch(source_id="ramp_ai_index", dataset_id="ramp_ai_adoption",
                                status=IngestionStatus.SUCCEEDED, fetched_at=NOW - timedelta(days=60), records=v1,
                                artifacts=[AdapterArtifact(artifact_key="v1", payload=b"v1", query_scope={},
                                                           source_url=page.PAGE_URL, source_version="v1",
                                                           media_type="text/plain", retention="query_slice",
                                                           storage_mode="full", pointer=page.PAGE_URL)])

    IngestionPipeline(repo).run(_V1(), FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_adoption"))


def test_v2_fred_value_is_not_a_conflict_with_v1_and_products_read_v2_only(tmp_path):
    repo = _repo(tmp_path)
    _seed_v1_overall(repo)
    client = _FredClient(values={"2026-08-01": 55.17, "2026-09-01": 55.83, "2026-10-01": 56.80})
    result = IngestionPipeline(repo).run(RampAIIndexFredAdapter(client=client, clock=lambda: NOW),
                                         _fred_request())
    assert result["status"] == "succeeded"
    products = DataProducts(structured_repository=repo)
    v2 = products.ramp_paid_adoption_snapshot(scope="adoption_overall")
    assert [(row["period"], row["value"]) for row in v2["rows"]] == [
        ("2026-08-01", 55.17), ("2026-09-01", 55.83), ("2026-10-01", 56.8)]
    assert {row["quality_status"] for row in v2["rows"]} == {"accepted"}       # not "conflict" with v1
    assert v2["methodology_regime"] == "ramp_ai_index_v2" and v2["source_ids"] == ["ramp_ai_index_fred"]
    v1 = products.ramp_paid_adoption_snapshot(scope="adoption_overall", methodology_regime="ramp_ai_index_v1")
    assert [(row["period"], row["value"]) for row in v1["rows"]] == [("2026-08-01", 56.13)]
    sizes = products.ramp_paid_adoption_snapshot(scope="adoption_business_size")
    assert {row["entity_id"] for row in sizes["rows"]} == {"RAMP_SIZE:SMALL", "RAMP_SIZE:MEDIUM", "RAMP_SIZE:LARGE"}
    repo.close()


def test_token_series_and_discontinued_model_share(tmp_path):
    repo = _repo(tmp_path)
    request = FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_spend", query_scope={"payloads": PAGE})
    assert IngestionPipeline(repo).run(_page_adapter(), request)["status"] == "succeeded"
    products = DataProducts(structured_repository=repo)
    makers = products.ramp_token_spend_share_series(scope="token_spend_share_by_maker")
    assert makers["status"] == "ok" and makers["periods"] == ["2026-10-25", "2026-11-01"]
    assert {row["maker"] for row in makers["rows"]} == {"Anthropic", "OpenAI", "Google"}
    gone = products.ramp_model_market_share_series(scope="model_market_share_overall")
    assert gone["status"] == "scope_discontinued" and gone["rows"] == []
    spend = products.ramp_spend_per_employee_series()
    assert spend["status"] == "ok" and spend["methodology_regime"] == "ramp_ai_index_v2"
    repo.close()


def test_page_part_status_reports_stale_and_failures(tmp_path):
    repo = _repo(tmp_path)
    products = DataProducts(structured_repository=repo)
    assert products.ramp_page_part_status(as_of=NOW)["status"] == "export_stale"     # never succeeded
    request = FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_spend", query_scope={"payloads": PAGE})
    IngestionPipeline(repo).run(_page_adapter(), request)
    assert products.ramp_page_part_status()["status"] == "ok"
    later = datetime.now(timezone.utc) + timedelta(days=40)
    assert products.ramp_page_part_status(as_of=later)["status"] == "export_stale"
    repo.save_source_check(source_id="ramp_ai_index", dataset_id="ramp_ai_spend", status="export_unreadable",
                           latest_upstream_identity="", latest_ingested_identity="", latest_available_period="",
                           candidates=[])
    assert products.ramp_page_part_status()["status"] == "export_unreadable"
    repo.close()


# --- report charts -----------------------------------------------------------------------------------------

def test_ramp_renderer_draws_v2_scopes_with_weekly_token_axis(tmp_path):
    def rows(scope, segment, metric="ai.ramp.paid_business_adoption_share", periods=("2026-09-01", "2026-10-01")):
        return {"rows": [{"period": period, "segment": segment, "value": 50 + index, "metric_id": metric,
                          "observation_id": f"{scope}-{index}", "artifact_id": "a"}
                         for index, period in enumerate(periods)], "status": "ok", "manifest": {"snapshot_id": "m"}}

    token_rows = [{"period": week, "maker": maker, "segment": maker, "value": value,
                   "metric_id": "ai.ramp.token_spend_share_by_maker"}
                  for week in ("2026-10-25", "2026-11-01") for maker, value in (("Anthropic", 60), ("OpenAI", 40))]
    signal = {"status": "ok", "slices": {
        "adoption_overall": rows("adoption_overall", "Ramp Overall"),
        "adoption_overall_models": rows("adoption_overall_models", "Mistral AI", "ai.ramp.vendor_adoption_share"),
        "adoption_sector": rows("adoption_sector", "Utilities"),
        "adoption_business_size": rows("adoption_business_size", "Small businesses"),
        "spend_per_employee_overall": {"rows": [
            {"period": "2026-10-01", "quantile": "median", "value": 16, "metric_id": "ai.ramp.ai_spend_per_employee"}],
            "status": "ok"},
        "token_spend_share_by_maker": {"rows": token_rows, "status": "ok"},
        "token_spend_share_by_model": {"rows": [dict(row, model=f"{row['maker']} M",
                                                     metric_id="ai.ramp.token_spend_share_by_model")
                                                for row in token_rows], "status": "ok"},
    }}
    result = render_ramp_charts(packet={"primary_signals": {"ramp_paid_adoption": signal}}, output_dir=tmp_path)
    assert {item["scope"] for item in result["descriptors"]} == set(signal["slices"])
    assert len(result["tables"]) == 7
    weekly = next(item for item in result["descriptors"] if item["scope"] == "token_spend_share_by_maker")
    assert weekly["display"]["latest_period"] == "2026-11-01"


# --- runner (needs the optional Playwright dependency) ------------------------------------------------------

def test_runner_exports_each_chart_from_a_local_fixture_page(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from ats.data.sources.ramp_browser_runner import CHART_CLICKS, RampBrowserRunner

    buttons = sorted({label for labels in CHART_CLICKS.values() for label in labels})
    page = tmp_path / "ramp.html"
    page.write_text("""<html><head><title>Ramp AI Index</title></head><body>
%s<button id='get'>Get the data.</button>
<script>
let chart = [];
const data = %s;
document.querySelectorAll('button.nav').forEach(b => b.onclick = () => { chart.push(b.textContent); });
document.getElementById('get').onclick = () => {
  const key = chart.slice(-3).join('|');
  const scope = Object.keys(data).find(s => key.endsWith(data[s].path));
  navigator.clipboard.writeText(data[scope].text);
};
</script></body></html>""" % ("".join(f"<button class='nav'>{label}</button>" for label in buttons),
                               json.dumps({scope: {"path": "|".join(CHART_CLICKS[scope][-2:]), "text": PAGE[scope]}
                                           for scope in CHART_CLICKS})), encoding="utf-8")
    runner = RampBrowserRunner(tmp_path / "inbox", page_url=page.as_uri(), chart_timeout=10)
    for scope in CHART_CLICKS:
        assert runner.export(scope) == PAGE[scope].encode()
        assert (tmp_path / "inbox" / f"{scope}.tsv").read_text(encoding="utf-8") == PAGE[scope]
        assert json.loads((tmp_path / "inbox" / f"{scope}.json").read_text())["payload_sha256"]


def test_runner_reports_missing_controls_and_blocked_pages(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from ats.data.sources.ramp_browser_runner import RampBrowserRunner

    blocked = tmp_path / "blocked.html"
    blocked.write_text("<html><head><title>Just a moment...</title></head><body></body></html>", encoding="utf-8")
    with pytest.raises(RampExportError, match="navigation_blocked"):
        RampBrowserRunner(tmp_path / "inbox", page_url=blocked.as_uri(), chart_timeout=5).export("adoption_sector")
    empty = tmp_path / "empty.html"
    empty.write_text("<html><head><title>Ramp AI Index</title></head><body></body></html>", encoding="utf-8")
    with pytest.raises(RampExportError, match="control_missing"):
        RampBrowserRunner(tmp_path / "inbox", page_url=empty.as_uri(), chart_timeout=5).export("adoption_sector")


# --- L1 observer: only the page part failing ---------------------------------------------------------------

def test_observer_keeps_fred_scopes_when_only_the_page_part_fails(tmp_path):
    from ats.agents.evidence.work_adoption import RAMP_SCOPES, _ramp_supplement

    repo = _repo(tmp_path)
    IngestionPipeline(repo).run(RampAIIndexFredAdapter(client=_FredClient(), clock=lambda: NOW), _fred_request())
    IngestionPipeline(repo).run(_page_adapter(), FetchRequest(
        source_id="ramp_ai_index", dataset_id="ramp_ai_spend", query_scope={"payloads": PAGE}))
    repo.save_source_check(source_id="ramp_ai_index", dataset_id="ramp_ai_spend", status="export_unreadable",
                           latest_upstream_identity="", latest_ingested_identity="", latest_available_period="",
                           candidates=[])
    signal = _ramp_supplement(DataProducts(structured_repository=repo), as_of=None)
    assert set(signal["slices"]) == set(RAMP_SCOPES)
    assert signal["status"] == "partial"
    for scope in ("adoption_overall", "adoption_business_size"):
        assert signal["slices"][scope]["status"] == "ok"
    for scope in ("spend_per_employee_overall", "token_spend_share_by_maker", "token_spend_share_by_model"):
        assert signal["slices"][scope]["status"] == "export_unreadable"
        assert signal["slices"][scope]["reason"]
    assert signal["page_part"]["status"] == "export_unreadable"
    assert any(item.startswith("ramp_page_part_export_unreadable") for item in signal["warnings"])
    repo.close()


def test_one_chart_the_runner_cannot_export_does_not_cost_the_others():
    def export(scope):
        if scope == "token_spend_share_by_model":
            raise RampExportError("export_unreadable:control_missing:Shares")
        return PAGE[scope]

    adapter = _page_adapter(browser=RampBrowserAdapter(export))
    request = FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_spend")
    found = adapter.discover(request)
    assert found.status.value == "partial"
    assert set(found.diagnostics["scope_failures"]) == {"token_spend_share_by_model"}
    batch = adapter.fetch(request)
    assert batch.status.value == "partial"
    assert {row.dimensions["scope"] for row in batch.records} == {"spend_per_employee_overall",
                                                                  "token_spend_share_by_maker"}
    assert [failure.slice_key for failure in batch.failures] == ["ramp:token_spend_share_by_model"]


def test_sectors_the_page_names_differently_are_still_recognised_as_fred_series():
    from ats.data.sources.ramp_ai_index_fred import fred_page_aliases

    aliases = fred_page_aliases(StructuredCatalog.load())
    assert aliases == {("adoption_sector", "NAICS:HEALTH_CARE"): "NAICS:HEALTH_CARE_AND_SOCIAL_ASSISTANCE",
                       ("adoption_sector", "NAICS:RETAIL"): "NAICS:RETAIL_TRADE",
                       ("adoption_sector", "NAICS:TECHNOLOGY_AND_MEDIA"): "NAICS:INFORMATION"}
    page = parse_adoption_tsv("Date\tSector\tAdoption rate (%)\n2026-10-01\tTechnology and media\t80.1\n"
                              "2026-10-01\tUtilities\t41.0\n", scope="adoption_sector", fetched_at=NOW, slice_key="s")
    kept, covered = split_fred_covered(page, COVERAGE)
    assert [row.entity_id for row in kept] == ["NAICS:UTILITIES"]
    assert cross_check_fred(covered, {("NAICS:INFORMATION", "2026-10"): 79.0}, aliases=aliases) == [
        "fred_page_mismatch:NAICS:TECHNOLOGY_AND_MEDIA:2026-10:page=80.1000:fred=79.0000"]

