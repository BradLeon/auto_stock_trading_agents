"""Phase C group 3: compensation semantics (§11.1, tasks 3.1–3.9).

Partial fills accumulate, late fills backfill and CORRECT inferences, terminal
states carry their basis, replays are idempotent, missed windows become visible
gaps, position/cash divergence is explicit, and the attempt sequence survives.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.execution import ledger as led
from ats.memory import get_store
from ats.schemas.memory import TradeLogEntry
from ats.trader import reconcile as rec

NOW = datetime.now(timezone.utc)
CYCLE = "chief-20260923-030000"
CHAIN = {"revision_no": 1, "decision_hash": "a" * 32,
         "approval_id": f"{CYCLE}:r1:approval"}


@pytest.fixture
def store():
    return get_store()


class _FakeBroker:
    def __init__(self, fills=None, completed=None):
        self._fills, self._completed = fills or [], completed or []

    def get_fills(self):
        return self._fills

    def completed_orders(self):
        return self._completed


def _entry(*, status="submitted", symbol="NVDA", qty=10.0, order_seq=0,
           submitted=NOW):
    return TradeLogEntry(order_id="", cycle_id=CYCLE, symbol=symbol, action="buy",
                         qty=qty, status=status, submitted_at=submitted,
                         first_submitted_at=submitted, order_seq=order_seq, **CHAIN)


def _fill(*, exec_id="e1", symbol="NVDA", shares=10.0, price=100.0,
          time=None, order_id="7", pnl=None,
          order_ref=f"ats:{CYCLE}:r1:0:NVDA:buy"):
    return {"exec_id": exec_id, "symbol": symbol, "side": "BOT", "shares": shares,
            "price": price, "time": time or NOW.isoformat(), "realized_pnl": pnl,
            "commission": 1.0, "order_id": order_id, "perm_id": "",
            "order_ref": order_ref}


def _seed(store, *, qty=10.0, symbol="NVDA", submitted=NOW):
    store.save_trades([_entry(symbol=symbol, qty=qty, submitted=submitted)],
                      cycle_id=CYCLE, source="chief")
    # fill matching needs the broker-side ref on the trade row
    store.conn.execute("UPDATE trades SET order_ref = ? WHERE symbol = ?",
                       (f"ats:{CYCLE}:r1:0:{symbol}:buy", symbol))
    store.conn.commit()


def _row(store, symbol="NVDA"):
    return store.conn.execute("SELECT * FROM trades WHERE symbol = ?",
                              (symbol,)).fetchone()


# --------------------------------------------------------------------------- #
# 3.1 — partial fills accumulate; 'filled' only at full quantity
# --------------------------------------------------------------------------- #

def test_two_partial_fills_accumulate_and_stay_partial(store):
    _seed(store, qty=10.0)
    rec.reconcile(_FakeBroker([
        _fill(exec_id="p1", shares=4.0, price=100.0),
        _fill(exec_id="p2", shares=3.0, price=110.0),
    ]), store=store)
    row = _row(store)
    assert row["filled_qty"] == pytest.approx(7.0)
    assert row["status"] == "partial"                    # NOT 'filled'
    assert row["avg_fill_price"] == pytest.approx((4 * 100 + 3 * 110) / 7.0)
    assert row["terminal_basis"] == "broker"


def test_accumulated_fills_close_as_filled_at_full_quantity(store):
    _seed(store, qty=10.0)
    rec.reconcile(_FakeBroker([
        _fill(exec_id="p1", shares=4.0, price=100.0),
        _fill(exec_id="p2", shares=6.0, price=105.0),
    ]), store=store)
    row = _row(store)
    assert row["status"] == "filled"
    assert row["filled_qty"] == pytest.approx(10.0)


def test_pnl_sums_across_partial_fills(store):
    _seed(store)
    rec.reconcile(_FakeBroker([
        _fill(exec_id="p1", shares=4.0, pnl=40.0),
        _fill(exec_id="p2", shares=3.0, pnl=-10.0),
    ]), store=store)
    assert _row(store)["realized_pnl"] == pytest.approx(30.0)


# --------------------------------------------------------------------------- #
# 3.2 / 3.4 — late fills backfill and CORRECT an inferred expiry
# --------------------------------------------------------------------------- #

def test_late_fill_is_flagged_and_corrects_inferred_expiry(store):
    submitted = NOW - timedelta(days=5)
    _seed(store, submitted=submitted)
    # first reconcile: no fills, DAY order gets inferred-expired
    rec.reconcile(_FakeBroker([]), store=store)
    row = _row(store)
    assert row["status"] == "expired"
    assert row["terminal_basis"] == "inferred"
    # days later the fill shows up
    late_fill = _fill(exec_id="late", shares=10.0, time=(NOW - timedelta(days=3)).isoformat())
    rec.reconcile(_FakeBroker([late_fill]), store=store)
    row = _row(store)
    assert row["status"] == "filled"                     # inference corrected
    assert row["terminal_basis"] == "broker"
    flag = store.conn.execute(
        "SELECT late_backfill FROM fills WHERE exec_id='late'").fetchone()
    assert flag["late_backfill"] == 1


def test_a_late_partial_fill_leaves_partial_not_filled(store):
    submitted = NOW - timedelta(days=5)
    _seed(store, submitted=submitted)
    rec.reconcile(_FakeBroker([]), store=store)          # inferred-expired
    rec.reconcile(_FakeBroker([_fill(exec_id="late", shares=4.0,
                                     time=(NOW - timedelta(days=3)).isoformat())]),
                  store=store)
    row = _row(store)
    assert row["status"] == "partial"
    assert row["terminal_basis"] == "broker"


# --------------------------------------------------------------------------- #
# 3.3 — broker-reported terminals carry the broker basis
# --------------------------------------------------------------------------- #

def test_broker_cancel_is_authoritative(store):
    _seed(store)
    completed = [{"perm_id": "", "order_ref": f"ats:{CYCLE}:r1:0:NVDA:buy",
                  "order_id": "7", "symbol": "NVDA", "status": "cancelled",
                  "avg_fill_price": None}]
    rec.reconcile(_FakeBroker([], completed=completed), store=store)
    row = _row(store)
    assert row["status"] == "cancelled"
    assert row["terminal_basis"] == "broker"


# --------------------------------------------------------------------------- #
# 3.5 / 3.6 — replay idempotency (duplicate exec, interrupted run)
# --------------------------------------------------------------------------- #

def test_replaying_the_same_window_changes_nothing(store):
    """Tasks 3.5/3.6: same fills returned twice (or the run interrupted and
    re-run) — one fill row, one order row, same numbers."""
    _seed(store)
    fills = [_fill(exec_id="x1", shares=4.0, pnl=40.0),
             _fill(exec_id="x2", shares=6.0, pnl=-5.0)]
    rec.reconcile(_FakeBroker(fills), store=store)
    snap1 = store.conn.execute(
        "SELECT symbol, status, filled_qty, avg_fill_price, realized_pnl, attempt "
        "FROM trades").fetchall()
    fills_n1 = store.conn.execute("SELECT COUNT(*) n FROM fills").fetchone()["n"]
    rec.reconcile(_FakeBroker(fills), store=store)      # duplicate replay
    snap2 = store.conn.execute(
        "SELECT symbol, status, filled_qty, avg_fill_price, realized_pnl, attempt "
        "FROM trades").fetchall()
    fills_n2 = store.conn.execute("SELECT COUNT(*) n FROM fills").fetchone()["n"]
    assert [tuple(r) for r in snap1] == [tuple(r) for r in snap2]
    assert fills_n1 == fills_n2 == 2
    assert store.conn.execute(
        "SELECT COUNT(*) n FROM ledger_exceptions "
        "WHERE kind='unattributed_fill'").fetchone()["n"] == 0


# --------------------------------------------------------------------------- #
# 3.7 — missed windows are explicit gaps
# --------------------------------------------------------------------------- #

def test_missed_window_becomes_a_visible_gap(store):
    led.register_reconciliation_gap(
        store, window_start="2026-07-23", window_end="2026-07-23",
        reason="scheduler missed the session and reqExecutions no longer "
               "returns that day",
        detail="fills of that session cannot be recovered")
    exc = store.conn.execute(
        "SELECT status, window_start, window_end FROM ledger_exceptions "
        "WHERE kind='reconciliation_gap'").fetchone()
    assert exc["status"] == "open"
    assert exc["window_start"] == "2026-07-23"
    # re-registering the same window does not duplicate
    led.register_reconciliation_gap(store, window_start="2026-07-23",
                                    window_end="2026-07-23", reason="r")
    n = store.conn.execute("SELECT COUNT(*) n FROM ledger_exceptions "
                           "WHERE kind='reconciliation_gap'").fetchone()["n"]
    assert n == 1


# --------------------------------------------------------------------------- #
# 3.8 — position/cash divergence is explicit, neither side is overwritten
# --------------------------------------------------------------------------- #

def test_position_and_cash_divergence_is_registered(store):
    broker_positions = [{"symbol": "NVDA", "qty": 100.0}, {"symbol": "AAPL", "qty": 5.0}]
    local_positions = [{"symbol": "NVDA", "qty": 60.0}]
    subjects = led.compare_holdings(
        store, as_of="2026-09-23T21:00:00+00:00",
        broker_positions=broker_positions, local_positions=local_positions,
        broker_cash=900_000.0, local_cash=900_500.0)
    assert len(subjects) == 3                            # NVDA, AAPL, cash
    detail = store.conn.execute(
        "SELECT detail_json FROM ledger_exceptions "
        "WHERE kind='position_cash_discrepancy' AND symbol='NVDA'").fetchone()
    import json

    d = json.loads(detail["detail_json"])
    assert d["local_qty"] == 60.0 and d["broker_qty"] == 100.0
    # the source rows were never overwritten
    assert local_positions[0]["qty"] == 60.0
    assert broker_positions[0]["qty"] == 100.0


def test_consistent_holdings_register_nothing(store):
    subjects = led.compare_holdings(
        store, as_of="2026-09-23T21:00:00+00:00",
        broker_positions=[{"symbol": "NVDA", "qty": 10.0}],
        local_positions=[{"symbol": "NVDA", "qty": 10.0}],
        broker_cash=1_000.0, local_cash=1_000.0)
    assert subjects == []


# --------------------------------------------------------------------------- #
# 3.9 — the attempt sequence survives the replay
# --------------------------------------------------------------------------- #

def test_attempt_count_survives_replay_and_flags_loss(store):
    """Five retries collapsed into one row (attempt=5): the replay must keep
    the counter; a ledger that lost it gets flagged."""
    for _ in range(5):
        store.save_trades([_entry(status="error")], cycle_id=CYCLE, source="chief")
    entry_id = store.conn.execute(
        "SELECT client_order_id FROM trades").fetchone()["client_order_id"]
    assert store.conn.execute("SELECT attempt FROM trades").fetchone()["attempt"] == 5
    # broker saw two distinct order_ids for this intent — fewer than 5, no flag
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, side, shares, price, time, order_id, "
        "origin, entry_id) VALUES ('f1', 'NVDA', 'BOT', 5, 100, ?, 'o1', "
        "'system', ?)", (NOW.isoformat(), entry_id))
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, side, shares, price, time, order_id, "
        "origin, entry_id) VALUES ('f2', 'NVDA', 'BOT', 5, 100, ?, 'o2', "
        "'system', ?)", (NOW.isoformat(), entry_id))
    store.conn.commit()
    assert led.verify_attempt_counts(store) == []
    # now simulate a trimmed ledger: the counter reset to 1 while the broker
    # evidence shows two submissions
    store.conn.execute("UPDATE trades SET attempt = 1")
    store.conn.commit()
    flagged = led.verify_attempt_counts(store)
    assert len(flagged) == 1
    assert store.conn.execute(
        "SELECT COUNT(*) n FROM ledger_exceptions "
        "WHERE kind='attempt_count_mismatch'").fetchone()["n"] == 1
