"""The dispatcher daemon must notice overdue jobs soon after the Mac wakes (heartbeat).

The scheduler sleeps on a monotonic timer that does not advance while the machine sleeps; due-ness is judged
against the wall clock. A frequent no-op job bounds how long a sleep can hide an overdue job (2026-10-21: the
08:00 ET information brief started 2.6 hours late)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import logging

from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.schedulers.blocking import BlockingScheduler

from ats.runtime import dispatcher_daemon as dd
from ats.workflow.triggers import TriggerPolicy, evaluate_misfire, load_trigger_policies


def test_the_heartbeat_wakes_the_scheduler_every_thirty_seconds_on_its_own_executor():
    scheduler = BlockingScheduler(timezone="UTC")
    dd._add_heartbeat(scheduler)
    job = scheduler.get_job(dd.HEARTBEAT_JOB_ID)
    assert job is not None and job.executor == "heartbeat"
    assert job.trigger.interval == timedelta(seconds=30)
    assert job.coalesce and job.max_instances == 1 and job.misfire_grace_time is None
    assert "heartbeat" in scheduler._executors             # not the single worker that runs the workflows


def test_the_heartbeat_leaves_no_log_line_and_no_ledger_row(monkeypatch, caplog):
    listeners = []
    scheduler = SimpleNamespace(add_listener=lambda fn, mask: listeners.append(fn))
    dd._attach_job_logging(scheduler)

    def forbidden(*args, **kwargs):
        raise AssertionError("the heartbeat must not write the schedule ledger")
    monkeypatch.setattr("ats.workflow.schedule_runtime.observe", forbidden)
    beat = SimpleNamespace(job_id=dd.HEARTBEAT_JOB_ID, code=EVENT_JOB_EXECUTED,
                           scheduled_run_time=datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc),
                           exception=None)
    with caplog.at_level(logging.DEBUG, logger="ats.dispatcher"):
        listeners[0](beat)
    assert caplog.records == []


def test_a_workflow_job_is_still_logged_and_recorded(monkeypatch, caplog):
    listeners, seen = [], []
    scheduler = SimpleNamespace(add_listener=lambda fn, mask: listeners.append(fn))
    dd._attach_job_logging(scheduler)
    monkeypatch.setattr("ats.workflow.schedule_runtime.observe", lambda *a, **k: seen.append(a))
    event = SimpleNamespace(job_id="phase_e:technical-review", code=EVENT_JOB_EXECUTED,
                            scheduled_run_time=datetime.now(dd.ET) - timedelta(hours=2), exception=None)
    with caplog.at_level(logging.INFO, logger="ats.dispatcher"):
        listeners[0](event)
    assert seen and any("technical-review" in r.getMessage() and "迟" in r.getMessage() for r in caplog.records)


def test_a_technical_run_that_is_late_because_the_mac_slept_still_runs():
    policy = load_trigger_policies()["technical-review"]
    assert policy.misfire_grace_seconds == 14400
    now = datetime(2026, 10, 21, 17, 0, tzinfo=timezone.utc)
    three_hours_late = "2026-10-21T14:00:00+00:00"
    action = evaluate_misfire(scheduled_for=three_hours_late, now=now, policy=policy)[:2]
    assert action[0] != "skip", action
    too_late = evaluate_misfire(scheduled_for="2026-10-21T10:00:00+00:00", now=now, policy=policy)[:2]
    assert too_late == ("skip", "misfire_grace_exceeded")
