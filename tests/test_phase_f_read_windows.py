"""Window/clock protocol with real joint SQL authority and schedule entry.

Qualification, enforcement and acceptance conclusions are isolated fixtures;
these tests do not issue production evidence or certify complete role coverage.
"""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_phase_f_joint_cutover import gates

from ats.agent.task_projection import ProjectionScope
from ats.workflow import cutover as plane
from ats.workflow import joint_cutover as joint
from ats.workflow import read_windows as windows
from ats.workflow import runtime_reads as reads
from ats.workflow import schedule_runtime as scheduling
from ats.workflow import scoped_routes as scopes
from ats.workflow.consumer_reads import record_read, trace_reads
from ats.workflow.cutover_routing import RouteUnavailable
from ats.workflow.isolation import isolated_run
from ats.workflow.phase_e import build_plan
from ats.workflow.run_contracts import TriggerContext
from ats.workflow.store import WorkflowStore
from ats.workflow.triggers import TriggerService


@pytest.fixture
def deployed(tmp_path, monkeypatch, request):
    persistent = getattr(request, "param", "finite") == "persistent"
    with isolated_run("read-windows", root=tmp_path / "iso"):
        scheduling.initialize()
        now = datetime.now(UTC)
        start, end = now - timedelta(minutes=1), now + timedelta(minutes=10)
        scope = {"kind": "entity", "id": "COHR", "entities": ["COHR"]}
        scope.update({"time_policy": "per_invocation", "deployment_purpose": "technical-review"} if persistent else
            {"time_range": {"start": start.isoformat(), "end": end.isoformat()}})
        who = reads.business_identity("technical", kind="entity", scope_id="COHR", entities=["COHR"],
            as_of=now, explicit=scope, purpose="technical-review")
        request = {"batch_id": "window-one", "workflow": "technical-review",
            "members": [{"boundary": plane.PROJECTION_READ, "identity": who.as_row(),
                         "route": "target", "expected_generation": 0}], "schedules": []}
        if persistent:
            request["deployment_policy"] = "until_stopped"
        monkeypatch.setattr("ats.workflow.boundary_evidence.assert_enforced", lambda *a, **k: None)
        monkeypatch.setattr("ats.data.assurance.qualification", lambda **k: {"status": "eligible"})
        monkeypatch.setattr("ats.workflow.shadow_reports.check_batch_report", lambda *a, **k: (True, []))
        adapter = gates()
        adapter["compatibility"] = lambda w, i, r: {"workflow": w, "identity": i.as_row(),
            "valid": True, "reference": "isolated-matrix", "edges": []}
        joint.record_authorization(request, reference="fixture-deployment",
            valid_until=(now + timedelta(hours=1)).isoformat(), actor="fixture", reason="explicit isolated window")
        joint.execute(request, gates=adapter, actor="fixture", reason="window protocol", mode="isolated")
        yield SimpleNamespace(who=who, now=now, start=start, end=end, request=request, gates=adapter)


def make_plan(point, trigger=None):
    return build_plan(run_id="window-" + point.isoformat(), requested_tasks=["technical-review"], scope=ProjectionScope(kind="entity", id="COHR"),
        as_of=point, trigger=trigger or TriggerContext(kind="manual", workflow_id="technical-review",
            trigger_id=point.isoformat()), enter_decision_cycle=False)


def test_same_window_two_ticks_keep_real_cutoffs_and_traces(deployed, record_property):
    s = deployed
    records = []
    before = s.who.key
    for point in (s.now, s.now + timedelta(minutes=1)):
        trigger = TriggerContext(kind="schedule", workflow_id="technical-review", schedule_id="tech",
            scheduled_for=point.isoformat(), requested_at=point.isoformat())
        def execute(point=point, trigger=trigger):
            plan = windows.bind_plan(make_plan(point, trigger))
            who = reads.task_identity(plan, plan.tasks[0])
            with reads.bind_read(who, as_of=plan.as_of), trace_reads() as rows:
                reads.guard_input("technical", {"entity": "COHR"}, as_of=point)
                record_read("technical", "isolated-input", refs=[point.isoformat()])
                records.append((who.key, plan.as_of, rows[0]["invocation_as_of"]))
            return {"as_of": plan.as_of, "identity": who.as_row()}
        scheduling.execute("technical-review", {"kind": "entity", "id": "COHR"}, trigger,
            execute, owner="legacy")
    assert [r[0] for r in records] == [before, before]
    assert records[0][1] != records[1][1]
    assert all(windows.invocation_time(r[1]) == windows.invocation_time(r[2]) for r in records)
    assert s.who.key == before  # identity selection must never mutate its scope
    record_property("independent_invocation_records", str(records))


@pytest.mark.parametrize("position", ["start", "end"])
def test_inclusive_boundaries(deployed, position):
    point = getattr(deployed, position)
    who = windows.select(reads.task_identity(make_plan(point), make_plan(point).tasks[0]),
        workflow="technical-review", as_of=point, purpose="technical-review")
    with reads.bind_read(who, as_of=point) as context:
        assert context.cutoff == point


@pytest.mark.parametrize("position", ["before", "after"])
def test_outside_window_refuses(deployed, position):
    point = deployed.start - timedelta(microseconds=1) if position == "before" else deployed.end + timedelta(microseconds=1)
    with pytest.raises(RouteUnavailable):
        windows.bind_plan(make_plan(point))
    with pytest.raises(RouteUnavailable, match="outside"):
        reads.bind_read(deployed.who, as_of=point).__enter__()


@pytest.mark.parametrize("deployed", ["finite", "persistent"], indirect=True)
def test_authorization_revoked_between_ticks(deployed):
    windows.bind_plan(make_plan(deployed.now))
    joint.revoke_authorization("fixture-deployment", actor="fixture", reason="revoked before next tick")
    with pytest.raises(RouteUnavailable, match="authorization"):
        windows.bind_plan(make_plan(deployed.now + timedelta(minutes=1)))


@pytest.mark.parametrize("deployed", ["finite", "persistent"], indirect=True)
def test_report_revoked_between_ticks(deployed, monkeypatch):
    windows.bind_plan(make_plan(deployed.now))
    monkeypatch.setattr("ats.workflow.shadow_reports.check_batch_report", lambda *a, **k: (False, ["report revoked"]))
    with pytest.raises(RouteUnavailable, match="report revoked"):
        windows.bind_plan(make_plan(deployed.now + timedelta(minutes=1)))


@pytest.mark.parametrize("deployed", ["finite", "persistent"], indirect=True)
def test_qualification_revoked_between_ticks(deployed, monkeypatch):
    windows.bind_plan(make_plan(deployed.now))
    monkeypatch.setattr("ats.data.assurance.qualification", lambda **k: {"status": "ineligible", "reasons": ["revoked"]})
    with pytest.raises(RouteUnavailable):
        windows.bind_plan(make_plan(deployed.now + timedelta(minutes=1)))


@pytest.mark.parametrize("change", ["workflow", "entity", "consumer", "event"])
@pytest.mark.parametrize("deployed", ["finite", "persistent"], indirect=True)
def test_binding_cannot_escape_business_identity(deployed, change):
    plan = make_plan(deployed.now)
    if change == "workflow":
        plan = replace(plan, trigger={**plan.trigger, "workflow_id": "other-workflow"})
    elif change == "entity":
        plan = build_plan(run_id="other", requested_tasks=["technical-review"], scope=ProjectionScope(kind="entity", id="NVDA"),
            as_of=deployed.now, trigger=TriggerContext(kind="manual", workflow_id="technical-review", trigger_id="other"),
            enter_decision_cycle=False)
    elif change == "consumer":
        row = deployed.who.as_row()
        explicit = {**row, "consumer_id": "macro"}
        plan = replace(plan, task_inputs={"read_scopes": {plan.tasks[0].instance_key: explicit}})
    else:
        plan = replace(plan, task_inputs={"event_id": "release", "event_version": "v2"})
    with pytest.raises(RouteUnavailable):
        windows.bind_plan(plan)


def test_overlapping_windows_refuse_unless_explicit(deployed):
    s = deployed
    other = scopes.RouteIdentity(s.who.domain_id, s.who.consumer_id, s.who.contract_version,
        {**s.who.scope, "time_range": {"start": s.start.isoformat(), "end": (s.end + timedelta(minutes=1)).isoformat()}})
    req = {**s.request, "batch_id": "window-two", "members": [{"boundary": plane.PROJECTION_READ,
        "identity": other.as_row(), "route": "target", "expected_generation": 0}]}
    joint.record_authorization(req, reference="second-deployment",
        valid_until=(s.now + timedelta(hours=1)).isoformat(), actor="fixture", reason="isolated overlap")
    adapter = {**s.gates, "authorization": lambda m, w: {"member": m, "workflow": w,
        "valid": True, "reference": "second-deployment"}}
    joint.execute(req, gates=adapter, actor="fixture", reason="overlap", mode="isolated")
    plan = make_plan(s.now)
    with pytest.raises(RouteUnavailable, match="ambiguous"):
        windows.bind_plan(plan)
    explicit = replace(plan, task_inputs={"read_scopes": {plan.tasks[0].instance_key: s.who.as_row()}})
    bound = windows.bind_plan(explicit)
    assert reads.task_identity(bound, bound.tasks[0]) == s.who


@pytest.mark.parametrize("deployed", ["finite", "persistent"], indirect=True)
def test_old_worker_cannot_refresh_after_stop(deployed):
    s = deployed
    with reads.bind_read(s.who, as_of=s.now):
        req = {**s.request, "batch_id": "stop-window", "members": [{"boundary": plane.PROJECTION_READ,
            "identity": s.who.as_row(), "route": "disabled", "expected_generation": 1}]}
        joint.execute(req, gates=s.gates, actor="fixture", reason="stop", mode="isolated")
        with pytest.raises(RouteUnavailable):
            with reads.bind_read(s.who, as_of=s.now):
                pytest.fail("stale worker regained read authority")


@pytest.mark.parametrize("deployed", ["finite", "persistent"], indirect=True)
def test_nested_binding_cannot_replace_cutoff(deployed):
    with reads.bind_read(deployed.who, as_of=deployed.now):
        with pytest.raises(RouteUnavailable, match="cannot change"):
            with reads.bind_read(deployed.who, as_of=deployed.now + timedelta(seconds=1)):
                pytest.fail("cutoff replaced")


@pytest.mark.parametrize("deployed", ["finite", "persistent"], indirect=True)
def test_window_rebinding_does_not_repeat_completed_trigger(deployed):
    store = WorkflowStore()
    ctx = TriggerContext(kind="manual", workflow_id="technical-review", trigger_id="once")
    service = TriggerService(store)
    executed = []
    class Dispatcher:
        def dispatch(self, plan, **kwargs):
            executed.append(plan.run_id)
            return SimpleNamespace(status="complete", as_dict=lambda: {"status": "complete"})
    request = {"task_inputs": {"read_scopes": {"binding": deployed.who.as_row()}}}
    def factory(context, body, run_id):
        return replace(make_plan(deployed.now, context), run_id=run_id)
    first = service.dispatch(ctx, request=request, plan_factory=factory, dispatcher=Dispatcher())
    second = service.dispatch(ctx, request={"task_inputs": {"read_scopes": {"binding": "different-window"}}},
        plan_factory=factory, dispatcher=Dispatcher())
    assert first["status"] == "complete" and second["duplicate"]
    assert len(executed) == 1


@pytest.mark.parametrize("deployed", ["persistent"], indirect=True)
def test_persistent_permission_crosses_days_without_changing_input_cutoff(deployed, monkeypatch):
    s = deployed
    future = s.now + timedelta(days=2)
    class LaterClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return future.astimezone(tz) if tz else future.replace(tzinfo=None)
    monkeypatch.setattr(joint, "datetime", LaterClock)
    # The action credential has expired: it cannot deploy a new operation.
    with pytest.raises(plane.CutoverError, match="expired"):
        joint._authorization({"reference": "fixture-deployment"}, s.request, None)
    # An explicit committed persistent permission still serves this invocation.
    plan = windows.bind_plan(make_plan(future))
    who = reads.task_identity(plan, plan.tasks[0])
    assert who == s.who and "time_range" not in who.scope
    with reads.bind_read(who, as_of=plan.as_of) as context:
        assert context.cutoff == future
        with pytest.raises(RouteUnavailable, match="input time"):
            reads.guard_input("technical", {"entity": "COHR"}, as_of=s.now)
    joint.revoke_authorization("fixture-deployment", actor="fixture", reason="explicit stop")
    with pytest.raises(RouteUnavailable, match="authorization"):
        windows.bind_plan(make_plan(future + timedelta(minutes=1)))


def test_legacy_finite_permission_is_not_promoted(deployed, monkeypatch):
    future = deployed.now + timedelta(days=2)
    class LaterClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return future.astimezone(tz) if tz else future.replace(tzinfo=None)
    monkeypatch.setattr(joint, "datetime", LaterClock)
    with pytest.raises(RouteUnavailable, match="expired"):
        windows.bind_plan(make_plan(deployed.now))


def test_persistent_request_cannot_silently_reinterpret_fixed_scope(deployed):
    with pytest.raises(plane.CutoverError, match="per-invocation"):
        joint._validate({**deployed.request, "deployment_policy": "until_stopped"})
    with pytest.raises(ValueError, match="fixed time_range"):
        scopes.RouteIdentity(deployed.who.domain_id, deployed.who.consumer_id,
            deployed.who.contract_version, {**deployed.who.scope, "time_policy": "per_invocation"})
