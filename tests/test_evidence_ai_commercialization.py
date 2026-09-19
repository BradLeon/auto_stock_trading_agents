"""L1 commercialization Observer, report and dual output-path tests.

Covers tasks 6.1-6.7 and 7.3-7.4: the commercialization claim is independent of
the production claim — separate run, separate report, separate failure.
"""

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
import yaml

from ats.agents.evidence.commercialization import (
    COMMERCIALIZATION_CLAIM_ID, REVENUE_SECTION_ID, observe_ai_commercialization,
    render_ai_commercialization_markdown)
from ats.agents.evidence.layer_runner import (
    OBSERVER_RUNNERS, _configured_observers, _rewrite_report_asset_links,
    render_layer_evidence_markdown, run_registered_layer_observers,
    write_layer_evidence_outputs)
from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import FetchRequest
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.products import DataProducts
from ats.data.sources.frontier_ai_labs_revenue import (
    SacraPublicCompanyProfilesAdapter, TickerTrendsPublicResearchAdapter)
from ats.data.sources.openrouter_rankings import OpenRouterRankingsAdapter
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.schemas.sector import EvidenceObserverRef, SectorConfig

FIXTURES = Path(__file__).parent / "fixtures" / "frontier_ai_labs_revenue"
NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)
PRODUCTION_CLAIM_ID = "ai_core_production_workflow_penetration"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

@pytest.fixture
def seeded_products(tmp_path):
    """A hermetic store holding exactly the two governed revenue sources."""
    repository = SQLiteStructuredRepository(tmp_path / "revenue.sqlite",
                                            artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog(StructuredCatalog.load())
    pipeline = IngestionPipeline(repository)
    pipeline.run(SacraPublicCompanyProfilesAdapter(fixture_dir=FIXTURES,
                                                   clock=lambda: NOW),
                 FetchRequest(source_id="sacra_public_company_profiles",
                              dataset_id="frontier_ai_labs_revenue"))
    pipeline.run(TickerTrendsPublicResearchAdapter(
        seed_path=FIXTURES / "tickertrends_anthropic_vs_openai_arr_tracking.json",
        clock=lambda: NOW),
        FetchRequest(source_id="tickertrends_public_research",
                     dataset_id="frontier_ai_labs_revenue"))
    return DataProducts(structured_repository=repository)


def _scope():
    return {"sector": "ai_hardware", "sector_label": "AI 硬件",
            "layer": "L1_app", "layer_label": "应用层",
            "evidence_sections": [REVENUE_SECTION_ID]}


def _commercialization_ref(**overrides):
    payload = {"claim_id": COMMERCIALIZATION_CLAIM_ID,
               "claim_definition_version": "v1", "runner": "ai_commercialization",
               "label": "AI 商业化能力", "enabled": True,
               "evidence_sections": [REVENUE_SECTION_ID]}
    payload.update(overrides)
    return EvidenceObserverRef(**payload)


def _production_ref(**overrides):
    payload = {"claim_id": PRODUCTION_CLAIM_ID, "claim_definition_version": "v2",
               "runner": "ai_production_penetration", "label": "AI 生产化与应用扩散",
               "enabled": True}
    payload.update(overrides)
    return EvidenceObserverRef(**payload)


# --------------------------------------------------------------------------- #
# 6.1 / 6.3 — packet shape and the fixed commercialization wording
# --------------------------------------------------------------------------- #

def test_packet_states_revenue_observed_and_economics_unverified(seeded_products):
    packet = observe_ai_commercialization(products=seeded_products,
                                          workflow_scope=_scope(), as_of=NOW)
    assert packet["claim_id"] == COMMERCIALIZATION_CLAIM_ID
    assert packet["claim_definition_version"] == "v1"
    assert packet["status"] == "ok"
    assert packet["economics_verified"] is False
    assert packet["retention_verified"] is False
    assert packet["overall_status"] == "revenue_monetization_expanding_but_economics_unverified"
    assert packet["coverage"]["revenue_scale_and_growth"] == "observed"
    for key in ("revenue_retention", "unit_economics", "business_model_durability"):
        assert packet["coverage"][key] == "not_yet_observed"
    assert packet["evidence_sections"][0]["section_id"] == REVENUE_SECTION_ID
    assert {company["entity_id"] for company in packet["companies"]} == {"OPENAI",
                                                                        "ANTHROPIC"}


def test_packet_says_monetization_direction_only_not_sustainability(seeded_products):
    packet = observe_ai_commercialization(products=seeded_products,
                                          workflow_scope=_scope(), as_of=NOW)
    text = render_ai_commercialization_markdown(packet)
    assert "尚未验证" in text
    assert "留存" in text and "单位经济" in text
    assert "不代表" in text  # Labs are not the whole L1 application layer
    assert packet["overall_interpretation"]


def test_openrouter_is_an_independent_commercialization_evidence_section(seeded_products):
    """The route-usage signal is additive and never replaces lab revenue."""
    rows = []
    start = datetime(2026, 9, 1, tzinfo=timezone.utc).date()
    from datetime import timedelta
    for offset in range(14):
        day = start + timedelta(days=offset)
        rows.extend([
            {"date": day.isoformat(), "model_permaslug": "openai/gpt-4o", "total_tokens": str(100 + offset)},
            {"date": day.isoformat(), "model_permaslug": "anthropic/claude-3", "total_tokens": str(50 + offset)},
            {"date": day.isoformat(), "model_permaslug": "other", "total_tokens": "20"},
        ])
    payload = json.dumps({"data": rows, "meta": {"version": "v1"}}).encode()

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return payload

    IngestionPipeline(seeded_products.structured).run(
        OpenRouterRankingsAdapter(api_key="fixture", opener=lambda _request, timeout=30: Response(),
                                  clock=lambda: datetime(2026, 9, 16, tzinfo=timezone.utc)),
        FetchRequest(source_id="openrouter_rankings", dataset_id="openrouter_rankings_daily",
                     query_scope={"start_date": "2026-09-01", "end_date": "2026-09-14"}))
    scope = {**_scope(), "supplemental_claims": [{"claim_id": "openrouter_routed_usage_and_competition"}]}
    packet = observe_ai_commercialization(products=seeded_products, workflow_scope=scope, as_of=NOW)
    assert packet["status"] == "ok"
    assert [item["section_id"] for item in packet["evidence_sections"]] == [
        REVENUE_SECTION_ID, "openrouter_routed_usage_and_competition"]
    assert packet["openrouter"]["status"] in {"ok", "insufficient_history", "unavailable"}
    assert "token" in render_ai_commercialization_markdown(packet)
    assert "金额-token" not in packet["context"]["compact"]


def test_unavailable_revenue_with_live_route_data_is_a_token_proxy_only():
    """A live route bundle keeps the report publishable, but never as revenue."""
    revenue_bundle = {"status": "unavailable", "companies": [], "limitations": [],
                      "section": {"status": "unavailable", "reason": "尚未加载收入 vintage"}}
    route_bundle = {"status": "ok", "directional_status": "expanding", "facts": {},
                    "warnings": [], "weeks": []}

    class Stub:
        def frontier_labs_revenue_evidence_bundle(self, **_kwargs):
            return revenue_bundle

        def openrouter_token_evidence_bundle(self, **_kwargs):
            return route_bundle

    scope = {**_scope(),
             "supplemental_claims": [{"claim_id": "openrouter_routed_usage_and_competition"}]}
    packet = observe_ai_commercialization(products=Stub(), workflow_scope=scope, as_of=NOW)

    assert packet["status"] == "ok"
    assert packet["section_status"] == "unavailable"
    assert packet["overall_status"] == "revenue_unavailable_token_proxy_observed"
    assert "不等于收入" in packet["overall_interpretation"]
    assert packet["cross_evidence"]["status"] == "openrouter_only"

    text = render_ai_commercialization_markdown(packet)
    assert "revenue_unavailable_token_proxy_observed" in text
    assert "不代表收入" in text


def test_section_not_enabled_is_reported_not_faked(seeded_products):
    packet = observe_ai_commercialization(
        products=seeded_products, as_of=NOW,
        workflow_scope={"sector": "ai_hardware", "layer": "L1_app",
                        "evidence_sections": ["unit_economics"]})
    assert packet["status"] == "section_not_enabled"
    assert packet["facts"] == []


def test_missing_dataproduct_is_unavailable_without_touching_other_observers():
    class Bare:
        pass

    packet = observe_ai_commercialization(products=Bare(), workflow_scope=_scope())
    assert packet["status"] == "unavailable"
    assert "不影响" in packet["warnings"][0]


# --------------------------------------------------------------------------- #
# 6.4 — bounded agent context
# --------------------------------------------------------------------------- #

def test_context_is_bounded_and_points_at_lineage(seeded_products):
    packet = observe_ai_commercialization(products=seeded_products,
                                          workflow_scope=_scope(), as_of=NOW)
    context = packet["context"]
    assert context["budget_status"] == "ok"
    compact = context["compact"]
    assert context["compact_char_count"] <= context["compact_budget"] == 12_000
    assert context["review_char_count"] <= context["review_budget"] == 120_000
    import json as _json

    essential = _json.loads(compact)
    for key in ("claim_id", "claim_definition_version", "coverage", "companies",
                "warnings", "lineage_pointer", "rows_hash"):
        assert key in essential
    assert essential["manifest_id"]
    assert essential["economics_verified"] is False
    # The context never flattens a web page into the prompt.
    assert "Sacra estimates" not in compact


# --------------------------------------------------------------------------- #
# 6.5 / 6.6 / 6.7 — report, charts and shared rows hash
# --------------------------------------------------------------------------- #

def test_report_and_charts_share_one_rows_hash(seeded_products, tmp_path):
    chart_dir = tmp_path / "charts"
    packet = observe_ai_commercialization(products=seeded_products,
                                          workflow_scope=_scope(), as_of=NOW,
                                          chart_dir=str(chart_dir))
    assert packet["visualization_descriptors"], "a chart must be produced"
    assert packet["chart_font"]["glyph_check"].get("status") == "ok"
    rows_hash = packet["rows_hash"]
    assert rows_hash
    for descriptor in packet["visualization_descriptors"]:
        assert descriptor["rows_hash"] == rows_hash
        assert Path(descriptor["png_path"]).exists()
        assert Path(descriptor["sidecar_path"]).exists()
    assert any(Path(descriptor["png_path"]).name ==
               "frontier_labs_revenue_trend.png"
               for descriptor in packet["visualization_descriptors"])
    history_tables = [t for t in packet["table_descriptors"]
                      if t["table_name"] == "frontier_labs_revenue_history"]
    assert history_tables, "the history table is the only one bound to packet.rows_hash"
    for table in history_tables:
        assert table["rows_hash"] == rows_hash
        assert table["chart_rows_hash"] == rows_hash
        assert Path(table["json_path"]).exists() and Path(table["csv_path"]).exists()

    markdown = render_ai_commercialization_markdown(packet)
    assert rows_hash in markdown
    for heading in ("命题覆盖", "最新可比收入", "历史观察表", "来源、口径与冲突",
                    "指标公式、统计范围与数据缺口", "尚待建设的留存"):
        assert heading in markdown


def test_report_asset_links_are_relative_to_the_report(tmp_path):
    asset = tmp_path / "assets" / "chart.png"
    asset.parent.mkdir()
    asset.write_bytes(b"png")
    report = tmp_path / "report.md"
    result = {"packets": [{"visualization_descriptors": [{"png_path": str(asset)}]}]}
    normalized = _rewrite_report_asset_links(f"![chart]({asset})", result, report)
    assert normalized == "![chart](assets/chart.png)"


def test_rendered_markdown_lists_every_observation_id_it_used(seeded_products):
    packet = observe_ai_commercialization(products=seeded_products,
                                          workflow_scope=_scope(), as_of=NOW)
    markdown = render_ai_commercialization_markdown(packet)
    for company in packet["companies"]:
        for observation_id in company["trend"].get("input_observation_ids") or []:
            assert observation_id in markdown


# --------------------------------------------------------------------------- #
# 6.2 / 7.3 — independent runners and independent output paths
# --------------------------------------------------------------------------- #

def test_sector_config_declares_two_independent_observers():
    raw = yaml.safe_load(Path("config/sectors/ai_hardware.yaml").read_text())
    layer = next(item for item in raw["layers"] if item["key"] == "L1_app")
    refs = [EvidenceObserverRef(**item) for item in layer["evidence_observers"]]
    by_claim = {ref.claim_id: ref for ref in refs}
    assert by_claim[PRODUCTION_CLAIM_ID].runner == "ai_production_penetration"
    assert by_claim[PRODUCTION_CLAIM_ID].claim_definition_version == "v3"
    assert by_claim[COMMERCIALIZATION_CLAIM_ID].runner == "ai_commercialization"
    assert by_claim[COMMERCIALIZATION_CLAIM_ID].evidence_sections == \
        [REVENUE_SECTION_ID]
    # The production declaration governs Ramp as a primary evidence source.
    assert by_claim[PRODUCTION_CLAIM_ID].primary_sources == ["ramp_ai_index"]


def test_registry_resolves_both_runners_with_their_fixed_claims():
    assert set(OBSERVER_RUNNERS) == {"ai_production_penetration", "ai_commercialization"}
    assert OBSERVER_RUNNERS["ai_commercialization"][0] == COMMERCIALIZATION_CLAIM_ID
    assert OBSERVER_RUNNERS["ai_production_penetration"][0] == PRODUCTION_CLAIM_ID


def test_configuration_is_rejected_when_claim_and_runner_disagree():
    _, error = _configured_observers(
        [_commercialization_ref(claim_id=PRODUCTION_CLAIM_ID)])
    assert error["status"] == "invalid_observer_configuration"
    _, error = _configured_observers([_commercialization_ref(runner="nope")])
    assert error["status"] == "unknown_observer_runner"
    _, error = _configured_observers([_commercialization_ref(),
                                      _commercialization_ref()])
    assert error["status"] == "invalid_observer_configuration"
    assert error["duplicate_claim_ids"] == [COMMERCIALIZATION_CLAIM_ID]


def test_commercialization_runs_alone_when_production_is_disabled(seeded_products):
    result = run_registered_layer_observers(
        sector="ai_hardware", sector_label="AI 硬件", layer="L1_app",
        layer_label="应用层",
        observer_refs=[_production_ref(enabled=False), _commercialization_ref()],
        as_of=NOW, products=seeded_products)
    assert result["status"] == "ok"
    assert [packet["claim_id"] for packet in result["packets"]] == \
        [COMMERCIALIZATION_CLAIM_ID]
    assert result["packets"][0]["status"] == "ok"


def test_two_observers_write_two_independent_reports(seeded_products, tmp_path):
    result = run_registered_layer_observers(
        sector="ai_hardware", sector_label="AI 硬件", layer="L1_app",
        layer_label="应用层",
        observer_refs=[_commercialization_ref()],
        as_of=NOW, products=seeded_products)
    commercialization = result["packets"][0]
    production = {"claim_id": PRODUCTION_CLAIM_ID,
                  "claim_definition_version": "v2",
                  "claim_text": "生产化命题占位", "status": "ok",
                  "facts": ["production fact"], "warnings": []}
    result["packets"].append(production)
    written = write_layer_evidence_outputs(result, tmp_path / "evidence" / "L1_app.md")
    names = sorted(path.name for path in written)
    assert names == sorted([f"L1_app-{COMMERCIALIZATION_CLAIM_ID}.md",
                           f"L1_app-{PRODUCTION_CLAIM_ID}.md", "L1_app.md"])
    commercialization_path = next(
        path for path in written if COMMERCIALIZATION_CLAIM_ID in path.name)
    body = commercialization_path.read_text(encoding="utf-8")
    assert body.startswith("# ") and COMMERCIALIZATION_CLAIM_ID not in body.splitlines()[0]
    assert "生产化命题占位" not in body
    combined = (tmp_path / "evidence" / "L1_app.md").read_text(encoding="utf-8")
    assert COMMERCIALIZATION_CLAIM_ID in combined and PRODUCTION_CLAIM_ID in combined
    # Independence guarantee: a commercialization failure cannot leave the
    # production report half-written.  We verify by re-writing with only the
    # production packet at a separate path and observing exactly one file.
    only_production = {"status": "ok",
                       "workflow_scope": result["workflow_scope"],
                       "packets": [production]}
    one_claim = write_layer_evidence_outputs(only_production,
                                             tmp_path / "evidence-bare" / "L1_app.md")
    assert [path.name for path in one_claim] == ["L1_app.md"]
    commercialization_path = next(
        path for path in written if COMMERCIALIZATION_CLAIM_ID in path.name)
    body = commercialization_path.read_text(encoding="utf-8")
    assert body.startswith("# ") and COMMERCIALIZATION_CLAIM_ID not in body.splitlines()[0]
    assert "生产化命题占位" not in body
    combined = (tmp_path / "evidence" / "L1_app.md").read_text(encoding="utf-8")
    assert COMMERCIALIZATION_CLAIM_ID in combined and PRODUCTION_CLAIM_ID in combined


def test_a_single_production_run_still_renders_the_legacy_document(tmp_path):
    """The existing L1 review artifact must not churn when only one claim runs."""
    from ats.agents.evidence.work_adoption import render_ai_production_markdown

    packet = {"claim_id": PRODUCTION_CLAIM_ID, "claim_definition_version": "v2",
              "claim_text": "生产化命题占位", "status": "ok",
              "facts": ["production fact"], "warnings": [], "companies": [],
              "history_rows": [], "rows_hash": "", "source_conflicts": [],
              "coverage": {}, "evidence_sections": [], "methodology_card": {},
              "context": {"compact": {}, "review": {}, "budget": {}}}
    result = {"status": "ok",
              "workflow_scope": {"sector": "ai_hardware", "sector_label": "AI 硬件",
                                 "layer": "L1_app", "layer_label": "应用层"},
              "packets": [packet]}
    assert render_layer_evidence_markdown(result) == \
        render_ai_production_markdown(packet)
    # One claim keeps one file: no per-claim sibling is invented for it.
    written = write_layer_evidence_outputs(result, tmp_path / "L1_app.md")
    assert [path.name for path in written] == ["L1_app.md"]


# --------------------------------------------------------------------------- #
# 7.4 — the production claim's own data is untouched
# --------------------------------------------------------------------------- #

def test_commercialization_never_reads_the_production_sources(seeded_products, monkeypatch):
    """BTOS / RPS / Ramp / Anthropic Economic Index must stay out of this claim."""
    forbidden = ("census_btos_ai_snapshot", "rps_genai_adoption_snapshot",
                 "ramp_ai_index_snapshot", "ai_work_adoption_snapshot")

    def blocked(name):
        def _raise(*args, **kwargs):
            raise AssertionError(f"commercialization observer read {name}")

        return _raise

    for name in forbidden:
        if hasattr(seeded_products, name):
            monkeypatch.setattr(seeded_products, name, blocked(name))
    packet = observe_ai_commercialization(products=seeded_products,
                                          workflow_scope=_scope(), as_of=NOW)
    assert packet["status"] == "ok"
    sources = {row["source_id"] for row in packet["history_rows"]}
    assert sources == {"sacra_public_company_profiles", "tickertrends_public_research"}


def test_sector_config_has_no_other_layer_running_this_observer():
    raw = yaml.safe_load(Path("config/sectors/ai_hardware.yaml").read_text())
    layers_with_observer = [
        layer["key"] for layer in raw["layers"]
        if any(item.get("claim_id") == COMMERCIALIZATION_CLAIM_ID
               for item in (layer.get("evidence_observers") or []))]
    assert layers_with_observer == ["L1_app"]
