"""One intent, one row.

The 2026-07-23 IBKR outage wrote the same GOOG/ASML/KLAC trim FIVE times each —
15 rows from 3 intents — because `trades` had no idempotency key. Retries must
collapse into a single row that counts attempts, without ever losing an execution
fact a previous attempt already learned.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ats.memory import get_store
from ats.schemas.memory import TradeLogEntry

NOW = datetime.now(timezone.utc)
CYCLE = "chief-20260723-140649"


def _entry(*, symbol="GOOG", action="trim", status="error", order_id="",
           avg_fill_price=None, error="", qty=13.0):
    return TradeLogEntry(order_id=order_id, cycle_id=CYCLE, symbol=symbol, action=action,
                         qty=qty, order_type="limit", status=status,
                         avg_fill_price=avg_fill_price, submitted_at=NOW,
                         rationale="trim per scorecard", error=error)


@pytest.fixture
def store():
    return get_store()


def _rows(store, symbol="GOOG"):
    return store.conn.execute(
        "SELECT * FROM trades WHERE symbol = ? ORDER BY rowid", (symbol,)).fetchall()


def test_retries_collapse_to_one_row_with_attempt_count(store):
    """Replay of the real outage: 5 submissions of the same intent -> 1 row."""
    for _ in range(5):
        store.save_trades([_entry(status="error", error="IBKR unavailable")],
                          cycle_id=CYCLE, source="chief")
    rows = _rows(store)
    assert len(rows) == 1
    assert rows[0]["attempt"] == 5
    assert rows[0]["status"] == "error"


def test_distinct_intents_stay_distinct(store):
    store.save_trades([_entry(symbol="GOOG"), _entry(symbol="ASML"), _entry(symbol="KLAC")],
                      cycle_id=CYCLE, source="chief")
    store.save_trades([_entry(symbol="GOOG"), _entry(symbol="ASML"), _entry(symbol="KLAC")],
                      cycle_id=CYCLE, source="chief")
    assert store.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 3
    assert {r["attempt"] for r in store.conn.execute("SELECT attempt FROM trades")} == {2}


def test_same_symbol_different_action_is_a_different_intent(store):
    store.save_trades([_entry(action="trim")], cycle_id=CYCLE, source="chief")
    store.save_trades([_entry(action="buy")], cycle_id=CYCLE, source="chief")
    assert len(_rows(store)) == 2


def test_different_cycles_are_different_intents(store):
    store.save_trades([_entry()], cycle_id="chief-A", source="chief")
    store.save_trades([_entry()], cycle_id="chief-B", source="chief")
    assert len(_rows(store)) == 2


def test_a_later_error_never_erases_an_earlier_fill(store):
    """The dangerous case: attempt 1 filled, attempt 2 errored. The fill must survive."""
    store.save_trades([_entry(status="filled", order_id="4", avg_fill_price=317.07)],
                      cycle_id=CYCLE, source="chief")
    store.save_trades([_entry(status="error", error="IBKR unavailable")],
                      cycle_id=CYCLE, source="chief")
    row = _rows(store)[0]
    assert row["status"] == "filled"
    assert row["avg_fill_price"] == 317.07
    assert row["order_id"] == "4"
    assert row["attempt"] == 2


def test_a_later_fill_is_recorded_over_an_earlier_error(store):
    """The normal retry: first attempt errored, the retry filled."""
    store.save_trades([_entry(status="error", error="IBKR unavailable")],
                      cycle_id=CYCLE, source="chief")
    store.save_trades([_entry(status="filled", order_id="6", avg_fill_price=318.0)],
                      cycle_id=CYCLE, source="chief")
    row = _rows(store)[0]
    assert row["status"] == "filled"
    assert row["avg_fill_price"] == 318.0
    assert row["attempt"] == 2


def test_first_submitted_at_is_preserved(store):
    store.save_trades([_entry()], cycle_id=CYCLE, source="chief")
    first = _rows(store)[0]["first_submitted_at"]
    store.save_trades([_entry()], cycle_id=CYCLE, source="chief")
    assert _rows(store)[0]["first_submitted_at"] == first


def test_client_order_id_is_deterministic_and_case_normalised(store):
    a = store.client_order_id("c1", "goog", "trim")
    b = store.client_order_id("c1", "GOOG", "trim")
    assert a == b == "c1:GOOG:trim"


def test_legacy_rows_without_the_key_coexist(store):
    """The 52 pre-existing rows have no client_order_id; NULLs are distinct in a
    SQLite unique index, so the constraint must not reject them."""
    for i in range(3):
        store.conn.execute(
            "INSERT INTO trades (order_id, cycle_id, symbol, action, qty, status) "
            "VALUES (?,?,?,?,?,?)", (str(i), "legacy", "GOOG", "trim", 1.0, "error"))
    store.conn.commit()
    assert store.conn.execute(
        "SELECT COUNT(*) FROM trades WHERE client_order_id IS NULL").fetchone()[0] == 3
