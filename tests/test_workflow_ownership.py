from ats.workflow.ownership import (dispatch_planned_calendar_events,
                                    dispatch_calendar_event,
                                    load_workflow_owners, owner_mode,
                                    phase_e_schedule_entries,
                                    record_due_calendar_material_waits,
                                    reconcile_calendar_trigger_versions,
                                    dispatch_released_calendar_events,
                                    run_owned_workflow)
from ats.workflow.run_contracts import TriggerContext
from ats.agent.task_projection import ProjectionScope


def test_all_phase_e_workflow_owners_default_to_legacy_until_opted_in():
    owners = load_workflow_owners()
    assert {value["mode"] for value in owners["workflows"].values()} == {"legacy"}
    assert owner_mode("macro-review") == "legacy"
    assert phase_e_schedule_entries() == {}


def test_legacy_owner_does_not_invoke_dispatcher_or_open_phase_e_run():
    result = run_owned_workflow(
        "macro-review", scope=ProjectionScope(kind="portfolio", id="portfolio"),
        trigger=TriggerContext(kind="manual", workflow_id="macro-review"))
    assert result == {"workflow_id": "macro-review", "owner": "legacy", "status": "legacy"}


def test_calendar_pre_earnings_route_runs_only_inside_configured_lead_window(
        tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from ats.data.stores.schedule_calendar import (ScheduleCalendarStore,
                                                   ScheduleEventCandidate,
                                                   earnings_identity)

    monkeypatch.setenv("ATS_DB_PATH", str(tmp_path / "workflow.sqlite"))
    calendar = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    for symbol, day in (("AAA", "2026-10-01"), ("BBB", "2026-11-01")):
        candidate = ScheduleEventCandidate(
            source_id="test", event_type="earnings",
            stable_identity=earnings_identity(symbol, 2026, 3),
            event_date=day, time_precision="date", market_session="unknown",
            metadata={"entity": symbol, "fiscal_label": "FY2026Q3"})
        saved = calendar.submit_candidate(candidate)
        calendar.publish_candidate(saved["candidate_id"])

    result = dispatch_planned_calendar_events(
        calendar_store=calendar, now=datetime(2026, 9, 24, 12, tzinfo=timezone.utc))

    assert [item["event_id"] for item in result] == ["earnings:AAA:FY2026Q3:release"]
    assert {item["workflow_id"] for item in result[0]["workflows"]} == {
        "information-brief", "fundamental-routine"}
    assert all(item["status"] == "legacy" for item in result[0]["workflows"])


def test_unresolved_calendar_conflict_blocks_event_routing(tmp_path):
    import pytest

    from ats.data.stores.schedule_calendar import (ScheduleCalendarStore,
                                                   ScheduleEventCandidate,
                                                   macro_identity)
    from ats.workflow.ownership import OwnershipError

    calendar = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    identity = macro_identity("CPI", "2026-08", "initial")
    primary = ScheduleEventCandidate(source_id="bls", event_type="cpi",
                                     stable_identity=identity, event_date="2026-09-11",
                                     time_precision="date")
    accepted = calendar.submit_candidate(primary)
    calendar.publish_candidate(accepted["candidate_id"])
    calendar.confirm_release(
        primary.event_id, expected_version=1,
        admitted_materials=[{"kind": "official_release", "status": "admitted",
                             "ref": "macro:v1"}], source_id="data-platform")
    conflicting = calendar.submit_candidate(primary.model_copy(update={
        "source_id": "mirror", "event_date": __import__("datetime").date(2026, 9, 12)}))
    assert conflicting["review_status"] == "conflict"
    event = {**calendar.latest_events()[0], "event_subtype": "release"}

    with pytest.raises(OwnershipError, match="unresolved source conflicts"):
        dispatch_calendar_event(event, admitted_materials=[
            {"kind": "official_release", "status": "admitted", "ref": "macro:v1"}],
            calendar_store=calendar)


def test_calendar_revision_supersedes_old_pending_trigger_in_shadow_store(
        tmp_path, monkeypatch):
    import yaml

    from ats.data.stores.schedule_calendar import (ScheduleCalendarStore,
                                                   ScheduleEventCandidate,
                                                   macro_identity)
    from ats.workflow.store import WorkflowStore

    config = tmp_path / "config"
    workflow_dir = config / "workflow"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "workflow_owners.yaml").write_text(yaml.safe_dump({
        "version": "1", "shadow_database": "shadow.sqlite",
        "workflows": {"macro-review": {"mode": "shadow",
                                          "task_ids": ["macro-review"]}},
    }), encoding="utf-8")
    (workflow_dir / "event_routes.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "events": [{"event_type": "cpi", "event_subtype": "release",
                    "required_event_state": "released", "workflow_ids": ["macro-review"],
                    "scope_resolver": "portfolio"}],
    }), encoding="utf-8")
    shadow_db = tmp_path / "shadow.sqlite"
    monkeypatch.setenv("ATS_SHADOW_DB_PATH", str(shadow_db))
    calendar = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    identity = macro_identity("CPI", "2026-08", "initial")
    first = ScheduleEventCandidate(source_id="bls", event_type="cpi",
                                   stable_identity=identity, event_date="2026-09-11",
                                   time_precision="date")
    candidate = calendar.submit_candidate(first)
    event_v1 = calendar.publish_candidate(candidate["candidate_id"])
    store = WorkflowStore(shadow_db)
    store.record_trigger(trigger_key="old-event", kind="event", workflow_id="macro-review",
                         request={"event_version": "1"}, event_id=event_v1["event_id"],
                         event_version="1")
    revised = calendar.submit_candidate(first.model_copy(update={
        "event_date": __import__("datetime").date(2026, 9, 12)}))
    calendar.publish_candidate(revised["candidate_id"])

    changes = reconcile_calendar_trigger_versions(calendar_store=calendar, config_dir=config)

    assert changes == [{"event_id": event_v1["event_id"], "event_version": 2,
                        "workflow_id": "macro-review", "superseded": 1}]
    assert store.get_trigger("old-event")["status"] == "superseded"


def test_due_planned_release_waits_for_materials_then_routes_new_calendar_version(
        tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import yaml

    from ats.data.stores.schedule_calendar import (ScheduleCalendarStore,
                                                   ScheduleEventCandidate,
                                                   macro_identity)
    from ats.workflow.store import WorkflowStore

    config = tmp_path / "config"
    workflow_dir = config / "workflow"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "workflow_owners.yaml").write_text(yaml.safe_dump({
        "version": "1", "shadow_database": "shadow.sqlite",
        "workflows": {"macro-review": {"mode": "shadow",
                                          "task_ids": ["macro-review"]}},
    }), encoding="utf-8")
    (workflow_dir / "event_routes.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "events": [{"event_type": "cpi", "event_subtype": "release",
                    "required_event_state": "released", "workflow_ids": ["macro-review"],
                    "scope_resolver": "portfolio", "required_admitted_material": True,
                    "required_material_kinds": ["official_release"]}],
    }), encoding="utf-8")
    shadow_db = tmp_path / "shadow.sqlite"
    monkeypatch.setenv("ATS_SHADOW_DB_PATH", str(shadow_db))
    calendar = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    event = ScheduleEventCandidate(
        source_id="bls", event_type="cpi",
        stable_identity=macro_identity("CPI", "2026-08", "initial"),
        event_date="2026-09-23", time_precision="date")
    candidate = calendar.submit_candidate(event)
    planned = calendar.publish_candidate(candidate["candidate_id"])
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)

    first = record_due_calendar_material_waits(
        calendar_store=calendar, now=now, config_dir=config)
    replay = record_due_calendar_material_waits(
        calendar_store=calendar, now=now, config_dir=config)
    store = WorkflowStore(shadow_db)
    waiting = store.list_triggers(workflow_id="macro-review")[0]

    assert first[0]["status"] == "waiting_material"
    assert replay[0]["workflows"][0]["status"] == "waiting_material"
    assert waiting["reason_code"] == "release_material_not_admitted"
    assert waiting["attempt_count"] == 0
    assert store.list_runs() == []
    assert [row["to_status"] for row in store.trigger_history(waiting["trigger_key"])] == [
        "planned", "waiting_material"]

    released = calendar.confirm_release(
        planned["event_id"], expected_version=planned["event_version"],
        admitted_materials=[{"kind": "official_release", "status": "admitted",
                             "ref": "bls:cpi:2026-08"}],
        source_id="data-platform", at=now)
    reconcile_calendar_trigger_versions(calendar_store=calendar, config_dir=config, now=now)
    assert store.get_trigger(waiting["trigger_key"])["status"] == "superseded"

    calls = []

    def fake_run(workflow_id, **kwargs):
        calls.append((workflow_id, kwargs["trigger"].event_version,
                      kwargs["request"]["task_inputs"]["admitted_material_refs"]))
        return {"workflow_id": workflow_id, "status": "complete"}

    monkeypatch.setattr("ats.workflow.ownership.run_owned_workflow", fake_run)
    routed = dispatch_released_calendar_events(
        calendar_store=calendar, now=now, config_dir=config)

    assert released["event_version"] == 2
    assert calls == [("macro-review", "2", ["bls:cpi:2026-08"])]
    assert routed[0]["workflows"][0]["status"] == "complete"


def test_date_precision_wait_is_not_recorded_before_local_calendar_date(
        tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import yaml

    from ats.data.stores.schedule_calendar import (ScheduleCalendarStore,
                                                   ScheduleEventCandidate,
                                                   macro_identity)

    config = tmp_path / "config"
    workflow_dir = config / "workflow"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "workflow_owners.yaml").write_text(yaml.safe_dump({
        "version": "1", "shadow_database": "shadow.sqlite",
        "workflows": {"macro-review": {"mode": "shadow",
                                          "task_ids": ["macro-review"]}},
    }), encoding="utf-8")
    (workflow_dir / "event_routes.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "events": [{"event_type": "cpi", "event_subtype": "release",
                    "required_event_state": "released", "workflow_ids": ["macro-review"],
                    "scope_resolver": "portfolio", "required_admitted_material": True,
                    "required_material_kinds": ["official_release"]}],
    }), encoding="utf-8")
    monkeypatch.setenv("ATS_SHADOW_DB_PATH", str(tmp_path / "shadow.sqlite"))
    calendar = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    event = ScheduleEventCandidate(
        source_id="bls", event_type="cpi",
        stable_identity=macro_identity("CPI", "2026-09", "initial"),
        event_date="2026-09-25", time_precision="date")
    candidate = calendar.submit_candidate(event)
    calendar.publish_candidate(candidate["candidate_id"])

    result = record_due_calendar_material_waits(
        calendar_store=calendar,
        now=datetime(2026, 9, 24, 12, tzinfo=timezone.utc), config_dir=config)

    assert result == []


def test_owner_rollback_stops_new_claims_but_preserves_and_finishes_active_lease(
        tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace
    import yaml

    from ats.workflow.store import WorkflowStore

    config = tmp_path / "config"
    workflow_dir = config / "workflow"
    workflow_dir.mkdir(parents=True)
    owners_path = workflow_dir / "workflow_owners.yaml"
    owners = {"version": "1", "shadow_database": "shadow.sqlite",
              "workflows": {"macro-review": {
                  "mode": "shadow", "task_ids": ["macro-review"]}}}
    owners_path.write_text(yaml.safe_dump(owners), encoding="utf-8")
    shadow_db = tmp_path / "shadow.sqlite"
    monkeypatch.setenv("ATS_SHADOW_DB_PATH", str(shadow_db))

    entered, allow_finish = Event(), Event()

    class BlockingDispatcher:
        def __init__(self, **_):
            pass

        def dispatch(self, plan, *, trigger_key="", **_):
            store = WorkflowStore(shadow_db)
            request = {
                "run_id": plan.run_id, "trigger": plan.trigger,
                "requested_tasks": list(plan.requested_tasks),
                "scope": plan.request_scope.model_dump(mode="json"),
                "as_of": plan.as_of, "task_inputs": plan.task_inputs,
                "enter_decision_cycle": plan.enter_decision_cycle,
            }
            store.create_run(run_id=plan.run_id, request=request, plan=plan.as_dict(),
                             trigger_key=trigger_key,
                             profile_version=plan.profile_version, plan_hash=plan.plan_hash)
            store.set_run_state(plan.run_id, status="running", expected=("planned",))
            entered.set()
            assert allow_finish.wait(5)
            store.set_run_state(plan.run_id, status="complete", expected=("running",))
            return SimpleNamespace(status="complete", as_dict=lambda: {"status": "complete"})

    monkeypatch.setattr("ats.workflow.ownership.Dispatcher", BlockingDispatcher)
    active_trigger = TriggerContext(kind="manual", workflow_id="macro-review",
                                    trigger_id="rollback-active")
    scope = ProjectionScope(kind="portfolio", id="portfolio")
    result = {}

    def active_run():
        result.update(run_owned_workflow(
            "macro-review", scope=scope, trigger=active_trigger,
            request={"requested_tasks": ["macro-review"]}, config_dir=config))

    with ThreadPoolExecutor(max_workers=1) as executor:
        active = executor.submit(active_run)
        assert entered.wait(5)
        owners["workflows"]["macro-review"]["mode"] = "legacy"
        owners_path.write_text(yaml.safe_dump(owners), encoding="utf-8")

        rolled_back = run_owned_workflow(
            "macro-review", scope=scope,
            trigger=TriggerContext(kind="manual", workflow_id="macro-review",
                                   trigger_id="rollback-new-request"),
            request={"requested_tasks": ["macro-review"]}, config_dir=config)
        store = WorkflowStore(shadow_db)
        existing = store.list_triggers(workflow_id="macro-review")
        assert rolled_back["status"] == "legacy"
        assert len(existing) == 1 and existing[0]["status"] == "running"
        assert store.list_runs()[0]["status"] == "running"

        allow_finish.set()
        active.result(timeout=5)

    persisted = WorkflowStore(shadow_db)
    final_trigger = persisted.list_triggers(workflow_id="macro-review")[0]
    assert result["status"] == "complete"
    assert final_trigger["status"] == "complete"
    assert [row["to_status"] for row in persisted.trigger_history(
        final_trigger["trigger_key"])] == ["planned", "running", "complete"]
    assert persisted.list_runs()[0]["status"] == "complete"
