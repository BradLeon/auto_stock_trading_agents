"""Post-close reconciliation: backfill outcomes, attribute fills, resolve stragglers.

Offline throughout — a fake broker stands in for IBKR. The cases that matter are the
ones that silently corrupt data if got wrong: orderId reuse across sessions, and
account-wide fills from manual TWS trades being claimed as ours.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.memory import get_store
from ats.trader import reconcile as rec

NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()


class FakeBroker:
    def __init__(self, fills=None, completed=None):
        self._fills, self._completed = fills or [], completed or []

    def get_fills(self):
        return self._fills

    def completed_orders(self):
        return self._completed


@pytest.fixture
def store():
    return get_store()


def _trade(store, *, order_id="", symbol="GOOG", status="submitted", submitted=None,
           perm_id=None, order_ref=None, coid=None):
    store.conn.execute(
        "INSERT INTO trades (order_id, cycle_id, symbol, action, qty, order_type, status, "
        "submitted_at, first_submitted_at, perm_id, order_ref, client_order_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (order_id, "c1", symbol, "trim", 10.0, "limit", status,
         submitted or NOW.isoformat(), submitted or NOW.isoformat(),
         perm_id, order_ref, coid or f"c1:{symbol}:trim:{order_id}"))
    store.conn.commit()


def _fill(*, exec_id="e1", symbol="GOOG", order_id="", pnl=None, price=100.0,
          time=None, perm_id="", order_ref=""):
    return {"exec_id": exec_id, "symbol": symbol, "side": "SLD", "shares": 10.0,
            "price": price, "time": time or NOW.isoformat(), "realized_pnl": pnl,
            "commission": 1.0, "order_id": order_id, "perm_id": perm_id,
            "order_ref": order_ref}


def _row(store, symbol="GOOG"):
    return store.conn.execute(
        "SELECT * FROM trades WHERE symbol = ?", (symbol,)).fetchone()


# --------------------------------------------------------------------------- #
# The headline fix: realized P&L reaches the order record
# --------------------------------------------------------------------------- #
def test_backfills_realized_pnl_onto_the_order(store):
    """The real case: GOOG trim made +495.19, which lived only in `fills`."""
    _trade(store, order_id="4", status="submitted")
    s = rec.reconcile(FakeBroker([_fill(order_id="4", pnl=495.193722, price=317.0705)]),
                      store=store)
    row = _row(store)
    assert row["realized_pnl"] == pytest.approx(495.193722)
    assert row["avg_fill_price"] == pytest.approx(317.0705)
    assert row["status"] == "filled"
    assert s["linked"] == 1 and s["pnl_backfilled"] == 1


def test_partial_fills_sum_onto_one_order(store):
    _trade(store, order_id="7")
    rec.reconcile(FakeBroker([_fill(exec_id="a", order_id="7", pnl=100.0),
                              _fill(exec_id="b", order_id="7", pnl=-20.0)]), store=store)
    assert _row(store)["realized_pnl"] == pytest.approx(80.0)


# --------------------------------------------------------------------------- #
# Attribution — the account-wide feed carries trades we never placed
# --------------------------------------------------------------------------- #
def test_manual_tws_fill_is_not_claimed(store):
    _trade(store, order_id="4", symbol="GOOG")
    rec.reconcile(FakeBroker([_fill(exec_id="mine", order_id="4", symbol="GOOG", pnl=10.0),
                              _fill(exec_id="theirs", order_id="34", symbol="SPCX",
                                    pnl=-60.01)]), store=store)
    origins = dict(store.conn.execute("SELECT exec_id, origin FROM fills").fetchall())
    assert origins == {"mine": "system", "theirs": "manual"}


def test_order_ref_wins_and_is_exact(store):
    """Stage 1d's tag: unambiguous, no date heuristics needed."""
    _trade(store, order_id="", order_ref="ats:c1:GOOG")
    rec.reconcile(FakeBroker([_fill(order_ref="ats:c1:GOOG", pnl=5.0)]), store=store)
    assert _row(store)["realized_pnl"] == 5.0
    assert store.conn.execute(
        "SELECT link_confidence FROM fills").fetchone()[0] == "order_ref"


def test_perm_id_matches_across_sessions(store):
    _trade(store, order_id="", perm_id="99887766",
           submitted=(NOW - timedelta(days=30)).isoformat())
    rec.reconcile(FakeBroker([_fill(perm_id="99887766", pnl=7.0)]), store=store)
    assert _row(store)["realized_pnl"] == 7.0


def test_order_id_reuse_across_sessions_is_rejected(store):
    """THE trap: IBKR resets orderId on TWS restart. An unscoped join would attach
    today's fill to an unrelated order from weeks ago."""
    _trade(store, order_id="4", symbol="GOOG",
           submitted=(NOW - timedelta(days=30)).isoformat())
    rec.reconcile(FakeBroker([_fill(order_id="4", symbol="GOOG", pnl=999.0)]), store=store)
    assert _row(store)["realized_pnl"] is None                 # not claimed
    assert store.conn.execute("SELECT origin FROM fills").fetchone()[0] == "manual"


def test_order_id_match_requires_same_symbol(store):
    _trade(store, order_id="4", symbol="GOOG")
    rec.reconcile(FakeBroker([_fill(order_id="4", symbol="ASML", pnl=50.0)]), store=store)
    assert _row(store, "GOOG")["realized_pnl"] is None


def test_order_id_zero_never_matches(store):
    """order_id 0 shows up on manual TWS fills; it must not join to a blank column."""
    _trade(store, order_id="", symbol="LITE")
    rec.reconcile(FakeBroker([_fill(order_id="0", symbol="LITE", pnl=3.0)]), store=store)
    assert _row(store, "LITE")["realized_pnl"] is None


# --------------------------------------------------------------------------- #
# Resolving orders stuck mid-flight
# --------------------------------------------------------------------------- #
def test_completed_orders_resolve_zombie_submitted_rows(store):
    """10 of the first 52 rows sat at 'submitted' forever after the 3s poll."""
    _trade(store, order_id="12", status="submitted")
    s = rec.reconcile(FakeBroker([], [{"order_id": "12", "perm_id": "", "order_ref": "",
                                       "symbol": "GOOG", "status": "cancelled",
                                       "filled": 0.0, "avg_fill_price": None}]),
                      store=store)
    assert _row(store)["status"] == "cancelled"
    assert s["status_resolved"] == 1


def test_old_unresolved_order_is_marked_expired(store):
    """A DAY order from a previous session did not fill — say so, don't leave it 'in flight'."""
    _trade(store, order_id="5", status="submitted",
           submitted=(NOW - timedelta(days=3)).isoformat())
    rec.reconcile(FakeBroker(), store=store)
    row = _row(store)
    assert row["status"] == "expired"
    assert "DAY 单已失效" in row["error"]


def test_todays_open_order_is_left_alone(store):
    _trade(store, order_id="5", status="submitted")
    rec.reconcile(FakeBroker(), store=store)
    assert _row(store)["status"] == "submitted"


# --------------------------------------------------------------------------- #
# Safety properties
# --------------------------------------------------------------------------- #
def test_is_idempotent(store):
    _trade(store, order_id="4")
    f = [_fill(order_id="4", pnl=495.19)]
    rec.reconcile(FakeBroker(f), store=store)
    snap = dict(_row(store))
    s2 = rec.reconcile(FakeBroker(f), store=store)
    assert dict(_row(store)) == snap
    assert s2["fills_new"] == 0


def test_dry_run_writes_nothing(store):
    _trade(store, order_id="4")
    s = rec.reconcile(FakeBroker([_fill(order_id="4", pnl=495.19)]), store=store, dry_run=True)
    assert s["linked"] == 1
    assert _row(store)["realized_pnl"] is None
    assert store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0


def test_broker_down_degrades_quietly(store):
    class Dead:
        def get_fills(self):
            raise RuntimeError("TWS not running")

        def completed_orders(self):
            return []

    s = rec.reconcile(Dead(), store=store)
    assert s["errors"] and "fetch failed" in s["errors"][0]


def test_records_last_reconcile_time(store):
    rec.reconcile(FakeBroker(), store=store)
    assert store.get_meta("last_reconcile_at")
