"""Operator re-collection: `refresh tick --job X --manual` (G0 re-verification, 2026-10-21).

A manual run gets its own window label, so it can repeat a job that already ran today and never consumes
the schedule's own window; it still refuses a disabled job.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from ats.data import persistent_queue, refresh

NOW = datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc)      # Wednesday: the Monday TrendForce job is not due
JOB_ID = "trendforce_news_weekly"


class _Queue:
    seen: list[dict] = []

    def __init__(self, *a, **k):
        pass

    def enqueue(self, **kwargs):
        type(self).seen.append(kwargs)
        return f"task-{len(type(self).seen)}", True

    def run_one(self, **kwargs):
        return {"stdout": {"status": "succeeded"}, "reported_status": "succeeded", "exit_code": 0,
                "result": {"stdout_sha256": "x"}}

    def record_lineage(self, *a, **k):
        pass


@pytest.fixture
def plan(tmp_path, monkeypatch):
    _Queue.seen = []
    monkeypatch.setattr(persistent_queue, "PersistentIngestionQueue", _Queue)
    monkeypatch.setattr(refresh, "_unstructured_references",
                        lambda db, packet, job: {"runs": ["run-1"], "checks": [], "raw": [], "admission": [],
                                                 "published": ["doc-1"]})
    shipped = refresh.load_plan()
    return replace(shipped, jobs=tuple(j for j in shipped.jobs if j.id == JOB_ID),
                   ledger_path=tmp_path / "ledger.sqlite")


def _tick(plan, tmp_path, *, manual, at=NOW):
    return refresh.run_tick(plan, now=at, only_job=JOB_ID, manual=manual, data_db=tmp_path / "missing.sqlite")[0]


def test_a_scheduled_tick_holds_back_a_weekly_job_two_days_after_its_window(plan, tmp_path):
    assert _tick(plan, tmp_path, manual=False)["status"] == "misfire"


def test_a_manual_run_ignores_the_schedule_and_labels_its_own_window(plan, tmp_path):
    row = _tick(plan, tmp_path, manual=True)
    assert row["status"] == "succeeded"
    assert row["window"].startswith("2026-10-19~manual-20261021T130000")
    assert _Queue.seen[0]["trigger_kind"] == "manual"
    assert _Queue.seen[0]["command"][:3] == ("ats", "data", "article-ingest")


def test_manual_runs_can_repeat_and_do_not_consume_the_scheduled_window(plan, tmp_path):
    _tick(plan, tmp_path, manual=True)
    again = _tick(plan, tmp_path, manual=True, at=NOW + timedelta(minutes=5))
    assert again["status"] == "succeeded" and again["window"] != _Queue.seen[0]["scope"]["window"]
    monday = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
    later = refresh.run_tick(plan, now=monday + timedelta(days=7), only_job=JOB_ID,
                             data_db=tmp_path / "missing.sqlite")[0]
    assert (later["window"], later["status"]) == ("2026-10-26", "succeeded")   # the schedule's next window still runs


def test_a_disabled_job_stays_disabled_even_when_run_manually(plan, tmp_path):
    disabled = replace(plan, jobs=tuple(replace(j, enabled=False) for j in plan.jobs))
    assert _tick(disabled, tmp_path, manual=True)["status"] == "disabled"
