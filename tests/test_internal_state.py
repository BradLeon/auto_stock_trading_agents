"""Phase C group 6: Internal State API + consumer migration (tasks 6.1–6.7)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.execution import state_api
from ats.memory import get_store
from ats.schemas.memory import TradeLogEntry

NOW = datetime.now(timezone.utc)
CYCLE = "chief-20260923-060000"
CHAIN = {"revision_no": 1, "decision_hash": "a" * 32,
         "approval_id": f"{CYCLE}:r1:approval"}


@pytest.fixture
def store():
    return get_store()


def _seed(store, *, symbol="NVDA", pnl=120.0):
    store.save_trades([TradeLogEntry(
        order_id="", cycle_id=CYCLE, symbol=symbol, action="buy", qty=10.0,
        status="filled", submitted_at=NOW, avg_fill_price=100.0,
        realized_pnl=pnl, order_seq=0, **CHAIN)],
        cycle_id=CYCLE, source="chief")
    store.conn.execute("UPDATE trades SET realized_pnl = ? WHERE symbol = ?",
                       (pnl, symbol))
    store.conn.commit()


# --------------------------------------------------------------------------- #
# 6.1 / 6.2 — the published state carries as-of, all sections, completeness
# --------------------------------------------------------------------------- #

def test_internal_state_carries_all_sections_and_as_of(store):
    from ats.schemas.memory import PerformanceRecord

    _seed(store)
    # portfolio section: seed a performance snapshot (hermetic — no TWS)
    rec = PerformanceRecord(cycle_id=CYCLE, as_of=NOW, net_liquidation=1_000_000.0,
                            daily_pnl=5.0, cumulative_pnl=100.0)
    store.conn.execute(
        "INSERT INTO performance (cycle_id, as_of, net_liquidation, daily_pnl, "
        "cumulative_pnl, payload) VALUES (?,?,?,?,?,?)",
        (CYCLE, NOW.isoformat(), rec.net_liquidation, rec.daily_pnl,
         rec.cumulative_pnl, rec.model_dump_json()))
    store.conn.commit()
    state = state_api.get_internal_state(store)
    assert state.as_of is not None
    assert state.trades and state.fills is not None
    assert state.portfolio["net_liquidation"] == 1_000_000.0
    assert "win_rate" in state.performance
    assert state.completeness.status == "complete"


def test_completeness_counts_each_gap_kind(store):
    store.record_ledger_exception(kind="reconciliation_gap",
                                  subject_key="2026-07-23", window_start="2026-07-23")
    store.record_ledger_exception(kind="unattributed_fill", subject_key="fill:x1")
    store.record_ledger_exception(kind="broken_link", subject_key="order:o1")
    comp = state_api.completeness(store)
    assert comp.status == "degraded"
    assert comp.unreconciled_windows == 1
    assert comp.unattributed_fills == 1
    assert comp.broken_links == 1


def test_resolved_gaps_restore_complete_status(store):
    store.record_ledger_exception(kind="unattributed_fill", subject_key="fill:x2")
    assert state_api.completeness(store).status == "degraded"
    store.conn.execute(
        "UPDATE ledger_exceptions SET status='resolved' WHERE subject_key='fill:x2'")
    store.conn.commit()
    assert state_api.completeness(store).status == "complete"


# --------------------------------------------------------------------------- #
# 6.3 — a degraded state is NEVER presented as complete
# --------------------------------------------------------------------------- #

def test_degraded_state_is_marked_in_the_published_state(store):
    _seed(store)
    store.record_ledger_exception(kind="unattributed_fill", subject_key="fill:x3")
    state = state_api.get_internal_state(store)
    assert state.completeness.status == "degraded"
    assert state.completeness.unattributed_fills >= 1


# --------------------------------------------------------------------------- #
# 6.4 / 6.6 — consumer migration: double-read comparison, then switch
# --------------------------------------------------------------------------- #

def test_state_api_trade_read_matches_the_old_direct_read(store):
    """The double-read stage of the migration: the State API view and the
    retired direct read return identical rows."""
    _seed(store, symbol="NVDA")
    _seed(store, symbol="MSFT", pnl=10.0)
    api = state_api.recent_trades(store, "NVDA", limit=8)
    direct = [dict(r) for r in store.recent_trades("NVDA", limit=8)]
    assert api == direct
    api_fills = state_api.recent_fills(store, limit=5)
    direct_fills = [dict(r) for r in store.recent_fills(limit=5)]
    assert api_fills == direct_fills


def test_chief_assembler_reads_via_state_api(store):
    from ats.agents.chief import assemble

    src = open(assemble.__file__).read()
    assert "state_api.recent_trades" in src
    assert "state_api.recent_fills" in src


def test_context_reader_reads_via_state_api(store):
    from ats.channel import context

    src = open(context.__file__).read()
    assert "state_api.recent_trades" in src
    # the completeness marker is part of the published bundle
    _seed(store)
    bundle = context.build_report_bundle("NVDA")
    assert "Ledger: complete" in bundle.summary


def test_context_report_shows_degraded_ledger(store):
    from ats.channel import context

    store.record_ledger_exception(kind="unattributed_fill", subject_key="fill:x9")
    bundle = context.build_report_bundle("NVDA")
    assert "Ledger: degraded" in bundle.summary


# --------------------------------------------------------------------------- #
# 6.5 — Risk reads via the API and degrades explicitly
# --------------------------------------------------------------------------- #

def test_risk_assess_performance_history_via_state_api(store):
    from ats.risk import assess

    src = open(assess.__file__).read()
    assert "state_api.performance_history" in src
    assert "state_api.completeness" in src


def test_performance_history_matches_direct_read(store):
    rows = state_api.performance_history(store, limit=250)
    direct = store.performance_history(limit=250)
    assert list(rows) == list(direct)


# --------------------------------------------------------------------------- #
# 6.7 — the retired direct reads are registered in the tombstone registry
# --------------------------------------------------------------------------- #

def test_direct_trade_reads_registered_as_pending():
    from ats.workflow import legacy_retirement as mod

    reg = mod.load_registry()
    ts = reg.tombstone("store.direct_trade_reads")
    assert ts is not None and ts.status == "pending"
    assert "risk.assess" in ts.consumers
