"""Controller handling of a `partial` article ingestion and of its retry (G0 U01-U03, 2026-10-21).

A `partial` packet used to be recorded as `failed`, and the retry then asked the queue for the same
task again, could not claim the finished task and crashed with `queue_task_not_claimable`.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from ats.data import persistent_queue, refresh

NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)        # 11:00 New York, after the Monday 10:00 job time
JOB_ID = "trendforce_news_weekly"


class _Queue:
    packets: list[dict] = []
    refs: list[str] = []
    seen: list[str] = []

    def __init__(self, *a, **k):
        pass

    def enqueue(self, **kwargs):
        type(self).seen.append(kwargs["trigger_ref"])
        return f"task-{kwargs['trigger_ref']}", True

    def run_one(self, **kwargs):
        packet = type(self).packets.pop(0)
        return {"stdout": packet, "reported_status": packet["status"], "exit_code": 0,
                "result": {"stdout_sha256": "x"}}

    def record_lineage(self, *a, **k):
        pass


@pytest.fixture
def controller(tmp_path, monkeypatch):
    _Queue.packets, _Queue.refs, _Queue.seen = [], ["doc-1"], []
    monkeypatch.setattr(persistent_queue, "PersistentIngestionQueue", _Queue)
    monkeypatch.setattr(refresh, "_unstructured_references",
                        lambda db, packet, job: {"runs": ["run-1"], "checks": [], "raw": [], "admission": [],
                                                 "published": list(_Queue.refs)})
    plan = refresh.load_plan()
    plan = replace(plan, jobs=tuple(j for j in plan.jobs if j.id == JOB_ID),
                   ledger_path=tmp_path / "ledger.sqlite")
    assert plan.jobs, "the shipped schedule must still define the TrendForce job"

    clock = {"now": NOW}
    monkeypatch.setattr(refresh, "_utc_now", lambda: clock["now"])     # the ledger stamps retry times with this

    def tick(*packets, at=NOW):
        clock["now"] = at
        _Queue.packets = list(packets)
        return refresh.run_tick(plan, now=at, data_db=tmp_path / "missing.sqlite")[0]

    return tick


def test_a_retry_is_a_new_queue_task_and_the_first_attempt_keeps_its_reference():
    assert refresh._queue_trigger_ref("job", "2026-10-19", 1) == "job:2026-10-19"
    assert refresh._queue_trigger_ref("job", "2026-10-19", 2) == "job:2026-10-19:attempt2"
    assert refresh._queue_trigger_ref("job", "2026-10-19", 3) != refresh._queue_trigger_ref("job", "2026-10-19", 2)


def test_partial_that_is_only_the_body_budget_is_a_normal_success(controller):
    row = controller({"status": "partial", "deferred": 3, "discovery": {"status": "succeeded", "failed_slices": []}})
    assert (row["status"], row["reason"]) == ("succeeded", "partial_budget_deferred")


def test_partial_with_a_failed_slice_but_published_documents_is_kept_and_the_gap_is_named(controller):
    row = controller({"status": "partial", "deferred": 0,
                      "discovery": {"status": "succeeded", "failed_slices": ["DJ-N"]}})
    assert (row["status"], row["reason"]) == ("succeeded", "partial_sources_held_back")


def test_a_failed_slice_and_budget_deferral_together_is_not_called_budget_only(controller):
    row = controller({"status": "partial", "deferred": 5,
                      "discovery": {"status": "succeeded", "failed_slices": ["DJ-N"]}})
    assert (row["status"], row["reason"]) == ("succeeded", "partial_sources_held_back")


def test_partial_with_nothing_published_is_failed_and_then_really_retried(controller):
    _Queue.refs = []
    first = controller({"status": "partial", "deferred": 0,
                        "discovery": {"status": "succeeded", "failed_slices": ["feed"]}})
    assert (first["status"], first["reason"], first["attempt"]) == ("failed", "article_source_partial", 1)

    _Queue.refs = ["doc-1"]
    second = controller({"status": "succeeded", "deferred": 0, "discovery": {"status": "succeeded"}},
                        at=NOW + timedelta(hours=2))
    assert (second["status"], second["attempt"]) == ("succeeded", 2)
    assert _Queue.seen == [f"{JOB_ID}:2026-10-19", f"{JOB_ID}:2026-10-19:attempt2"]


def test_a_retry_never_crashes_with_queue_task_not_claimable(controller):
    _Queue.refs = []
    controller({"status": "partial", "discovery": {"status": "succeeded", "failed_slices": ["feed"]}})
    second = controller({"status": "partial", "discovery": {"status": "succeeded", "failed_slices": ["feed"]}},
                        at=NOW + timedelta(hours=2))
    assert second["reason"] == "article_source_partial"


def test_structured_partial_runs_that_hold_data_are_not_failures():
    assert refresh._partial_runs_hold_data([("partial", 3, 6)])
    assert refresh._partial_runs_hold_data([("succeeded", 5, 0), ("partial", 0, 9)])
    assert not refresh._partial_runs_hold_data([("partial", 0, 0)])             # nothing in, nothing known
    assert not refresh._partial_runs_hold_data([("partial", 2, 0), ("parse_failed", 0, 0)])
    assert not refresh._partial_runs_hold_data([("succeeded", 5, 0)])           # nothing to excuse
    assert not refresh._partial_runs_hold_data([])
