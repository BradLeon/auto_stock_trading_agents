"""Phase C group 4: the Clerk orchestration entry (§11, tasks 4.1–4.5)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.execution import clerk
from ats.memory import get_store
from ats.schemas.memory import TradeLogEntry

NOW = datetime.now(timezone.utc)
CYCLE = "chief-20260923-040000"
CHAIN = {"revision_no": 1, "decision_hash": "a" * 32,
         "approval_id": f"{CYCLE}:r1:approval"}


@pytest.fixture
def store():
    return get_store()


class _FakeBroker:
    """A broker that HAS fills to reconcile and a portfolio for marks."""

    def __init__(self, fills=None):
        self._fills = fills or []

    def get_fills(self):
        return self._fills

    def completed_orders(self):
        return []

    def get_portfolio(self):
        class _P:
            positions = []

        return _P()


def _seed_system_order(store, *, symbol="NVDA", qty=10.0):
    store.save_trades([TradeLogEntry(
        order_id="", cycle_id=CYCLE, symbol=symbol, action="buy", qty=qty,
        status="submitted", submitted_at=NOW, order_seq=0, **CHAIN)],
        cycle_id=CYCLE, source="chief")
    ref = f"ats:{CYCLE}:r1:0:{symbol}:buy"
    store.conn.execute("UPDATE trades SET order_ref = ? WHERE symbol = ?",
                       (ref, symbol))
    store.conn.commit()
    return ref


def _fill(exec_id, symbol="NVDA", shares=10.0, ref=None):
    return {"exec_id": exec_id, "symbol": symbol, "side": "BOT", "shares": shares,
            "price": 100.0, "time": NOW.isoformat(), "realized_pnl": None,
            "commission": 1.0, "order_id": "7", "perm_id": "",
            "order_ref": ref or f"ats:{CYCLE}:r1:0:{symbol}:buy"}


# --------------------------------------------------------------------------- #
# 4.1 / 4.2 — one idempotent pipeline over the existing deterministic steps
# --------------------------------------------------------------------------- #

def test_clerk_run_chains_all_steps_and_leaves_one_trail(store):
    _seed_system_order(store)
    out = clerk.clerk_run(store=store, broker=_FakeBroker([_fill("f1")]))
    assert out["status"] == "completed" and not out["reused"]
    assert set(out["steps"]) == set(clerk.DEFAULT_STEPS)
    # one trail row, window-keyed
    trail = store.conn.execute("SELECT * FROM clerk_runs").fetchall()
    assert len(trail) == 1 and trail[0]["status"] == "completed"
    assert trail[0]["kind"] == "full"
    # the fill actually got attributed by the pipeline
    assert store.conn.execute(
        "SELECT origin FROM fills").fetchone()["origin"] == "system"


def test_same_window_rerun_is_reused_without_new_facts(store):
    _seed_system_order(store)
    out1 = clerk.clerk_run(store=store, broker=_FakeBroker([_fill("f1")]))
    assert out1["status"] == "completed"
    before = store.conn.execute("SELECT COUNT(*) n FROM clerk_runs").fetchone()["n"]
    out2 = clerk.clerk_run(store=store, broker=_FakeBroker([_fill("f1")]))
    assert out2["reused"] is True
    after = store.conn.execute("SELECT COUNT(*) n FROM clerk_runs").fetchone()["n"]
    assert before == after == 1
    assert store.conn.execute("SELECT COUNT(*) n FROM fills").fetchone()["n"] == 1


def test_different_window_is_a_new_run(store):
    _seed_system_order(store)
    clerk.clerk_run(store=store, broker=_FakeBroker([_fill("f1")]))
    out = clerk.clerk_run(store=store, broker=_FakeBroker([]),
                          window_start="2026-09-22", window_end="2026-09-22",
                          as_of="2026-09-22")
    assert out["reused"] is False
    assert store.conn.execute("SELECT COUNT(*) n FROM clerk_runs").fetchone()["n"] == 2


# --------------------------------------------------------------------------- #
# 4.3 — the Clerk never rewrites domain facts
# --------------------------------------------------------------------------- #

def test_clerk_run_leaves_domain_facts_byte_identical(store):
    _seed_system_order(store)
    # an error attempt first (attempt counting is a domain fact too)
    store.save_trades([TradeLogEntry(
        order_id="", cycle_id=CYCLE, symbol="NVDA", action="buy", qty=10.0,
        status="error", submitted_at=NOW, order_seq=0, error="IBKR down",
        **CHAIN)], cycle_id=CYCLE, source="chief")
    snap = clerk.protected_trade_snapshot(store)
    out = clerk.clerk_run(store=store, broker=_FakeBroker([_fill("f1")]))
    assert out["status"] == "completed"
    assert clerk.protected_trade_snapshot(store) == snap


# --------------------------------------------------------------------------- #
# 3.7 wiring — broker unavailable + uncovered windows become gaps
# --------------------------------------------------------------------------- #

def test_broker_unavailable_registers_missed_window_gaps(store, monkeypatch):
    _seed_system_order(store)
    # a fill from an earlier, never-reconciled session sits in the ledger
    old = (NOW - timedelta(days=10)).isoformat()
    store.conn.execute(
        "INSERT INTO fills (exec_id, symbol, side, shares, price, time) "
        "VALUES ('old', 'AAPL', 'BOT', 5, 100, ?)", (old,))
    store.conn.commit()
    # hermetic: no live TWS connection attempts
    def _no_broker(*a, **k):
        raise RuntimeError("broker unavailable: test")

    import ats.trader.execute as texec
    monkeypatch.setattr(texec, "IBKRBroker", _no_broker)
    out = clerk.clerk_run(store=store, broker=None,
                          window_start="2026-09-23", window_end="2026-09-23")
    assert out["gaps_registered"] >= 1
    gap = store.conn.execute(
        "SELECT window_start FROM ledger_exceptions "
        "WHERE kind='reconciliation_gap'").fetchone()
    assert gap is not None
    assert gap["window_start"] == old[:10]


# --------------------------------------------------------------------------- #
# 4.5 — derived computation skips when the facts hash matches
# --------------------------------------------------------------------------- #

def test_source_facts_hash_is_stable_and_read_model_hit_skips(store):
    _seed_system_order(store)
    h1 = clerk.source_facts_hash(store)
    assert h1 == clerk.source_facts_hash(store)
    assert clerk.read_model_hit(store, kind="performance", period="2026-09",
                                method_version="v1", facts_hash=h1) is False
    store.conn.execute(
        "INSERT INTO ledger_read_models (model_id, kind, period, method_version, "
        "source_facts_hash, payload, rebuilt_at) VALUES ('m1', 'performance', "
        "'2026-09', 'v1', ?, '{}', ?)", (h1, NOW.isoformat()))
    store.conn.commit()
    assert clerk.read_model_hit(store, kind="performance", period="2026-09",
                                method_version="v1", facts_hash=h1) is True
    # facts changed → the cached model no longer matches
    store.conn.execute("UPDATE trades SET realized_pnl = 12.0")
    store.conn.commit()
    h2 = clerk.source_facts_hash(store)
    assert h2 != h1
    assert clerk.read_model_hit(store, kind="performance", period="2026-09",
                                method_version="v1", facts_hash=h2) is False


def test_failed_step_is_recorded_and_run_marked_failed(store, monkeypatch):
    _seed_system_order(store)

    def _boom(store, *, broker, dry_run):
        raise RuntimeError("marks exploded")

    monkeypatch.setitem(clerk._STEPS, "marks", _boom)
    out = clerk.clerk_run(store=store, broker=_FakeBroker([_fill("f1")]))
    assert out["status"] == "failed"
    assert any("marks" in e for e in out["errors"])
    trail = store.conn.execute(
        "SELECT status, error FROM clerk_runs").fetchone()
    assert trail["status"] == "failed" and "marks" in trail["error"]
    # the other steps still ran
    assert "reconcile" in out["steps"]
