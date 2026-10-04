from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
import time

import pytest

from ats.workflow.store import IdentityConflict, LeaseLost, WorkflowStore


def test_additive_bootstrap_and_run_recovery_are_idempotent(tmp_path):
    path = tmp_path / "workflow.sqlite"
    store = WorkflowStore(path)
    created = store.create_run(run_id="run-1", request={"tasks": ["macro"]},
                               plan={"order": ["macro"]}, trigger_key="manual-1")
    # Simulate restart: constructors repeat only the additive IF NOT EXISTS bootstrap.
    restarted = WorkflowStore(path)
    recovered = restarted.get_run("run-1")
    assert recovered["request"] == created["request"]
    assert recovered["plan"] == created["plan"]
    assert restarted.create_run(run_id="run-1", request={"tasks": ["macro"]},
                                plan={"order": ["macro"]}, trigger_key="manual-1")["run_id"] == "run-1"
    with pytest.raises(IdentityConflict):
        restarted.create_run(run_id="run-1", request={"tasks": ["technical"]},
                             plan={"order": ["macro"]}, trigger_key="manual-1")


def test_attempt_terminal_update_and_run_compare_and_set(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite")
    store.create_run(run_id="r", request={}, plan={}, status="running")
    attempt = store.start_attempt(run_id="r", task_instance_key="macro:portfolio",
                                  task_id="macro-review", scope={"kind": "portfolio"},
                                  attempt_no=1, input_refs=["doc:v1"],
                                  data_vintage_refs=["macro:2026-09"])
    assert store.finish_attempt(attempt["agent_run_id"], status="succeeded",
                                projection_refs=["p-1"])
    assert not store.finish_attempt(attempt["agent_run_id"], status="failed")
    assert store.set_run_state("r", status="complete", result={"projection_refs": ["p-1"]},
                               expected=("running",))
    assert not store.set_run_state("r", status="failed", expected=("running",))
    recovered = WorkflowStore(tmp_path / "workflow.sqlite").attempts_for_run("r")
    assert recovered[0]["status"] == "succeeded"
    assert recovered[0]["projection_refs"] == ["p-1"]


def test_trigger_claim_lease_duplicate_takeover_and_supersede(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite")
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    request = {"event_id": "earnings:NVDA:FY26Q3", "event_version": "v1"}
    store.record_trigger(trigger_key="event-k", kind="event", workflow_id="fundamental-event",
                         request=request, event_id="earnings:NVDA:FY26Q3", event_version="v1",
                         at=now)
    first = store.claim_trigger(trigger_key="event-k", kind="event",
                                workflow_id="fundamental-event", request=request,
                                owner_id="worker-a", lease_seconds=10, at=now)
    duplicate = store.claim_trigger(trigger_key="event-k", kind="event",
                                    workflow_id="fundamental-event", request=request,
                                    owner_id="worker-b", lease_seconds=10,
                                    at=now + timedelta(seconds=1))
    assert first["acquired"] and not duplicate["acquired"]
    takeover = store.claim_trigger(trigger_key="event-k", kind="event",
                                   workflow_id="fundamental-event", request=request,
                                   owner_id="worker-b", lease_seconds=10,
                                   at=now + timedelta(seconds=11))
    assert takeover["acquired"] and takeover["attempt_count"] == 2
    with pytest.raises(LeaseLost):
        store.finish_trigger("event-k", owner_id="worker-a", status="complete")
    assert store.finish_trigger("event-k", owner_id="worker-b", status="complete")

    store.record_trigger(trigger_key="old", kind="event", workflow_id="macro-review",
                         request={"event": "fed:sep", "version": "v1"},
                         event_id="fed:sep", event_version="v1", at=now)
    assert store.supersede_event(event_id="fed:sep", current_version="v2", at=now) == 1


def test_concurrent_workers_create_independent_runs(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite")

    def write(i):
        return store.create_run(run_id=f"r-{i}", request={"i": i}, plan={"i": i})["run_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(write, range(24)))
    assert len(set(ids)) == 24
    assert len(store.list_runs(limit=100)) == 24


def test_busy_timeout_is_applied_to_sqlite_writer_lock(tmp_path):
    path = tmp_path / "workflow.sqlite"
    store = WorkflowStore(path, busy_timeout_ms=40)
    blocker = sqlite3.connect(path, timeout=0.1, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.create_run(run_id="blocked", request={}, plan={})
    finally:
        blocker.rollback()
        blocker.close()
    assert time.monotonic() - started < 1


def test_same_trigger_key_different_request_is_rejected(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite")
    store.record_trigger(trigger_key="same", kind="schedule", workflow_id="macro-review",
                         request={"schedule_id": "daily", "scheduled_for": "t1"})
    with pytest.raises(IdentityConflict):
        store.claim_trigger(trigger_key="same", kind="schedule", workflow_id="macro-review",
                            request={"schedule_id": "daily", "scheduled_for": "t2"},
                            owner_id="w")


def test_trigger_transition_history_and_explicit_same_key_compensation(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite")
    request = {"trigger": {"event_id": "earnings:NVDA", "event_version": "1"},
               "request": {"scope": "NVDA"}}
    store.record_trigger(trigger_key="event", kind="event", workflow_id="fundamental-event",
                         request=request, event_id="earnings:NVDA", event_version="1")
    first = store.claim_trigger(trigger_key="event", kind="event",
                                workflow_id="fundamental-event", request=request,
                                owner_id="worker-a", run_id="run-event")
    store.create_run(run_id="run-event", request=request, plan={"tasks": ["fundamental-event"]},
                     trigger_key="event", status="running")
    store.set_run_state("run-event", status="incomplete", result={"missing": ["projection"]},
                        expected=("running",))
    store.finish_trigger("event", owner_id="worker-a", status="incomplete",
                         reason_code="analysis_missing")
    assert store.compensate_trigger("event", actor="alice", reason="admitted filing arrived")
    second = store.claim_trigger(trigger_key="event", kind="event",
                                 workflow_id="fundamental-event", request=request,
                                 owner_id="worker-b", run_id="run-event")
    assert first["attempt_count"] == 1
    assert second["attempt_count"] == 2
    assert second["run_id"] == "run-event"
    assert store.reopen_run_for_compensation(
        "run-event", actor="alice", reason="admitted filing arrived")
    assert store.get_run("run-event")["status"] == "planned"
    transitions = [(item["from_status"], item["to_status"])
                   for item in store.trigger_history("event")]
    assert transitions == [("", "planned"), ("planned", "running"),
                           ("running", "incomplete"), ("incomplete", "planned"),
                           ("planned", "running")]
    assert store.trigger_history("event")[3]["detail"] == {
        "actor": "alice", "reason": "admitted filing arrived"}
    assert [(item["from_status"], item["to_status"])
            for item in store.run_history("run-event")] == [
                ("", "running"), ("running", "incomplete"), ("incomplete", "planned")]


def test_trigger_queries_filter_by_normalized_schedule_window(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite")
    store.record_trigger(
        trigger_key="morning", kind="schedule", workflow_id="macro-review",
        request={"tick": "morning"}, schedule_id="daily",
        scheduled_for="2026-09-24T08:00:00-04:00")
    store.record_trigger(
        trigger_key="evening", kind="schedule", workflow_id="macro-review",
        request={"tick": "evening"}, schedule_id="daily",
        scheduled_for="2026-09-24T18:00:00Z")

    rows = store.list_triggers(
        workflow_id="macro-review", schedule_id="daily",
        scheduled_from="2026-09-24T11:00:00Z",
        scheduled_to="2026-09-24T13:00:00Z")
    assert [row["trigger_key"] for row in rows] == ["morning"]
    assert rows[0]["scheduled_for"] == "2026-09-24T12:00:00.000000+00:00"


def test_running_old_event_is_marked_then_finishes_as_superseded(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite")
    request = {"trigger": {"event_id": "fomc:2026-09:statement", "event_version": "1"}}
    store.record_trigger(trigger_key="old-event", kind="event", workflow_id="macro-review",
                         request=request, event_id="fomc:2026-09:statement",
                         event_version="1")
    store.claim_trigger(trigger_key="old-event", kind="event", workflow_id="macro-review",
                        request=request, owner_id="worker", run_id="run-old")

    assert store.supersede_event(event_id="fomc:2026-09:statement",
                                 current_version="2") == 1
    running = store.get_trigger("old-event")
    assert running["status"] == "running"
    assert running["superseded_after_run"] == 1
    assert store.finish_trigger("old-event", owner_id="worker", status="complete")

    terminal = store.get_trigger("old-event")
    assert terminal["status"] == "superseded"
    assert terminal["reason_code"] == "event_version_changed_in_flight"
    assert store.trigger_history("old-event")[-1]["to_status"] == "superseded"


def test_workflow_store_rejects_ephemeral_memory_database():
    with pytest.raises(ValueError, match="file-backed"):
        WorkflowStore(":memory:")
