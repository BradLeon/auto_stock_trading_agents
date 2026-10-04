"""Trade episodes: net position 0 → nonzero → 0, built purely from fills.

The acceptance bar is not "the code runs" — it is SUM(episode.realized_pnl) matching
SUM(fills.realized_pnl) to the cent, because a journal that disagrees with the broker's
own numbers is untrustworthy exactly when it matters most.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ats.journal import episodes as ej
from ats.memory import get_store
from ats.schemas.journal import JournalEntry
from ats.schemas.portfolio import PortfolioSnapshot, Position

NOW = datetime(2026, 7, 23, 14, 0, tzinfo=timezone.utc)


def _fill(*, exec_id, symbol="GOOG", side="BOT", shares, price, t="2026-07-01T10:00:00+00:00",
          realized_pnl=None, commission=1.0, origin="system", entry_id=None):
    return {"exec_id": exec_id, "symbol": symbol, "side": side, "shares": shares,
            "price": price, "time": t, "realized_pnl": realized_pnl,
            "commission": commission, "origin": origin, "entry_id": entry_id,
            "order_id": "1"}


@pytest.fixture
def store():
    return get_store()


# --------------------------------------------------------------------------- #
# the headline guarantee
# --------------------------------------------------------------------------- #
def test_realized_pnl_sums_exactly_what_ibkr_reported():
    """Never re-derive P&L from prices — sum IBKR's own per-fill number."""
    fills = [
        _fill(exec_id="a", side="BOT", shares=100, price=300.0, t="2026-07-01T10:00:00Z"),
        _fill(exec_id="b", side="SLD", shares=100, price=317.07, t="2026-07-23T14:06:00Z",
              realized_pnl=1707.0),
    ]
    eps = ej.build_episodes("GOOG", fills)
    assert len(eps) == 1
    assert eps[0].realized_pnl == pytest.approx(1707.0)
    assert eps[0].status == "closed"


def test_partial_trims_accumulate_realized_pnl_while_still_open():
    """A trim that doesn't reach zero still has a real IBKR realizedPNL — it must not
    wait for full closure to be counted."""
    fills = [
        _fill(exec_id="a", side="BOT", shares=100, price=300.0, t="2026-07-01T10:00:00Z"),
        _fill(exec_id="b", side="SLD", shares=30, price=320.0, t="2026-07-10T10:00:00Z",
              realized_pnl=600.0),
    ]
    eps = ej.build_episodes("GOOG", fills)
    assert len(eps) == 1
    ep = eps[0]
    assert ep.status == "open"
    assert ep.realized_pnl == pytest.approx(600.0)
    assert ep.avg_exit == pytest.approx(320.0)


# --------------------------------------------------------------------------- #
# a scaled-out position is ONE episode, not N trades
# --------------------------------------------------------------------------- #
def test_scaling_out_is_one_episode_not_n():
    """The bug this whole stage exists to fix: old analytics counted each partial
    exit as a separate 'trade'."""
    fills = [
        _fill(exec_id="a", side="BOT", shares=100, price=300.0, t="2026-07-01T10:00:00Z"),
        _fill(exec_id="b", side="SLD", shares=40, price=310.0, t="2026-07-05T10:00:00Z",
              realized_pnl=400.0),
        _fill(exec_id="c", side="SLD", shares=30, price=315.0, t="2026-07-10T10:00:00Z",
              realized_pnl=450.0),
        _fill(exec_id="d", side="SLD", shares=30, price=320.0, t="2026-07-15T10:00:00Z",
              realized_pnl=600.0),
    ]
    eps = ej.build_episodes("GOOG", fills)
    assert len(eps) == 1
    assert eps[0].status == "closed"
    assert eps[0].realized_pnl == pytest.approx(1450.0)
    assert eps[0].avg_exit == pytest.approx((40 * 310 + 30 * 315 + 30 * 320) / 100, rel=1e-6)


def test_add_then_close_averages_entry_correctly():
    fills = [
        _fill(exec_id="a", side="BOT", shares=50, price=100.0, t="2026-07-01T10:00:00Z"),
        _fill(exec_id="b", side="BOT", shares=50, price=110.0, t="2026-07-05T10:00:00Z"),
        _fill(exec_id="c", side="SLD", shares=100, price=120.0, t="2026-07-10T10:00:00Z",
              realized_pnl=1000.0),
    ]
    eps = ej.build_episodes("GOOG", fills)
    assert eps[0].avg_entry == pytest.approx(105.0)
    assert eps[0].realized_pnl == pytest.approx(1000.0)


def test_two_full_round_trips_are_two_episodes():
    fills = [
        _fill(exec_id="a", side="BOT", shares=10, price=100.0, t="2026-06-01T10:00:00Z"),
        _fill(exec_id="b", side="SLD", shares=10, price=110.0, t="2026-06-05T10:00:00Z",
              realized_pnl=100.0),
        _fill(exec_id="c", side="BOT", shares=20, price=90.0, t="2026-07-01T10:00:00Z"),
        _fill(exec_id="d", side="SLD", shares=20, price=95.0, t="2026-07-05T10:00:00Z",
              realized_pnl=100.0),
    ]
    eps = ej.build_episodes("GOOG", fills)
    assert len(eps) == 2
    assert all(e.status == "closed" for e in eps)


# --------------------------------------------------------------------------- #
# the zero-crossing edge case: one fill flips long -> short
# --------------------------------------------------------------------------- #
def test_a_single_fill_flipping_the_position_splits_into_two_episodes():
    fills = [
        _fill(exec_id="a", side="BOT", shares=10, price=100.0, t="2026-07-01T10:00:00Z"),
        _fill(exec_id="b", side="SLD", shares=30, price=105.0, t="2026-07-05T10:00:00Z",
              realized_pnl=50.0, commission=3.0),
    ]
    eps = ej.build_episodes("GOOG", fills)
    assert len(eps) == 2
    closed, opened = eps[0], eps[1]
    assert closed.status == "closed" and closed.direction == "long"
    assert opened.status == "open" and opened.direction == "short"
    # The flip fill's commission (3.0) is pro-rated 10/30 to the close, 20/30 to the
    # open; `closed` also carries the FULL $1.0 commission of the original opening
    # fill (that one wasn't split), so closed = 1.0 + 1.0 = 2.0, opened = 2.0.
    assert closed.commission == pytest.approx(2.0, abs=1e-6)
    assert opened.commission == pytest.approx(2.0, abs=1e-6)
    assert (closed.realized_pnl or 0) + (opened.realized_pnl or 0) == pytest.approx(50.0)


# --------------------------------------------------------------------------- #
# origin attribution
# --------------------------------------------------------------------------- #
def test_all_system_legs_are_system_origin():
    fills = [_fill(exec_id="a", side="BOT", shares=10, price=100.0, origin="system"),
             _fill(exec_id="b", side="SLD", shares=10, price=110.0, origin="system",
                   realized_pnl=100.0, t="2026-07-05T10:00:00Z")]
    assert ej.build_episodes("GOOG", fills)[0].origin == "system"


def test_mixed_system_and_manual_legs_are_mixed():
    fills = [_fill(exec_id="a", side="BOT", shares=10, price=100.0, origin="system"),
             _fill(exec_id="b", side="BOT", shares=5, price=105.0, origin="manual",
                   t="2026-07-02T10:00:00Z")]
    assert ej.build_episodes("GOOG", fills)[0].origin == "mixed"


def test_missing_origin_defaults_to_manual_not_system():
    """No positive evidence of a system order -> never assume system."""
    fills = [_fill(exec_id="a", side="BOT", shares=10, price=100.0, origin=None)]
    assert ej.build_episodes("GOOG", fills)[0].origin == "manual"


# --------------------------------------------------------------------------- #
# primary_entry_id / decision_gradeable
# --------------------------------------------------------------------------- #
def test_primary_entry_id_is_the_first_leg_with_a_real_plan():
    """Not necessarily the chronological opener — a pre-tracking seed never carries
    a real entry_id, so a later Chief-driven add-on is what should be attached."""
    fills = [
        _fill(exec_id="seed:GOOG", side="BOT", shares=100, price=280.0, origin="pre_tracking",
             entry_id=None, t="2026-06-30T00:00:00Z"),
        _fill(exec_id="a", side="BOT", shares=20, price=300.0, origin="system",
             entry_id="c1:GOOG:add", t="2026-07-05T10:00:00Z"),
    ]
    ep = ej.build_episodes("GOOG", fills)[0]
    assert ep.primary_entry_id == "c1:GOOG:add"
    assert ep.decision_gradeable is True


def test_no_real_plan_anywhere_is_not_gradeable():
    fills = [_fill(exec_id="a", side="BOT", shares=10, price=100.0, entry_id=None)]
    ep = ej.build_episodes("GOOG", fills)[0]
    assert ep.primary_entry_id == ""
    assert ep.decision_gradeable is False


def test_manual_trade_is_not_gradeable_even_though_not_pre_tracking():
    """A manual order also bypasses persist_decision — origin alone must not decide
    gradeability, only the presence of an actual plan does."""
    fills = [_fill(exec_id="a", side="BOT", shares=10, price=100.0, origin="manual",
                  entry_id=None)]
    ep = ej.build_episodes("GOOG", fills)[0]
    assert ep.origin == "manual"
    assert ep.decision_gradeable is False


# --------------------------------------------------------------------------- #
# pre-tracking seeding — the real bug this stage found
# --------------------------------------------------------------------------- #
def test_a_lone_trim_on_a_held_position_is_long_not_short(store):
    """THE bug: GOOG's only recorded fill is a single SLD of 13 shares. Without
    knowing about the other 87 shares held before tracking began, a naive reducer
    reads that as opening a SHORT position — wrong, GOOG is a long holding."""
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, side, shares, price, time, realized_pnl, "
        "commission, order_id, origin) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("f1", "GOOG", "SLD", 13.0, 317.07, "2026-07-23T14:06:00+00:00", 495.19, 1.09,
         "4", "system"))
    store.conn.commit()
    pf = PortfolioSnapshot(as_of=NOW, net_liquidation=100_000.0, positions=[
        Position(symbol="GOOG", qty=87.0, avg_cost=280.0, market_price=328.0,
                market_value=28536.0, unrealized_pnl=4176.0)])

    s = ej.rebuild_all(store=store, portfolio=pf)
    assert s["seeded"] == 1
    ep = store.list_episodes(symbol="GOOG")[0]
    assert ep.direction == "long"          # not "short"
    assert ep.status == "open"
    assert ep.realized_pnl == pytest.approx(495.19)   # unaffected by the cost estimate
    assert ep.basis_source == "ibkr_avg_cost"


def test_fully_closed_position_with_no_current_holding_still_seeds(store):
    """SPCX: one SLD fill, zero held now -> the shares sold must have existed before
    tracking; seed it even though there's no live position to read a cost from."""
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, side, shares, price, time, realized_pnl, "
        "commission, order_id, origin) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("f1", "SPCX", "SLD", 1.0, 115.28, "2026-07-17T10:00:00+00:00", -60.01, 1.0,
         "34", "manual"))
    store.conn.commit()
    pf = PortfolioSnapshot(as_of=NOW, net_liquidation=100_000.0, positions=[])

    ej.rebuild_all(store=store, portfolio=pf)
    ep = store.list_episodes(symbol="SPCX")[0]
    assert ep.status == "closed"
    assert ep.realized_pnl == pytest.approx(-60.01)


def test_position_with_zero_fills_gets_a_standalone_seed(store):
    """NVDA: held, but never appears in fills at all (no reconcile ever saw an
    execution for it) — it must not be silently absent from the episode ledger."""
    pf = PortfolioSnapshot(as_of=NOW, net_liquidation=100_000.0, positions=[
        Position(symbol="NVDA", qty=50.0, avg_cost=150.0, market_price=170.0,
                market_value=8500.0, unrealized_pnl=1000.0)])
    # No fills for NVDA anywhere, and no OTHER symbol's fills either -> exercise the
    # zero-fills branch directly.
    ej.rebuild_all(store=store, portfolio=pf)
    eps = store.list_episodes(symbol="NVDA")
    assert len(eps) == 1
    assert eps[0].origin == "pre_tracking"
    assert eps[0].unrealized_pnl == pytest.approx(1000.0)
    assert eps[0].decision_gradeable is False


def test_no_portfolio_means_no_seeding_at_all(store):
    """Without a live snapshot the reducer must still run (fills-only), just without
    the pre-tracking correction — never silently guess a holding."""
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, side, shares, price, time, realized_pnl, "
        "commission, order_id, origin) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("f1", "GOOG", "SLD", 13.0, 317.07, "2026-07-23T14:06:00+00:00", 495.19, 1.09,
         "4", "system"))
    store.conn.commit()
    s = ej.rebuild_all(store=store, portfolio=None)
    assert s["seeded"] == 0


# --------------------------------------------------------------------------- #
# storage round-trip + idempotency
# --------------------------------------------------------------------------- #
def test_save_and_get_episode_round_trips(store):
    ep = ej.build_episodes("GOOG", [
        _fill(exec_id="a", side="BOT", shares=10, price=100.0)])[0]
    store.save_episode(ep)
    got = store.get_episode(ep.episode_id)
    assert got is not None and got.symbol == "GOOG" and got.avg_entry == 100.0


def test_episode_id_is_deterministic_across_reruns():
    """A random UUID here would make save_episode's upsert create a NEW row every
    rerun instead of replacing one — episode_id must be derived from the fills."""
    fills = [_fill(exec_id="a", side="BOT", shares=10, price=100.0)]
    id1 = ej.build_episodes("GOOG", fills)[0].episode_id
    id2 = ej.build_episodes("GOOG", fills)[0].episode_id
    assert id1 == id2


def test_rebuild_is_idempotent_on_realized_pnl(store):
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, side, shares, price, time, realized_pnl, "
        "commission, order_id, origin) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("f1", "GOOG", "SLD", 13.0, 317.07, "2026-07-23T14:06:00+00:00", 495.19, 1.09,
         "4", "system"))
    store.conn.commit()
    ej.rebuild_all(store=store)
    ej.rebuild_all(store=store)
    ej.rebuild_all(store=store)
    rows = store.list_episodes(symbol="GOOG")
    assert len(rows) == 1, "rerunning the reducer must REPLACE the row, not duplicate it"
    assert rows[0].realized_pnl == pytest.approx(495.19)


def test_fills_episode_id_is_backfilled(store):
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, side, shares, price, time, realized_pnl, "
        "commission, order_id, origin) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("f1", "GOOG", "SLD", 13.0, 317.07, "2026-07-23T14:06:00+00:00", 495.19, 1.09,
         "4", "system"))
    store.conn.commit()
    ej.rebuild_all(store=store)
    row = store.conn.execute("SELECT episode_id FROM fills WHERE exec_id='f1'").fetchone()
    assert row["episode_id"]


def test_setup_is_pulled_from_the_linked_journal_entry(store):
    entry = JournalEntry(entry_id="c1:GOOG:trim", cycle_id="c1", as_of=NOW,
                         symbol="GOOG", action="trim", setup="pead_event")
    store.save_journal_entry(entry)
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, side, shares, price, time, realized_pnl, "
        "commission, order_id, origin, entry_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("f1", "GOOG", "SLD", 13.0, 317.07, "2026-07-23T14:06:00+00:00", 495.19, 1.09,
         "4", "system", "c1:GOOG:trim"))
    store.conn.commit()
    ej.rebuild_all(store=store)
    ep = store.list_episodes(symbol="GOOG")[0]
    assert ep.setup == "pead_event"
    assert ep.decision_gradeable is True


# --------------------------------------------------------------------------- #
# the real acceptance test, against a realistic multi-symbol batch
# --------------------------------------------------------------------------- #
def test_total_realized_pnl_matches_fills_across_all_symbols(store):
    rows = [
        ("f1", "GOOG", "SLD", 13.0, 317.07, "2026-07-23T14:06:00+00:00", 495.193722, 1.09, "4"),
        ("f2", "ASML", "SLD", 1.0, 1818.78, "2026-07-23T14:06:05+00:00", -3.457165, 1.04, "6"),
        ("f3", "SPCX", "SLD", 1.0, 115.28, "2026-07-17T10:00:00+00:00", -60.012573, 1.0, "34"),
        ("f4", "LITE", "SLD", 1.0, 41.2, "2026-07-17T09:00:00+00:00", 0.0, -0.05, "0"),
        ("f5", "7709", "BOT", 300.0, 46.1, "2026-07-17T08:00:00+00:00", 0.0, 18.39, "0"),
    ]
    for r in rows:
        store.conn.execute(
            "INSERT INTO fills (exec_id, symbol, side, shares, price, time, "
            "realized_pnl, commission, order_id) VALUES (?,?,?,?,?,?,?,?,?)", r)
    store.conn.commit()

    ej.rebuild_all(store=store)
    ep_total = sum(e.realized_pnl or 0 for sym in store.symbols_with_fills()
                  for e in store.list_episodes(symbol=sym))
    fill_total = store.conn.execute("SELECT SUM(realized_pnl) FROM fills").fetchone()[0]
    assert ep_total == pytest.approx(fill_total, abs=0.01)
