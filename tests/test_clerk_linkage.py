"""Phase C group 2: decision-chain enforcement, three-way attribution, and
the origin split (§11.1; tasks 2.1–2.5)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.memory import get_store
from ats.memory.store import MissingDecisionChainError
from ats.schemas.memory import TradeLogEntry
from ats.trader import reconcile as rec

NOW = datetime.now(timezone.utc)
CYCLE = "chief-20260923-000000"
CHAIN = {"revision_no": 1, "decision_hash": "a" * 32,
         "approval_id": f"{CYCLE}:r1:approval"}


@pytest.fixture
def store():
    return get_store()


def _entry(*, status="submitted", symbol="NVDA", **overrides):
    fields = dict(cycle_id=CYCLE, symbol=symbol, action="buy", qty=10.0,
                  status=status, submitted_at=NOW, **CHAIN)
    fields.update(overrides)
    return TradeLogEntry(order_id="", **fields)


# --------------------------------------------------------------------------- #
# 2.1 — chain-less system submissions are refused AND recorded
# --------------------------------------------------------------------------- #

def test_chainless_system_submission_is_refused_and_recorded(store):
    with pytest.raises(MissingDecisionChainError):
        store.save_trades([_entry(decision_hash="")], cycle_id=CYCLE, source="chief")
    # ...and the refusal left an explicit audit exception (not a silent drop)
    exc = store.conn.execute(
        "SELECT kind, status, detail_json FROM ledger_exceptions "
        "WHERE kind='broken_link' AND subject_key LIKE '%NVDA%'").fetchone()
    assert exc is not None and exc["status"] == "open"
    assert "decision_hash" in exc["detail_json"]
    # the write itself was refused
    n = store.conn.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"]
    assert n == 0


def test_missing_approval_id_is_refused_too(store):
    with pytest.raises(MissingDecisionChainError):
        store.save_trades([_entry(approval_id="")], cycle_id=CYCLE, source="chief")


def test_non_system_source_stays_exempt(store):
    """Standalone/manual bookkeeping writes are not system submissions."""
    store.save_trades([_entry(decision_hash="")], cycle_id=CYCLE, source="standalone")
    assert store.conn.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"] == 1
    assert store.conn.execute(
        "SELECT COUNT(*) n FROM ledger_exceptions").fetchone()["n"] == 0


def test_cancelled_and_rejected_entries_are_exempt(store):
    """They never reach the broker — there is nothing for a chain to authorize."""
    store.save_trades([_entry(status="cancelled", decision_hash=""),
                       _entry(status="rejected", approval_id="", symbol="MSFT")],
                      cycle_id=CYCLE, source="chief")
    assert store.conn.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"] == 2
    assert store.conn.execute(
        "SELECT COUNT(*) n FROM ledger_exceptions").fetchone()["n"] == 0


def test_fully_chained_system_submission_writes_cleanly(store):
    store.save_trades([_entry()], cycle_id=CYCLE, source="chief")
    row = store.conn.execute("SELECT * FROM trades").fetchone()
    assert row["decision_hash"] == CHAIN["decision_hash"]
    assert row["approval_id"] == CHAIN["approval_id"]
    assert row["revision_no"] == 1
    assert store.conn.execute(
        "SELECT COUNT(*) n FROM ledger_exceptions").fetchone()["n"] == 0


# --------------------------------------------------------------------------- #
# 2.2 — a matched fill inherits its order's full chain
# --------------------------------------------------------------------------- #

class _FakeBroker:
    def __init__(self, fills):
        self._fills = fills

    def get_fills(self):
        return self._fills

    def completed_orders(self):
        return []


def _seed_system_order(store):
    ref = f"ats:{CYCLE}:r1:0:NVDA:buy"
    store.save_trades([_entry(status="submitted", symbol="NVDA", order_ref=ref)],
                      cycle_id=CYCLE, source="chief")
    return store.conn.execute("SELECT client_order_id FROM trades").fetchone()["client_order_id"]


def test_matched_fill_inherits_full_decision_chain(store):
    _seed_system_order(store)
    fill = {"exec_id": "e9", "symbol": "NVDA", "side": "BOT", "shares": 10,
            "price": 100.0, "time": NOW.isoformat(), "realized_pnl": None,
            "commission": 1.0, "order_id": "",
            "order_ref": store.conn.execute(
                "SELECT order_ref FROM trades").fetchone()[0], "perm_id": ""}
    rec.reconcile(_FakeBroker([fill]), store=store)
    row = store.conn.execute("SELECT * FROM fills").fetchone()
    assert row["origin"] == "system"
    assert row["cycle_id"] == CYCLE
    assert row["revision_no"] == 1
    assert row["decision_hash"] == CHAIN["decision_hash"]
    assert row["approval_id"] == CHAIN["approval_id"]


# --------------------------------------------------------------------------- #
# 2.3 — pre-Phase-C system orders become explicit legacy gaps
# --------------------------------------------------------------------------- #

def test_legacy_chainless_system_orders_are_registered_as_gaps(store):
    """A direct INSERT bypasses the write-path check (it IS the legacy path),
    so the one-time migration must catch those rows and register gaps —
    without fabricating a chain (linkage columns stay empty)."""
    conn = store.conn
    conn.execute(
        "INSERT INTO trades (cycle_id, symbol, action, qty, status, source, "
        "submitted_at) VALUES (?, 'MSFT', 'trim', 5.0, 'filled', 'chief', ?)",
        (CYCLE, NOW.isoformat()))
    conn.commit()
    # A fresh test DB ran the backfill at __init__ (finding nothing) and burned
    # its one-time key; simulate the real pre-Phase-C state by re-arming it.
    conn.execute("DELETE FROM data_migrations WHERE key='ledger_legacy_gaps_v1'")
    conn.commit()
    store._register_legacy_link_gaps()
    exc = store.conn.execute(
        "SELECT kind, basis FROM ledger_exceptions WHERE kind='broken_link' "
        "AND subject_key LIKE '%MSFT%'").fetchone()
    assert exc is not None
    row = conn.execute("SELECT cycle_id, revision_no, decision_hash FROM trades "
                       "WHERE symbol='MSFT'").fetchone()
    assert row["decision_hash"] is None          # honest gap, not fabricated
    # one-time: re-running does not duplicate
    store._register_legacy_link_gaps()
    n = conn.execute("SELECT COUNT(*) n FROM ledger_exceptions "
                     "WHERE kind='broken_link'").fetchone()["n"]
    assert n == 1


# --------------------------------------------------------------------------- #
# 2.4 / 2.5 — three-way attribution and the origin split
# --------------------------------------------------------------------------- #

def test_manual_order_ref_is_manual_without_exception(store):
    """A human-typed orderRef in TWS is EVIDENCE of a manual order — recorded
    as manual, no exception."""
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, side, shares, price, time) "
        "VALUES ('m1', 'AAPL', 'BOT', 5, 100, ?)", (NOW.isoformat(),))
    store.conn.commit()
    rec.reconcile(_FakeBroker([{"exec_id": "m1", "symbol": "AAPL", "side": "BOT",
                                "shares": 5, "price": 100.0,
                                "time": NOW.isoformat(), "realized_pnl": None,
                                "commission": 1.0, "order_id": "",
                                "perm_id": "", "order_ref": "my-weekend-trade"}]),
                  store=store)
    row = store.conn.execute("SELECT origin FROM fills").fetchone()
    assert row["origin"] == "manual"
    assert store.conn.execute(
        "SELECT COUNT(*) n FROM ledger_exceptions").fetchone()["n"] == 0


def test_ats_prefixed_ref_that_matches_nothing_is_unattributed(store):
    """An ats: ref with no local order is a LOST system order — unknown, not
    manual (task 2.4)."""
    fill = {"exec_id": "lost", "symbol": "GOOG", "side": "SLD", "shares": 3,
            "price": 100.0, "time": NOW.isoformat(), "realized_pnl": None,
            "commission": 1.0, "order_id": "", "perm_id": "",
            "order_ref": "ats:cX:r1:0:GOOG:trim"}
    rec.reconcile(_FakeBroker([fill]), store=store)
    assert store.conn.execute(
        "SELECT origin FROM fills").fetchone()[0] == "unattributed"
    assert store.conn.execute(
        "SELECT COUNT(*) n FROM ledger_exceptions "
        "WHERE kind='unattributed_fill'").fetchone()["n"] == 1


def test_repeated_discovery_refreshes_last_seen_without_duplicating(store):
    fill = {"exec_id": "dup", "symbol": "GOOG", "side": "SLD", "shares": 3,
            "price": 100.0, "time": NOW.isoformat(), "realized_pnl": None,
            "commission": 1.0, "order_id": "", "perm_id": "", "order_ref": ""}
    rec.reconcile(_FakeBroker([fill]), store=store)
    rec.reconcile(_FakeBroker([fill]), store=store)
    n = store.conn.execute(
        "SELECT COUNT(*) n FROM ledger_exceptions WHERE kind='unattributed_fill'"
    ).fetchone()["n"]
    assert n == 1                                # same fact seen twice, not two facts


def test_split_by_origin_separates_the_three_classes():
    from ats.execution.ledger import split_by_origin, system_only

    rows = [
        {"symbol": "A", "origin": "system", "realized_pnl": 10.0},
        {"symbol": "B", "origin": "manual", "realized_pnl": -5.0},
        {"symbol": "C", "origin": "unattributed", "realized_pnl": 99.0},
        {"symbol": "D", "origin": None, "realized_pnl": 1.0},   # no evidence
    ]
    split = split_by_origin(rows)
    assert [r["symbol"] for r in split["system"]] == ["A"]
    assert [r["symbol"] for r in split["manual"]] == ["B"]
    assert [r["symbol"] for r in split["unattributed"]] == ["C", "D"]
    # Task 2.5: only system rows may enter system performance.
    assert [r["symbol"] for r in system_only(rows)] == ["A"]
