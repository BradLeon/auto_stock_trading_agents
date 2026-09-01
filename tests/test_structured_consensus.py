"""Consensus snapshot ingestion, quality, replay and reversible PEAD reads."""

from datetime import datetime, timedelta, timezone
import json

from ats.data import consensus as legacy_consensus
from ats.data.sources.market_consensus import (
    YFinanceConsensusAdapter,
    _json_safe,
    _next_quarter_end,
)
from ats.data.products import DataProducts
from ats.data.structured import (
    FetchRequest,
    IngestionPipeline,
    SQLiteStructuredRepository,
    StructuredCatalog,
)
from ats.data.structured.quality import consensus_quality


T1 = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


class _Store:
    def projection_lineage(self, _identifier):
        return None


def _repo(tmp_path):
    repo = SQLiteStructuredRepository(
        tmp_path / "consensus.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    return repo


def _snapshot(eps=3.2, *, target="2026-09-30", include_target=True):
    return {
        "symbol": "MSFT", "currency": "USD",
        "target_period": target,
        "target_event_date": "2026-10-28",
        "estimates": {
            "eps_0q": {"avg": eps, "low": eps - 0.2, "high": eps + 0.2},
            "revenue_0q": {"avg": 90_000_000_000, "low": 88_000_000_000,
                             "high": 92_000_000_000},
        },
        "price_targets": ({"mean": 250, "median": 245, "low": 200, "high": 300}
                          if include_target else {}),
        "rating_trend": [
            {"period": "0m", "strongBuy": 10, "buy": 20, "hold": 5,
             "sell": 1, "strongSell": 0},
            {"period": "-1m", "strongBuy": 9, "buy": 19, "hold": 6,
             "sell": 1, "strongSell": 0},
        ],
        "rating_changes": [
            {"date": "2026-08-18", "Firm": "Fixture Research",
             "FromGrade": "Hold", "ToGrade": "Buy", "Action": "up"},
            {"date": "2020-01-01", "Firm": "Old Research",
             "FromGrade": "Sell", "ToGrade": "Hold", "Action": "up"},
        ],
        "reported_actuals": [{"quarter": "2026-06-30", "epsActual": 3.0}],
    }


def _request():
    return FetchRequest(
        source_id="yfinance_consensus", dataset_id="market_consensus",
        entities=["MSFT"], query_scope={"currency": "USD"})


def test_consensus_adapter_binds_relative_period_and_persists_all_available_types(tmp_path):
    repo = _repo(tmp_path)
    result = IngestionPipeline(repo).run(
        YFinanceConsensusAdapter(snapshot_loader=lambda _: _snapshot(), clock=lambda: T1),
        _request())
    rows = repo.observations(dataset_id="market_consensus", latest_only=False)

    assert result["status"] == "succeeded" and result["quarantined"] == 0
    assert {row["metric_id"] for row in rows} >= {
        "consensus.eps.reported_actual", "consensus.eps.mean",
        "consensus.revenue.mean", "consensus.price_target.mean",
        "consensus.rating.buy_count", "consensus.rating.change",
    }
    estimates = [row for row in rows if row["period_basis"] == "target_quarter"]
    assert {row["period"] for row in estimates} == {"2026-09-30"}
    assert all(row["published_at"] == "" and
               datetime.fromisoformat(row["known_at"]) == T1 for row in rows)
    rating_periods = {row["period"] for row in rows
                      if row["metric_id"] == "consensus.rating.buy_count"}
    assert rating_periods == {"2026-07-31", "2026-08-31"}
    assert sum(row["metric_id"] == "consensus.rating.change" for row in rows) == 1
    artifact = repo.lineage(rows[0]["observation_id"])["artifact"]
    payload = json.loads(repo.artifacts.read(artifact["relative_path"]))
    assert payload["published_at"] is None
    assert payload["target_period"] == "2026-09-30"


def test_relative_estimate_without_concrete_target_is_rejected_not_backfilled(tmp_path):
    repo = _repo(tmp_path)
    snapshot = _snapshot(target="")
    snapshot["target_event_date"] = ""
    result = IngestionPipeline(repo).run(
        YFinanceConsensusAdapter(snapshot_loader=lambda _: snapshot, clock=lambda: T1),
        _request())

    assert result["status"] == "partial"
    assert not [row for row in repo.observations()
                if row["period_basis"] == "target_quarter"]
    history = repo.ingestion_history()[0]
    assert json.loads(history["reason_codes_json"]) == {"validation_failed": 1}


def test_two_real_snapshots_create_vintages_and_as_of_blocks_future_revision(tmp_path):
    repo = _repo(tmp_path)
    pipeline = IngestionPipeline(repo)
    pipeline.run(YFinanceConsensusAdapter(
        snapshot_loader=lambda _: _snapshot(3.2), clock=lambda: T1), _request())
    pipeline.run(YFinanceConsensusAdapter(
        snapshot_loader=lambda _: _snapshot(3.4), clock=lambda: T2), _request())
    products = DataProducts(store=_Store(), structured_repository=repo)

    early = products.consensus_legacy_dict(
        entity="MSFT", as_of=T1 + timedelta(hours=1))
    latest = products.consensus_legacy_dict(entity="MSFT")
    eps_rows = repo.observations(
        metric_id="consensus.eps.mean", latest_only=False)

    assert len(eps_rows) == 2
    assert early["eps"] == 3.2 and latest["eps"] == 3.4
    assert latest["target_current"] is None  # ticker price remains runtime/excluded
    assert latest["rating_trend"][0]["period"] == "0m"
    assert latest["upgrades_downgrades"][0]["firm"] == "Fixture Research"


def test_consensus_quality_reports_range_stale_target_conflict_and_unreachable():
    base = {
        "entity_id": "MSFT", "known_at": T1.isoformat(),
        "period": "2026-09-30", "period_basis": "target_quarter",
    }
    rows = [
        {**base, "metric_id": "consensus.eps.low", "value": 3.5},
        {**base, "metric_id": "consensus.eps.mean", "value": 3.2},
        {**base, "metric_id": "consensus.eps.high", "value": 3.4},
        {**base, "metric_id": "consensus.revenue.mean", "value": 90},
        {**base, "metric_id": "consensus.revenue.low", "value": 80,
         "period": "2026-12-31"},
    ]
    result = consensus_quality(
        rows, now=T1 + timedelta(hours=200), freshness_hours_max=168,
        latest_ingestion_status="unreachable")
    codes = {issue["code"] for issue in result["issues"]}

    assert result["status"] == "failed"
    assert {"invalid_estimate_range", "target_period_conflict", "stale",
            "source_unavailable"} <= codes


def test_nan_and_empty_snapshot_are_explicit_no_coverage(tmp_path):
    repo = _repo(tmp_path)
    empty = {"currency": "USD", "target_period": "2026-09-30",
             "estimates": {"eps_0q": {"avg": float("nan")}},
             "price_targets": {}, "rating_trend": [], "rating_changes": [],
             "reported_actuals": []}
    result = IngestionPipeline(repo).run(
        YFinanceConsensusAdapter(snapshot_loader=lambda _: empty, clock=lambda: T1),
        _request())

    assert result["status"] == "no_coverage"
    assert repo.observations() == []


def test_quarter_binding_rolls_calendar_and_non_calendar_fiscal_ends():
    assert _next_quarter_end("2026-06-30") == "2026-09-30"
    assert _next_quarter_end("2026-07-31") == "2026-10-31"
    assert _next_quarter_end("2026-11-30") == "2027-02-28"


def test_provider_scalar_objects_are_normalized_before_artifact_persistence():
    class ProviderInteger:
        def item(self):
            return 12

    assert _json_safe({"rating": ProviderInteger()}) == {"rating": 12}
    json.dumps(_json_safe({"rating": ProviderInteger()}))


def test_consensus_read_mode_shadow_and_platform_are_independently_reversible(
        monkeypatch):
    legacy = {"eps": 3.2, **legacy_consensus._ANALYST_DEFAULTS}
    platform = {"eps": 3.4, **legacy_consensus._ANALYST_DEFAULTS}
    monkeypatch.setattr(legacy_consensus, "_legacy_fetch", lambda _: legacy)
    monkeypatch.setattr(legacy_consensus, "_platform_fetch", lambda _: platform)

    monkeypatch.setenv("ATS_STRUCTURED_PEAD_CONSENSUS_MODE", "shadow")
    assert legacy_consensus.fetch("MSFT") is legacy
    monkeypatch.setenv("ATS_STRUCTURED_PEAD_CONSENSUS_MODE", "platform")
    assert legacy_consensus.fetch("MSFT") is platform
    monkeypatch.setenv("ATS_STRUCTURED_PEAD_CONSENSUS_MODE", "legacy")
    assert legacy_consensus.fetch("MSFT") is legacy


def test_consensus_provider_revision_is_accepted_with_auditable_provider_identity(monkeypatch):
    from ats.data import cutover

    recorded = []
    monkeypatch.setattr(cutover, "record_consumer_comparison",
                        lambda **kwargs: recorded.append(kwargs))

    legacy_consensus._record_shadow_comparison(
        consumer="pead_consensus", symbol="MSFT",
        legacy={"eps": 3.2, **legacy_consensus._ANALYST_DEFAULTS},
        platform={"eps": 3.4, **legacy_consensus._ANALYST_DEFAULTS},
        matched=False, reason="consensus_signature_mismatch")

    assert recorded[0]["status"] == "reconciled"
    reconciliation = recorded[0]["details"]["reconciliation"]
    assert reconciliation["kind"] == "authoritative_provider_snapshot"
    assert reconciliation["source_id"] == "yfinance_consensus"
