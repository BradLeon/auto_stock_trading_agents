"""Tag outgoing orders so their executions can be recognised as ours.

`reqExecutions` returns the whole ACCOUNT's fills, so it also carries orders placed by
hand in TWS. Without a tag the only link is orderId — a per-client sequence TWS resets,
which therefore cannot be joined on across sessions. IBKR echoes `orderRef` back on
every execution and assigns a permanent `permId` at acknowledgement; both are durable.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ats.broker.ibkr import order_ref
from ats.memory import get_store
from ats.schemas.memory import TradeLogEntry
from ats.trader import reconcile as rec

NOW = datetime.now(timezone.utc)


def test_order_ref_format_and_prefix():
    ref = order_ref("chief-20260728-200500", "klac")
    assert ref == "ats:chief-20260728-200500:KLAC"
    assert ref.startswith("ats:")          # the system-vs-manual discriminator


def test_order_ref_fits_ibkr_field():
    """IBKR silently truncates a long orderRef; the submit path caps at 60."""
    ref = order_ref("chief-20260728-200500", "GOOG")
    assert len(ref) <= 60


def test_entry_carries_the_identities_to_the_store():
    store = get_store()
    e = TradeLogEntry(order_id="12", cycle_id="c1", symbol="GOOG", action="trim",
                      qty=13.0, status="submitted", submitted_at=NOW,
                      perm_id="998877", order_ref="ats:c1:GOOG")
    store.save_trades([e], cycle_id="c1", source="chief")
    row = store.conn.execute("SELECT perm_id, order_ref FROM trades").fetchone()
    assert row["perm_id"] == "998877"
    assert row["order_ref"] == "ats:c1:GOOG"


def test_a_retry_does_not_lose_the_identities():
    """The first attempt may be the only one that reached IBKR and got a permId."""
    store = get_store()
    tagged = TradeLogEntry(order_id="12", cycle_id="c1", symbol="GOOG", action="trim",
                           qty=13.0, status="submitted", submitted_at=NOW,
                           perm_id="998877", order_ref="ats:c1:GOOG")
    untagged = TradeLogEntry(order_id="", cycle_id="c1", symbol="GOOG", action="trim",
                             qty=13.0, status="error", submitted_at=NOW,
                             error="IBKR unavailable")
    store.save_trades([tagged], cycle_id="c1", source="chief")
    store.save_trades([untagged], cycle_id="c1", source="chief")
    row = store.conn.execute("SELECT perm_id, order_ref, attempt FROM trades").fetchone()
    assert row["perm_id"] == "998877"
    assert row["order_ref"] == "ats:c1:GOOG"
    assert row["attempt"] == 2


# --------------------------------------------------------------------------- #
# What the tags buy us at reconcile time
# --------------------------------------------------------------------------- #
class FakeBroker:
    def __init__(self, fills):
        self._f = fills

    def get_fills(self):
        return self._f

    def completed_orders(self):
        return []


def _fill(**kw):
    base = {"exec_id": "e1", "symbol": "GOOG", "side": "SLD", "shares": 10.0,
            "price": 317.07, "time": NOW.isoformat(), "realized_pnl": 495.19,
            "commission": 1.0, "order_id": "", "perm_id": "", "order_ref": ""}
    return {**base, **kw}


def test_tagged_fill_links_without_any_date_heuristic():
    """An order from a month ago still links — the whole point of a durable id."""
    store = get_store()
    old = (NOW.replace(year=NOW.year - 1)).isoformat()
    store.conn.execute(
        "INSERT INTO trades (order_id, cycle_id, symbol, action, qty, status, "
        "submitted_at, first_submitted_at, order_ref, client_order_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("", "c1", "GOOG", "trim", 13.0, "submitted", old, old, "ats:c1:GOOG", "k1"))
    store.conn.commit()

    rec.reconcile(FakeBroker([_fill(order_ref="ats:c1:GOOG")]), store=store)
    row = store.conn.execute("SELECT realized_pnl FROM trades").fetchone()
    assert row["realized_pnl"] == pytest.approx(495.19)
    assert store.conn.execute(
        "SELECT link_confidence FROM fills").fetchone()[0] == "order_ref"


def test_untagged_manual_fill_is_still_rejected():
    """A fill with no ats: tag and no matching order stays manual."""
    store = get_store()
    rec.reconcile(FakeBroker([_fill(exec_id="m", symbol="SPCX", order_id="34",
                                    order_ref="", perm_id="")]), store=store)
    assert store.conn.execute("SELECT origin FROM fills").fetchone()[0] == "manual"
