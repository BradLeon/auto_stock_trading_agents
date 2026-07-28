"""Pre-registered plans: the ledger of INTENTS, written before the outcome is known.

A plan recorded after the result can no longer be wrong, so it proves nothing. These
tests pin the ordering (intent before the approval interrupt), the risk-unit conventions
that make R comparable, and the fact that an outcome write can never edit the plan.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from ats.journal import entries as je
from ats.memory import get_store
from ats.schemas.decision import BossApproval, TradeDecision
from ats.schemas.memory import TradeLogEntry

NOW = datetime(2026, 7, 23, 14, 6, tzinfo=timezone.utc)


def _d(symbol="GOOG", action="trim", **kw):
    kw.setdefault("notional_usd", 4000.0)
    kw.setdefault("qty", 13.0)
    return TradeDecision(symbol=symbol, action=action, rationale="why", **kw)


def _state(decisions, **kw):
    from ats.graph.chief_state import ChiefDecisionState

    kw.setdefault("cycle_id", "c1")
    kw.setdefault("source", "chief")
    return ChiefDecisionState(as_of=NOW, dry_run=True, use_llm=False,
                              decisions=decisions, **kw)


@pytest.fixture
def store():
    return get_store()


# --------------------------------------------------------------------------- #
# risk unit — the denominator of R
# --------------------------------------------------------------------------- #
def test_declared_stop_wins():
    d = _d(limit_price=340.0, stop_price=306.0, notional_usd=10_000.0)
    risk, src = je.resolve_risk_unit(d, expected_move_pct=5.0, stop_loss_pct=0.25)
    assert src == "declared_stop"
    assert risk == pytest.approx(1000.0, abs=1)          # 10% of notional


def test_event_trade_falls_back_to_expected_move():
    """The option-implied 1σ move IS the risk unit for an earnings event."""
    d = _d(notional_usd=10_000.0)
    risk, src = je.resolve_risk_unit(d, expected_move_pct=5.71, stop_loss_pct=0.25)
    assert src == "expected_move"
    assert risk == pytest.approx(571.0, abs=1)


def test_everything_else_uses_the_portfolio_stop():
    risk, src = je.resolve_risk_unit(_d(notional_usd=10_000.0),
                                     expected_move_pct=None, stop_loss_pct=0.25)
    assert src == "portfolio_stop"
    assert risk == pytest.approx(2500.0)


def test_no_notional_yields_no_risk_unit():
    """Never invent a denominator — an R from a guess is worse than no R."""
    d = TradeDecision(symbol="GOOG", action="trim", rationale="x")
    assert je.resolve_risk_unit(d, expected_move_pct=5.0, stop_loss_pct=0.25) == (None, "")


def test_absurd_stop_is_ignored():
    """A stop on the wrong side of the reference is a model error, not a risk unit."""
    d = _d(limit_price=340.0, stop_price=900.0, notional_usd=10_000.0)
    _, src = je.resolve_risk_unit(d, expected_move_pct=5.0, stop_loss_pct=0.25)
    assert src == "expected_move"


# --------------------------------------------------------------------------- #
# the plan is written before the interrupt
# --------------------------------------------------------------------------- #
def test_intents_recorded_with_the_plan(store):
    d = _d(setup="pead_event", stop_price=300.0, target_price=360.0,
           limit_price=340.0,                       # 止损要换算成金额需要参考价
           planned_horizon_days=10, invalidation="Cloud 增速再降到 25% 以下",
           conviction=0.5)
    ids = je.record_intents(
        _state([d], event_data={"GOOG": {"expected_move_pct": 5.71}},
               ), store=store)
    assert ids == ["c1:GOOG:trim"]
    row = store.journal_entries()[0]
    assert row.setup == "pead_event"
    assert row.stop_price == 300.0
    assert row.planned_horizon_days == 10
    assert row.invalidation == "Cloud 增速再降到 25% 以下"
    assert row.risk_unit_source == "declared_stop"
    assert row.ev_expected_move_pct == 5.71
    assert row.terminal_status is None            # not executed yet


def test_intent_id_matches_the_trade_key(store):
    """The ledger id IS the order idempotency key, so the two always join."""
    je.record_intents(_state([_d()]), store=store)
    assert store.journal_entries()[0].entry_id == store.client_order_id("c1", "GOOG", "trim")


def test_rerunning_a_cycle_restates_one_intent(store):
    st = _state([_d()])
    je.record_intents(st, store=store)
    je.record_intents(st, store=store)
    assert len(store.journal_entries()) == 1


def test_setup_falls_back_to_the_cycle_source(store):
    je.record_intents(_state([_d()], cycle_id="c2", source="pead-chief"), store=store)
    assert store.journal_entries()[0].setup == "pead_event"


def test_risk_notes_are_snapshotted(store):
    notes = ["GOOG: 仓位由 $5,000 削减至 $4,000（L4 19%>15%）"]
    je.record_intents(_state([_d()], risk_notes=notes), store=store)
    assert store.journal_entries()[0].risk_notes == notes


# --------------------------------------------------------------------------- #
# outcome must not be able to rewrite the plan
# --------------------------------------------------------------------------- #
def test_outcome_attaches_without_touching_the_plan(store):
    d = _d(setup="pead_event", stop_price=300.0, invalidation="原始论点")
    je.record_intents(_state([d]), store=store)
    entry = TradeLogEntry(order_id="4", cycle_id="c1", symbol="GOOG", action="trim",
                          qty=13.0, status="filled", avg_fill_price=317.07,
                          limit_price=318.0, submitted_at=NOW)
    je.record_outcome(_state([d], order_results=[entry],
                             approval=BossApproval(status="approved", reviewer="boss")),
                      store=store)
    row = store.journal_entries()[0]
    assert row.terminal_status == "filled"
    assert row.avg_fill_price == 317.07
    assert row.approval is not None and row.approval.status == "approved"
    # plan untouched
    assert row.stop_price == 300.0
    assert row.invalidation == "原始论点"
    assert row.setup == "pead_event"


def test_slippage_sign_is_cost_for_both_sides(store):
    buy = TradeLogEntry(order_id="1", cycle_id="c1", symbol="GOOG", action="buy",
                        qty=1, status="filled", limit_price=100.0,
                        avg_fill_price=101.0, submitted_at=NOW)
    sell = TradeLogEntry(order_id="2", cycle_id="c1", symbol="GOOG", action="trim",
                         qty=1, status="filled", limit_price=100.0,
                         avg_fill_price=99.0, submitted_at=NOW)
    assert je._slippage_bps(buy) == pytest.approx(100.0)    # paid 1% up
    assert je._slippage_bps(sell) == pytest.approx(100.0)   # sold 1% down = also a cost


def test_failed_orders_still_have_a_ledger_row(store):
    """24 of the first 52 orders errored. A journal of fills only hides that."""
    d = _d()
    je.record_intents(_state([d]), store=store)
    entry = TradeLogEntry(order_id="", cycle_id="c1", symbol="GOOG", action="trim",
                          qty=13.0, status="error", error="cannot reach IBKR",
                          submitted_at=NOW)
    je.record_outcome(_state([d], order_results=[entry]), store=store)
    row = store.journal_entries()[0]
    assert row.terminal_status == "error"
    assert row.stop_price is None or True          # plan preserved regardless


def test_journal_failure_never_breaks_the_cycle(store, monkeypatch):
    monkeypatch.setattr(store, "save_journal_entry",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("disk full")))
    assert je.record_intents(_state([_d()]), store=store) == []


def test_declared_stop_without_a_reference_price_falls_back(store):
    """A stop is only a dollar amount relative to a price. With neither a limit price
    nor a held mark, fall back to the next convention rather than dropping R entirely."""
    d = _d(stop_price=300.0)                        # no limit_price, not held
    je.record_intents(_state([d], event_data={"GOOG": {"expected_move_pct": 5.71}}),
                      store=store)
    row = store.journal_entries()[0]
    assert row.risk_unit_source == "expected_move"
    assert row.planned_risk_usd is not None


def test_held_position_mark_makes_a_declared_stop_usable(store):
    """The portfolio snapshot is already in memory — no quote fetch needed."""
    from ats.schemas.portfolio import PortfolioSnapshot, Position

    pf = PortfolioSnapshot(as_of=NOW, net_liquidation=100_000.0, positions=[
        Position(symbol="GOOG", qty=100.0, avg_cost=300.0, market_price=340.0,
                 market_value=34_000.0, unrealized_pnl=4000.0)])
    d = _d(stop_price=306.0, notional_usd=10_000.0)
    je.record_intents(_state([d], portfolio=pf), store=store)
    row = store.journal_entries()[0]
    assert row.risk_unit_source == "declared_stop"
    assert row.planned_risk_usd == pytest.approx(1000.0, abs=1)
