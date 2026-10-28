"""13.10 actual approval/Clerk/state stop and exact-scope joint recovery.

Quotes, accounts, human approval and external qualification are fixture inputs.
Actual revision, Trader, FakeBroker, Clerk, SQL authority and guards are retained.
"""

import copy
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from test_phase_f_trade_stop import reconcile as initial_reconcile
from test_phase_f_trade_switch_drill import submit
from test_phase_f_trader_a import execution as _execution
from test_phase_f_trader_a import price_clock as _price_clock
from test_phase_f_trader_a import ready_state

from ats.decision.repository import DecisionAuditRepository
from ats.execution import clerk
from ats.execution.state_api import get_internal_state
from ats.workflow import cutover as plane
from ats.workflow import joint_cutover as joint
from ats.workflow import runtime_reads as reads
from ats.workflow import scoped_routes as scopes
from ats.workflow.business_replay_inputs import implementation_hashes
from ats.workflow.cutover_routing import RouteUnavailable
from ats.workflow.cutover_wiring import BoundaryWriteRefused
from ats.workflow.isolation import verified_isolation_root

execution = _execution
price_clock = _price_clock
BOUNDARIES = (plane.APPROVAL_LIFECYCLE, plane.CLERK_PUBLICATION)


def reconcile(execution, at):
    class ReadOnly:
        def get_fills(self):
            return execution.broker.get_fills()

        def get_portfolio(self):
            return execution.pf.model_copy(update={"as_of": at})

        def completed_orders(self):
            return []

    return clerk.clerk_run(
        store=execution.store,
        broker=ReadOnly(),
        steps=("reconcile",),
        as_of=at.isoformat(),
        window_start=(at - timedelta(days=2)).date().isoformat(),
        window_end=at.date().isoformat(),
        read_scope=execution.clerk_identity.as_row(),
    )


def identities(state):
    point = state.as_of
    return [
        reads.business_identity(
            "chief", kind="decision", scope_id=state.cycle_id, entities=["AAPL"], as_of=point
        ),
        reads.business_identity(
            "clerk",
            kind="portfolio",
            scope_id="portfolio",
            entities=reads.configured_entities("portfolio", "portfolio"),
            explicit={
                "kind": "portfolio",
                "id": "portfolio",
                "entities": reads.configured_entities("portfolio", "portfolio"),
                "time_range": {
                    "start": (point - timedelta(minutes=5)).isoformat(),
                    "end": (point + timedelta(days=2)).isoformat(),
                },
            },
        ),
    ]


def request(batch, who, route):
    return {
        "batch_id": batch,
        "workflow": "chief-decision",
        "schedules": [],
        "members": [
            {
                "boundary": b,
                "identity": i.as_row(),
                "route": route,
                "expected_generation": scopes.resolve_route(b, i)["generation"],
            }
            for i in who
            for b in BOUNDARIES
        ],
    }


def gates(execution, who, proof):
    # The receipt is produced only after an actual accepted/fill/reconcile chain.
    # It is an isolated availability adapter, never production eligibility.
    def target(i):
        d = json.loads(proof.read_text())
        state = get_internal_state(execution.store)
        valid = (
            i.as_row() in d["identities"]
            and d["implementation"] == implementation_hashes()
            and datetime.fromisoformat(d["valid_until"]) > datetime.now(UTC)
            and bool(state.fills)
            and state.completeness.status == "complete"
        )
        return {"identity": i.as_row(), "valid": valid, "reference": str(proof)}

    return {
        "authorization": lambda m, w: {
            "member": m,
            "workflow": w,
            "valid": True,
            "reference": "isolated-operator",
        },
        "qualification": lambda i: {"identity": i.as_row(), "status": "eligible"},
        "report": target,
        "fallback": target,
        "compatibility": lambda w, i, r: {
            "workflow": w,
            "identity": i.as_row(),
            "valid": True,
            "reference": str(proof),
            "edges": [
                {
                    "boundaries": list(BOUNDARIES),
                    "mixed_compatible": False,
                    "reference": "isolated-approval-clerk-schema",
                }
            ],
        },
    }


def run(req, checks, **kw):
    return joint.execute(
        req,
        gates=checks,
        actor="isolated-operator",
        reason="13.10 safety stop/recovery",
        mode="isolated",
        **kw,
    )


@pytest.fixture
def accepted(execution, monkeypatch):
    # Actual enforcement is measured below; the external inventory adapter alone
    # is fixture so this drill cannot register production enforced/eligible.
    monkeypatch.setattr("ats.workflow.boundary_evidence.assert_enforced", lambda *a, **k: None)
    monkeypatch.setattr("ats.data.assurance.qualification", lambda **k: {"status": "eligible"})
    state = ready_state(execution, cycle="state-stop")
    submit(execution, state)
    order = execution.broker.accepted[0]
    execution.broker.simulate_fill(order.order_id, shares=order.qty, price=100.1)
    initial_reconcile(execution, datetime.now(UTC))
    who = identities(state)
    execution.clerk_identity = who[1]
    proof = verified_isolation_root() / "accepted-state-target.json"
    proof.write_text(
        json.dumps(
            {
                "identities": [i.as_row() for i in who],
                "implementation": implementation_hashes(),
                "valid_until": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
        )
    )
    return state, who, proof, gates(execution, who, proof)


def approval(execution, state, key="recovered"):
    return DecisionAuditRepository(execution.store).record_approval(
        approval_id=key,
        idempotency_key=key,
        cycle_id=state.cycle_id,
        revision_no=state.revision_no,
        decision_hash=state.revision_hash,
        decision="approved",
        reviewer="fixture-human",
    )


def snapshot(store):
    tables = (
        "decision_revisions",
        "decision_risk_reviews",
        "boss_approvals",
        "cycle_events",
        "trades",
        "fills",
        "clerk_runs",
        "ledger_read_models",
    )
    return {
        t: [dict(r) for r in store.conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]
        for t in tables
    }


def refused(execution, state, who):
    before = snapshot(execution.store)
    original_review = before["decision_risk_reviews"][0]
    with reads.bind_read(who[0]), pytest.raises(BoundaryWriteRefused):
        DecisionAuditRepository(execution.store).record_review(
            review_id="stopped-review",
            cycle_id=state.cycle_id,
            revision_no=state.revision_no,
            decision_hash=state.revision_hash,
            ruleset_version=original_review["ruleset_version"],
            portfolio_snapshot_id=original_review["portfolio_snapshot_id"],
            market_as_of=original_review["market_as_of"],
            verdict="approved",
        )
    for identity in (who[0], None):
        if identity:
            with reads.bind_read(identity), pytest.raises(BoundaryWriteRefused):
                approval(execution, state, "during-stop")
        else:
            with pytest.raises(BoundaryWriteRefused):
                approval(execution, state, "unbound-stop")
    with reads.bind_read(who[1]), pytest.raises(BoundaryWriteRefused):
        reconcile(execution, datetime.now(UTC))
    with pytest.raises(BoundaryWriteRefused):
        reconcile(execution, datetime.now(UTC))
    assert snapshot(execution.store) == before
    assert get_internal_state(execution.store).fills  # Historical read remains available.


def test_actual_stop_restore_preserves_revision_and_idempotency(
    execution, accepted, record_property
):
    state, who, _proof, checks = accepted
    original = snapshot(execution.store)
    stopped = run(request("stop", who, "disabled"), checks)
    assert stopped["can_run"]  # Journal released, actual scoped route still disabled.
    refused(execution, state, who)
    with pytest.raises(plane.CutoverError, match="incompatible"):
        run(
            {
                **request("half", who, "target"),
                "members": request("half", who, "target")["members"][:1],
            },
            checks,
        )
    restored = run(request("restore", who, "target"), checks)
    assert restored["can_run"]
    with reads.bind_read(who[0]):
        first = dict(approval(execution, state))
        assert dict(approval(execution, state)) == first
    with reads.bind_read(who[1]):
        one = reconcile(execution, datetime.now(UTC))
    assert one["status"] == "completed"
    final = snapshot(execution.store)
    assert final["decision_revisions"] == original["decision_revisions"]
    assert final["decision_risk_reviews"] == original["decision_risk_reviews"]
    assert final["fills"] == original["fills"] and final["trades"] == original["trades"]
    assert len(execution.broker.accepted) == 1
    assert run(request("idempotent-stop", who, "disabled"), checks)["can_run"]
    refused(execution, state, who)
    record_property(
        "actual_state_stop_restore",
        json.dumps(
            {
                "root": str(verified_isolation_root()),
                "stopped": stopped,
                "restored": restored,
                "revision": state.revision_hash,
            }
        ),
    )


@pytest.mark.parametrize("bad", ["expired", "drift", "missing", "qualification"])
def test_missing_valid_target_never_releases_stop(execution, accepted, bad):
    state, who, proof, checks = accepted
    run(request("stop", who, "disabled"), checks)
    recovery = request("restore", who, "target")
    if bad == "missing":
        proof.unlink()
    elif bad == "qualification":
        checks["qualification"] = lambda i: {"identity": i.as_row(), "status": "ineligible"}
    else:
        d = json.loads(proof.read_text())
        d["implementation" if bad == "drift" else "valid_until"] = (
            {} if bad == "drift" else (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        )
        proof.write_text(json.dumps(d))
    with pytest.raises((plane.CutoverError, FileNotFoundError)):
        run(recovery, checks)
    refused(execution, state, who)
    assert all(scopes.resolve_route(b, i)["route"] == "disabled" for i in who for b in BOUNDARIES)


@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_old_bound_worker_cannot_publish_after_independent_stop_restore(
    execution, accepted, boundary
):
    state, who, _, checks = accepted
    identity = who[0] if boundary == plane.APPROVAL_LIFECYCLE else who[1]

    def move(route):
        return scopes.transition_route(
            boundary,
            identity,
            route,
            actor="fixture",
            reason="stop/recover",
            expected_generation=scopes.resolve_route(boundary, identity)["generation"],
            mode="isolated",
            qualification=checks["qualification"],
            report=checks["report"],
            fallback=checks["fallback"],
        )

    with reads.bind_read(identity):
        move("disabled")
        move("legacy")
        with pytest.raises(BoundaryWriteRefused, match="authority changed"):
            approval(execution, state) if boundary == plane.APPROVAL_LIFECYCLE else reconcile(
                execution, datetime.now(UTC)
            )
    with reads.bind_read(identity):
        approval(execution, state) if boundary == plane.APPROVAL_LIFECYCLE else reconcile(
            execution, datetime.now(UTC)
        )


@pytest.mark.parametrize("stage", ["prepared", "routes_committed", "owners_committed"])
def test_joint_recovery_failure_keeps_actual_writes_frozen(execution, accepted, stage):
    state, who, _, checks = accepted
    run(request("stop", who, "disabled"), checks)
    recovery = request("restore", who, "target")

    def crash(phase):
        if phase == stage:
            raise RuntimeError("injected joint interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        run(recovery, checks, checkpoint=crash)
    assert not joint.inspect("restore")["can_run"]
    before = snapshot(execution.store)
    with pytest.raises(plane.CutoverError):
        approval(execution, state)
    with pytest.raises(RouteUnavailable, match="joint batch frozen"):
        reconcile(execution, datetime.now(UTC))
    bad = copy.copy(checks)
    bad["report"] = lambda i: {"identity": i.as_row(), "valid": False, "reference": "withdrawn"}
    with pytest.raises(plane.CutoverError):
        run(recovery, bad)
    assert snapshot(execution.store) == before and not joint.inspect("restore")["can_run"]
    final = run(recovery, checks)
    assert final["can_run"] and all(
        scopes.resolve_route(b, i)["generation"] == 2 for i in who for b in BOUNDARIES
    )


def test_new_process_observes_stop_at_repository_clerk_and_state(
    execution, accepted, record_property
):
    state, who, _, checks = accepted
    run(request("stop", who, "disabled"), checks)
    code = """import json,sys
from ats.execution.broker_write_guard import prohibit_broker_writes,REASON_ISOLATED
from ats.memory import get_store
from ats.decision.repository import DecisionAuditRepository
from ats.execution.clerk import clerk_run
from ats.execution.state_api import get_internal_state
from ats.workflow.cutover_wiring import BoundaryWriteRefused
prohibit_broker_writes(reason_code=REASON_ISOLATED)
s=get_store();repo=DecisionAuditRepository(s);rev=repo.latest_revision(sys.argv[1]);before=s.conn.total_changes
for action in (lambda:repo.record_approval(approval_id='child',idempotency_key='child',cycle_id=sys.argv[1],revision_no=rev['revision_no'],decision_hash=rev['decision_hash'],decision='approved',reviewer='fixture'),lambda:clerk_run(store=s,steps=(),read_scope=json.loads(sys.argv[2]))):
 try:action()
 except BoundaryWriteRefused:pass
 else:raise AssertionError('stopped actual publication passed')
assert s.conn.total_changes==before
assert get_internal_state(s).fills
print(json.dumps({'revision':dict(rev),'writes':'refused','state':'readable'}))
"""
    child = subprocess.run(
        [
            "uv",
            "run",
            "--offline",
            "--no-sync",
            "python",
            "-c",
            code,
            state.cycle_id,
            json.dumps(who[1].as_row()),
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    record_property("persistent_state_stop_child", child.stdout)


def test_clerk_inflight_actual_fill_survives_stop_and_recovery(execution, accepted, monkeypatch):
    _state, who, _, checks = accepted
    # Another actual Trader submission establishes unfinished work, never a prewritten trade.
    late_state = ready_state(execution, cycle="late-state-stop")
    submit(execution, late_state)
    order = execution.broker.accepted[-1]
    execution.broker.simulate_fill(order.order_id, shares=2, price=100.1)
    original = clerk._STEPS["reconcile"]

    def stop_after_real_step(*args, **kwargs):
        result = original(*args, **kwargs)
        run(request("stop", who, "disabled"), checks)
        return result

    monkeypatch.setitem(clerk._STEPS, "reconcile", stop_after_real_step)
    interrupted_at = datetime.now(UTC)
    with reads.bind_read(who[1]), pytest.raises(RouteUnavailable, match="authority changed"):
        reconcile(execution, interrupted_at)
    partial = snapshot(execution.store)
    assert len(partial["fills"]) == 2 and partial["clerk_runs"][-1]["status"] == "running"
    assert partial["trades"][-1]["filled_qty"] == 2
    run(request("restore", who, "target"), checks)
    monkeypatch.setitem(clerk._STEPS, "reconcile", original)
    with reads.bind_read(who[1]):
        resumed = reconcile(execution, interrupted_at)
        repeated = reconcile(execution, interrupted_at)
    assert resumed["status"] == "completed" and repeated["reused"]
    assert len(snapshot(execution.store)["fills"]) == 2
    late = order.submitted_at + timedelta(days=1)
    execution.broker.simulate_fill(order.order_id, shares=order.qty - 2, price=100.2, at=late)
    with reads.bind_read(who[1]):
        first = reconcile(execution, late + timedelta(seconds=1))
        again = reconcile(execution, late + timedelta(seconds=1))
    assert first["status"] == again["status"] == "completed" and again["reused"]
    assert len(snapshot(execution.store)["fills"]) == 3
    assert snapshot(execution.store)["decision_revisions"] == partial["decision_revisions"]
    assert len(execution.broker.accepted) == 2


def test_process_death_during_joint_recovery_keeps_real_entries_closed(
    execution, accepted, record_property
):
    state, who, proof, checks = accepted
    run(request("stop", who, "disabled"), checks)
    recovery = request("process-restore", who, "target")
    code = """import json,os,sys
from types import SimpleNamespace
sys.path.insert(0,'tests')
from test_phase_f_state_stop import gates,run
from ats.workflow import boundary_evidence as be
be.assert_enforced=lambda *a,**k:None
from ats.execution.broker_write_guard import prohibit_broker_writes,REASON_ISOLATED
from ats.memory import get_store
from ats.workflow.scoped_routes import RouteIdentity
from pathlib import Path
prohibit_broker_writes(reason_code=REASON_ISOLATED)
req=json.loads(sys.argv[1]);who=[RouteIdentity(**m['identity']) for m in req['members']]
checks=gates(SimpleNamespace(store=get_store()),who,Path(sys.argv[2]))
run(req,checks,checkpoint=lambda phase:os._exit(73) if phase=='routes_committed' else None)
"""
    child = subprocess.run(
        [
            "uv",
            "run",
            "--offline",
            "--no-sync",
            "python",
            "-c",
            code,
            json.dumps(recovery),
            str(proof),
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 73, child.stdout + child.stderr
    assert not joint.inspect("process-restore")["can_run"]
    before = snapshot(execution.store)
    with pytest.raises(plane.CutoverError):
        approval(execution, state)
    with pytest.raises(RouteUnavailable, match="joint batch frozen"):
        reconcile(execution, datetime.now(UTC))
    assert snapshot(execution.store) == before
    result = run(recovery, checks)
    assert result["can_run"] and run(recovery, checks) == result
    with reads.bind_read(who[1]):
        assert reconcile(execution, datetime.now(UTC))["status"] == "completed"
    record_property(
        "actual_process_death_restore",
        json.dumps(
            {
                "root": str(verified_isolation_root()),
                "exit_code": child.returncode,
                "restore": result,
                "revision": state.revision_hash,
            }
        ),
    )
