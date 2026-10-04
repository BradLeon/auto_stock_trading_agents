from threading import Lock
import time

import pytest

from ats.agent.task_projection import (ProjectionScope, build_envelope,
                                      projection_context)
from ats.config import REPO_ROOT
from ats.memory import get_store, reset_store_cache, task_store_scope
from ats.workflow.dispatcher import (AdapterUnavailable, Dispatcher,
                                     TaskAdapterResult)
from ats.workflow.phase_e import TASK_ROLE, build_plan
from ats.workflow.run_contracts import TriggerContext
from ats.workflow.store import WorkflowStore


def _config(tmp_path, targets=("AAA", "BBB")):
    root = tmp_path / "config"
    (root / "workflow").mkdir(parents=True)
    (root / "sectors").mkdir()
    (root / "workflow" / "decision_profiles.yaml").write_text(
        "schema_version: 1\nprofiles: {}\n", encoding="utf-8")
    (root / "sectors" / "custom.yaml").write_text(
        "name: custom\nlayers:\n  - key: L1\n    claims: []\n", encoding="utf-8")
    (root / "risk.yaml").write_text("sector_layer_caps: {}\n", encoding="utf-8")
    (root / "pead.yaml").write_text(
        "targets: [" + ", ".join(targets) + "]\n", encoding="utf-8")
    (root / "technical.yaml").write_text("name: technical\n", encoding="utf-8")
    return root


@pytest.fixture
def db_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("ATS_DB_PATH", str(tmp_path / "workflow.sqlite"))
    monkeypatch.setenv("ATS_DATA_DB_PATH", str(tmp_path / "data.sqlite"))
    reset_store_cache()
    yield tmp_path / "workflow.sqlite"
    try:
        get_store().close()
    except Exception:
        pass
    reset_store_cache()


def _trigger():
    return TriggerContext(kind="schedule", schedule_id="test-daily",
                          scheduled_for="2026-09-24T00:00:00+00:00")


def _payload(role, scope):
    symbol = scope.id or "AAA"
    return {
        "layer_analysis": {"layer": symbol, "status": "expanding", "summary": "s",
                           "findings": ["f"], "confidence": 0.8},
        "information_brief": {"entity": symbol, "headline": "h", "summary": "s",
                               "relevance": "high", "sources": ["doc:v1"]},
        "sector_allocation": {"sector": symbol, "stance": "neutral", "target_weight": 0.2,
                              "rationale": "r", "drivers": []},
        "fundamental_expectation_update": {"entity": symbol, "metric": "revenue",
                                           "period": "FY26Q3", "new_value": 10,
                                           "driver": "d"},
        "fundamental_event_review": {"entity": symbol, "event": "earnings",
                                     "period": "FY26Q3", "direction": 1,
                                     "magnitude": 0.1},
        "macro_review": {"regime": "transition", "summary": "s", "indicators": ["i"]},
        "technical_review": {"entity": symbol, "signal": "neutral", "summary": "s",
                             "levels": {"close": 100}},
    }[role]


def _fake_adapter(context, *, calls=None, lock=None, fail=None, delay=0.0):
    if calls is not None:
        with lock:
            calls.append((context.task.task_id, context.task.scope.key, "start"))
    if delay:
        time.sleep(delay)
    if fail and fail(context):
        raise AdapterUnavailable("synthetic role adapter unavailable")
    role = TASK_ROLE[context.task.task_id]
    envelope = build_envelope(
        role=role, payload=_payload(role, context.task.scope), scope=context.task.scope,
        as_of=context.plan.as_of, input_refs=context.input_refs,
        data_vintage_refs=context.data_vintage_refs)
    context.store.save_task_projection_envelope(envelope)
    if calls is not None:
        with lock:
            calls.append((context.task.task_id, context.task.scope.key, "end"))
    return TaskAdapterResult(detail="synthetic")


def _adapter_map(**kwargs):
    return {task_id: (lambda context, **kw: _fake_adapter(context, **kw))
            for task_id in TASK_ROLE}


def test_dispatcher_runs_independent_branches_and_blocks_only_failed_dependents(
        tmp_path, db_paths):
    config = _config(tmp_path)
    plan = build_plan(
        requested_tasks=("sector-review", "fundamental-routine", "macro-review",
                         "technical-review"),
        scope=ProjectionScope(kind="sector", id="custom"), trigger=_trigger(),
        as_of="2026-09-24T00:00:00+00:00", run_id="dispatch-1", config_dir=config,
        task_inputs={"data_vintage_refs": ["facts@v1"]})
    calls, lock = [], Lock()

    def fail_information_for_aaa(context):
        return (context.task.task_id == "information-brief"
                and context.task.scope.id == "AAA")

    adapters = {task_id: (lambda context: _fake_adapter(
        context, calls=calls, lock=lock, fail=fail_information_for_aaa, delay=0.01))
        for task_id in TASK_ROLE}
    result = Dispatcher(workflow_store=WorkflowStore(db_paths), adapters=adapters,
                        max_workers=4, resource_limits={"llm": 4}).dispatch(plan)
    by_task = {}
    for outcome in result.outcomes:
        by_task.setdefault(outcome.task_id, []).append(outcome)
    assert result.status == "incomplete"
    assert by_task["information-brief"][0].status == "missing"
    blocked = [item for item in by_task["fundamental-routine"] if item.scope.id == "AAA"]
    assert blocked[0].status == "blocked"
    assert all(item.status == "succeeded" for item in by_task["macro-review"])
    assert all(item.status == "succeeded" for item in by_task["technical-review"])
    assert all(item.status == "succeeded" for item in by_task["sector-review"])
    assert result.decision_cycle_entered is False
    # A run_once coordinator gives the full Information fan-out one shared corpus pass.
    # Synthetic adapters do not call it, but the execution context carries it.
    starts = [item for item in calls if item[2] == "start"]
    assert len(starts) == len(plan.tasks) - 1  # the failed upstream blocks its dependent


def test_projection_reuse_requires_exact_inputs_and_data_vintage(tmp_path, db_paths):
    config = _config(tmp_path, targets=("AAA",))
    calls = []
    adapters = {task_id: (lambda context: _fake_adapter(context, calls=calls, lock=Lock()))
                for task_id in TASK_ROLE}
    dispatcher = Dispatcher(workflow_store=WorkflowStore(db_paths), adapters=adapters)

    def make_plan(run_id, vintage):
        return build_plan(requested_tasks=("macro-review",),
                          scope=ProjectionScope(kind="portfolio"), trigger=_trigger(),
                          as_of="2026-09-24T00:00:00+00:00", run_id=run_id,
                          config_dir=config,
                          task_inputs={"data_vintage_refs": [vintage]})

    first = dispatcher.dispatch(make_plan("reuse-1", "macro@2026-09-24"))
    second = dispatcher.dispatch(make_plan("reuse-2", "macro@2026-09-24"))
    third = dispatcher.dispatch(make_plan("reuse-3", "macro@2026-09-25"))
    assert sum(1 for item in calls if item[2] == "start") == 2
    assert first.outcomes[0].reused is False
    assert second.outcomes[0].reused is True
    assert third.outcomes[0].reused is False
    attempt = WorkflowStore(db_paths).attempts_for_run("reuse-2")[0]
    assert attempt["reuse_decision"]["reason"] == "reusable"


def test_recovery_finds_projection_published_before_worker_crash(tmp_path, db_paths):
    config = _config(tmp_path)
    plan = build_plan(requested_tasks=("macro-review",),
                      scope=ProjectionScope(kind="portfolio"), trigger=_trigger(),
                      as_of="2026-09-24T00:00:00+00:00", run_id="crash-recovery",
                      config_dir=config,
                      task_inputs={"data_vintage_refs": ["macro@v1"]})
    task = plan.tasks[0]
    wf = WorkflowStore(db_paths)
    request = {"run_id": plan.run_id, "trigger": plan.trigger,
               "requested_tasks": list(plan.requested_tasks),
               "scope": plan.request_scope.model_dump(mode="json"), "as_of": plan.as_of,
               "task_inputs": plan.task_inputs, "enter_decision_cycle": False}
    wf.create_run(run_id=plan.run_id, request=request, plan=plan.as_dict(),
                  profile_version=plan.profile_version, plan_hash=plan.plan_hash)
    wf.set_run_state(plan.run_id, status="running", expected=("planned",))
    input_refs = [f"config:{p}@{digest}"
                  for p, digest in sorted(plan.source_config_hashes.items())]
    attempt = wf.start_attempt(
        run_id=plan.run_id, task_instance_key=task.instance_key, task_id=task.task_id,
        scope=task.scope.model_dump(mode="json"), attempt_no=1,
        input_refs=input_refs, data_vintage_refs=["macro@v1"])
    with task_store_scope(db_paths) as memory:
        with projection_context(workflow_run_id=plan.run_id,
                               agent_run_id=attempt["agent_run_id"],
                               input_refs=input_refs,
                               data_vintage_refs=["macro@v1"]):
            role = TASK_ROLE[task.task_id]
            memory.save_task_projection_envelope(build_envelope(
                role=role, payload=_payload(role, task.scope), scope=task.scope,
                as_of=plan.as_of, input_refs=input_refs,
                data_vintage_refs=["macro@v1"]))

    def should_not_run(_):
        raise AssertionError("restored projection must be used")

    result = Dispatcher(workflow_store=wf,
                        adapters={"macro-review": should_not_run}).dispatch(plan)
    assert result.complete
    assert result.outcomes[0].detail == "recovered_published_projection"
    assert wf.attempts_for_run(plan.run_id)[0]["status"] == "succeeded"


def test_timeout_fences_late_projection_publication(tmp_path, db_paths):
    config = _config(tmp_path)
    plan = build_plan(requested_tasks=("macro-review",),
                      scope=ProjectionScope(kind="portfolio"), trigger=_trigger(),
                      as_of="2026-09-24T00:00:00+00:00", run_id="late-write",
                      config_dir=config,
                      task_inputs={"data_vintage_refs": ["macro@v1"]})

    def slow(context):
        time.sleep(0.08)
        role = TASK_ROLE[context.task.task_id]
        context.store.save_task_projection_envelope(build_envelope(
            role=role, payload=_payload(role, context.task.scope), scope=context.task.scope,
            as_of=context.plan.as_of, input_refs=context.input_refs,
            data_vintage_refs=context.data_vintage_refs))
        return TaskAdapterResult()

    result = Dispatcher(workflow_store=WorkflowStore(db_paths),
                        adapters={"macro-review": slow}, max_workers=2,
                        timeout_overrides={"macro-review": 0.01}).dispatch(plan)
    assert result.status == "incomplete"
    assert result.outcomes[0].status == "stale"
    time.sleep(0.2)  # let the quarantined worker attempt its fenced write
    with task_store_scope(db_paths) as memory:
        assert memory.task_projection_envelopes(agent_role="macro_review",
                                                workflow_run_id=plan.run_id) == []
    attempts = WorkflowStore(db_paths).attempts_for_run(plan.run_id)
    assert all(item["status"] == "cancelled" for item in attempts)


def test_complete_profile_passes_exact_manifest_to_chief_snapshot_gate(db_paths):
    as_of = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    plan = build_plan(
        requested_tasks=("sector-review", "fundamental-routine", "macro-review",
                         "technical-review"),
        scope=ProjectionScope(kind="sector", id="ai_hardware"), trigger=_trigger(),
        as_of=as_of, run_id="complete-profile", profile_id="ai_hardware",
        enter_decision_cycle=True, config_dir=REPO_ROOT / "config",
        task_inputs={"data_vintage_refs": ["research-snapshot@v1"]})
    adapters = {task_id: _fake_adapter for task_id in TASK_ROLE}
    called = []

    def chief_runner(snapshot, manifest):
        called.append((snapshot, manifest))
        return {"accepted_snapshot": snapshot.complete}

    result = Dispatcher(
        workflow_store=WorkflowStore(db_paths), adapters=adapters,
        max_workers=8,
        resource_limits={"llm": 8, "information": 8, "pead": 8, "market-data": 8},
        chief_runner=chief_runner).dispatch(plan)
    assert result.complete
    assert result.decision_cycle_ready and result.decision_cycle_entered
    assert result.chief_snapshot["complete"] is True
    assert len(called) == 1 and called[0][0].complete
    manifest = called[0][1]
    assert len(manifest) == sum(len(item.projections) for item in result.outcomes)
    assert {item["role"] for item in manifest} >= {
        "layer_analysis", "information_brief", "sector_allocation",
        "fundamental_expectation_update", "macro_review", "technical_review"}


def test_event_research_with_superseded_calendar_version_cannot_enter_chief(db_paths):
    from ats.data.stores.schedule_calendar import ScheduleCalendarStore, ScheduleEventCandidate

    calendar = ScheduleCalendarStore()
    event_id = "cpi:macro:CPI:2026-08:initial"
    first = calendar.submit_candidate(ScheduleEventCandidate(
        source_id="bls", event_type="cpi", stable_identity="macro:CPI:2026-08:initial",
        event_date="2026-09-11", time_precision="date"))
    calendar.publish_candidate(first["candidate_id"])
    revised = calendar.submit_candidate(ScheduleEventCandidate(
        source_id="bls", event_type="cpi", stable_identity="macro:CPI:2026-08:initial",
        event_date="2026-09-12", time_precision="date"))
    calendar.publish_candidate(revised["candidate_id"])

    trigger = TriggerContext(kind="event", workflow_id="macro-review",
                             event_id=event_id, event_version="1")
    plan = build_plan(
        requested_tasks=("sector-review", "fundamental-routine", "macro-review",
                         "technical-review"),
        scope=ProjectionScope(kind="sector", id="ai_hardware"), trigger=trigger,
        as_of="2026-09-11T12:00:00+00:00", run_id="stale-calendar-event",
        profile_id="ai_hardware", enter_decision_cycle=True,
        config_dir=REPO_ROOT / "config",
        task_inputs={"data_vintage_refs": ["research-snapshot@v1"]})
    called = []
    result = Dispatcher(
        workflow_store=WorkflowStore(db_paths), adapters=_adapter_map(), max_workers=8,
        resource_limits={"llm": 8, "information": 8, "pead": 8, "market-data": 8},
        chief_runner=lambda *_: called.append(True), calendar_store=calendar).dispatch(plan)

    assert result.status == "complete"  # the old-version analysis remains auditable
    assert result.decision_cycle_ready is False
    assert result.decision_cycle_entered is False
    assert result.decision_block_reason == "stale_event_version"
    assert result.chief_snapshot["event_guard"]["current_version"] == "2"
    assert called == []


def test_event_route_material_contract_reaches_fundamental_adapter(db_paths):
    trigger = TriggerContext(kind="event", workflow_id="fundamental-event",
                             event_id="earnings:AAA:FY2026Q3:release", event_version="2")
    plan = build_plan(
        requested_tasks=("fundamental-event",),
        scope=ProjectionScope(kind="entity", id="AAA"), trigger=trigger,
        as_of="2026-09-24T12:00:00+00:00", run_id="release-inputs",
        task_inputs={"event_id": "earnings:AAA:FY2026Q3:release",
                     "event_version": "2", "fiscal_label": "FY2026Q3",
                     "fundamental_trigger": "earnings_release",
                     "material_state": "admitted",
                     "admitted_material_refs": ["filing:AAA@v2"]})
    dispatcher = Dispatcher(workflow_store=WorkflowStore(db_paths), adapters={})
    task = next(item for item in plan.tasks if item.task_id == "fundamental-event")

    _, _, task_inputs = dispatcher._task_inputs(plan, task, {})

    assert task_inputs["event_id"] == "earnings:AAA:FY2026Q3:release"
    assert task_inputs["event_version"] == "2"
    assert task_inputs["fiscal_label"] == "FY2026Q3"
    assert task_inputs["fundamental_trigger"] == "earnings_release"
    assert task_inputs["material_state"] == "admitted"
    assert task_inputs["admitted_material_refs"] == ["filing:AAA@v2"]
