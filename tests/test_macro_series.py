"""Macro indicators as governed observations: adapter -> ingestion -> read product -> same snapshot."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from ats.data.products import macro_series as product
from ats.data.sources import macro_series as adapter_mod
from ats.data.structured import FetchRequest, IngestionPipeline, SQLiteStructuredRepository, StructuredCatalog

NOW = datetime(2026, 10, 16, 10, 30, tzinfo=timezone.utc)


def _daily(start, values):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="D"))


def _monthly(start, values):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"))


class _Fred:
    """code -> Series; an unknown code returns empty like a dead feed."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def get_series(self, code, observation_start=None):
        self.calls.append((code, observation_start))
        series = self.table.get(code, pd.Series(dtype=float))
        if observation_start:
            series = series[series.index >= pd.Timestamp(observation_start)]
        return series


def _yf(codes, since):
    index = pd.date_range("2026-10-01", periods=5, freq="D")
    return pd.concat({"Close": pd.DataFrame({c: [100.0 + i for i in range(5)] for c in codes}, index=index)}, axis=1)


def _fear():
    return pd.Series([29.0, 30.0], index=pd.to_datetime(["2026-10-14", "2026-10-15"]))


def _adapter(table=None):
    return adapter_mod.MacroSeriesAdapter(fred=_Fred(table or {"DGS10": _daily("2026-10-01", [4.1, 4.2, 4.3]),
                                                                "PAYEMS": _monthly("2026-06-01", [150000, 150100, 150150]),
                                                                "ICSA": _monthly("2026-08-01", [200000, 199000])}),
                                          yf_download=_yf, fear_greed=_fear, clock=lambda: NOW)


def _request(**scope):
    return FetchRequest(source_id=adapter_mod.SOURCE_ID, dataset_id=adapter_mod.DATASET_ID, query_scope=scope)


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #
def test_records_carry_units_currency_and_period_basis():
    batch = _adapter().fetch(_request())
    by_field = {}
    for r in batch.records:
        by_field.setdefault(r.provider_field, []).append(r)
    ust = by_field["macro.ust_10y.value"][0]
    assert (ust.unit, ust.currency, ust.period_basis, ust.entity_id) == ("percent", "", "day", "US_MACRO")
    assert by_field["macro.payrolls.value"][0].unit == "thousand persons"
    assert by_field["macro.initial_claims.value"][0].unit == "persons"
    assert by_field["macro.oil_wti.value"][0].currency == "USD"     # price series are currency
    assert by_field["macro.spx.value"][0].unit == "index" and by_field["macro.spx.value"][0].currency == ""
    assert by_field["macro.fear_greed.value"][-1].value == 30.0


def test_a_dead_feed_is_a_named_gap_not_an_exception_or_a_silent_hole():
    batch = _adapter().fetch(_request())
    gaps = batch.provider_metadata["coverage"]["gaps"]
    assert gaps["ust_2y"] == "provider_empty" and "ust_10y" not in gaps


def test_a_missing_fred_key_marks_every_fred_series_and_still_returns_market_data(monkeypatch):
    from ats.data import macro

    monkeypatch.setattr(macro, "_fred_client", lambda: None)
    batch = adapter_mod.MacroSeriesAdapter(yf_download=_yf, fear_greed=_fear, clock=lambda: NOW).fetch(_request())
    gaps = batch.provider_metadata["coverage"]["gaps"]
    assert gaps["ust_10y"] == "fred_key_missing"
    assert {r.provider_field for r in batch.records} >= {"macro.spx.value", "macro.fear_greed.value"}


def test_since_and_lookback_limit_what_is_requested_and_returned():
    a = _adapter()
    batch = a.fetch(_request(since="2026-10-02"))
    assert min(r.period for r in batch.records if r.provider_field == "macro.ust_10y.value") == "2026-10-02"
    rolling = a.fetch(_request(lookback_days=10))               # NOW - 10d = 2026-10-06
    assert all(r.period >= "2026-10-06" for r in rolling.records)


def test_registry_lists_every_series_the_review_reads_plus_the_spot_extras():
    from ats.data import macro

    keys = set(adapter_mod.all_series())
    assert set(macro.series_spec()) <= keys
    assert {"fed_funds", "ndx", "gold", "fear_greed"} <= keys


def test_every_series_has_a_registered_metric():
    metrics = StructuredCatalog.load().raw["metric_definitions"]
    assert {adapter_mod.metric_id(k) for k in adapter_mod.all_series()} <= set(metrics)


# --------------------------------------------------------------------------- #
# Ingestion -> read product
# --------------------------------------------------------------------------- #
@pytest.fixture
def repo(tmp_path):
    repository = SQLiteStructuredRepository(tmp_path / "s.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog(StructuredCatalog.load())
    yield repository
    repository.close()


def test_ingested_series_are_accepted_and_idempotent(repo):
    a = _adapter()
    first = IngestionPipeline(repo).run(a, _request())
    again = IngestionPipeline(repo).run(a, _request())
    assert first["status"] == "succeeded" and first["quarantined"] == 0
    assert again["status"] == "no_change" and again["accepted"] == 0


def test_a_revised_value_becomes_a_new_vintage_and_the_reader_sees_the_latest(repo):
    IngestionPipeline(repo).run(_adapter(), _request())
    revised = _adapter({"DGS10": _daily("2026-10-01", [4.1, 4.2, 4.35])})
    IngestionPipeline(repo).run(revised, _request())
    data = product.load(repository=repo, now=NOW)
    assert float(data.series["ust_10y"].iloc[-1]) == 4.35
    versions = [r for r in repo.observations(metric_id="macro.ust_10y.value", latest_only=False)
                if r["period"] == "2026-10-03"]
    assert len(versions) == 2                                   # the earlier value is kept as history


def test_the_reader_reports_gaps_and_stale_series(repo):
    IngestionPipeline(repo).run(_adapter(), _request())
    data = product.load(repository=repo, now=datetime(2026, 11, 20, tzinfo=timezone.utc))
    assert "ust_2y" in data.gaps
    assert any(key == "ust_10y" for key, _last, _age in data.stale)      # last print 2026-10-03, > 10 days


def test_a_known_at_cut_hides_later_vintages(repo):
    IngestionPipeline(repo).run(_adapter(), _request())
    before = product.load(repository=repo, as_of=datetime(2026, 10, 1, tzinfo=timezone.utc), now=NOW)
    assert before.series == {}


# --------------------------------------------------------------------------- #
# Snapshot: reproduces what fetch() derived
# --------------------------------------------------------------------------- #
def _set(**series):
    return product.MacroSeriesSet(series=series, as_of=NOW)


def test_snapshot_derives_yoy_nfp_claims_and_pct_changes_like_the_old_fetch():
    cpi = _monthly("2025-09-01", [100.0 + i for i in range(14)])          # 14 monthly prints
    snap = product.snapshot(_set(
        headline_cpi=cpi, core_pce=cpi, payrolls=_monthly("2026-08-01", [158000.0, 158029.0]),
        initial_claims=_daily("2026-10-01", [210000.0, 199000.0]),
        spx=_daily("2026-10-01", [7000.0, 6930.0]), ndx=_daily("2026-10-01", [26000.0, 26260.0]),
        fear_greed=_daily("2026-10-01", [31.4, 29.6]), ust_10y=_daily("2026-10-01", [4.1, 5.19]),
        oil_wti=_daily("2026-10-01", [90.0, 95.694])))
    assert snap.cpi_yoy == round((113.0 / 101.0 - 1) * 100, 2)
    assert snap.nfp_change_k == 29.0 and snap.jobless_claims_k == 199.0
    assert snap.spx_chg_pct == -1.0 and snap.ndx_chg_pct == 1.0
    assert snap.fear_greed == 30 and snap.ust_10y == 5.19 and snap.oil_wti == 95.69


def test_snapshot_names_what_is_missing_and_stale_in_notes():
    data = product.MacroSeriesSet(series={"ust_10y": _daily("2026-10-01", [4.0])}, gaps=("hy_oas",),
                                  stale=(("ust_10y", "2026-10-01", 40),), as_of=NOW)
    snap = product.snapshot(data)
    assert snap.hy_oas is None
    assert any("hy_oas" in n for n in snap.notes) and any("stale: ust_10y" in n for n in snap.notes)


def test_the_shipped_job_is_defined_for_the_macro_series_source():
    from ats.data.refresh import load_plan

    job = next(j for j in load_plan().jobs if j.id == "macro_series_daily")
    assert job.sources == ("macro_series_feeds",) and job.domain == "structured"
