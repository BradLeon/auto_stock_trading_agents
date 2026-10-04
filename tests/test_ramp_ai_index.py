from datetime import datetime, timezone

import pytest

from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import FetchRequest
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.pipelines.structured.discovery import discover_source, release_check
from ats.data.adapters.structured.registry import validate_source_registration
from ats.data.products.reporting import build_quality_report
from ats.data.release import ReleaseManager, load_release_overlay
from ats.agents.evidence.layer_runner import run_registered_layer_observers
from ats.agents.evidence.ramp_visualization import render_ramp_charts
from ats.config import load_sector_config
from ats.data.sources.ramp_ai_index import (
    RampAIIndexAdapter,
    RampBrowserAdapter,
    RampExportError,
    OUT_OF_SCOPE,
    parse_adoption_tsv,
    parse_model_market_share_tsv,
    parse_spend_tsv,
    validate_ramp_records,
)
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.data.products.base import DataProducts


NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


def test_catalog_registers_ramp_scopes_and_metrics():
    catalog = StructuredCatalog.load()
    source = next(item for item in catalog.sources() if item.id == "ramp_ai_index")
    assert source.datasets == ["ramp_ai_adoption", "ramp_ai_spend"]
    assert {item.id for item in catalog.datasets() if item.id.startswith("ramp_")} == {
        "ramp_ai_adoption", "ramp_ai_spend"
    }
    assert {item.id for item in catalog.metrics() if item.id.startswith("ai.ramp")} >= {
        "ai.ramp.paid_business_adoption_share", "ai.ramp.vendor_adoption_share",
        "ai.ramp.ai_spend_per_employee", "ai.ramp.api_spend_share",
    }


def test_adoption_parser_preserves_scope_and_provider_changes():
    payload = (
        "Date\tSeries\tAdoption rate (%)\tMonthly change (pp)\tYearly change (pp)\n"
        "Aug 2026\tRamp Overall\t56.13\t1.20\t8.50\n"
    ).encode()
    rows = parse_adoption_tsv(payload, scope="adoption_overall", fetched_at=NOW, slice_key="s")
    assert rows[0].entity_id == "RAMP_OVERALL"
    assert rows[0].period == "2026-08-01"
    assert rows[0].value == 56.13
    assert {row.provider_field for row in rows} == {
        "adoption_rate_pct", "monthly_change_pp", "yearly_change_pp"
    }


def test_vendor_shares_may_overlap_and_sector_entities_are_stable():
    payload = (
        "Date\tSeries\tAdoption rate (%)\n"
        "Aug 2026\tAnthropic\t43.8\n"
        "Aug 2026\tOpenAI\t39.8\n"
    ).encode()
    rows = parse_adoption_tsv(payload, scope="adoption_overall_models", fetched_at=NOW, slice_key="s")
    assert {row.entity_id for row in rows} == {"RAMP_VENDOR:ANTHROPIC", "RAMP_VENDOR:OPENAI"}
    assert not validate_ramp_records(rows)
    sectors = parse_adoption_tsv(
        b"Date\tSector\tAdoption rate (%)\nAug 2026\tTechnology and media\t61.2\n",
        scope="adoption_sector", fetched_at=NOW, slice_key="s")
    assert sectors[0].entity_id == "NAICS:TECHNOLOGY_AND_MEDIA"


def test_spend_parser_and_quantile_gate():
    payload = (
        "Date\tMedian\tTop 10%\tTop 1%\nAug 2026\t12.50\t675.60\t7205.13\n"
    ).encode()
    rows = parse_spend_tsv(payload, fetched_at=NOW, slice_key="spend")
    assert {row.entity_id for row in rows} == {"QUANTILE:MEDIAN", "QUANTILE:TOP10", "QUANTILE:TOP1"}
    assert not validate_ramp_records(rows)
    bad = parse_spend_tsv(b"Date\tMedian\tTop 10%\tTop 1%\nAug 2026\t10\t5\t7\n", fetched_at=NOW, slice_key="s")
    assert any(item.startswith("spend_quantile_order_failed") for item in validate_ramp_records(bad))


def test_model_share_parser_keeps_token_spend_cohort():
    payload = b"Date\tProvider\tModel\tSpend type\tAPI spend share (%)\nAug 2026\tOpenAI\tGPT-5.6 Sol\tAPI\t17.9\n"
    row = parse_model_market_share_tsv(payload, fetched_at=NOW, slice_key="models")[0]
    assert row.entity_id == "MODEL:OPENAI:GPT_5_6_SOL:API"
    assert row.dimensions["cohort"] == "Token Spend Management connected businesses"


def test_clipboard_and_browser_fail_closed():
    with pytest.raises(RampExportError, match="export_unreadable"):
        RampBrowserAdapter(lambda scope: b"").export("adoption_overall")
    with pytest.raises(RampExportError, match="permission"):
        RampBrowserAdapter(lambda scope: (_ for _ in ()).throw(PermissionError())).export("adoption_overall")


def test_discovery_identity_is_idempotent_and_methodology_drift_is_explicit(tmp_path, monkeypatch):
    import ats.data.pipelines.structured.discovery as discovery

    payload = {
        "adoption_overall": "Date\tSeries\tAdoption rate (%)\nAug 2026\tRamp Overall\t56.13\n",
        "adoption_overall_models": "Date\tSeries\tAdoption rate (%)\nAug 2026\tAnthropic\t43.8\n",
        "adoption_sector": "Date\tSector\tAdoption rate (%)\nAug 2026\tTechnology\t61.2\n",
    }
    state = {"payload": payload}
    repo = SQLiteStructuredRepository(tmp_path / "discovery.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())

    def fake_build(source_id, *, catalog=None, query_scope=None, **kwargs):
        return (RampAIIndexAdapter(clock=lambda: NOW), FetchRequest(
            source_id=source_id, dataset_id="ramp_ai_adoption",
            query_scope={"payloads": state["payload"]}))

    monkeypatch.setattr(discovery, "build_ingestion", fake_build)
    first = discover_source(repo, "ramp_ai_index", force=True)
    assert first["status"] == "new_release"
    repo.save_source_check(
        source_id="ramp_ai_index", dataset_id="ramp_ai_adoption", status="succeeded",
        latest_upstream_identity=first["latest_upstream_identity"],
        latest_ingested_identity=first["latest_upstream_identity"],
        latest_available_period=first["latest_available_period"], candidates=first["candidates"])
    unchanged = discover_source(repo, "ramp_ai_index", force=True)
    assert unchanged["status"] == "no_change"

    state["payload"] = {**payload, "adoption_overall":
        "Date\tSeries\tAdoption rate (%)\tNew source column\nAug 2026\tRamp Overall\t56.13\tkept\n"}
    drift = discover_source(repo, "ramp_ai_index", force=True)
    assert drift["status"] == "methodology_drift"
    assert drift["diagnostics"]["methodology_drift"]
    repo.close()


def test_release_check_hands_discovered_payloads_to_ingest_once(tmp_path, monkeypatch):
    """A scheduled probe must not invoke the browser twice for one release."""
    import ats.data.pipelines.structured.discovery as discovery

    calls = {"exports": 0, "payloads": {}}
    payload = "Date\tSeries\tAdoption rate (%)\nAug 2026\tRamp Overall\t56.13\n"

    def export(scope):
        calls["exports"] += 1
        return payload

    adapter = RampAIIndexAdapter(browser=RampBrowserAdapter(export), clock=lambda: NOW)
    request = FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_adoption")
    repo = SQLiteStructuredRepository(tmp_path / "release-check.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())

    monkeypatch.setattr(discovery, "build_ingestion", lambda *args, **kwargs: (adapter, request))

    def fake_ingest(repository, source_id, *, query_scope, **kwargs):
        calls["payloads"] = query_scope.get("payloads", {})
        return {"status": "succeeded", "run_id": "run-once"}

    monkeypatch.setattr(discovery, "ingest_source", fake_ingest)
    result = release_check(repo, source_ids=["ramp_ai_index"], dataset_id="ramp_ai_adoption",
                           ingest_new=True, force=True, catalog=StructuredCatalog.load())
    assert result["status"] == "succeeded"
    # The five-scope run is split by dataset; this adoption job exports each of
    # its three registered charts exactly once, then reuses those payloads for
    # ingestion instead of opening the browser again.
    assert calls["exports"] == 3
    assert calls["payloads"]["adoption_overall"] == payload
    repo.close()


def test_partial_discovery_ingests_successful_scopes_only(tmp_path, monkeypatch):
    import ats.data.pipelines.structured.discovery as discovery

    payloads = {
        "adoption_overall": "Date\tSeries\tAdoption rate (%)\nAug 2026\tRamp Overall\t56.13\n",
        "adoption_overall_models": "Date\tSeries\tWRONG\nAug 2026\tOpenAI\t39.8\n",
        "adoption_sector": "Date\tSeries\tAdoption rate (%)\nAug 2026\tTechnology\t61.2\n",
    }
    adapter = RampAIIndexAdapter(browser=RampBrowserAdapter(lambda scope: payloads[scope]), clock=lambda: NOW)
    request = FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_adoption")
    repo = SQLiteStructuredRepository(tmp_path / "partial.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    monkeypatch.setattr(discovery, "build_ingestion", lambda *args, **kwargs: (adapter, request))
    captured = {}

    def fake_ingest(repository, source_id, *, query_scope, **kwargs):
        captured.update(query_scope)
        return {"status": "succeeded", "run_id": "partial-run"}

    monkeypatch.setattr(discovery, "ingest_source", fake_ingest)
    result = release_check(repo, source_ids=["ramp_ai_index"], dataset_id="ramp_ai_adoption",
                           ingest_new=True, force=True, catalog=StructuredCatalog.load())
    assert result["status"] == "partial"
    assert set(captured["payloads"]) == {"adoption_overall", "adoption_sector"}
    repo.close()


def test_environment_api_key_does_not_activate_a_fallback(monkeypatch):
    monkeypatch.setenv("RAMP_DATA_API_KEY", "should-not-be-read")
    adapter = RampAIIndexAdapter(clock=lambda: NOW)
    request = FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_adoption", query_scope={})
    batch = adapter.fetch(request)
    assert batch.status.value == "export_unreadable"
    assert "official_web_export_required" in batch.failures[0].message


def test_headless_probe_reads_governed_export_inbox(tmp_path):
    inbox = tmp_path / "ramp_exports"
    inbox.mkdir()
    (inbox / "adoption_overall.tsv").write_text(
        "Date\tSeries\tAdoption rate (%)\nAug 2026\tRamp Overall\t56.13\n",
        encoding="utf-8",
    )
    adapter = RampAIIndexAdapter(export_dir=inbox, clock=lambda: NOW)
    request = FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_adoption",
                           query_scope={"scope": "adoption_overall"})
    batch = adapter.fetch(request)
    assert batch.status.value == "succeeded"
    assert batch.artifacts[0].metadata["export_method"] == "official_clipboard"


def test_adapter_fetches_independent_scopes_and_persists_lineage(tmp_path):
    payload = b"Date\tSeries\tAdoption rate (%)\tMonthly change (pp)\nAug 2026\tRamp Overall\t56.13\t1.1\n"
    adapter = RampAIIndexAdapter(clock=lambda: NOW)
    request = FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_adoption",
                           query_scope={"payloads": {"adoption_overall": payload.decode(),
                                                    "adoption_overall_models": payload.decode(),
                                                    "adoption_sector": "Date\tSector\tAdoption rate (%)\nAug 2026\tTechnology\t55\n"}})
    batch = adapter.fetch(request)
    assert batch.status.value == "succeeded"
    assert len(batch.artifacts) == 3 and len({item.artifact_key for item in batch.artifacts}) == 3
    repo = SQLiteStructuredRepository(tmp_path / "ramp.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    result = IngestionPipeline(repo).run(adapter, request)
    assert result["accepted"] >= 3
    product = DataProducts(structured_repository=repo)
    snapshot = product.ramp_paid_adoption_snapshot(scope="adoption_overall", period="2026-08")
    assert snapshot["rows"][0]["value"] == 56.13
    assert snapshot["lineage"]["artifact_ids"]
    repo.close()


def test_ramp_products_pandas_sql_and_exports_share_observation_identity(tmp_path):
    payloads = {
        "adoption_overall": "Date\tSeries\tAdoption rate (%)\tMonthly change (pp)\nAug 2026\tRamp Overall\t56.13\t1.2\n",
        "adoption_overall_models": "Date\tSeries\tAdoption rate (%)\nAug 2026\tAnthropic\t43.8\n",
        "adoption_sector": "Date\tSector\tAdoption rate (%)\nAug 2026\tTechnology\t61.2\n",
        "spend_per_employee_overall": "Date\tMedian\tTop 10%\tTop 1%\nAug 2026\t12.50\t675.60\t7205.13\n",
        "model_market_share_overall": "Date\tProvider\tModel\tSpend type\tAPI spend share (%)\nAug 2026\tOpenAI\tGPT-5.6 Sol\tAPI\t17.9\n",
    }
    repo = SQLiteStructuredRepository(tmp_path / "ramp-products.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    pipeline = IngestionPipeline(repo)
    adoption_request = FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_adoption",
                                    query_scope={"payloads": payloads})
    spend_request = FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_spend",
                                 query_scope={"payloads": payloads})
    adoption_result = pipeline.run(RampAIIndexAdapter(clock=lambda: NOW), adoption_request)
    spend_result = pipeline.run(RampAIIndexAdapter(clock=lambda: NOW), spend_request)
    assert adoption_result["accepted"] >= 3 and spend_result["accepted"] >= 4
    products = DataProducts(structured_repository=repo)
    adoption = products.ramp_paid_adoption_snapshot(scope="adoption_overall", period="2026-08")
    frame = products.ramp_paid_adoption_snapshot(scope="adoption_overall", period="2026-08", as_frame=True)
    assert set(frame["observation_id"]) == {row["observation_id"] for row in adoption["rows"]}
    assert 56.13 in set(frame["value"])
    with products.read_only_sql() as conn:
        sql_rows = conn.execute(
            "SELECT o.observation_id,o.value,o.period,s.dimensions_json "
            "FROM structured_observations o JOIN structured_series s ON s.series_id=o.series_id "
            "WHERE s.source_id='ramp_ai_index' AND s.dataset_id='ramp_ai_adoption' "
            "AND o.period='2026-08-01' AND o.quality_status IN ('accepted','warning','conflict')"
        ).fetchall()
    assert {row[0] for row in sql_rows} >= {row["observation_id"] for row in adoption["rows"]}
    spend = products.ramp_spend_per_employee_series(scope="spend_per_employee_overall")
    models = products.ramp_model_market_share_series(scope="model_market_share_overall")
    assert spend["rows"] and models["rows"]
    assert spend["lineage"]["artifact_ids"] and models["lineage"]["artifact_ids"]
    repo.close()


def test_current_public_page_fixture_covers_all_five_scopes_and_excludes_out_of_scope():
    overall = parse_adoption_tsv(
        b"Date\tSeries\tAdoption rate (%)\tMonthly change (pp)\tYearly change (pp)\n"
        b"Aug 2026\tRamp Overall\t56.13\t1.20\t8.50\n",
        scope="adoption_overall", fetched_at=NOW, slice_key="fixture")
    vendors = parse_adoption_tsv(
        b"Date\tSeries\tAdoption rate (%)\nAug 2026\tAnthropic\t43.8\nAug 2026\tOpenAI\t39.8\n",
        scope="adoption_overall_models", fetched_at=NOW, slice_key="fixture")
    sectors = parse_adoption_tsv(
        b"Date\tSector\tAdoption rate (%)\nAug 2026\tTechnology and media\t61.2\n",
        scope="adoption_sector", fetched_at=NOW, slice_key="fixture")
    spend = parse_spend_tsv(
        b"Date\tMedian\tTop 10%\tTop 1%\nAug 2026\t12.50\t675.60\t7205.13\n",
        fetched_at=NOW, slice_key="fixture")
    models = parse_model_market_share_tsv(
        b"Date\tProvider\tModel\tSpend type\tAPI spend share (%)\n"
        b"Aug 2026\tOpenAI\tGPT-5.6 Sol\tAPI\t17.9\n",
        fetched_at=NOW, slice_key="fixture")
    assert overall[0].value == 56.13 and overall[0].period == "2026-08-01"
    assert {row.entity_id for row in vendors} == {"RAMP_VENDOR:ANTHROPIC", "RAMP_VENDOR:OPENAI"}
    assert sectors[0].entity_id.startswith("NAICS:")
    assert {row.entity_id for row in spend} == {"QUANTILE:MEDIAN", "QUANTILE:TOP10", "QUANTILE:TOP1"}
    assert models[0].dimensions["cohort"] == "Token Spend Management connected businesses"
    assert set(OUT_OF_SCOPE) == {"business_size", "geographies"}


def test_ramp_renderer_keeps_five_history_charts_tables_and_sidecars_separate(tmp_path):
    def adoption(scope, segment, value1, value2, metric="ai.ramp.paid_business_adoption_share"):
        return {
            "scope": scope, "rows": [
                {"period": "2026-07-01", "segment": segment, "value": value1,
                 "metric_id": metric, "observation_id": f"{scope}-1", "artifact_id": "a"},
                {"period": "2026-08-01", "segment": segment, "value": value2,
                 "metric_id": metric, "observation_id": f"{scope}-2", "artifact_id": "a"},
            ], "period": "2026-08-01", "status": "ok",
            "manifest": {"snapshot_id": "m"},
        }

    signal = {"status": "ok", "scope_count": 5, "available_scope_count": 5, "slices": {
        "adoption_overall": adoption("adoption_overall", "Ramp Overall", 55, 56),
        "adoption_overall_models": {"rows": [
            {"period": "2026-07-01", "segment": "OpenAI", "value": 38, "metric_id": "ai.ramp.vendor_adoption_share"},
            {"period": "2026-08-01", "segment": "OpenAI", "value": 40, "metric_id": "ai.ramp.vendor_adoption_share"},
        ], "period": "2026-08-01", "status": "ok"},
        "adoption_sector": adoption("adoption_sector", "Information", 79, 81),
        "spend_per_employee_overall": {"rows": [
            {"period": "2026-07-01", "quantile": "median", "value": 12, "metric_id": "ai.ramp.ai_spend_per_employee"},
            {"period": "2026-08-01", "quantile": "median", "value": 12.5, "metric_id": "ai.ramp.ai_spend_per_employee"},
        ], "period": "2026-08-01", "status": "ok"},
        "model_market_share_overall": {"rows": [
            {"period": "2026-07-01", "provider": "OpenAI", "model": "GPT", "value": 18, "metric_id": "ai.ramp.api_spend_share"},
            {"period": "2026-08-01", "provider": "OpenAI", "model": "GPT", "value": 19, "metric_id": "ai.ramp.api_spend_share"},
        ], "period": "2026-08-01", "status": "ok"},
    }}
    result = render_ramp_charts(
        packet={"supplemental_signals": {"ramp_paid_adoption": signal}},
        output_dir=tmp_path,
    )
    assert len(result["descriptors"]) == 5
    assert {item["scope"] for item in result["descriptors"]} == {
        "adoption_overall", "adoption_overall_models", "adoption_sector",
        "spend_per_employee_overall", "model_market_share_overall",
    }
    for item in result["descriptors"]:
        assert item["sidecar_path"].endswith(".sidecar.json")
        assert item["sidecar_path"] != item["png_path"].replace(".png", ".json")
    assert len(result["tables"]) == 5


def test_slice_failure_and_methodology_change_are_isolated():
    adapter = RampAIIndexAdapter(clock=lambda: NOW)
    request = FetchRequest(source_id="ramp_ai_index", dataset_id="ramp_ai_adoption",
                           query_scope={"payloads": {
                               "adoption_overall": "Date\tSeries\tAdoption rate (%)\nAug 2026\tRamp Overall\t56.13\n",
                               "adoption_overall_models": "Date\tSeries\tAdoption rate (%)\nAug 2026\tOpenAI\t39.8\n",
                               "adoption_sector": "Date\tSector\tADOPTION_CHANGED\nAug 2026\tTechnology\t61.2\n",
                           }})
    batch = adapter.fetch(request)
    assert batch.status.value == "partial"
    assert len(batch.records) >= 1 and batch.failures
    assert batch.failures[0].status.value in {"export_unreadable", "methodology_drift"}


def test_isolated_acceptance_chain_release_check_ingest_quality_product_lineage_replay(tmp_path, monkeypatch):
    payloads = {
        "adoption_overall": "Date\tSeries\tAdoption rate (%)\tMonthly change (pp)\tYearly change (pp)\nAug 2026\tRamp Overall\t56.13\t1.2\t8.5\n",
        "adoption_overall_models": "Date\tSeries\tAdoption rate (%)\nAug 2026\tAnthropic\t43.8\n",
        "adoption_sector": "Date\tSector\tAdoption rate (%)\nAug 2026\tTechnology\t61.2\n",
        "spend_per_employee_overall": "Date\tMedian\tTop 10%\tTop 1%\nAug 2026\t12.50\t675.60\t7205.13\n",
        "model_market_share_overall": "Date\tProvider\tModel\tSpend type\tAPI spend share (%)\nAug 2026\tOpenAI\tGPT-5.6 Sol\tAPI\t17.9\n",
    }
    inbox = tmp_path / "ramp_exports"
    inbox.mkdir()
    for scope, text in payloads.items():
        (inbox / f"{scope}.tsv").write_text(text, encoding="utf-8")
    monkeypatch.setenv("ATS_RAMP_OFFICIAL_EXPORT_DIR", str(inbox))
    catalog = StructuredCatalog.load()
    assert validate_source_registration("ramp_ai_index", catalog=catalog)["valid"]
    repo = SQLiteStructuredRepository(tmp_path / "acceptance.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(catalog)
    check = release_check(repo, group="ai_adoption", source_ids=["ramp_ai_index"], force=True, catalog=catalog)
    # The governed export inbox is available in this checkout, so discovery is
    # successful without an API key or a live browser session.
    assert check["status"] == "succeeded"
    pipeline = IngestionPipeline(repo)
    for dataset_id in ("ramp_ai_adoption", "ramp_ai_spend"):
        run = pipeline.run(RampAIIndexAdapter(clock=lambda: NOW), FetchRequest(
            source_id="ramp_ai_index", dataset_id=dataset_id, query_scope={"payloads": payloads}))
        assert run["status"] in {"succeeded", "partial"}
    quality = build_quality_report(repo)
    ramp_reports = {row["dataset_id"]: row for row in quality["datasets"] if row["dataset_id"].startswith("ramp_")}
    assert set(ramp_reports) == {"ramp_ai_adoption", "ramp_ai_spend"}
    assert all(row["dimensions"]["coverage"]["status"] == "passed" for row in ramp_reports.values())
    products = DataProducts(structured_repository=repo)
    availability = products.availability(dataset="ramp_ai_adoption")
    assert availability["datasets"] and availability["datasets"][0]["status"] == "queryable"
    adoption = products.ramp_paid_adoption_snapshot(scope="adoption_overall", period="2026-08")
    assert adoption["status"] == "ok" and adoption["manifest"]["snapshot_id"]
    assert products.lineage(adoption["rows"][0]["observation_id"])
    replay = products.replay_snapshot(adoption["manifest"]["snapshot_id"])
    assert replay and replay["rows"] and replay["artifacts"]
    # The layer runner is exercised with the same isolated DataProducts object;
    # absent Anthropic/BTOS/RPS fixtures remain unavailable without blocking the
    # Ramp supplemental slices or changing the runner scope.
    cfg = load_sector_config("ai_hardware")
    layer = cfg.layer_by_key("L1_app")
    layer_result = run_registered_layer_observers(
        sector="ai_hardware", sector_label=cfg.label, layer="L1_app",
        layer_label=layer.label, observer_refs=layer.evidence_observers,
        products=products)
    assert layer_result["status"] == "ok"
    assert layer_result["packets"][0]["supplemental_signals"]["ramp_paid_adoption"]["status"] in {
        "ok", "partial", "unavailable"
    }
    # Publish only after the isolated source/quality checks pass, then roll back
    # routing without deleting the governed Ramp artifacts or vintages.
    manager = ReleaseManager(repo, catalog=catalog, path=tmp_path / "releases.yaml")
    release = manager.check_source("ramp_ai_index", mode="platform")
    assert release["ready"] is True
    assert manager.apply(release, actor="ramp-acceptance")["applied"] is True
    assert load_release_overlay(tmp_path / "releases.yaml")["sources"]["ramp_ai_index"] == "platform"
    artifact_ids_before = {row["artifact_id"] for row in replay["artifacts"]}
    assert manager.rollback(kind="source", target_id="ramp_ai_index", mode="legacy")["applied"] is True
    assert load_release_overlay(tmp_path / "releases.yaml")["sources"]["ramp_ai_index"] == "legacy"
    replay_after_rollback = products.replay_snapshot(adoption["manifest"]["snapshot_id"])
    assert artifact_ids_before == {row["artifact_id"] for row in replay_after_rollback["artifacts"]}
    repo.close()
