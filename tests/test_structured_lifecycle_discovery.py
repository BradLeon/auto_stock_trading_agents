from __future__ import annotations

from datetime import datetime, timezone
import json

import yaml

from ats.data_platform import DataProducts
from ats.structured import (
    AdapterArtifact,
    AdapterBatch,
    DataDiscovery,
    IngestionStatus,
    NativeRecord,
    ReleaseManager,
    RuntimeSourceSpec,
    SQLiteStructuredRepository,
    StructuredCatalog,
    ingest_source,
    load_release_overlay,
    register_runtime,
    validate_source_registration,
)


class _FakeAdapter:
    def fetch(self, request):
        now = datetime.now(timezone.utc)
        return AdapterBatch(
            source_id=request.source_id,
            dataset_id=request.dataset_id,
            status=IngestionStatus.SUCCEEDED,
            fetched_at=now,
            records=[
                NativeRecord(
                    entity_id="ACME", provider_field="revenue", period="2026-Q1",
                    value=100.0, unit="USD", currency="USD", period_basis="quarter"),
                NativeRecord(
                    entity_id="ACME", provider_field="revenue", period="2025-Q1",
                    value=80.0, unit="USD", currency="USD", period_basis="quarter"),
            ],
            artifacts=[AdapterArtifact(payload={"rows": 2})],
        )


def _catalog(tmp_path) -> StructuredCatalog:
    path = tmp_path / "catalog.yaml"
    raw = {
        "version": 1,
        "feature_flags": {"default_mode": "legacy", "sources": {},
                          "consumers": {"test_consumer": "legacy"}},
        "sources": {
            "fake_source": {
                "catalog_status": "current_partial", "persistence": "persistent",
                "provider": "Fixture", "adapter": "fixture_adapter",
                "datasets": ["fake_dataset"], "cadence": "quarterly",
                "retention": "query_slice",
                "internal_request_budget": {"concurrency": 1},
            }
        },
        "datasets": {
            "fake_dataset": {
                "catalog_status": "current_partial", "entities": ["ACME"],
                "expected_cadence": "quarterly", "primary_sources": ["fake_source"],
                "core_metrics": ["financial.revenue.gaap"],
                "quality": {"coverage_ratio_min": 1.0,
                            "freshness_hours_max": 24},
                "acceptance_samples": ["ACME"],
            }
        },
        "metric_definitions": {
            "financial.revenue.gaap": {
                "value_type": "number", "unit_family": "currency",
                "period_basis": "quarter"}
        },
        "provider_mappings": {
            "fake_source": {"revenue": "financial.revenue.gaap"}
        },
    }
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return StructuredCatalog.load(path)


def _repository(tmp_path, catalog):
    repository = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog(catalog)
    return repository


def test_source_lifecycle_validates_ingests_publishes_and_rolls_back(tmp_path, monkeypatch):
    catalog = _catalog(tmp_path)
    register_runtime(RuntimeSourceSpec("fixture_adapter", _FakeAdapter, requires_entities=True))
    validation = validate_source_registration("fake_source", catalog=catalog)
    assert validation["valid"] is True

    repository = _repository(tmp_path, catalog)
    result = ingest_source(
        repository, "fake_source", entities=["ACME"], catalog=catalog, force=True)
    assert result["status"] == "succeeded"
    assert result["accepted"] == 2

    release_file = tmp_path / "releases.yaml"
    manager = ReleaseManager(repository, catalog=catalog, path=release_file)
    check = manager.check_source("fake_source")
    assert check["ready"] is True
    applied = manager.apply(check, actor="pytest")
    assert applied["applied"] is True
    assert load_release_overlay(release_file)["sources"]["fake_source"] == "platform"

    monkeypatch.setenv("ATS_STRUCTURED_RELEASE_FILE", str(release_file))
    from ats.structured.flags import source_mode

    assert source_mode("fake_source") == "platform"
    manager.rollback(kind="source", target_id="fake_source", actor="pytest")
    assert source_mode("fake_source") == "legacy"
    assert len(load_release_overlay(release_file)["history"]) == 2


def test_release_check_blocks_no_run_and_runtime_source(tmp_path):
    catalog = _catalog(tmp_path)
    register_runtime(RuntimeSourceSpec("fixture_adapter", _FakeAdapter, requires_entities=True))
    repository = _repository(tmp_path, catalog)
    check = ReleaseManager(repository, catalog=catalog,
                           path=tmp_path / "release.yaml").check_source("fake_source")
    assert check["ready"] is False
    assert next(row for row in check["checks"]
                if row["check"] == "latest_ingestion")["detail"] == "no_run"

    runtime = validate_source_registration("ibkr_market")
    assert runtime["valid"] is False
    assert "runtime_source_excluded" in runtime["reason_codes"]
    assert validate_source_registration("trendforce_dram")["valid"] is True

    consumer = ReleaseManager(repository, catalog=catalog,
                              path=tmp_path / "release.yaml").check_consumer(
                                  "test_consumer")
    assert consumer["ready"] is False
    assert any(row["detail"] == "consumer_not_approved_for_platform_in_checked_config"
               for row in consumer["checks"])


def test_trendforce_current_source_uses_unified_batch_and_raw_artifact(tmp_path):
    from ats.data.sources.trendforce import TrendForceDRAMAdapter
    from ats.structured import FetchRequest, IngestionPipeline

    html = """
    DRAM Contract Price (2H Aug) Last Update 2026-08-25
    <table><tr><td>DDR5 8GB SO-DIMM</td><td>10</td><td>8</td>
    <td>9.25</td><td>&#9650; 2.00 %</td><td>0</td></tr></table>
    """

    class _Response:
        text = html
        headers = {"etag": "fixture-v1"}

        @staticmethod
        def raise_for_status():
            return None

    class _Client:
        @staticmethod
        def get(*args, **kwargs):
            return _Response()

    repository = SQLiteStructuredRepository(
        tmp_path / "trendforce.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog()
    result = IngestionPipeline(repository).run(
        TrendForceDRAMAdapter(
            client=_Client(), clock=lambda: datetime(2026, 8, 26, tzinfo=timezone.utc)),
        FetchRequest(source_id="trendforce_dram",
                     dataset_id="industry_dram_contract_price"))
    assert result["status"] == "succeeded"
    row = repository.observations(dataset_id="industry_dram_contract_price")[0]
    assert row["value"] == 9.25
    artifact = repository.lineage(row["observation_id"])["artifact"]
    assert artifact["media_type"] == "text/html"
    assert artifact["source_version"] == "fixture-v1"


def test_dynamic_catalog_changes_with_actual_observations(tmp_path):
    catalog = _catalog(tmp_path)
    register_runtime(RuntimeSourceSpec("fixture_adapter", _FakeAdapter, requires_entities=True))
    repository = _repository(tmp_path, catalog)
    discovery = DataDiscovery(repository, catalog=catalog)
    before = discovery.catalog_view()
    dataset = before["datasets"][0]
    assert dataset["availability"] == "registered_no_data"
    assert discovery.examples(dataset="fake_dataset")["status"] == "no_data"

    ingest_source(repository, "fake_source", entities=["ACME"],
                  catalog=catalog, force=True)
    after = discovery.catalog_view()
    dataset = after["datasets"][0]
    assert dataset["availability"] == "queryable"
    assert dataset["actual_metrics"] == ["financial.revenue.gaap"]
    assert dataset["entities"] == ["ACME"]
    availability = discovery.availability(entity="ACME", dataset="fake_dataset")
    assert availability["datasets"][0]["accepted_observations"] == 2
    examples = discovery.examples(dataset="fake_dataset")
    assert examples["status"] == "ok"
    assert all("fake_dataset" in command or "lineage" in command
               for command in examples["examples"])
    assert discovery.describe("fake_dataset")["examples"] == examples


def test_data_products_and_cli_expose_same_dynamic_catalog(tmp_path, monkeypatch, capsys):
    catalog = _catalog(tmp_path)
    register_runtime(RuntimeSourceSpec("fixture_adapter", _FakeAdapter, requires_entities=True))
    repository = _repository(tmp_path, catalog)
    ingest_source(repository, "fake_source", entities=["ACME"],
                  catalog=catalog, force=True)
    products = DataProducts(store=object(), structured_repository=repository)

    # DataProducts uses the default catalog; bind discovery through the same repository
    # and assert CLI serialization against the product object it receives.
    expected = DataDiscovery(repository, catalog=catalog).catalog_view()
    monkeypatch.setattr(products, "structured_catalog", lambda: expected)
    import ats.data_platform as platform
    from ats.runtime.cli import run_data

    monkeypatch.setattr(platform, "get_data_products", lambda: products)
    assert run_data("catalog") == 0
    assert json.loads(capsys.readouterr().out) == expected

    availability = DataDiscovery(repository, catalog=catalog).availability(
        entity="ACME", dataset="fake_dataset")
    examples = DataDiscovery(repository, catalog=catalog).examples(dataset="fake_dataset")
    monkeypatch.setattr(products, "structured_availability", lambda **_: availability)
    monkeypatch.setattr(products, "structured_examples", lambda **_: examples)
    assert run_data("availability", entity="ACME", dataset="fake_dataset") == 0
    assert json.loads(capsys.readouterr().out) == availability
    assert run_data("examples", dataset="fake_dataset") == 0
    assert json.loads(capsys.readouterr().out) == examples
