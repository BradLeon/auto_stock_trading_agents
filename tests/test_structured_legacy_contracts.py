"""Characterization tests for structured inputs before platform migration.

These assertions intentionally freeze public DTO/missing semantics. Platform-backed
compatibility paths must satisfy them before a consumer can leave legacy mode.
"""

from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from ats.data import consensus, fundamentals
from ats.schemas.fundamentals import FinancialStatements, StatementMetric


ROOT = Path(__file__).resolve().parents[1]


def test_fundamentals_public_contract_preserves_values_and_statement_shape(monkeypatch):
    statement = FinancialStatements(
        period="2026-06-30",
        lines=[StatementMetric(label="Revenue", value=10_000, qoq=5.0, yoy=20.0,
                               unit="$M", delta_unit="%")],
    )
    monkeypatch.setattr(fundamentals, "_yf_info", lambda _: {
        "marketCap": 1_000_000,
        "trailingPE": 25,
        "profitMargins": 0.20,
        "freeCashflow": 50_000,
    })
    monkeypatch.setattr(fundamentals, "_statements", lambda _: statement)
    monkeypatch.setattr(fundamentals, "_sec_filings", lambda _: [
        fundamentals.Filing(form="10-Q", filed=date(2026, 7, 25),
                            url="https://www.sec.gov/example")])

    result = fundamentals.fetch("MSFT")

    assert result.symbol == "MSFT"
    assert result.market_cap == 1_000_000.0
    assert result.trailing_pe == 25.0
    assert result.profit_margin == 0.20
    assert result.free_cashflow == 50_000.0
    assert result.statements == statement
    assert result.recent_filings[0].form == "10-Q"
    assert result.notes == []


def test_fundamentals_public_contract_degrades_with_none_empty_and_notes(monkeypatch):
    monkeypatch.setattr(fundamentals, "safe_fetch", lambda *_, **__: None)

    result = fundamentals.fetch("NO_DATA")

    assert result.market_cap is None
    assert result.statements is None
    assert result.recent_filings == []
    assert result.notes == [
        "yfinance fundamentals unavailable",
        "quarterly statements unavailable",
        "SEC filings unavailable",
    ]


def test_fundamentals_light_contract_is_none_filled_and_never_raises(monkeypatch):
    fundamentals._LIGHT_CACHE.clear()
    monkeypatch.setattr(fundamentals, "safe_fetch", lambda *_, **__: None)

    result = fundamentals.fetch_light("NO_DATA")

    assert result == {
        "market_cap": None,
        "pe": None,
        "fwd_pe": None,
        "gross_margin": None,
        "op_margin": None,
        "rev_growth": None,
        "beta": None,
    }


def test_consensus_public_contract_has_fixed_keys_and_partial_success(monkeypatch):
    monkeypatch.setenv("ATS_STRUCTURED_PEAD_CONSENSUS_MODE", "legacy")
    estimates = {
        "eps": 3.20,
        "revenue": 90_000_000_000,
        "eps_low": 3.0,
        "eps_high": 3.4,
        "revenue_low": None,
        "revenue_high": None,
    }
    analyst = {
        **consensus._ANALYST_DEFAULTS,
        "target_mean": 250.0,
        "rating_buy": 20,
        "rating_trend": [{"period": "0m", "strong_buy": 5, "buy": 20,
                           "hold": 3, "sell": 0, "strong_sell": 0}],
        "upgrades_downgrades": [],
    }

    def fake_safe_fetch(fn, *, source, **_):
        return estimates if source.startswith("consensus:") else analyst

    monkeypatch.setattr(consensus, "safe_fetch", fake_safe_fetch)
    result = consensus.fetch("AMZN")

    assert result["eps"] == 3.20
    assert result["revenue"] == 90_000_000_000
    assert result["target_mean"] == 250.0
    assert result["rating_buy"] == 20
    assert result["rating_trend"][0]["period"] == "0m"
    assert set(result) == {
        "eps", "revenue", "eps_low", "eps_high", "revenue_low", "revenue_high",
        "target_mean", "target_median", "target_low", "target_high", "target_current",
        "rating_strong_buy", "rating_buy", "rating_hold", "rating_sell",
        "rating_strong_sell", "rating_trend", "upgrades_downgrades",
    }


def test_consensus_public_contract_degrades_to_none_and_empty_collections(monkeypatch):
    monkeypatch.setenv("ATS_STRUCTURED_PEAD_CONSENSUS_MODE", "legacy")
    monkeypatch.setattr(consensus, "safe_fetch", lambda *_, **__: None)

    result = consensus.fetch("NO_DATA")

    assert all(result[key] is None for key in result if key not in {
        "rating_trend", "upgrades_downgrades"})
    assert result["rating_trend"] == []
    assert result["upgrades_downgrades"] == []


def test_structured_catalog_freezes_scope_metrics_gates_and_runtime_exclusions():
    catalog = yaml.safe_load((ROOT / "config" / "structured_data.yaml").read_text())

    assert catalog["version"] == 1
    assert catalog["unmapped_policy"] == {
        "action": "retain_pending_mapping",
        "preserve_provider_field": True,
        "preserve_raw_artifact": True,
        "publish_to_default_query": False,
    }
    assert catalog["datasets"]["company_financials"]["acceptance_samples"] == [
        "AMZN", "MSFT", "KLAC", "TSM", "mirror_missing_entity"]
    assert catalog["datasets"]["market_consensus"]["quality"][
        "require_concrete_target_period"] is True
    assert "financial.revenue.gaap" in catalog["metric_definitions"]
    assert catalog["provider_mappings"]["legacy_consensus"]["eps"] == \
        "consensus.eps.mean"

    excluded = {name for name, source in catalog["sources"].items()
                if source["catalog_status"] == "runtime_excluded"}
    assert excluded == {
        "ibkr_market", "yfinance_market", "yfinance_options", "thetadata_options"}
    assert all(catalog["sources"][name]["datasets"] == [] for name in excluded)


def test_migration_matrix_inventory_date_and_runtime_boundary_are_explicit():
    text = (ROOT / "docs" / "STRUCTURED_DATA_MIGRATION_MATRIX.md").read_text()

    assert "2026-08-25" in text
    assert "PEAD prep / score" in text
    assert "Sector cross-section" in text
    assert "stock_statement" in text
    assert "Runtime / excluded" in text
    assert "不得为以上路径增加采集调度" in text


def test_fundamental_as_of_is_a_real_utc_timestamp(monkeypatch):
    before = datetime.now(timezone.utc)
    monkeypatch.setattr(fundamentals, "safe_fetch", lambda *_, **__: None)
    result = fundamentals.fetch("MSFT")
    after = datetime.now(timezone.utc)

    assert result.as_of.tzinfo is not None
    assert before <= result.as_of <= after
