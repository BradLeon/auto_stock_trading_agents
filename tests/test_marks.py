"""Deterministic marks: holding period, R-multiple, MAE/MFE, exit classification.

Everything here is arithmetic over already-stored data — no LLM. The gate that
matters most: a `pre_tracking`-tainted episode's `opened_at` is a FABRICATED
timestamp (one second before the earliest real fill), so every mark that depends on
it being real must be skipped, not silently computed on a fake window.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from ats.journal import marks, prices
from ats.memory import get_store
from ats.schemas.journal import ApprovalDivergence, JournalEntry, TradeEpisode

NOW = datetime(2026, 7, 23, 14, 0, tzinfo=timezone.utc)


def _bar(d, h, low, c=None):
    from ats.schemas.market import OHLCV

    return OHLCV(date=date.fromisoformat(d), open=c or h, high=h, low=low,
                close=c or h, volume=1.0)


@pytest.fixture
def store():
    return get_store()


@pytest.fixture(autouse=True)
def _price_cache(monkeypatch):
    """Stub `prices.bars` itself, not just its cache dict — a cache MISS must return
    empty, never fall through to a real network fetch. (Patching only `_CACHE` left
    exactly this hole: a test that forgot to pre-populate a symbol silently hit
    yfinance for real.)"""
    cache: dict[str, list] = {}
    monkeypatch.setattr(prices, "bars", lambda symbol: cache.get(symbol, []))
    return cache


def _episode(**kw):
    base = dict(episode_id="e1", symbol="GOOG", direction="long", status="closed",
               origin="system", basis_source="observed_fills",
               opened_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
               closed_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
               avg_entry=100.0, avg_exit=110.0, realized_pnl=1000.0)
    return TradeEpisode(**{**base, **kw})


def _entry(**kw):
    base = dict(entry_id="c1:GOOG:trim", cycle_id="c1", as_of=NOW, symbol="GOOG",
               action="trim", setup="pead_event")
    return JournalEntry(**{**base, **kw})


# --------------------------------------------------------------------------- #
# the basis_source gate — the design correction this stage exists to encode
# --------------------------------------------------------------------------- #
def test_pre_tracking_episode_gets_no_price_dependent_marks(store, _price_cache):
    """opened_at on a pre_tracking episode is fabricated (1s before the earliest
    real fill) — computing holding_days/MAE/MFE against it would produce a
    plausible-looking but meaningless number."""
    _price_cache["GOOG"] = [_bar("2026-07-01", 120, 90), _bar("2026-07-10", 115, 105)]
    ep = _episode(basis_source="ibkr_avg_cost", origin="pre_tracking")
    out = marks.mark_episode(store, ep)
    assert out.holding_days is None
    assert out.mae_pct is None and out.mfe_pct is None
    assert out.r_multiple is None
    assert out.exit_reason is None


def test_observed_fills_episode_gets_full_marks(store, _price_cache):
    _price_cache["GOOG"] = [_bar("2026-07-01", 105, 95), _bar("2026-07-05", 120, 100),
                            _bar("2026-07-10", 112, 108)]
    ep = _episode()
    out = marks.mark_episode(store, ep)
    assert out.holding_days == 9
    assert out.mfe_pct == pytest.approx(20.0)     # (120/100 - 1) * 100
    assert out.mae_pct == pytest.approx(-5.0)      # (95/100 - 1) * 100


# --------------------------------------------------------------------------- #
# MAE/MFE direction-awareness
# --------------------------------------------------------------------------- #
def test_short_episode_inverts_favorable_and_adverse(store, _price_cache):
    _price_cache["GOOG"] = [_bar("2026-07-01", 105, 80)]
    ep = _episode(direction="short", avg_exit=90.0)
    out = marks.mark_episode(store, ep)
    # For a short: price falling is favorable, rising is adverse.
    assert out.mfe_pct == pytest.approx(20.0)      # (100-80)/100
    assert out.mae_pct == pytest.approx(-5.0)      # (100-105)/100


def test_no_bars_leaves_mae_mfe_none(store, _price_cache):
    ep = _episode()
    out = marks.mark_episode(store, ep)
    assert out.mae_pct is None and out.mfe_pct is None


# --------------------------------------------------------------------------- #
# r_multiple
# --------------------------------------------------------------------------- #
def test_r_multiple_uses_the_opening_entrys_risk_unit(store, _price_cache):
    store.save_journal_entry(_entry(planned_risk_usd=500.0, risk_unit_source="expected_move"))
    ep = _episode(primary_entry_id="c1:GOOG:trim", realized_pnl=1000.0)
    out = marks.mark_episode(store, ep)
    assert out.r_multiple == pytest.approx(2.0)
    assert out.risk_unit_source == "expected_move"


def test_r_multiple_is_none_without_a_risk_unit(store, _price_cache):
    """Never invent a denominator — an R from a guess is worse than no R."""
    store.save_journal_entry(_entry(planned_risk_usd=None))
    ep = _episode(primary_entry_id="c1:GOOG:trim")
    out = marks.mark_episode(store, ep)
    assert out.r_multiple is None


def test_r_multiple_is_none_without_any_plan(store, _price_cache):
    ep = _episode(primary_entry_id="")
    out = marks.mark_episode(store, ep)
    assert out.r_multiple is None


# --------------------------------------------------------------------------- #
# exit_reason classification
# --------------------------------------------------------------------------- #
def test_no_plan_means_no_exit_classification_not_drift(store, _price_cache):
    """No plan to grade compliance against -> None, never a false 'drift' signal."""
    ep = _episode(primary_entry_id="")
    out = marks.mark_episode(store, ep)
    assert out.exit_reason is None
    assert out.exit_as_planned is None


def test_target_hit_on_a_long(store):
    entry = _entry(target_price=108.0, stop_price=90.0)
    ep = _episode(avg_exit=110.0)
    reason, planned = marks.classify_exit(ep, entry, None, holding_days=5)
    assert (reason, planned) == ("target_hit", True)


def test_stop_hit_on_a_long(store):
    entry = _entry(target_price=130.0, stop_price=95.0)
    ep = _episode(avg_exit=94.0)
    reason, planned = marks.classify_exit(ep, entry, None, holding_days=2)
    assert (reason, planned) == ("stop_hit", True)


def test_target_and_stop_are_inverted_for_a_short():
    entry = _entry(target_price=80.0, stop_price=110.0)
    ep = _episode(direction="short", avg_exit=78.0)
    reason, _ = marks.classify_exit(ep, entry, None, holding_days=3)
    assert reason == "target_hit"


def test_horizon_reached_when_neither_band_hit():
    entry = _entry(target_price=200.0, stop_price=10.0, planned_horizon_days=10)
    ep = _episode(avg_exit=101.0)
    reason, planned = marks.classify_exit(ep, entry, None, holding_days=10)
    assert (reason, planned) == ("horizon_reached", True)


def test_drift_when_nothing_explains_the_exit():
    entry = _entry(target_price=200.0, stop_price=10.0, planned_horizon_days=30)
    ep = _episode(avg_exit=101.0)
    reason, planned = marks.classify_exit(ep, entry, None, holding_days=5)
    assert (reason, planned) == ("drift", False)


def test_risk_forced_from_the_closing_legs_own_setup():
    """The closing leg's OWN entry — not the opening plan — is what carries this."""
    entry = _entry(target_price=200.0, stop_price=10.0)
    closing = _entry(entry_id="c2:GOOG:trim", setup="risk_repair")
    ep = _episode(avg_exit=101.0)
    reason, planned = marks.classify_exit(ep, entry, closing, holding_days=1)
    assert (reason, planned) == ("risk_forced", False)


def test_boss_override_from_the_closing_legs_approval():
    entry = _entry(target_price=200.0, stop_price=10.0)
    div = ApprovalDivergence(status="modified", diverged=True)
    closing = _entry(entry_id="c2:GOOG:trim", approval=div)
    ep = _episode(avg_exit=101.0)
    reason, planned = marks.classify_exit(ep, entry, closing, holding_days=1)
    assert (reason, planned) == ("boss_override", False)


def test_open_episode_is_never_classified():
    entry = _entry(target_price=200.0)
    ep = _episode(status="open", closed_at=None)
    reason, planned = marks.classify_exit(ep, entry, None, holding_days=None)
    assert (reason, planned) == (None, None)


# --------------------------------------------------------------------------- #
# closing-entry lookup
# --------------------------------------------------------------------------- #
def test_closing_entry_is_the_most_recent_leg_with_a_plan(store):
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, side, shares, price, time, episode_id, "
        "entry_id) VALUES (?,?,?,?,?,?,?,?)",
        ("f1", "GOOG", "BOT", 10, 100.0, "2026-07-01T10:00:00+00:00", "e1", None))
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, side, shares, price, time, episode_id, "
        "entry_id) VALUES (?,?,?,?,?,?,?,?)",
        ("f2", "GOOG", "SLD", 10, 110.0, "2026-07-10T10:00:00+00:00", "e1", "c2:GOOG:trim"))
    store.save_journal_entry(_entry(entry_id="c2:GOOG:trim", setup="stop_loss"))
    got = marks._closing_entry(store, _episode())
    assert got is not None and got.setup == "stop_loss"


def test_no_legs_at_all_returns_none(store):
    assert marks._closing_entry(store, _episode()) is None


# --------------------------------------------------------------------------- #
# mark_all / idempotency
# --------------------------------------------------------------------------- #
def test_mark_all_persists_updates(store, _price_cache):
    _price_cache["GOOG"] = [_bar("2026-07-01", 105, 95), _bar("2026-07-10", 112, 108)]
    store.save_episode(_episode())
    s = marks.mark_all(store=store)
    assert s["marked"] == 1
    got = store.get_episode("e1")
    assert got.holding_days == 9


def test_mark_all_is_idempotent(store, _price_cache):
    _price_cache["GOOG"] = [_bar("2026-07-01", 105, 95), _bar("2026-07-10", 112, 108)]
    store.save_episode(_episode())
    marks.mark_all(store=store)
    s2 = marks.mark_all(store=store)
    # Second pass recomputes the same values -> still "updated" (model_copy always
    # returns a new object), but the persisted numbers must not drift.
    assert store.get_episode("e1").holding_days == 9
    assert s2["marked"] == 1
