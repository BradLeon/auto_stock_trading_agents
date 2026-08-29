"""Ticker prices and options stay runtime-only and never enter structured storage."""

from datetime import datetime, timezone

import pandas as pd
import pytest

from ats.data import market_data, options
from ats.data_platform import DataProducts
from ats.schemas.market import Ticker
from ats.structured import (
    AdapterBatch,
    ArtifactDescriptor,
    FetchRequest,
    IngestionPipeline,
    ObservationInput,
    Persistence,
    SeriesIdentity,
    SQLiteStructuredRepository,
    StructuredCatalog,
)


NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


class _Store:
    def projection_lineage(self, _identifier):
        return None


def _repo(tmp_path):
    repo = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    return repo


def _counts(repo):
    tables = [row[0] for row in repo.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'structured_%'")]
    return {table: repo.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tables}


def _persistent(repo):
    artifact = repo.put_artifact(
        {"revenue": 100}, ArtifactDescriptor(
            source_id="sec_companyfacts", dataset_id="company_financials",
            fetched_at=NOW, query_scope={"entity": "MSFT"}))
    return repo.save_observation(ObservationInput(
        series=SeriesIdentity(
            source_id="sec_companyfacts", dataset_id="company_financials",
            entity_id="MSFT", metric_id="financial.revenue.gaap",
            unit="USD", currency="USD", period_basis="quarter", adjustment="gaap"),
        period="FY2026Q4", value=100, published_at=NOW, known_at=NOW,
        fetched_at=NOW, artifact_id=artifact.id)).id


def test_runtime_sources_are_explicit_non_datasets():
    catalog = StructuredCatalog.load()
    runtime = catalog.runtime_excluded()

    assert {source.id for source in runtime} == {
        "ibkr_market", "yfinance_market", "yfinance_options", "thetadata_options"}
    assert all(source.persistence == Persistence.RUNTIME for source in runtime)
    assert all(source.datasets == [] for source in runtime)
    assert not any(source.id in {candidate for dataset in catalog.datasets()
                                for candidate in dataset.primary_sources +
                                dataset.fallback_sources} for source in runtime)


def test_runtime_market_and_option_implementations_keep_legacy_imports() -> None:
    import importlib

    from ats.data.runtime import market_data as runtime_market, options as runtime_options

    assert importlib.import_module("ats.data.market_data") is runtime_market
    assert importlib.import_module("ats.data.options") is runtime_options


def test_runtime_queries_preserve_existing_contract_without_database_writes(
        tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    before = _counts(repo)
    artifact_before = repo.artifacts.usage()
    index = pd.to_datetime(["2026-08-24", "2026-08-25"])
    frame = pd.DataFrame({
        "Open": [99, 100], "High": [101, 102], "Low": [98, 99],
        "Close": [100, 101], "Volume": [1000, 1100]}, index=index)
    monkeypatch.setattr(market_data, "_download", lambda *_, **__: frame)
    monkeypatch.setattr(options, "_thetadata", lambda *_, **__: None)
    monkeypatch.setattr(options, "_yfinance", lambda *_, **__: {
        "expected_move_pct": 5.5, "atm_iv": 32.1, "iv_skew": 1.2,
        "expiration": "2026-08-28"})

    market = market_data.fetch_snapshot(Ticker(symbol="MSFT"))
    option = options.fetch("MSFT")

    assert market.last_price == 101 and len(market.history) == 2
    assert option == {"expected_move_pct": 5.5, "atm_iv": 32.1, "iv_skew": 1.2,
                      "expiration": "2026-08-28", "source": "yfinance"}
    assert _counts(repo) == before
    assert repo.artifacts.usage() == artifact_before


def test_repository_and_pipeline_reject_runtime_persistence_before_fetch(tmp_path):
    repo = _repo(tmp_path)

    with pytest.raises(ValueError, match="cannot persist artifacts"):
        repo.put_artifact({}, ArtifactDescriptor(
            source_id="yfinance_market", dataset_id="company_financials",
            fetched_at=NOW))
    with pytest.raises(ValueError, match="cannot persist series"):
        repo.save_observation(ObservationInput(
            series=SeriesIdentity(
                source_id="ibkr_market", dataset_id="company_financials",
                entity_id="MSFT", metric_id="financial.revenue.gaap",
                unit="USD", currency="USD"), period="2026-08-25", value=100,
            known_at=NOW, fetched_at=NOW))

    class MustNotFetch:
        called = False

        def fetch(self, request):
            self.called = True
            return AdapterBatch(
                source_id=request.source_id, dataset_id=request.dataset_id,
                status="zero_match", fetched_at=NOW)

    adapter = MustNotFetch()
    result = IngestionPipeline(repo).run(adapter, FetchRequest(
        source_id="yfinance_options", dataset_id="company_financials"))

    assert result["status"] == "runtime_excluded" and adapter.called is False
    assert repo.ingestion_history(source_id="yfinance_options") == []


def test_composite_workflow_snapshot_records_only_persistent_input(tmp_path):
    repo = _repo(tmp_path)
    observation_id = _persistent(repo)
    products = DataProducts(store=_Store(), structured_repository=repo)
    query = products.metric_series(
        metric="financial.revenue.gaap", entity="MSFT", dataset="company_financials")
    combined = products.compose_inputs(
        persistent=query["rows"],
        runtime=[{"kind": "ticker_price", "symbol": "MSFT", "value": 420.5},
                 {"kind": "option_chain", "symbol": "MSFT", "atm_iv": 32.1}])
    manifest = products.snapshot_manifest(
        consumer="pead", purpose="runtime-boundary", as_of=NOW,
        rows=combined["persistent"] + combined["runtime"])
    replay = products.replay_snapshot(manifest["snapshot_id"])

    assert combined["runtime_replayable"] is False
    assert {row["input_mode"] for row in combined["persistent"]} == {"persistent"}
    assert {row["input_mode"] for row in combined["runtime"]} == {"runtime"}
    assert [item["observation_id"] for item in replay["items"]] == [observation_id]
    assert [row["value"] for row in replay["rows"]] == [100]
