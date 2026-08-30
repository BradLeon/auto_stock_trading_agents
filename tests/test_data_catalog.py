"""Tests for the unified catalog and legacy configuration overlay."""

from pathlib import Path

import pytest

from ats.data.catalog import DataCatalog, load_data_catalog


ROOT = Path(__file__).resolve().parents[1]


def test_unified_catalog_loads_legacy_structured_and_unstructured_registries():
    catalog = load_data_catalog()

    assert catalog.path == (ROOT / "config" / "data" / "catalog.yaml").resolve()
    assert {item.id for item in catalog.datasets()} >= {
        "company_financials", "market_consensus", "regional_tw_exports",
    }
    assert {item.id for item in catalog.unstructured_sources()} >= {
        "trendforce_news", "ibkr_news", "semianalysis", "yfinance_live_news",
    }
    assert catalog.validate().valid is True


def test_unified_catalog_preserves_runtime_excluded_boundary():
    catalog = load_data_catalog()
    runtime = {item.id for item in catalog.sources() if item.domain == "runtime"}

    assert runtime == {"ibkr_market", "yfinance_market", "yfinance_options", "thetadata_options"}
    assert all(not item.datasets for item in catalog.sources() if item.domain == "runtime")


def test_catalog_statuses_distinguish_config_from_actual_coverage():
    statuses = load_data_catalog().statuses()

    assert statuses["sources"]["sec_companyfacts"]["status"] == "current_partial"
    assert "coverage_note" in statuses
    assert "accepted observations" in statuses["coverage_note"]


def test_catalog_rejects_missing_legacy_file(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text(
        "version: 1\nlegacy:\n  structured_catalog: missing.yaml\n"
        "  sources: missing-sources.yaml\n  news_sources: missing-news.yaml\n",
        encoding="utf-8",
    )
    catalog = DataCatalog.load(path)

    result = catalog.validate()
    assert result.valid is False
    assert "legacy_config_missing:structured_catalog" in result.reason_codes


def test_catalog_rejects_unsupported_version(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text("version: 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported data catalog version"):
        DataCatalog.load(path)


def test_catalog_rejects_duplicate_yaml_keys(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text("version: 1\nversion: 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate catalog key"):
        DataCatalog.load(path)


def test_catalog_rejects_unregistered_explicit_adapter(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text(
        "version: 1\n"
        "legacy:\n"
        f"  structured_catalog: {ROOT / 'config' / 'structured_data.yaml'}\n"
        f"  sources: {ROOT / 'config' / 'sources.yaml'}\n"
        f"  news_sources: {ROOT / 'config' / 'news_sources.yaml'}\n"
        "sources:\n"
        "  new_source:\n"
        "    domain: structured\n"
        "    status: published\n"
        "    adapter: missing_adapter\n"
        "    datasets: [new_dataset]\n"
        "datasets:\n"
        "  new_dataset:\n"
        "    domain: structured\n"
        "    sources: [new_source]\n",
        encoding="utf-8",
    )
    result = DataCatalog.load(path).validate()

    assert result.valid is False
    assert "adapter_runtime_unregistered" in result.reason_codes


def test_cli_config_validation_is_read_only(capsys):
    from ats.runtime.cli import main

    assert main(["data", "config"]) == 0
    output = capsys.readouterr().out
    assert "config/data/catalog.yaml" in output
    assert '"valid": true' in output
