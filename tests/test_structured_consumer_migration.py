from datetime import datetime, timedelta, timezone

from ats.data import fundamentals
from ats.data.financial_release import company_financial_release_check
from ats.data.products import DataProducts
from ats.schemas.fundamentals import FinancialStatements, FundamentalData, StatementMetric
from ats.data.structured import (
    ArtifactDescriptor,
    ObservationInput,
    SeriesIdentity,
    SQLiteStructuredRepository,
    read_mode,
    source_mode,
)


NOW = datetime(2026, 8, 25, 8, tzinfo=timezone.utc)


class _Store:
    def projection_lineage(self, _identifier):
        return None


def _repo(tmp_path):
    repo = SQLiteStructuredRepository(
        tmp_path / "consumer.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog()
    return repo


def _save(repo, *, metric, period, value, basis="quarter", known_at=NOW,
          entity="MSFT", unit="USD", currency="USD", source_id="sec_companyfacts",
          adjustment="gaap"):
    artifact = repo.put_artifact(
        {"metric": metric, "period": period, "value": value},
        ArtifactDescriptor(
            source_id=source_id, dataset_id="company_financials",
            fetched_at=known_at, query_scope={"metric": metric, "period": period}))
    return repo.save_observation(ObservationInput(
        series=SeriesIdentity(
            source_id=source_id, dataset_id="company_financials",
            entity_id=entity, metric_id=metric, unit=unit, currency=currency,
            period_basis=basis, adjustment=adjustment),
        period=period, value=value, published_at=known_at,
        known_at=known_at, fetched_at=known_at, artifact_id=artifact.id)).id


def test_source_and_consumer_rollout_flags_are_independent(monkeypatch, tmp_path) -> None:
    # Runtime release overlays are machine state and may be intentionally ahead
    # of the checked-in baseline.  This test exercises baseline/env precedence.
    monkeypatch.setenv("ATS_STRUCTURED_RELEASE_FILE", str(tmp_path / "releases.yaml"))
    assert source_mode("tw_mof_exports") == "platform"
    # 10.5 reverts the historic platform baseline until this consumer has
    # consumer-specific shadow evidence rather than source-level evidence only.
    assert read_mode("chain_regional", source_id="tw_mof_exports") == "shadow"
    assert source_mode("sec_companyfacts") == "shadow"
    assert source_mode("company_disclosures") == "shadow"
    assert read_mode("pead_fundamentals", source_id="sec_companyfacts") == "shadow"

    monkeypatch.setenv("ATS_STRUCTURED_SOURCE_SEC_COMPANYFACTS_MODE", "platform")
    monkeypatch.setenv("ATS_STRUCTURED_PEAD_FUNDAMENTALS_MODE", "fallback")
    assert source_mode("sec_companyfacts") == "platform"
    assert read_mode("pead_fundamentals", source_id="sec_companyfacts") == "fallback"


def test_fundamental_platform_assembles_legacy_statement_shape_and_snapshot(tmp_path) -> None:
    repo = _repo(tmp_path)
    periods = ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
    for index, period in enumerate(periods, start=1):
        revenue = (100 + index * 10) * 1_000_000
        for metric, value in {
            "financial.revenue.gaap": revenue,
            "financial.gross_profit.gaap": revenue * 0.5,
            "financial.operating_income.gaap": revenue * 0.2,
            "financial.net_income.gaap": revenue * 0.1,
            "financial.eps.diluted.gaap": 1 + index * 0.1,
            "financial.cash_from_operations.gaap": revenue * 0.15,
            "financial.capex.gaap": revenue * 0.05,
        }.items():
            _save(repo, metric=metric, period=period, value=value)
        _save(repo, metric="financial.total_debt.gaap", period=period,
              value=50_000_000 + index * 1_000_000, basis="instant")

    products = DataProducts(store=_Store(), structured_repository=repo)
    statement, rows = fundamentals._structured_statements("MSFT", products)
    assert statement is not None and statement.period == "2026-06-30"
    rendered = {line.label: line for line in statement.lines}
    assert rendered["Revenue"].value == 150
    assert rendered["Gross Margin"].value == 50.0
    assert rendered["Operating Margin"].value == 20.0
    assert rendered["CapEx"].value < 0
    assert rendered["Free Cash Flow"].value == 15
    assert all(row["observation_id"] for row in rows)

    manifest = products.snapshot_manifest(
        consumer="pead_fundamentals", purpose="fixture", as_of=NOW, rows=rows)
    replay = products.replay_snapshot(manifest["snapshot_id"])
    assert len(replay["rows"]) == len(rows)


def test_fundamental_platform_prefers_disclosed_adr_eps_for_tsm(tmp_path) -> None:
    repo = _repo(tmp_path)
    periods = ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
    for index, period in enumerate(periods, start=1):
        revenue = (100 + index) * 1_000_000
        for metric, value in {
            "financial.revenue.gaap": revenue,
            "financial.gross_profit.gaap": revenue * 0.5,
            "financial.operating_income.gaap": revenue * 0.2,
            "financial.net_income.gaap": revenue * 0.1,
            "financial.eps.diluted.gaap": 20 + index,
            "financial.cash_from_operations.gaap": revenue * 0.15,
            "financial.capex.gaap": revenue * 0.05,
        }.items():
            _save(repo, metric=metric, period=period, value=value, entity="TSM",
                  unit="TWD" if metric != "financial.eps.diluted.gaap" else "TWD/share",
                  currency="TWD")
        _save(repo, metric="financial.total_debt.gaap", period=period,
              value=50_000_000 + index * 1_000_000, basis="instant", entity="TSM",
              unit="TWD", currency="TWD")
    for index, period in enumerate(periods, start=1):
        _save(repo, metric="financial.eps.diluted.adr", period=period,
              value=3 + index / 10, entity="TSM", unit="USD/ADR", currency="USD")

    products = DataProducts(store=_Store(), structured_repository=repo)
    statement, _ = fundamentals._structured_statements("TSM", products)

    rendered = {line.label: line for line in statement.lines}
    assert rendered["Diluted EPS"].value == 3.5
    assert rendered["Diluted EPS"].unit == "USD/ADR"


def test_complete_report_package_requires_one_source_and_all_three_statements(tmp_path) -> None:
    repo = _repo(tmp_path)
    period = "2026-06-30"
    source = "defeatbeta_stock_statement"
    for metric, value in {
        "financial.revenue.gaap": 100_000_000,
        "financial.gross_profit.gaap": 50_000_000,
        "financial.operating_income.gaap": 20_000_000,
        "financial.net_income.gaap": 10_000_000,
        "financial.eps.diluted.market_adjusted": 1.2,
        "financial.cash_from_operations.gaap": 15_000_000,
        "financial.capex.gaap": 5_000_000,
    }.items():
        _save(repo, metric=metric, period=period, value=value, source_id=source,
              adjustment="split_adjusted" if metric.endswith("market_adjusted") else "gaap")
    for metric, value in {
        "financial.cash_and_equivalents.gaap": 20_000_000,
        "financial.total_debt.provider_reported": 30_000_000,
        "financial.total_assets.gaap": 200_000_000,
        "financial.total_liabilities.gaap": 80_000_000,
        "financial.stockholders_equity.gaap": 120_000_000,
    }.items():
        _save(repo, metric=metric, period=period, value=value, basis="instant",
              source_id=source,
              adjustment="provider_reported" if metric.endswith("provider_reported") else "gaap")

    package = fundamentals._complete_report_package(
        repo, source_id=source, symbol="MSFT")
    assert package is not None
    assert package["source_id"] == source and package["period"] == period

    # A different source cannot patch a missing report-package field.
    repo2 = _repo(tmp_path / "incomplete")
    for metric, value in {
        "financial.revenue.gaap": 100_000_000,
        "financial.gross_profit.gaap": 50_000_000,
    }.items():
        _save(repo2, metric=metric, period=period, value=value, source_id=source)
    _save(repo2, metric="financial.operating_income.gaap", period=period,
          value=20_000_000, source_id="sec_companyfacts")
    assert fundamentals._complete_report_package(repo2, source_id=source, symbol="MSFT") is None


def test_company_financial_release_is_data_only_and_checks_complete_package(tmp_path) -> None:
    repo = _repo(tmp_path)
    period = "2026-06-30"
    source = "defeatbeta_stock_statement"
    quarterly = {
        "financial.revenue.gaap": 100_000_000,
        "financial.gross_profit.gaap": 50_000_000,
        "financial.operating_income.gaap": 20_000_000,
        "financial.net_income.gaap": 10_000_000,
        "financial.eps.diluted.market_adjusted": 1.2,
        "financial.cash_from_operations.gaap": 15_000_000,
        "financial.capex.gaap": 5_000_000,
    }
    instant = {
        "financial.cash_and_equivalents.gaap": 20_000_000,
        "financial.total_debt.provider_reported": 30_000_000,
        "financial.total_assets.gaap": 200_000_000,
        "financial.total_liabilities.gaap": 80_000_000,
        "financial.stockholders_equity.gaap": 120_000_000,
    }
    for metric, value in quarterly.items():
        _save(repo, metric=metric, period=period, value=value, source_id=source,
              adjustment="split_adjusted" if metric.endswith("market_adjusted") else "gaap")
    for metric, value in instant.items():
        _save(repo, metric=metric, period=period, value=value, basis="instant", source_id=source,
              adjustment="provider_reported" if metric.endswith("provider_reported") else "gaap")

    result = company_financial_release_check(repo, entities=["MSFT"], now=NOW)

    assert result["ready"] is True
    assert result["scope"] == "data_only_no_consumer_or_workflow_gate"
    assert all(check["passed"] for check in result["entities"][0]["checks"])


def test_official_disclosure_bundle_only_combines_sec_and_issuer_release(tmp_path) -> None:
    repo = _repo(tmp_path)
    period = "2026-06-30"
    for metric, value in {
        "financial.revenue.gaap": 100_000_000,
        "financial.gross_profit.gaap": 50_000_000,
        "financial.operating_income.gaap": 20_000_000,
        "financial.net_income.gaap": 10_000_000,
        "financial.eps.diluted.gaap": 1.2,
        "financial.cash_from_operations.gaap": 15_000_000,
    }.items():
        _save(repo, metric=metric, period=period, value=value, source_id="sec_companyfacts")
    for metric, value in {
        "financial.cash_and_equivalents.gaap": 20_000_000,
        "financial.total_debt.gaap": 30_000_000,
        "financial.total_assets.gaap": 200_000_000,
        "financial.total_liabilities.gaap": 80_000_000,
        "financial.stockholders_equity.gaap": 120_000_000,
    }.items():
        _save(repo, metric=metric, period=period, value=value, basis="instant",
              source_id="sec_companyfacts")
    _save(repo, metric="financial.capex.gaap", period=period, value=5_000_000,
          source_id="company_disclosures")

    assert fundamentals._complete_report_package(
        repo, source_id="sec_companyfacts", symbol="MSFT") is None
    package = fundamentals._complete_report_package(
        repo, source_id=("sec_companyfacts", "company_disclosures"), symbol="MSFT")

    assert package is not None
    assert package["source_id"] == "official_disclosure_bundle"
    assert package["source_by_metric"]["financial.capex.gaap"] == "company_disclosures"


def test_financial_source_chain_stops_after_first_complete_package(monkeypatch) -> None:
    calls = []

    class _Pipeline:
        def __init__(self, _repository):
            pass

        def run(self, _adapter, request):
            calls.append(request.source_id)
            return {"status": "succeeded"}

    class _Adapter:
        pass

    monkeypatch.setattr("ats.data.structured.IngestionPipeline", _Pipeline)
    monkeypatch.setattr(fundamentals, "_complete_report_package", lambda _repo, *, source_id, symbol:
                        {"source_id": source_id, "period": "2026-06-30"}
                        if source_id == "defeatbeta_stock_statement" else None)

    package = fundamentals._refresh_report_package(object(), symbol="MSFT")

    assert package is not None and package["source_id"] == "defeatbeta_stock_statement"
    assert calls == ["defeatbeta_stock_statement"]


def test_fundamental_read_modes_preserve_public_dto(monkeypatch) -> None:
    legacy = FundamentalData(symbol="MSFT", as_of=NOW, notes=["legacy"])
    platform = FundamentalData(symbol="MSFT", as_of=NOW, notes=["platform"])
    monkeypatch.setattr(fundamentals, "_legacy_fetch", lambda *_, **__: legacy)
    monkeypatch.setattr(fundamentals, "_platform_fetch", lambda *_, **__: platform)
    comparisons = []
    monkeypatch.setattr(
        fundamentals, "_record_shadow_comparison",
        lambda **kwargs: comparisons.append(kwargs))

    monkeypatch.setenv("ATS_STRUCTURED_PEAD_FUNDAMENTALS_MODE", "shadow")
    assert fundamentals.fetch("MSFT") is legacy
    assert comparisons == [{
        "consumer": "pead_fundamentals", "symbol": "MSFT", "matched": True,
        "legacy_signature": (), "platform_signature": (),
        "reconciliation": {
            "matched": True, "kind": "exact", "reason": "identical_statement_dto"}}]
    monkeypatch.setenv("ATS_STRUCTURED_PEAD_FUNDAMENTALS_MODE", "platform")
    assert fundamentals.fetch("MSFT") is platform
    monkeypatch.setenv("ATS_STRUCTURED_PEAD_FUNDAMENTALS_MODE", "fallback")
    assert fundamentals.fetch("MSFT") is legacy  # platform has no statement rows


def test_sector_consensus_shadow_returns_legacy_on_platform_failure(monkeypatch) -> None:
    from ats.data import consensus

    legacy = {"eps": 1.0, "revenue": 100.0}
    recorded = []
    monkeypatch.setattr(consensus, "_legacy_fetch", lambda *_: legacy)
    monkeypatch.setattr(consensus, "_platform_fetch",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(consensus, "_record_shadow_comparison",
                        lambda **kwargs: recorded.append(kwargs))
    monkeypatch.setenv("ATS_STRUCTURED_SECTOR_CONSENSUS_MODE", "shadow")

    assert consensus.fetch("MSFT", consumer="sector_consensus") is legacy
    assert recorded[0]["reason"] == "platform_failure:RuntimeError"


def test_pead_shadow_reconciles_complete_newer_governed_statement() -> None:
    labels = ("Revenue", "Gross Margin", "Operating Margin", "Net Income", "Diluted EPS",
              "CapEx", "Free Cash Flow", "Total Debt")
    legacy = FinancialStatements(
        period="2026-03-31", lines=[StatementMetric(label=label, value=1.0) for label in labels])
    platform = FinancialStatements(
        period="2026-06-30", lines=[StatementMetric(label=label, value=2.0) for label in labels])

    result = fundamentals._reconcile_statements(legacy, platform)

    assert result["matched"] is True
    assert result["kind"] == "governed_period_upgrade"
    assert result["value_difference_review_required"] is True


def test_pead_shadow_blocks_incomplete_or_non_newer_statement() -> None:
    labels = ("Revenue", "Gross Margin", "Operating Margin", "Net Income", "Diluted EPS",
              "CapEx", "Free Cash Flow", "Total Debt")
    legacy = FinancialStatements(
        period="2026-06-30", lines=[StatementMetric(label=label, value=1.0) for label in labels])
    platform = FinancialStatements(
        period="2026-06-30", lines=[StatementMetric(label="Revenue", value=2.0)])

    result = fundamentals._reconcile_statements(legacy, platform)

    assert result["matched"] is False
    assert result["reason"] == "core_statement_incomplete"


def test_pead_shadow_accepts_same_period_unit_and_official_debt_correction() -> None:
    labels = ("Revenue", "Gross Margin", "Operating Margin", "Net Income", "Diluted EPS",
              "CapEx", "Free Cash Flow", "Total Debt")
    legacy = FinancialStatements(
        period="2026-06-30", lines=[StatementMetric(label=label, value=1.0, unit="$M")
                                      for label in labels])
    platform = FinancialStatements(
        period="2026-06-30", lines=[
            StatementMetric(label=label, value=2.0 if label == "Total Debt" else 1.0,
                            unit="USD/share" if label == "Diluted EPS" else "$M")
            for label in labels])

    result = fundamentals._reconcile_statements(legacy, platform)

    assert result["matched"] is True
    assert result["kind"] == "governed_semantic_upgrade"
    assert result["changed_units"] == ["Diluted EPS"]
    assert result["debt_definition_changed"] is True


def test_pead_shadow_and_fallback_keep_legacy_output_on_platform_failure(monkeypatch) -> None:
    legacy = FundamentalData(symbol="MSFT", as_of=NOW, notes=["legacy"])
    monkeypatch.setattr(fundamentals, "_legacy_fetch", lambda *_, **__: legacy)
    monkeypatch.setattr(
        fundamentals, "_platform_fetch",
        lambda *_, **__: (_ for _ in ()).throw(RuntimeError("platform unavailable")))
    comparisons = []
    monkeypatch.setattr(
        fundamentals, "_record_shadow_comparison", lambda **kwargs: comparisons.append(kwargs))

    monkeypatch.setenv("ATS_STRUCTURED_PEAD_FUNDAMENTALS_MODE", "shadow")
    assert fundamentals.fetch("MSFT") is legacy
    assert comparisons[0]["reconciliation"]["kind"] == "platform_failure"

    monkeypatch.setenv("ATS_STRUCTURED_PEAD_FUNDAMENTALS_MODE", "fallback")
    assert fundamentals.fetch("MSFT") is legacy


def test_metric_query_keeps_distinct_period_bases_and_records_chart_snapshot(tmp_path) -> None:
    repo = _repo(tmp_path)
    quarter = _save(repo, metric="financial.revenue.gaap", period="2026-06-30",
                    value=100_000_000, basis="quarter")
    annual = _save(repo, metric="financial.revenue.gaap", period="2026-06-30",
                   value=400_000_000, basis="annual")
    products = DataProducts(store=_Store(), structured_repository=repo)

    result = products.metric_series(
        metric="financial.revenue.gaap", entity="MSFT", dataset="company_financials",
        snapshot_consumer="chart", snapshot_purpose="period-basis-regression")
    assert {row["period_basis"] for row in result["rows"]} == {"quarter", "annual"}
    replay = products.replay_snapshot(result["snapshot"]["snapshot_id"])
    assert {row["observation_id"] for row in replay["rows"]} == {quarter, annual}

    # Later revisions cannot alter the materialized manifest.
    _save(repo, metric="financial.revenue.gaap", period="2026-06-30",
          value=101_000_000, basis="quarter", known_at=NOW + timedelta(days=1))
    replay_again = products.replay_snapshot(result["snapshot"]["snapshot_id"])
    assert {row["observation_id"] for row in replay_again["rows"]} == {quarter, annual}
