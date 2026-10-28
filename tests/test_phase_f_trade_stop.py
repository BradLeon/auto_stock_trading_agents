"""13.3 actual stopped arbitration, read-only Clerk and validated simulation recovery."""

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from test_phase_f_trade_switch_drill import submit
from test_phase_f_trader_a import execution as _execution
from test_phase_f_trader_a import price_clock as _price_clock
from test_phase_f_trader_a import ready_state

from ats.decision.repository import DecisionAuditRepository
from ats.execution import broker_write_guard as guard
from ats.execution import route_registry as rr
from ats.execution import route_switch as rs
from ats.execution.authorization import (
    AuthorizationError,
    bind_to_active_route,
    build_authorization,
)
from ats.execution.authorization_lifecycle import AuthorizationLifecycle, lifecycle_for_cycle
from ats.execution.clerk import clerk_run
from ats.workflow.business_replay_inputs import implementation_hashes
from ats.workflow.isolation import verified_isolation_root

execution = _execution
price_clock = _price_clock


def reconcile(execution, at):
    class ReadOnly:
        def get_fills(self):
            return execution.broker.get_fills()

        def get_portfolio(self):
            return execution.pf.model_copy(update={"as_of": at})

        def completed_orders(self):
            return []

    return clerk_run(
        store=execution.store,
        broker=ReadOnly(),
        steps=("reconcile",),
        as_of=at.isoformat(),
        window_start=(at - timedelta(days=2)).date().isoformat(),
        window_end=at.date().isoformat(),
    )


def validated_target(execution):
    state = ready_state(execution, cycle="accepted-target")
    submit(execution, state)
    order = execution.broker.accepted[-1]
    execution.broker.simulate_fill(order.order_id, shares=order.qty, price=100.1)
    reconcile(execution, datetime.now(UTC))
    receipt = rr.submission_receipts()[0]
    assert receipt["status"] == "filled"
    path = verified_isolation_root() / "validated-target.json"
    body = {
        "route_id": "simulation",
        "environment": "paper",
        "account": "DU1",
        "implementation": implementation_hashes(),
        "intent_id": receipt["intent_id"],
        "valid_until": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    path.write_text(json.dumps(body))

    def check():
        data = json.loads(path.read_text())
        rows = {r["intent_id"]: r for r in rr.submission_receipts()}
        prior = rows.get(data["intent_id"], {})
        valid = (
            prior.get("status") == "filled"
            and prior.get("route_id") == data["route_id"]
            and prior.get("account") == data["account"]
            and prior.get("environment") == data["environment"]
            and datetime.fromisoformat(data["valid_until"]) > datetime.now(UTC)
        )
        return {**data, "reference": str(path), "valid": valid}

    return state, path, check


def restore(freeze, check, life, expected=1):
    return rs.restore_stopped_simulation(
        freeze.switch_token,
        expected_generation=expected,
        lifecycle=life,
        target_check=check,
        actor="isolated-operator",
        reason="restore actually validated simulator",
    )


def assert_no_submit(execution, state):
    count = len(execution.broker.accepted)
    with pytest.raises(guard.BrokerWriteProhibited):
        execution.broker.place_orders(
            [(d, d.qty) for d in state.decisions],
            "blocked-new-intent",
            execution_check=lambda *a: None,
        )
    assert len(execution.broker.accepted) == count


@pytest.mark.parametrize("status", ["partial", "unknown"])
def test_stop_late_read_only_settlement_then_fresh_generation(execution, status, record_property):
    _, _, check = validated_target(execution)
    state = ready_state(execution, cycle="inflight")
    _, old_auth = submit(execution, state)
    order = execution.broker.accepted[-1]
    repo = DecisionAuditRepository(execution.store)
    revision = dict(repo.latest_revision(state.cycle_id))
    if status == "partial":
        execution.broker.simulate_fill(order.order_id, shares=2, price=100.1)
        reconcile(execution, datetime.now(UTC))
    else:
        receipt = rr.submission_receipts()[-1]
        rr.record_submission_result(receipt["intent_id"], "unknown", order.order_id)
    stopped = rr.freeze_submissions(
        actor="isolated-operator", reason="safety stop; read-only takeover", expected_generation=1
    )
    counter = rr.issuance_counter()
    with pytest.raises(AuthorizationError, match="frozen"):
        bind_to_active_route(build_authorization(repo, state.cycle_id))
    assert rr.issuance_counter() == counter
    assert_no_submit(execution, state)
    # Even a caller's false empty lifecycle cannot conceal the actual broker receipt.
    held = restore(stopped, check, AuthorizationLifecycle(cycle_status="completed", order_rows=[]))
    assert not held.succeeded and rr.read_freeze().frozen and rr.read_state().generation == 1
    late = order.submitted_at + timedelta(days=1)
    execution.broker.simulate_fill(
        order.order_id, shares=order.qty - (2 if status == "partial" else 0), price=100.2, at=late
    )
    clerk = reconcile(execution, late + timedelta(seconds=1))
    again = reconcile(execution, late + timedelta(seconds=1))
    assert clerk["status"] == again["status"] == "completed"
    assert rr.read_freeze().frozen and rr.issuance_counter() == counter
    assert dict(repo.latest_revision(state.cycle_id)) == revision
    assert_no_submit(execution, state)
    recovered = restore(stopped, check, lifecycle_for_cycle(state.cycle_id, store=execution.store))
    assert recovered.succeeded and recovered.steps_completed == ["freeze", "drain", "bump", "open"]
    assert rr.read_state().generation == 2
    # Old process grant stays stale even though the route name is unchanged.
    assert_no_submit(execution, state)
    guard.grant_write("simulation", 2, environment="paper", account="DU1")
    fresh = ready_state(execution, cycle="new-approval")
    fresh_rows, _ = submit(execution, fresh)
    assert fresh_rows[0].status == "submitted" and len(execution.broker.accepted) == 3
    assert len(execution.store.conn.execute("SELECT * FROM fills").fetchall()) == (
        3 if status == "partial" else 2
    )
    assert old_auth["route_generation"] == 1
    record_property(
        "actual_stop_restore",
        json.dumps(
            {
                "status": status,
                "root": str(verified_isolation_root()),
                "stop": stopped.as_row(),
                "held": held.as_row(),
                "restore": recovered.as_row(),
                "receipts": rr.submission_receipts(),
                "clerk": clerk,
            }
        ),
    )


@pytest.mark.parametrize(
    "failure", ["missing", "expired", "drift", "account", "unavailable", "no_lifecycle"]
)
def test_invalid_target_keeps_original_stop_and_history(
    execution, monkeypatch, failure, record_property
):
    state, path, check = validated_target(execution)
    freeze = rr.freeze_submissions(
        actor="operator", reason="hold for target validation", expected_generation=1
    )
    life = lifecycle_for_cycle(state.cycle_id, store=execution.store)
    history = rr.history()
    receipts = rr.submission_receipts()
    if failure == "missing":
        check = None
    elif failure == "unavailable":
        monkeypatch.setattr("ats.execution.simulation.selected_simulation", lambda: None)
    elif failure == "no_lifecycle":
        life = None
    else:
        body = json.loads(path.read_text())
        if failure == "expired":
            body["valid_until"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        elif failure == "drift":
            body["implementation"] = {}
        else:
            body["account"] = "DU-WRONG"
        path.write_text(json.dumps(body))
    result = restore(freeze, check, life)
    assert not result.succeeded and rr.read_freeze().frozen and rr.read_state().generation == 1
    assert rr.history() == history and rr.submission_receipts() == receipts
    assert_no_submit(execution, state)
    record_property(
        "held_target_failure",
        json.dumps(
            {"failure": failure, "result": result.as_row(), "freeze": rr.read_freeze().as_row()}
        ),
    )


def test_post_bump_failure_retry_does_not_bump_twice(execution, monkeypatch, record_property):
    state, _, check = validated_target(execution)
    freeze = rr.freeze_submissions(
        actor="operator", reason="stopped recovery fault", expected_generation=1
    )
    life = lifecycle_for_cycle(state.cycle_id, store=execution.store)
    original = rr.open_submissions

    def fail(*a, **k):
        raise RuntimeError("open crash")

    monkeypatch.setattr(rr, "open_submissions", fail)
    failed = restore(freeze, check, life)
    assert not failed.succeeded and rr.read_state().generation == 2 and rr.read_freeze().frozen
    guard.grant_write("simulation", 2, environment="paper", account="DU1")
    assert_no_submit(execution, state)
    missing = restore(freeze, None, life, expected=2)
    assert not missing.succeeded and rr.read_freeze().frozen
    monkeypatch.setattr(rr, "open_submissions", original)
    done = restore(freeze, check, life, expected=2)
    assert done.succeeded and rr.read_state().generation == 2
    assert len(rr.history()) == 2
    record_property(
        "post_bump_retry",
        json.dumps(
            {"failed": failed.as_row(), "missing": missing.as_row(), "recovered": done.as_row()}
        ),
    )


def test_new_process_observes_stop_and_cannot_issue_or_submit(execution, record_property):
    state, _, _ = validated_target(execution)
    freeze = rr.freeze_submissions(
        actor="operator", reason="persistent safety stop", expected_generation=1
    )
    code = """import json,sys
from ats.execution import route_registry as rr,broker_write_guard as g
from ats.execution.simulation import simulated_execution
from ats.execution.authorization import build_authorization,bind_to_active_route,AuthorizationError
from ats.decision.repository import DecisionAuditRepository
from ats.memory import get_store
from ats.schemas.decision import TradeDecision
g.prohibit_broker_writes(reason_code=g.REASON_ISOLATED)
store=get_store();state=rr.read_state();freeze=rr.read_freeze()
assert freeze.frozen and state.generation==1
counter=rr.issuance_counter()
try:bind_to_active_route(build_authorization(DecisionAuditRepository(store),sys.argv[1]))
except AuthorizationError:pass
else:raise AssertionError('new process issued during stop')
with simulated_execution(store=store,account='DU1') as broker:
    g.grant_write(state.route_id,state.generation,environment='paper',account='DU1')
    try:broker.place_orders([(TradeDecision(symbol='AAPL',action='buy',qty=1),1)],'restart-intent',execution_check=lambda *a:None)
    except g.BrokerWriteProhibited as exc:reason=exc.reason_code
    else:raise AssertionError('new process submitted during stop')
    assert not broker.accepted
assert rr.issuance_counter()==counter
print(json.dumps({'freeze':freeze.as_row(),'generation':state.generation,'refusal':reason}))
"""
    child = subprocess.run(
        ["uv", "run", "--offline", "--no-sync", "python", "-c", code, state.cycle_id],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    assert rr.read_freeze().switch_token == freeze.switch_token
    record_property("persistent_stop_child", child.stdout)


def test_recovery_cannot_use_an_isolated_label_to_touch_production(execution, monkeypatch):
    monkeypatch.setattr("ats.workflow.isolation.verified_isolation_root", lambda: None)
    with pytest.raises(rs.RouteSwitchBlocked, match="physical isolation"):
        rs.restore_stopped_simulation(
            "fake",
            expected_generation=1,
            lifecycle=None,
            target_check=None,
            actor="fixture",
            reason="fake label",
        )


def test_single_cycle_view_cannot_hide_another_approved_cycle(execution):
    accepted, _, check = validated_target(execution)
    unused = ready_state(execution, cycle="approved-not-submitted")
    freeze = rr.freeze_submissions(
        actor="operator", reason="all cycle drain", expected_generation=1
    )
    result = restore(freeze, check, lifecycle_for_cycle(accepted.cycle_id, store=execution.store))
    assert not result.succeeded and rr.read_freeze().frozen
    assert any(unused.cycle_id in reason for reason in result.reasons)


def test_proof_expires_after_bump_keeps_stop(execution):
    state, path, check = validated_target(execution)
    freeze = rr.freeze_submissions(
        actor="operator", reason="proof expiration window", expected_generation=1
    )
    calls = 0

    def expire_before_open():
        nonlocal calls
        calls += 1
        if calls == 3:
            body = json.loads(path.read_text())
            body["valid_until"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            path.write_text(json.dumps(body))
        return check()

    result = restore(
        freeze, expire_before_open, lifecycle_for_cycle(state.cycle_id, store=execution.store)
    )
    assert not result.succeeded and rr.read_state().generation == 2 and rr.read_freeze().frozen
    guard.grant_write("simulation", 2, environment="paper", account="DU1")
    assert_no_submit(execution, state)


@pytest.mark.parametrize("wrong", ["token", "generation"])
def test_competing_recovery_cannot_release_stop(execution, wrong):
    state, _, check = validated_target(execution)
    freeze = rr.freeze_submissions(actor="owner", reason="owned stop", expected_generation=1)
    token = freeze.switch_token if wrong == "generation" else "other-operator"
    result = rs.restore_stopped_simulation(
        token,
        expected_generation=0 if wrong == "generation" else 1,
        lifecycle=lifecycle_for_cycle(state.cycle_id, store=execution.store),
        target_check=check,
        actor="competitor",
        reason="stale",
    )
    assert (
        not result.succeeded
        and rr.read_freeze().switch_token == freeze.switch_token
        and rr.read_state().generation == 1
    )
