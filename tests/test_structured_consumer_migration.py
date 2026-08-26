from datetime import datetime, timedelta, timezone

from ats.data import fundamentals
from ats.data_platform import DataProducts
from ats.schemas.fundamentals import FundamentalData
from ats.structured import (
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


def _save(repo, *, metric, period, value, basis="quarter", known_at=NOW):
    artifact = repo.put_artifact(
        {"metric": metric, "period": period, "value": value},
        ArtifactDescriptor(
            source_id="sec_companyfacts", dataset_id="company_financials",
            fetched_at=known_at, query_scope={"metric": metric, "period": period}))
    return repo.save_observation(ObservationInput(
        series=SeriesIdentity(
            source_id="sec_companyfacts", dataset_id="company_financials",
            entity_id="MSFT", metric_id=metric, unit="USD", currency="USD",
            period_basis=basis, adjustment="gaap"),
        period=period, value=value, published_at=known_at,
        known_at=known_at, fetched_at=known_at, artifact_id=artifact.id)).id


def test_source_and_consumer_rollout_flags_are_independent(monkeypatch) -> None:
    assert source_mode("tw_mof_exports") == "platform"
    assert read_mode("chain_regional", source_id="tw_mof_exports") == "platform"
    assert source_mode("sec_companyfacts") == "shadow"
    assert read_mode("pead_fundamentals", source_id="sec_companyfacts") == "legacy"

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
    assert rendered["Free Cash Flow"].value == 15
    assert all(row["observation_id"] for row in rows)

    manifest = products.snapshot_manifest(
        consumer="pead_fundamentals", purpose="fixture", as_of=NOW, rows=rows)
    replay = products.replay_snapshot(manifest["snapshot_id"])
    assert len(replay["rows"]) == len(rows)


def test_fundamental_read_modes_preserve_public_dto(monkeypatch) -> None:
    legacy = FundamentalData(symbol="MSFT", as_of=NOW, notes=["legacy"])
    platform = FundamentalData(symbol="MSFT", as_of=NOW, notes=["platform"])
    monkeypatch.setattr(fundamentals, "_legacy_fetch", lambda *_, **__: legacy)
    monkeypatch.setattr(fundamentals, "_platform_fetch", lambda *_, **__: platform)

    monkeypatch.setenv("ATS_STRUCTURED_PEAD_FUNDAMENTALS_MODE", "shadow")
    assert fundamentals.fetch("MSFT") is legacy
    monkeypatch.setenv("ATS_STRUCTURED_PEAD_FUNDAMENTALS_MODE", "platform")
    assert fundamentals.fetch("MSFT") is platform
    monkeypatch.setenv("ATS_STRUCTURED_PEAD_FUNDAMENTALS_MODE", "fallback")
    assert fundamentals.fetch("MSFT") is legacy  # platform has no statement rows


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
