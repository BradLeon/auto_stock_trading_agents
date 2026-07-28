"""Scoring the falsifiable claims the system already makes.

Calibration is the FAST loop: it accrues per score (~52/yr across 13 targets, traded or
not) while P&L accrues per fill (3 so far). The measurement choices below are the ones
that decide whether the resulting series means anything.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from ats.journal import predictions as jp
from ats.journal import prices
from ats.memory import get_store
from ats.schemas.market import OHLCV
from ats.schemas.pead import Scorecard, ScorecardLine

NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


def _bar(d: str, close: float) -> OHLCV:
    return OHLCV(date=date.fromisoformat(d), open=close, high=close, low=close,
                 close=close, volume=1.0)


def _series(start_close=100.0, **overrides):
    """20 consecutive sessions from 2026-07-20 (Mon), flat unless overridden."""
    days = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24",
            "2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31",
            "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07",
            "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14",
            "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"]
    return [_bar(d, overrides.get(d, start_close)) for d in days]


@pytest.fixture(autouse=True)
def _prices(monkeypatch):
    """Stub `prices.bars` itself — a cache MISS must return empty, never fall through
    to a real network fetch."""
    cache: dict[str, list] = {}
    monkeypatch.setattr(prices, "bars", lambda symbol: cache.get(symbol, []))
    return cache


def _card(total=1.25, band="达到做多门槛"):
    return Scorecard(symbol="TSM", fiscal_label="Q2 FY2026", as_of=NOW,
                     lines=[ScorecardLine(dim_key="rev", label="营收", weight=1.0,
                                          score=total, weighted=total, note="")],
                     total=total, threshold=1.2, band=band)


@pytest.fixture
def store():
    return get_store()


# --------------------------------------------------------------------------- #
# the reference point — the measurement decision that matters most
# --------------------------------------------------------------------------- #
def test_reference_is_the_actionable_date_not_the_print(store, _prices):
    """The earnings gap is NOT capturable: you cannot buy at the pre-announcement
    close after seeing the result. PEAD is post-announcement drift, so the reference
    must be the first close we could actually have traded."""
    _prices["TSM"] = _series(100.0, **{"2026-07-22": 100.0, "2026-07-23": 92.0})
    jp.record_pead_prediction(store=store, symbol="TSM", fiscal_label="Q2 FY2026",
                              scorecard=_card(),
                              earnings_date="2026-07-22",     # print (gap day)
                              scored_at=NOW)                  # when we could act
    p = store.open_predictions([1])[0]
    assert str(p.ref_date) == "2026-07-23"       # not 07-22
    assert p.ref_price == 92.0              # post-gap
    assert str(p.print_date) == "2026-07-22"     # kept for context


def test_accepts_an_iso_string_earnings_date(store, _prices):
    """PeadDossier.earnings_date is a STRING; MarketSetup's is a date."""
    _prices["TSM"] = _series()
    jp.record_pead_prediction(store=store, symbol="TSM", fiscal_label="Q1",
                              scorecard=_card(), earnings_date="2026-07-20")
    assert str(store.open_predictions([1])[0].print_date) == "2026-07-20"


# --------------------------------------------------------------------------- #
# horizons — all of them are kept
# --------------------------------------------------------------------------- #
def test_all_horizons_are_scored_and_retained(store, _prices):
    """Right at T+1 and wrong at T+20 = right entry, wrong holding period. Collapsing
    that into one verdict throws away the finding."""
    # ref = 07-23; T+1 = 07-24, T+5 = 07-30 (trading days, weekends skipped)
    _prices["TSM"] = _series(100.0, **{"2026-07-24": 102.0, "2026-07-30": 90.0})
    _prices["SMH"] = _series(50.0)
    _prices["QQQ"] = _series(400.0)
    jp.record_pead_prediction(store=store, symbol="TSM", fiscal_label="Q2",
                              scorecard=_card(), scored_at=NOW,
                              sector_etf="SMH", benchmark="QQQ")
    jp.score_open_predictions(store=store, horizons=[1, 5])

    outs = {o.horizon_days: o for o in store.prediction_outcomes("TSM:Q2:pead_score")}
    assert outs[1].realized_pct == pytest.approx(2.0)
    assert outs[5].realized_pct == pytest.approx(-10.0)
    assert outs[1].excess_vs_sector_pct == pytest.approx(2.0)   # sector flat
    assert outs[5].excess_vs_sector_pct == pytest.approx(-10.0)


def test_unelapsed_horizons_are_pending_not_zero(store, _prices):
    _prices["TSM"] = [_bar("2026-07-23", 100.0), _bar("2026-07-24", 101.0)]
    jp.record_pead_prediction(store=store, symbol="TSM", fiscal_label="Q2",
                              scorecard=_card(), scored_at=NOW)
    s = jp.score_open_predictions(store=store, horizons=[1, 20])
    assert s["scored"] == 1 and s["pending"] == 1
    assert [o.horizon_days for o in store.prediction_outcomes("TSM:Q2:pead_score")] == [1]


def test_scoring_is_idempotent(store, _prices):
    _prices["TSM"] = _series(100.0, **{"2026-07-24": 105.0})
    jp.record_pead_prediction(store=store, symbol="TSM", fiscal_label="Q2",
                              scorecard=_card(), scored_at=NOW)
    jp.score_open_predictions(store=store, horizons=[1])
    again = jp.score_open_predictions(store=store, horizons=[1])
    assert again["scored"] == 0
    assert len(store.prediction_outcomes("TSM:Q2:pead_score")) == 1


# --------------------------------------------------------------------------- #
# untraded predictions are the point, not a gap
# --------------------------------------------------------------------------- #
def test_a_prediction_with_no_trade_is_still_scored(store, _prices):
    """entry_id NULL = we predicted but did not trade. That sample is exactly what
    keeps the calibration free of survivorship bias."""
    _prices["TSM"] = _series(100.0, **{"2026-07-24": 103.0})
    jp.record_pead_prediction(store=store, symbol="TSM", fiscal_label="Q2",
                              scorecard=_card(), scored_at=NOW, entry_id=None)
    jp.score_open_predictions(store=store, horizons=[1])
    row = store.conn.execute("SELECT entry_id FROM predictions").fetchone()
    assert row["entry_id"] is None
    assert store.prediction_outcomes("TSM:Q2:pead_score")[0].realized_pct == pytest.approx(3.0)


# --------------------------------------------------------------------------- #
# the other two claim types
# --------------------------------------------------------------------------- #
def test_expected_move_and_target_price_are_registered(store, _prices):
    from ats.schemas.pead import ExpectationSet, MarketSetup

    _prices["TSM"] = _series(100.0)
    jp.record_pead_prediction(
        store=store, symbol="TSM", fiscal_label="Q2", scorecard=_card(), scored_at=NOW,
        market_setup=MarketSetup(symbol="TSM", as_of=NOW, expected_move_pct=4.23),
        expectation_set=ExpectationSet(symbol="TSM", fiscal_label="Q2", as_of=NOW,
                                       consensus_target_price=120.0))
    srcs = {r["source"]: r for r in store.conn.execute("SELECT * FROM predictions")}
    assert set(srcs) == {"pead_score", "expected_move", "consensus_pt"}
    assert srcs["expected_move"]["predicted_value"] == pytest.approx(4.23)
    assert srcs["consensus_pt"]["predicted_value"] == pytest.approx(20.0)   # +20% to PT


def test_no_price_history_degrades_quietly(store, monkeypatch):
    monkeypatch.setattr(prices, "bars", lambda s: [])
    ids = jp.record_pead_prediction(store=store, symbol="ZZZZ", fiscal_label="Q2",
                                    scorecard=_card(), scored_at=NOW)
    assert ids                                        # claim still recorded
    assert store.open_predictions([1])[0].ref_price is None
