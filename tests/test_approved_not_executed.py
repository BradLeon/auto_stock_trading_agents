"""Terminal state `approved_not_executed` (switch-trade-path task 2.4).

An approval that stands but sends nothing — a rehearsal, or the production live lock — ends in its
own state with the pre-execution checks on record; it is never recorded as executed.
"""
import json

import pytest
from test_phase_f_trader_a import execution, price_clock, quote, ready_state  # noqa: F401

from ats.decision.repository import DecisionAuditRepository
from ats.decision.state import TERMINAL_STATUSES, CycleStatus
from ats.execution import state_api
from ats.graph import chief


def _production_lock(monkeypatch, *, route=False):
    """The facts of production: no isolated transport selected (C3), and optionally no trade route."""
    monkeypatch.setattr("ats.execution.simulation.selected_simulation", lambda: None)
    monkeypatch.setattr("ats.execution.shadow_execution.selected_shadow", lambda: None)
    if not route:
        monkeypatch.setattr("ats.execution.authorization.active_route_state", lambda: None)


def _run(execution, state):
    out = chief.trader(state)
    state = state.model_copy(update=out)
    return out, chief.persist(state), state


def _cycle(execution, cycle="test-a"):
    repo = DecisionAuditRepository(execution.store)
    event = execution.store.conn.execute(
        "SELECT payload FROM cycle_events WHERE cycle_id = ? ORDER BY rowid DESC LIMIT 1", (cycle,)).fetchone()
    return repo.get_cycle(cycle), json.loads(event["payload"]) if event and event["payload"] else {}


def test_the_new_state_is_terminal():
    assert CycleStatus.APPROVED_NOT_EXECUTED in TERMINAL_STATUSES
    assert CycleStatus("approved_not_executed") is CycleStatus.APPROVED_NOT_EXECUTED


def test_locked_approval_runs_every_check_then_ends_approved_not_executed(execution, monkeypatch):
    state = ready_state(execution)
    _production_lock(monkeypatch)
    out, final, _ = _run(execution, state)

    assert out["gate_outcome"] == "approved_not_executed"
    checks = out["execution_checks"]["checks"]
    by_name = {c["check"]: c for c in checks}
    assert by_name["authorization_fields"]["ok"] and by_name["orders_match_approved_revision"]["ok"]
    assert by_name["live_quote_and_funds"]["ok"] and by_name["live_quote_and_funds"]["symbol"] == "AAPL"
    # no route exists while the lock is on: recorded, informational, and it did not stop the checks
    assert by_name["route_binding"]["informational"] and not by_name["route_binding"]["ok"]
    assert "route_unbound" in by_name["route_binding"]["detail"]
    # the quote that was checked is on the order record
    assert all(e.execution_price_audit and e.execution_price_audit[0]["quote"] for e in out["order_results"])
    assert all(e.status == "rejected" and e.error.startswith("auto execution disabled")
               for e in out["order_results"])
    # zero broker submissions
    assert execution.broker.placed == []

    assert final["cycle_status"] == "approved_not_executed"
    cycle, event = _cycle(execution)
    assert cycle["status"] == "approved_not_executed"
    assert cycle["final_outcome"] == "approved_not_executed:live_lock"
    assert {c["check"] for c in event["execution_checks"]["checks"]} >= {"live_quote_and_funds", "route_binding"}


def test_a_failed_quote_check_under_the_lock_is_not_an_approved_not_executed_cycle(execution, monkeypatch):
    state = ready_state(execution)
    _production_lock(monkeypatch)
    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price",
                        lambda symbol, **k: quote(symbol=symbol, bid=110.0, ask=110.1))   # +10% since approval
    out = chief.trader(state)

    assert out["gate_outcome"] == "stale"                       # back to the risk gate for re-approval
    assert all(e.error.startswith("price conditions rejected; reapprove") for e in out["order_results"])
    assert execution.broker.placed == []
    assert execution.store.conn.execute(
        "SELECT status FROM decision_cycles WHERE cycle_id = 'test-a'").fetchone()[0] == "pending_risk"


def test_a_rehearsal_approval_is_not_recorded_as_executed(execution):
    state = ready_state(execution).model_copy(update={"dry_run": True})
    out, final, _ = _run(execution, state)

    assert out["gate_outcome"] == "approved_not_executed" and out["execution_checks"]["reason"] == "dry_run"
    assert final["cycle_status"] == "approved_not_executed"
    cycle, _ = _cycle(execution)
    assert cycle["status"] != "executed" and cycle["final_outcome"] == "approved_not_executed:dry_run"
    assert execution.broker.placed == []


def test_a_rejected_approval_is_still_an_approval_rejection(execution):
    state = ready_state(execution)
    rejected = state.approval.model_copy(update={"status": "rejected"})
    out, final, _ = _run(execution, state.model_copy(update={"approval": rejected, "dry_run": True}))
    assert out["gate_outcome"] == "cancelled"
    assert final["cycle_status"] == "approval_rejected"


def test_internal_state_lists_held_back_cycles_apart_from_trades_and_performance(execution, monkeypatch):
    before = state_api.get_internal_state(execution.store)
    assert before.approved_not_executed == []
    state = ready_state(execution).model_copy(update={"dry_run": True})
    _run(execution, state)

    after = state_api.get_internal_state(execution.store)
    assert [row["cycle_id"] for row in after.approved_not_executed] == ["test-a"]
    assert after.approved_not_executed[0]["final_outcome"] == "approved_not_executed:dry_run"
    assert after.fills == before.fills and after.performance == before.performance
    assert not any(t["status"] == "filled" for t in after.trades)
