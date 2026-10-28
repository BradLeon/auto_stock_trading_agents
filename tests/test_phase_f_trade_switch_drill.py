"""11.3 real approved Trader/FakeBroker, Clerk and persisted route arbitration."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from test_phase_f_trader_a import execution as _execution
from test_phase_f_trader_a import price_clock as _price_clock
from test_phase_f_trader_a import ready_state

from ats.decision.repository import DecisionAuditRepository
from ats.execution import broker_write_guard as guard
from ats.execution import route_registry as rr
from ats.execution import route_switch as rs
from ats.execution.authorization import (
    ExecutionAuthorization,
    bind_to_active_route,
    build_authorization,
    validate_authorization,
)
from ats.execution.authorization_lifecycle import lifecycle_for_cycle
from ats.execution.clerk import clerk_run
from ats.trader import execute as trader

execution = _execution
price_clock = _price_clock


def submit(execution, state):
    repo = DecisionAuditRepository(execution.store)
    auth = bind_to_active_route(build_authorization(repo, state.cycle_id)).model_dump(mode="json")
    rows, _ = trader.place_orders(
        [(d, d.qty) for d in state.decisions],
        state.cycle_id,
        revision_no=state.revision_no,
        authorization=auth,
    )
    execution.store.save_trades(rows, cycle_id=state.cycle_id, source="chief")
    repo.transition(
        state.cycle_id,
        to_status="executed",
        actor="trader",
        payload={"submitted_order_ids": [r.order_id for r in rows]},
    )
    return rows, auth


def test_actual_partial_late_fill_blocks_drain_then_switch_and_rollback(execution, record_property):
    state = ready_state(execution, cycle="switch-partial")
    _rows, old_auth = submit(execution, state)
    order = execution.broker.accepted[0]
    execution.broker.simulate_fill(order.order_id, shares=2, price=100.1)

    class Readback:
        def get_fills(self):
            return execution.broker.get_fills()

        def get_portfolio(self):
            return execution.pf.model_copy(update={"as_of": datetime.now(UTC)})

        def completed_orders(self):
            return []

    reader = Readback()
    day = order.submitted_at.date().isoformat()
    clerk_run(
        store=execution.store, broker=reader, steps=("reconcile",), window_start=day, window_end=day
    )
    blocked = rs.perform_switch(
        "B",
        expected_generation=1,
        lifecycle=lifecycle_for_cycle(state.cycle_id, store=execution.store),
        environment="paper",
        account="DU1",
        actor="fixture",
        reason="actual partial",
    )
    assert not blocked.succeeded and blocked.aborted_at == "drain"
    late = order.submitted_at + timedelta(days=1)
    execution.broker.simulate_fill(order.order_id, shares=order.qty - 2, price=100.2, at=late)
    complete = clerk_run(
        store=execution.store,
        broker=reader,
        steps=("reconcile",),
        as_of=(late + timedelta(seconds=1)).isoformat(),
        window_start=day,
        window_end=late.date().isoformat(),
    )
    assert complete["status"] == "completed"
    life = lifecycle_for_cycle(state.cycle_id, store=execution.store)
    switched = rs.perform_switch(
        "B",
        expected_generation=1,
        lifecycle=life,
        environment="paper",
        account="DU1",
        actor="fixture",
        reason="actual settled",
    )
    assert switched.steps_completed == ["freeze", "drain", "bump", "open"]
    rejected, _ = trader.place_orders(
        [(d, d.qty) for d in state.decisions],
        state.cycle_id,
        revision_no=state.revision_no,
        authorization=old_auth,
    )
    # A settled logical order is returned unchanged; it never creates a new order.
    assert rejected[0].status == "filled" and len(execution.broker.accepted) == 1
    reasons = validate_authorization(
        DecisionAuditRepository(execution.store),
        ExecutionAuthorization.model_validate(old_auth),
        route_state=rr.read_state(),
    )
    assert any("generation" in item or "route" in item for item in reasons)
    with pytest.raises(guard.BrokerWriteProhibited):
        execution.broker.place_orders(
            [(d, d.qty) for d in state.decisions],
            "stale-grant-new-intent",
            execution_check=lambda *a: None,
        )
    rollback = rs.perform_switch(
        "simulation",
        expected_generation=2,
        lifecycle=life,
        environment="paper",
        account="DU1",
        actor="fixture",
        reason="restore validated simulator",
    )
    assert rollback.succeeded and rr.read_state().generation == 3
    guard.grant_write("simulation", 3, environment="paper", account="DU1")
    fresh = ready_state(execution, cycle="restored-approved")
    fresh_rows, _ = submit(execution, fresh)
    assert fresh_rows[0].status == "submitted" and len(execution.broker.accepted) == 2
    assert len(execution.store.conn.execute("SELECT * FROM fills").fetchall()) == 2
    record_property(
        "actual_trade_drill",
        json.dumps(
            {
                "blocked": blocked.as_row(),
                "switch": switched.as_row(),
                "restore": rollback.as_row(),
                "receipts": rr.submission_receipts(),
                "clerk": complete,
            }
        ),
    )


def test_failure_after_generation_bump_keeps_real_submission_frozen(
    execution, monkeypatch, record_property
):
    state = ready_state(execution, cycle="crash-open")
    repo = DecisionAuditRepository(execution.store)
    old_auth = bind_to_active_route(build_authorization(repo, state.cycle_id)).model_dump(
        mode="json"
    )
    repo.transition(
        state.cycle_id,
        to_status="superseded",
        actor="fixture",
        payload={"reason": "stop unused approved cycle for drill"},
    )
    original = rr.open_submissions
    monkeypatch.setattr(
        rr,
        "open_submissions",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("open unavailable")),
    )
    report = rs.perform_switch(
        "B",
        expected_generation=1,
        lifecycle=lifecycle_for_cycle(state.cycle_id, store=execution.store),
        environment="paper",
        account="DU1",
        actor="fixture",
        reason="crash drill",
    )
    assert not report.succeeded and rr.read_freeze().frozen
    guard.grant_write("B", 2, environment="paper", account="DU1")
    with pytest.raises(guard.BrokerWriteProhibited, match="frozen"):
        execution.broker.place_orders(
            [(d, d.qty) for d in state.decisions],
            "frozen-new-intent",
            execution_check=lambda *a: None,
        )
    rows, _ = trader.place_orders(
        [(d, d.qty) for d in state.decisions],
        state.cycle_id,
        revision_no=state.revision_no,
        authorization=old_auth,
    )
    assert rows[0].status == "rejected" and not execution.broker.accepted
    pending = rs.recover_interrupted_switch()
    assert not pending["can_submit"] and pending["generation_moved"]
    monkeypatch.setattr(rr, "open_submissions", original)
    recovered = rs.open_after_recovery(pending["switch_token"])
    assert recovered.generation == 2
    record_property(
        "actual_recovery",
        json.dumps({"switch": report.as_row(), "held": pending, "recovered": recovered.as_row()}),
    )
