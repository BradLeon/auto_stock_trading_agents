from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
import time

from ats.workflow.run_contracts import TriggerContext
from ats.workflow.store import WorkflowStore
from ats.workflow.triggers import (TriggerPolicy, TriggerService, evaluate_misfire,
                                   _deterministic_run_id, _request_fingerprint,
                                   reconcile_schedule, trigger_key)


class _Result:
    status = "complete"

    def as_dict(self):
        return {"status": self.status}


class _Dispatcher:
    def __init__(self, entered=None):
        self.entered = entered
        self.calls = 0

    def dispatch(self, plan, **kwargs):
        self.calls += 1
        if self.entered:
            self.entered.set()
            time.sleep(0.15)
        return _Result()


def _event_context(requested_at="2026-09-24T10:00:00+00:00"):
    return TriggerContext(kind="event", workflow_id="fundamental-event",
                          event_id="earnings:NVDA:FY2026Q3:release", event_version="v2",
                          requested_at=requested_at)


def test_trigger_keys_use_original_schedule_tick_or_versioned_event():
    t1 = TriggerContext(kind="schedule", workflow_id="macro-review", schedule_id="weekly",
                        scheduled_for="2026-09-24T08:00:00-04:00",
                        requested_at="2026-09-24T12:00:00+00:00")
    t2 = t1.model_copy(update={"scheduled_for": "2026-09-24T12:00:00Z",
                               "requested_at": "2026-09-25T12:00:00Z"})
    assert trigger_key(t1) == trigger_key(t2)
    assert trigger_key(_event_context()) == trigger_key(_event_context("2026-09-25T00:00:00Z"))
    assert trigger_key(_event_context()) != trigger_key(
        _event_context().model_copy(update={"event_version": "v3"}))


def test_equivalent_schedule_offsets_share_fingerprint_and_logical_run(tmp_path):
    store = WorkflowStore(tmp_path / "wf.sqlite")
    dispatcher = _Dispatcher()
    eastern = TriggerContext(kind="schedule", workflow_id="macro-review", schedule_id="weekly",
                             scheduled_for="2026-09-24T08:00:00-04:00")
    utc = eastern.model_copy(update={"scheduled_for": "2026-09-24T12:00:00Z"})
    service = TriggerService(store)
    first = service.dispatch(eastern, request={"scope": "portfolio"},
                             plan_factory=lambda *_: object(), dispatcher=dispatcher)
    replay = service.dispatch(utc, request={"scope": "portfolio"},
                              plan_factory=lambda *_: object(), dispatcher=dispatcher)

    assert replay["duplicate"] is True
    assert replay["run_id"] == first["run_id"]
    assert dispatcher.calls == 1


def test_misfire_grace_lookback_and_budget_are_auditable(tmp_path):
    policy = TriggerPolicy(misfire_grace_seconds=3600, max_lookback_seconds=7200,
                          compensation_budget=1)
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    planned = now - timedelta(minutes=30)
    assert evaluate_misfire(scheduled_for=planned, now=now, policy=policy)[0] == "dispatch"
    assert evaluate_misfire(scheduled_for=now - timedelta(hours=3), now=now,
                             policy=policy)[1] == "misfire_grace_exceeded"
    assert evaluate_misfire(scheduled_for=planned, now=now, policy=policy,
                             compensation_count=1)[1] == "compensation_budget_exhausted"

    store = WorkflowStore(tmp_path / "wf.sqlite")
    ctx = TriggerContext(kind="schedule", workflow_id="macro-review", schedule_id="weekly",
                         scheduled_for=(now - timedelta(hours=3)).isoformat())
    decisions = reconcile_schedule(store, expected=[(ctx, {"task": "macro-review"})],
                                   now=now, policy=policy)
    assert decisions[0]["status"] == "skipped"
    row = store.list_triggers(workflow_id="macro-review")[0]
    assert row["status"] == "skipped"
    assert row["reason_code"] == "misfire_grace_exceeded"
    assert row["actual_lag_seconds"] == 10_800


def test_trading_session_policy_skips_holiday_and_fails_closed_without_calendar(monkeypatch):
    import ats.runtime.scheduler as scheduler

    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    policy = TriggerPolicy(require_trading_session=True)
    monkeypatch.setattr(scheduler, "is_trading_session", lambda _: False)
    assert evaluate_misfire(
        scheduled_for="2026-09-24T08:00:00-04:00", now=now,
        policy=policy)[:2] == ("skip", "non_trading_session")

    def unavailable(_):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(scheduler, "is_trading_session", unavailable)
    assert evaluate_misfire(
        scheduled_for="2026-09-24T08:00:00-04:00", now=now,
        policy=policy)[:2] == ("skip", "trading_calendar_unavailable")


def test_misfire_never_overwrites_active_lease_and_skips_expired_claim(tmp_path):
    store = WorkflowStore(tmp_path / "wf.sqlite")
    planned = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    policy = TriggerPolicy(misfire_grace_seconds=3600, max_lookback_seconds=14_400)
    dispatcher = _Dispatcher()

    def claimed_context(schedule_id):
        context = TriggerContext(kind="schedule", workflow_id="macro-review",
                                 schedule_id=schedule_id, scheduled_for=planned.isoformat())
        request = {"scope": "portfolio"}
        fingerprint = _request_fingerprint(context, request)
        key = trigger_key(context)
        store.record_trigger(trigger_key=key, kind="schedule", workflow_id="macro-review",
                             request=fingerprint, schedule_id=schedule_id,
                             scheduled_for=planned.isoformat(), at=planned)
        store.claim_trigger(trigger_key=key, kind="schedule", workflow_id="macro-review",
                            request=fingerprint, owner_id="worker-old", lease_seconds=10,
                            at=planned)
        return context, request, key

    active_context, active_request, active_key = claimed_context("active")
    active_time = planned + timedelta(hours=2)
    store.renew_trigger(active_key, owner_id="worker-old", lease_seconds=3600, at=active_time)
    active_result = TriggerService(store, policy=policy).dispatch(
        active_context, request=active_request, plan_factory=lambda *_: object(),
        dispatcher=dispatcher, now=active_time)
    assert active_result["status"] == "running"
    assert store.get_trigger(active_key)["status"] == "running"

    expired_context, expired_request, expired_key = claimed_context("expired")
    expired_result = TriggerService(store, policy=policy).dispatch(
        expired_context, request=expired_request, plan_factory=lambda *_: object(),
        dispatcher=dispatcher, now=planned + timedelta(hours=2))
    assert expired_result["status"] == "skipped"
    assert store.get_trigger(expired_key)["status"] == "skipped"
    assert dispatcher.calls == 0


def test_explicit_compensation_reopens_same_logical_run_before_dispatch(tmp_path):
    store = WorkflowStore(tmp_path / "wf.sqlite")
    context = _event_context()
    request = {"scope": "NVDA"}
    fingerprint = _request_fingerprint(context, request)
    key = trigger_key(context)
    run_id = _deterministic_run_id(key)
    store.record_trigger(trigger_key=key, kind="event", workflow_id=context.workflow_id,
                         request=fingerprint, event_id=context.event_id,
                         event_version=context.event_version)
    store.claim_trigger(trigger_key=key, kind="event", workflow_id=context.workflow_id,
                        request=fingerprint, owner_id="old-worker", run_id=run_id)
    store.create_run(run_id=run_id, request=fingerprint, plan={"version": 1},
                     trigger_key=key, status="incomplete")
    store.finish_trigger(key, owner_id="old-worker", status="incomplete",
                         reason_code="missing_material")
    assert store.compensate_trigger(key, actor="operator", reason="material admitted")

    observed = []

    class InspectingDispatcher:
        def dispatch(self, plan, **_):
            observed.append((plan.run_id, store.get_run(plan.run_id)["status"]))
            return _Result()

    result = TriggerService(store, owner_id="retry-worker").dispatch(
        context, request=request, plan_factory=lambda *_: type(
            "Plan", (), {"run_id": run_id})(),
        dispatcher=InspectingDispatcher())

    assert result["run_id"] == run_id
    assert observed == [(run_id, "planned")]
    assert store.get_trigger(key)["attempt_count"] == 2
    assert store.get_run(run_id)["status"] == "planned"


def test_lease_renewal_failure_fences_success_result(tmp_path):
    class LostRenewalStore(WorkflowStore):
        def renew_trigger(self, *args, **kwargs):
            return False

    class SlowDispatcher:
        def dispatch(self, plan, **_):
            time.sleep(1.2)
            return _Result()

    store = LostRenewalStore(tmp_path / "wf.sqlite")
    result = TriggerService(
        store, policy=TriggerPolicy(lease_seconds=3)).dispatch(
            _event_context(), request={"scope": "NVDA"},
            plan_factory=lambda *_: object(), dispatcher=SlowDispatcher())

    assert result["status"] == "lease_lost"
    assert store.list_triggers(workflow_id="fundamental-event")[0]["status"] == "running"


def test_same_event_replay_uses_first_created_at_and_only_dispatches_once(tmp_path):
    store = WorkflowStore(tmp_path / "wf.sqlite")
    service = TriggerService(store, owner_id="worker-a")
    dispatcher = _Dispatcher()
    seen = []

    def plan_factory(context, request, run_id):
        seen.append((context.requested_at, run_id))
        return object()

    first = service.dispatch(_event_context(), request={"scope": "NVDA"},
                             plan_factory=plan_factory, dispatcher=dispatcher,
                             now=datetime(2026, 9, 24, 10, tzinfo=timezone.utc))
    replay = TriggerService(store, owner_id="worker-b").dispatch(
        _event_context("2026-09-25T10:00:00+00:00"), request={"scope": "NVDA"},
        plan_factory=plan_factory, dispatcher=dispatcher,
        now=datetime(2026, 9, 25, 10, tzinfo=timezone.utc))
    assert first["status"] == "complete"
    assert replay["duplicate"] is True
    assert replay["run_id"] == first["run_id"]
    assert dispatcher.calls == 1
    assert seen[0][0] == "2026-09-24T10:00:00.000000+00:00"


def test_manual_dispatch_returns_reusable_operator_trigger_id(tmp_path):
    store = WorkflowStore(tmp_path / "wf.sqlite")
    result = TriggerService(store).dispatch(
        TriggerContext(kind="manual", workflow_id="macro-review"),
        request={"scope": "portfolio"}, plan_factory=lambda *_: object(),
        dispatcher=_Dispatcher())

    assert result["trigger_id"].startswith("manual-")
    replay = TriggerService(store).dispatch(
        TriggerContext(kind="manual", workflow_id="macro-review",
                       trigger_id=result["trigger_id"]),
        request={"scope": "portfolio"}, plan_factory=lambda *_: object(),
        dispatcher=_Dispatcher())
    assert replay["duplicate"] is True
    assert replay["trigger_key"] == result["trigger_key"]


def test_two_workers_cannot_execute_same_trigger_concurrently(tmp_path):
    store = WorkflowStore(tmp_path / "wf.sqlite")
    entered = threading.Event()
    dispatcher = _Dispatcher(entered)
    results = []

    def invoke(owner):
        return TriggerService(store, owner_id=owner).dispatch(
            _event_context(), request={"scope": "NVDA"},
            plan_factory=lambda *_: object(), dispatcher=dispatcher)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke, "worker-a")
        assert entered.wait(2)
        second = pool.submit(invoke, "worker-b")
        results.append(first.result(timeout=3))
        results.append(second.result(timeout=3))
    assert dispatcher.calls == 1
    assert sum(result.get("duplicate", False) for result in results) == 1


def test_event_route_rejects_unadmitted_release_material():
    from ats.workflow.routing import RouteError, resolve_event_route

    event = {"event_type": "earnings", "event_subtype": "release", "status": "released",
             "event_id": "earnings:NVDA:FY2026Q3:release", "event_version": "1"}
    try:
        resolve_event_route(event, materials=[])
    except RouteError as exc:
        assert "not admitted" in str(exc) or "no required" in str(exc)
    else:
        raise AssertionError("an unadmitted event must not route")

    route = resolve_event_route(event, materials=[
        {"kind": "filing", "status": "admitted"},
    ])
    assert route.workflow_ids == ("information-brief", "fundamental-event")
