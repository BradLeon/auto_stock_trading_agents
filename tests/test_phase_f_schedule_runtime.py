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

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.memory import task_store_scope
from ats.runtime import cli, dispatcher_daemon
from ats.workflow import schedule_runtime as rt
from ats.workflow.dispatcher import Dispatcher
from ats.workflow.planning import build_plan
from ats.workflow.run_contracts import TriggerContext
from ats.workflow.store import WorkflowStore

SCOPE = {"kind": "entity", "id": "COHR"}


class FakeScheduler:
    """Records add_job calls instead of running them."""

    def __init__(self, *a, **kw):
        self.init_kwargs = kw
        self.jobs: list[dict] = []
        self.listeners: list = []
        self.started = False

    def add_job(self, fn, trigger, *, id, misfire_grace_time=None, **kw):
        self.jobs.append({"fn": fn, "trigger": trigger, "id": id,
                          "misfire_grace_time": misfire_grace_time, **kw})

    def add_listener(self, fn, mask):
        self.listeners.append((fn, mask))

    def add_executor(self, executor, alias="default"):
        self.executors = getattr(self, "executors", {}) | {alias: executor}

    def get_jobs(self):
        return [SimpleNamespace(id=job["id"]) for job in self.jobs]

    def start(self):
        self.started = True
        raise SystemExit          # don't block the test


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


def transfer(workflow, scope, owner):
    """Move one scope to `owner` and bump its generation, as an owner switch does. The scope-level freeze/handover
    protocol of the joint coordinator was retired (switch-trade r5 9.3); these tests only need the owner change."""
    from contextlib import closing

    with closing(rt._connect(write=True)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute("UPDATE schedule_scope_owners SET owner=?, generation=generation+1 "
                               "WHERE workflow=? AND scope=?", (owner, workflow, rt.canonical(scope))).rowcount
        assert changed == 1
        conn.commit()


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


def test_actual_runtime_cli_queries_existing_isolated_ledger(isolated, capsys):
    # `runtime-state` was retired in r5 8.8; the owner log is the kept read of the same authority.
    assert cli.main(["dispatch","owner-log"]) == 0
    assert isinstance(json.loads(capsys.readouterr().out), list)
    assert rt.snapshot()["owners"]


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
    record_property("event_identity_and_publication",json.dumps(rt.snapshot()))


def test_phase_e_startup_consumes_without_restarting_collectors(business_inputs, isolated, monkeypatch, record_property):
    from apscheduler.schedulers import blocking

    from ats.config import reset_config_cache
    from ats.data.persistent_queue import PersistentIngestionQueue
    context=tick()
    Dispatcher(workflow_store=WorkflowStore(str(isolated.path)),max_workers=1).dispatch(plan(business_inputs,context,"prepare-dispatcher-owner"))
    path=business_inputs.root/"workflow/research_schedules.yaml"
    config=yaml.safe_load(path.read_text()); config["workflows"]["technical-review"]["enabled"]=True
    path.write_text(yaml.safe_dump(config)); reset_config_cache()
    task_id=os.environ["ATS_PERSISTENT_QUEUE_TASK_ID"]
    queue=PersistentIngestionQueue()
    before=queue.get(task_id)
    monkeypatch.setattr("ats.data.calendar_refresh.enqueue_schedule_calendar_refresh",lambda **kwargs:pytest.fail("research daemon attempted source collection"))
    holder={}
    monkeypatch.setattr(blocking,"BlockingScheduler",lambda *a,**kw:holder.setdefault("scheduler",FakeScheduler(*a,**kw)))
    dispatcher_daemon.start()
    resident=holder["scheduler"]
    assert "phase_e:technical-review" in [job["id"] for job in resident.jobs]
    assert not any(job["id"]=="calendar_refresh" or job["id"].startswith("factset") for job in resident.jobs)
    fire(resident,"phase_e:technical-review",datetime.now(UTC)+timedelta(minutes=1))
    assert queue.get(task_id)==before
    assert rt.current_owner("technical-review",SCOPE)=="dispatcher"
    record_property("collector_queue_unchanged",json.dumps({"before":before,"after":queue.get(task_id)}))
    record_property("new_runtime",json.dumps(rt.snapshot()))


def test_independent_oracle_detects_the_dispatcher_omitting_a_tick(business_inputs, isolated):
    from ats.config import reset_config_cache
    schedules=business_inputs.root/"workflow/research_schedules.yaml"
    config=yaml.safe_load(schedules.read_text())
    for name,entry in config["workflows"].items(): entry["enabled"]=(name=="technical-review")
    schedules.write_text(yaml.safe_dump(config)); reset_config_cache()
    expected=rt.expected_triggers(datetime(2026,10,9,8,29,tzinfo=dispatcher_daemon.ET),datetime(2026,10,9,8,31,tzinfo=dispatcher_daemon.ET))
    assert len(expected["expected"])==1
    # Only the dispatcher path exists since the legacy serial scheduler was removed (2026-10-20).
    assert set(expected["expected"][0]["paths"])=={"dispatcher"}
    comparison=rt.compare_triggers(expected)
    assert not comparison["clean"] and comparison["rows"][0]["both_missing"]
    assert set(comparison["rows"][0]["missing_paths"])=={"dispatcher"}
    assert {row["job_id"] for row in comparison["missing_wakes"]}=={"phase_e:technical-review"}


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
