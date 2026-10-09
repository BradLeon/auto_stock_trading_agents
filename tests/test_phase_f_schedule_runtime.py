"""Real entry/publisher drills; external transports remain controlled fixtures."""
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest
import yaml
from test_phase_f_research_gate import business_inputs as _business_inputs
from test_phase_f_research_gate import research_isolation as _research_isolation
from test_scheduler_jobs import FakeScheduler

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.memory import task_store_scope
from ats.runtime import cli, scheduler
from ats.workflow import schedule_runtime as rt
from ats.workflow.dispatcher import Dispatcher
from ats.workflow.phase_e import build_plan
from ats.workflow.run_contracts import TriggerContext
from ats.workflow.store import WorkflowStore

SCOPE = {"kind": "entity", "id": "COHR"}


@pytest.fixture
def business_inputs(isolated, monkeypatch, tmp_path):
    yield from _business_inputs.__wrapped__(isolated, monkeypatch, tmp_path)


@pytest.fixture(name="isolated")
def schedule_isolation(tmp_path):
    yield from _research_isolation.__wrapped__(tmp_path)


def fire(resident, job_id, planned, *, missed=False):
    from apscheduler.events import EVENT_JOB_MISSED

    from ats.workflow.schedule_executor import run_planned_job
    job=next(job for job in resident.jobs if job["id"]==job_id)
    if missed:
        events=[SimpleNamespace(code=EVENT_JOB_MISSED,job_id=job_id,scheduled_run_time=planned)]
    else:
        events=run_planned_job(SimpleNamespace(id=job_id,func=job["fn"],args=(),kwargs={},misfire_grace_time=None),
                               "default",[planned],"ats.test.scheduler")
    for event in events:
        for callback,mask in resident.listeners:
            if mask & event.code:
                callback(event)


def tick(point=None, *, manual=False):
    return TriggerContext(kind="manual" if manual else "schedule", trigger_id="operator-replay" if manual else "",
        schedule_id="technical-review", scheduled_for=(point or datetime.now(UTC)-timedelta(seconds=1)).isoformat())


def transfer(workflow, scope, owner, dispositions=None):
    token = rt.freeze(workflow, scope, actor="drill", reason="explicit isolated handover")
    inventory = rt.frozen_inventory(token)
    rt.handover(token, to_owner=owner, dispositions=dispositions or {}, actor="drill", reason="fence tested")
    return token, inventory


def plan(inputs, trigger, run_id):
    return build_plan(requested_tasks=["technical-review"], scope=ProjectionScope.model_validate(SCOPE),
        trigger=trigger, run_id=run_id, as_of=datetime.now(UTC).isoformat(), config_dir=inputs.root,
        task_inputs={"data_vintage_refs":[inputs.document.sha256]})


def test_real_cli_dispatcher_dedup_and_rollback(business_inputs, isolated, monkeypatch, record_property):
    from ats.agents.technical import review
    monkeypatch.setattr(review, "resolve_universe", lambda *a, **kw: (["COHR"], []))
    context = tick()
    with rt.wake(context):
        assert cli.run_technical_review(write_report=False) == 0
    first = rt.snapshot()
    row = next(row for row in first["claims"] if row["workflow"] == "technical-review")
    assert row["owner"] == "legacy" and row["status"] == "complete"
    refs = json.loads(row["result_refs"])
    before = isolated.task_projection_envelopes(agent_role="technical_review", scope_id="COHR")
    assert before and before[0]["projection_id"] in refs
    transfer("technical-review", SCOPE, "dispatcher")
    result = Dispatcher(workflow_store=WorkflowStore(str(isolated.path)), max_workers=1).dispatch(plan(business_inputs, context, "same-tick"))
    # The old projection has different input lineage. It must not be republished
    # or declared reusable under the new plan merely because its trigger completed.
    assert not result.complete
    assert len(isolated.task_projection_envelopes(agent_role="technical_review", scope_id="COHR")) == len(before)
    new_context = tick(datetime.now(UTC))
    completed = Dispatcher(workflow_store=WorkflowStore(str(isolated.path)), max_workers=1).dispatch(plan(business_inputs, new_context, "new-tick"))
    assert completed.complete, completed.as_dict()
    transfer("technical-review", SCOPE, "legacy")
    count = len(isolated.task_projection_envelopes(agent_role="technical_review", scope_id="COHR"))
    with rt.wake(new_context.model_copy(update={"kind":"manual", "trigger_id":"replay-new-tick"})):
        assert cli.run_technical_review(write_report=False) == 0
    assert len(isolated.task_projection_envelopes(agent_role="technical_review", scope_id="COHR")) == count
    record_property("authority", json.dumps(rt.snapshot()))
    record_property("actual_dispatch", json.dumps(completed.as_dict()))


@pytest.mark.parametrize("disposition", ["carry_over", "void"])
def test_actual_late_memory_publisher_fenced_with_worker_alive(business_inputs, isolated, disposition, record_property):
    entered, release = Event(), Event()
    context = tick()
    errors = []
    def late_worker():
        def action():
            with task_store_scope(isolated.path) as store:
                entered.set()
                assert release.wait(10)
                envelope = build_envelope(role="technical_review", scope=ProjectionScope.model_validate(SCOPE),
                    payload={"entity":"COHR","signal":"neutral","summary":"late old worker","levels":{"close":100}},
                    as_of=datetime.now(UTC).isoformat())
                store.save_task_projection_envelope(envelope)
        try:
            rt.execute("technical-review", SCOPE, context, action, actor="resident-old-worker")
        except rt.ScheduleAuthorityError as exc:
            errors.append(str(exc))
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(late_worker)
        assert entered.wait(10)
        token = rt.freeze("technical-review", SCOPE, actor="operator", reason="retain running old worker")
        inventory = rt.frozen_inventory(token)
        assert len(inventory) == 1
        with pytest.raises(rt.ScheduleAuthorityError, match="frozen"):
            rt.execute("technical-review", SCOPE, tick(datetime.now(UTC)), lambda: pytest.fail("new claim executed"))
        with pytest.raises(rt.ScheduleAuthorityError, match="each unfinished"):
            rt.handover(token, to_owner="dispatcher", dispositions={}, actor="operator", reason="missing disposition")
        with pytest.raises(rt.ScheduleAuthorityError, match="recorded completion"):
            rt.handover(token, to_owner="dispatcher", dispositions={inventory[0]["key"]:{"kind":"already_run","reason":"unverified"}}, actor="operator", reason="invalid completion")
        rt.handover(token, to_owner="dispatcher", dispositions={inventory[0]["key"]:{"kind":disposition,"reason":"explicit disposition"}}, actor="operator", reason="handover while old worker alive")
        assert not worker.done()
        release.set(); worker.result(timeout=10)
    assert errors and "publication" in errors[0]
    assert not isolated.task_projection_envelopes(agent_role="technical_review", scope_id="COHR")
    assert any(row["action"] == "publication_refused" for row in rt.snapshot()["history"])
    result=Dispatcher(workflow_store=WorkflowStore(str(isolated.path)),max_workers=1).dispatch(plan(business_inputs,context,"carry-or-void"))
    assert result.complete == (disposition == "carry_over"),result.as_dict()
    assert len(isolated.task_projection_envelopes(agent_role="technical_review",scope_id="COHR")) == (1 if disposition == "carry_over" else 0)
    record_property("late_publish_proof", json.dumps(rt.snapshot()))


def test_freeze_allows_real_old_completion_without_operator_assertion(isolated):
    entered, release = Event(), Event()
    context = tick()
    def worker():
        def action():
            with task_store_scope(isolated.path) as store:
                entered.set(); assert release.wait(10)
                store.save_task_projection_envelope(build_envelope(role="technical_review", scope=ProjectionScope.model_validate(SCOPE),
                    payload={"entity":"COHR","signal":"neutral","summary":"completed before transfer","levels":{}}, as_of=datetime.now(UTC).isoformat()))
        rt.execute("technical-review", SCOPE, context, action, actor="finish-before-handover")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(worker); assert entered.wait(10)
        token=rt.freeze("technical-review", SCOPE, actor="operator", reason="finish old work")
        assert rt.frozen_inventory(token)
        release.set(); future.result(timeout=10)
        assert rt.frozen_inventory(token) == []
        rt.handover(token, to_owner="dispatcher", dispositions={}, actor="operator", reason="actual completion recorded")
    row = rt.snapshot()["claims"][0]
    assert row["status"] == "complete" and json.loads(row["result_refs"])


def test_owner_reload_restart_and_missing_authority_fail_closed(isolated, tmp_path, monkeypatch):
    rt.check_owner("technical-review", SCOPE, "legacy")
    transfer("technical-review", SCOPE, "dispatcher")
    state = rt.snapshot()["owners"]
    rt.reload_authority(); rt.startup()
    assert rt.snapshot()["owners"] == state
    with pytest.raises(rt.ScheduleAuthorityError, match="fenced"):
        rt.check_owner("technical-review", SCOPE, "legacy")
    missing = tmp_path / "missing.sqlite"
    monkeypatch.setenv("ATS_DISPATCH_STATE_PATH", str(missing))
    with pytest.raises(rt.ScheduleAuthorityError, match="missing"):
        rt.startup()
    assert not missing.exists()


def test_real_startup_planned_tick_manual_duplicate_and_collector_unchanged(business_inputs, isolated, monkeypatch, record_property):
    from apscheduler.schedulers import blocking

    from ats.agents.technical import review
    from ats.config import reset_config_cache
    settings_path=business_inputs.root/"settings.yaml"
    config=yaml.safe_load(settings_path.read_text())
    config["schedule"]["daily_stages"]={key: key=="technical_daily" for key in scheduler.get_config().app.schedule.daily_stages.model_dump()}
    config["schedule"]["jobs"]={key: key=="factset_weekly_ingest" for key in scheduler.get_config().app.schedule.jobs.model_dump()}
    settings_path.write_text(yaml.safe_dump(config)); reset_config_cache()
    monkeypatch.setattr(review, "resolve_universe", lambda *a, **kw: (["COHR"], []))
    monkeypatch.setattr(scheduler,"is_trading_session",lambda *a:True)
    holder={}
    monkeypatch.setattr(blocking,"BlockingScheduler",lambda *a,**kw:holder.setdefault("scheduler",FakeScheduler(*a,**kw)))
    scheduler.start(dry_run=True)
    resident=holder["scheduler"]
    collectors=[job for job in resident.jobs if job["id"].startswith("factset")]
    assert len(collectors)==1 and collectors[0]["fn"] is scheduler._factset_weekly_ingest
    collector_before=[(job["id"],id(job["fn"]),str(job["trigger"])) for job in collectors]
    planned=datetime(2026,10,9,10,30,tzinfo=scheduler.ET)
    fire(resident,"daily_cycle",planned)
    rows=isolated.task_projection_envelopes(agent_role="technical_review",scope_id="COHR")
    assert rows
    transfer("technical-review",SCOPE,"dispatcher")
    # Same resident keeps its old cached callback and unchanged collector.
    fire(resident,"daily_cycle",planned+timedelta(days=3))
    assert len(isolated.task_projection_envelopes(agent_role="technical_review",scope_id="COHR"))==len(rows)
    fire(resident,"daily_cycle",planned+timedelta(days=4),missed=True)
    rt.reload_authority()
    assert [(job["id"],id(job["fn"]),str(job["trigger"])) for job in collectors]==collector_before
    expected=rt.expected_triggers(planned-timedelta(minutes=1),planned+timedelta(minutes=1),config_dir=business_inputs.root)
    comparison=rt.compare_triggers(expected)
    assert len(expected["expected"])==1 and not comparison["rows"][0]["both_missing"]
    replay=rt.adapt_wake("technical-review",TriggerContext(kind="schedule",schedule_id="daily_cycle",scheduled_for=planned.isoformat()))
    with rt.wake(replay.model_copy(update={"kind":"manual","trigger_id":"manual-same-tick"})):
        cli.run_technical_review(write_report=False)
    assert rt.compare_triggers(expected)["duplicates"]
    omitted=rt.expected_triggers(planned+timedelta(days=5)-timedelta(minutes=1),planned+timedelta(days=5,minutes=1),config_dir=business_inputs.root)
    assert rt.compare_triggers(omitted)["rows"][0]["both_missing"]
    assert comparison["misfires"]
    record_property("expected_and_actual",json.dumps(rt.compare_triggers(expected)))
    record_property("collector_lifecycle",json.dumps({"before":collector_before,"after":collector_before}))


def test_actual_runtime_cli_queries_existing_isolated_ledger(isolated, capsys):
    assert cli.main(["dispatch","runtime-state"]) == 0
    state=json.loads(capsys.readouterr().out)
    assert state["owners"] and "history" in state


def test_history_append_only_and_corrupt_records_are_not_repaired(isolated):
    with sqlite3.connect(rt.state_path()) as conn:
        with pytest.raises(sqlite3.IntegrityError,match="append-only"):
            conn.execute("DELETE FROM schedule_runtime_history")
        conn.execute("DROP TABLE schedule_runtime_claims")
    with pytest.raises(rt.ScheduleAuthorityError,match="unreadable"):
        rt.startup()
    with pytest.raises(rt.ScheduleAuthorityError):
        rt.initialize()


def test_actual_config_event_identity_uses_published_calendar_version(business_inputs, isolated, monkeypatch, record_property):
    from ats.config import reset_config_cache
    from ats.data.calendar_refresh import _manual_overlay_candidates
    from ats.data.stores.schedule_calendar import ScheduleCalendarStore
    event={"date":"2026-10-09","kind":"cpi","label":"9月 CPI","triggers":["macro"]}
    (business_inputs.root/"events.yaml").write_text(yaml.safe_dump({"events":[event]},allow_unicode=True))
    reset_config_cache()
    calendar=ScheduleCalendarStore(os.environ["ATS_DATA_DB_PATH"])
    candidate=_manual_overlay_candidates(business_inputs.root)[0]
    saved=calendar.submit_candidate(candidate)
    published=calendar.publish_candidate(saved["candidate_id"])
    context=rt.config_event_context(event)
    assert context.event_id==published["event_id"] and context.event_version==str(published["event_version"])
    monkeypatch.setattr(scheduler,"_today",lambda:datetime(2026,10,9,tzinfo=UTC).date())
    assert scheduler._event_triggers(pead=False,macro_sector=True)==["9月 CPI->macro"]
    claims=[row for row in rt.snapshot()["claims"] if row["workflow"]=="macro-review"]
    assert len(claims)==1 and claims[0]["status"]=="complete" and json.loads(claims[0]["result_refs"])
    scheduler._event_triggers(pead=False,macro_sector=True)
    assert len([row for row in rt.snapshot()["claims"] if row["workflow"]=="macro-review"])==1
    assert any(row["action"]=="skip" for row in rt.snapshot()["history"])
    record_property("event_identity_and_publication",json.dumps(rt.snapshot()))


def test_phase_e_startup_consumes_without_restarting_collectors(business_inputs, isolated, monkeypatch, record_property):
    from apscheduler.schedulers import blocking

    from ats.config import reset_config_cache
    from ats.data.persistent_queue import PersistentIngestionQueue
    context=tick()
    Dispatcher(workflow_store=WorkflowStore(str(isolated.path)),max_workers=1).dispatch(plan(business_inputs,context,"prepare-dispatcher-owner"))
    path=business_inputs.root/"workflow/phase_e_schedules.yaml"
    config=yaml.safe_load(path.read_text()); config["workflows"]["technical-review"]["enabled"]=True
    path.write_text(yaml.safe_dump(config)); reset_config_cache()
    task_id=os.environ["ATS_PERSISTENT_QUEUE_TASK_ID"]
    queue=PersistentIngestionQueue()
    before=queue.get(task_id)
    monkeypatch.setattr("ats.data.calendar_refresh.enqueue_schedule_calendar_refresh",lambda **kwargs:pytest.fail("research daemon attempted source collection"))
    holder={}
    monkeypatch.setattr(blocking,"BlockingScheduler",lambda *a,**kw:holder.setdefault("scheduler",FakeScheduler(*a,**kw)))
    scheduler.start(phase_e=True)
    resident=holder["scheduler"]
    assert "phase_e:technical-review" in [job["id"] for job in resident.jobs]
    assert not any(job["id"]=="calendar_refresh" or job["id"].startswith("factset") for job in resident.jobs)
    fire(resident,"phase_e:technical-review",datetime.now(UTC)+timedelta(minutes=1))
    assert queue.get(task_id)==before
    assert rt.current_owner("technical-review",SCOPE)=="dispatcher"
    record_property("collector_queue_unchanged",json.dumps({"before":before,"after":queue.get(task_id)}))
    record_property("new_runtime",json.dumps(rt.snapshot()))


def test_independent_oracle_detects_both_paths_omitting_same_tick(business_inputs, isolated):
    from ats.config import reset_config_cache
    settings=business_inputs.root/"settings.yaml"
    cfg=yaml.safe_load(settings.read_text())
    cfg["schedule"]["daily_stages"]={key:key=="technical_daily" for key in scheduler.get_config().app.schedule.daily_stages.model_dump()}
    settings.write_text(yaml.safe_dump(cfg))
    schedules=business_inputs.root/"workflow/phase_e_schedules.yaml"
    config=yaml.safe_load(schedules.read_text());config["workflows"]["technical-review"]["enabled"]=True
    schedules.write_text(yaml.safe_dump(config)); reset_config_cache()
    expected=rt.expected_triggers(datetime(2026,10,9,9,59,tzinfo=scheduler.ET),datetime(2026,10,9,10,31,tzinfo=scheduler.ET))
    assert len(expected["expected"])==1
    assert set(expected["expected"][0]["paths"])=={"legacy","dispatcher"}
    comparison=rt.compare_triggers(expected)
    assert not comparison["clean"] and comparison["rows"][0]["both_missing"]
    assert set(comparison["rows"][0]["missing_paths"])=={"legacy","dispatcher"}
    assert {row["job_id"] for row in comparison["missing_wakes"]}=={"daily_cycle","phase_e:technical-review"}


def test_new_process_reads_same_owner_generation_without_reset(isolated, record_property):
    import subprocess
    import sys

    from ats.config import REPO_ROOT
    rt.check_owner("technical-review",SCOPE,"legacy")
    transfer("technical-review",SCOPE,"dispatcher")
    env=dict(os.environ);env["PYTHONPATH"]=str(REPO_ROOT/"src")
    child=subprocess.run([sys.executable,"-c","import json;from ats.workflow.schedule_runtime import startup,snapshot;startup();print(json.dumps(snapshot()))"],env=env,capture_output=True,text=True,check=True)
    state=json.loads(child.stdout)
    assert state==rt.snapshot()
    row=next(row for row in state["owners"] if row["scope"]==rt.canonical(SCOPE))
    assert row["owner"]=="dispatcher" and row["generation"]==2
    record_property("cross_process_authority",json.dumps(state))


def test_committed_result_survives_control_record_crash_without_republication(business_inputs, isolated, monkeypatch):
    from ats.agents.technical import review
    monkeypatch.setattr(review,"resolve_universe",lambda *a,**kw:(["COHR"],[]))
    original=rt._history
    class ProcessCrash(BaseException):
        pass
    def crash(conn,action,key,payload):
        if action=="publication":
            raise ProcessCrash("result committed; control publication record not committed")
        return original(conn,action,key,payload)
    monkeypatch.setattr(rt,"_history",crash)
    context=tick()
    with rt.wake(context),pytest.raises(ProcessCrash):
        cli.run_technical_review(write_report=False)
    monkeypatch.setattr(rt,"_history",original)
    row=next(row for row in rt.snapshot()["claims"] if row["workflow"]=="technical-review")
    assert row["status"]=="running" and not json.loads(row["result_refs"])
    raw=isolated.latest_technical_review("technical").model_dump(mode="json")
    token=rt.freeze("technical-review",SCOPE,actor="operator",reason="crash between two databases")
    rt.handover(token,to_owner="dispatcher",dispositions={row["key"]:{"kind":"carry_over","reason":"unrecorded result must still fence actual writer"}},actor="operator",reason="verify persisted result binding")
    result=Dispatcher(workflow_store=WorkflowStore(str(isolated.path)),max_workers=1).dispatch(plan(business_inputs,context,"crash-recovery"))
    assert not result.complete
    assert isolated.latest_technical_review("technical").model_dump(mode="json")==raw
    assert isolated.conn.execute("SELECT COUNT(*) FROM schedule_result_publications WHERE claim_key=?",(row["key"],)).fetchone()[0]


def test_partial_publication_cannot_be_declared_carry_over(isolated):
    class WorkerLost(BaseException):
        pass
    def action():
        isolated.save_task_projection_envelope(build_envelope(role="technical_review",scope=ProjectionScope.model_validate(SCOPE),
            payload={"entity":"COHR","signal":"neutral","summary":"partly completed","levels":{}},as_of=datetime.now(UTC).isoformat()))
        raise WorkerLost()
    with pytest.raises(WorkerLost):
        rt.execute("technical-review",SCOPE,tick(),action)
    token=rt.freeze("technical-review",SCOPE,actor="operator",reason="unfinished after actual publication")
    row=rt.frozen_inventory(token)[0]
    with pytest.raises(rt.ScheduleAuthorityError,match="partly published"):
        rt.handover(token,to_owner="dispatcher",dispositions={row["key"]:{"kind":"carry_over","reason":"incorrect replay"}},actor="operator",reason="reject published replay")
    assert rt.frozen_inventory(token)==[row]


def test_rejected_handover_keeps_freeze_until_explicit_recovery(isolated):
    rt.check_owner("technical-review",SCOPE,"legacy")
    token=rt.freeze("technical-review",SCOPE,actor="operator",reason="test aborted migration")
    before=rt.snapshot()["owners"]
    with pytest.raises(rt.ScheduleAuthorityError,match="each unfinished"):
        rt.handover(token,to_owner="dispatcher",dispositions={"unknown":{"kind":"void","reason":"wrong inventory"}},actor="operator",reason="invalid disposition")
    assert rt.snapshot()["owners"]==before
    rt.cancel_freeze(token,actor="operator",reason="abort handover; original owner retained")
    owner=rt.check_owner("technical-review",SCOPE,"legacy")
    assert owner["generation"]==1 and owner["owner"]=="legacy" and not owner["frozen"]
    assert any(row["action"]=="cancel_freeze" for row in rt.snapshot()["history"])


def test_claim_cannot_publish_into_different_authority_database(isolated, monkeypatch, tmp_path):
    previous=os.environ["ATS_DISPATCH_STATE_PATH"]
    def action():
        monkeypatch.setenv("ATS_DISPATCH_STATE_PATH",str(tmp_path/"another.sqlite"))
        with pytest.raises(rt.ScheduleAuthorityError,match="path changed"):
            isolated.save_task_projection_envelope(build_envelope(role="technical_review",scope=ProjectionScope.model_validate(SCOPE),
                payload={"entity":"COHR","signal":"neutral","summary":"must not publish","levels":{}},as_of=datetime.now(UTC).isoformat()))
        monkeypatch.setenv("ATS_DISPATCH_STATE_PATH",previous)
    rt.execute("technical-review",SCOPE,tick(),action)
    assert not isolated.task_projection_envelopes(agent_role="technical_review",scope_id="COHR")


def test_failed_attempt_lease_cannot_be_revived_with_same_actor(isolated):
    context=tick()
    def failed():
        raise RuntimeError("nothing published; retry may be allowed")
    with pytest.raises(RuntimeError):
        rt.execute("technical-review",SCOPE,context,failed,actor="old-attempt")
    with pytest.raises(rt.ScheduleAuthorityError,match="distinct attempt"):
        rt.execute("technical-review",SCOPE,context,lambda:pytest.fail("old lease revived"),actor="old-attempt")
    assert rt.execute("technical-review",SCOPE,context,lambda:"new attempt",actor="new-attempt")=="new attempt"


@pytest.mark.parametrize("label",["FY2026Q3","FY26Q3","Q3 FY2026","Q3 2026"])
def test_score_window_and_event_route_share_published_event_version(isolated,label):
    from ats.data.stores.schedule_calendar import (
        ScheduleCalendarStore,
        ScheduleEventCandidate,
        earnings_identity,
    )
    calendar=ScheduleCalendarStore(os.environ["ATS_DATA_DB_PATH"])
    candidate=calendar.submit_candidate(ScheduleEventCandidate(source_id="controlled-release",event_type="earnings",stable_identity=earnings_identity("COHR",2026,3),event_date="2026-10-09",status="released"))
    event=calendar.publish_candidate(candidate["candidate_id"])
    context=rt.released_earnings_context("COHR",label)
    routed=TriggerContext(kind="event",event_id=event["event_id"],event_version=str(event["event_version"]))
    assert rt.identity("fundamental-event",SCOPE,context)[0]==rt.identity("fundamental-event",SCOPE,routed)[0]


def test_metadata_finalization_requires_original_claim_and_current_generation(isolated):
    context=TriggerContext(kind="event",event_id="earnings:COHR:FY2026Q3:release",event_version="1")
    with pytest.raises(rt.ScheduleAuthorityError,match="completed result"):
        rt.finalize_metadata("fundamental-event",SCOPE,context,lambda:True,reason="no original claim")
    rt.execute("fundamental-event",SCOPE,context,lambda:isolated.record_score_run(symbol="COHR",fiscal_label="Q3 FY2026",version=1,
        earnings_date="2026-10-09",has_transcript=False))
    assert rt.finalize_metadata("fundamental-event",SCOPE,context,
        lambda:isolated.promote_score_run("COHR","Q3 FY2026"),reason="existing v1 finalization")
    with pytest.raises(rt.ScheduleAuthorityError,match="new analysis"):
        rt.finalize_metadata("fundamental-event",SCOPE,context,
            lambda:isolated.save_task_projection_envelope(None),reason="cannot reopen analysis")
    transfer("fundamental-event",SCOPE,"dispatcher")
    with pytest.raises(rt.ScheduleAuthorityError,match="fenced"):
        rt.finalize_metadata("fundamental-event",SCOPE,context,
            lambda:isolated.promote_score_run("COHR","Q3 FY2026"),reason="old daemon cached result")


def test_score_window_without_published_version_stops_before_claim(isolated):
    with pytest.raises(rt.ScheduleAuthorityError,match="event version"):
        rt.released_earnings_context("COHR","Q3 FY2026")
    assert rt.snapshot()["claims"]==[]
