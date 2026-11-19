"""2026-11-19: an incomplete Technical run is retried before the Chief, and trade-path failures reach the Boss.

11-13 and 11-17 the 08:30 technical run lost GOOG and MSFT to a proxy burst, the 09:45 chief-daily stopped at
`assemble_context`, and the only trace was a log line.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ats.agent.task_projection import ProjectionScope
from ats.runtime import alerts, dispatcher_daemon
from ats.workflow import schedule_runtime
from ats.workflow.run_contracts import TriggerContext

ENTRY = {"task_ids": ["technical-review"], "scope": ProjectionScope(kind="portfolio", id="portfolio"),
         "cron": {"day_of_week": "mon-fri", "hour": 8, "minute": 30, "timezone": "America/New_York"}}
INCOMPLETE = {"status": "incomplete", "missing_requirements": [
    {"scope": "entity:GOOG", "detail": "Technical Analyst produced no reading for GOOG", "kind": "missing"}]}


@pytest.fixture
def pushed(monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr("ats.workflow.consumer_health.feishu_notify", lambda title, body: sent.append((title, body)))
    return sent


@pytest.fixture
def daemon(monkeypatch):
    """The dispatcher wired to a recording scheduler, with technical-review as its only workflow."""
    from apscheduler.schedulers import blocking
    from test_phase_f_schedule_runtime import FakeScheduler

    monkeypatch.setattr("ats.workflow.ownership.install_product_health_gate", lambda: None)
    monkeypatch.setattr("ats.workflow.ownership.research_schedule_entries", lambda **kw: {"technical-review": ENTRY})
    monkeypatch.setattr("ats.workflow.trade_schedules.trade_schedule_entries", lambda *a, **kw: {})
    monkeypatch.setattr("ats.workflow.triggers.reconcile_schedule", lambda *a, **kw: [])
    monkeypatch.setattr("ats.workflow.ownership.workflow_store_for_owner", lambda *a, **kw: None)
    holder = {}
    monkeypatch.setattr(blocking, "BlockingScheduler",
                        lambda *a, **kw: holder.setdefault("s", FakeScheduler(*a, **kw)))
    return holder


def _results(monkeypatch, *results):
    runs: list[TriggerContext] = []
    queue = list(results)

    def fake(workflow_id, *, scope, trigger, request=None, now=None, config_dir=None):
        runs.append(trigger)
        return queue.pop(0)

    monkeypatch.setattr("ats.workflow.ownership.run_owned_workflow", fake)
    return runs


def _fire_tick(holder):
    job = next(job for job in holder["s"].jobs if job["id"] == "phase_e:technical-review")
    planned = datetime(2026, 11, 17, 13, 30, tzinfo=UTC)
    with schedule_runtime.wake(TriggerContext(kind="schedule", schedule_id=job["id"],
                                              scheduled_for=planned.isoformat())):
        job["fn"]()


def _retries(holder):
    return [job for job in holder["s"].jobs if job["id"].startswith("retry:technical-review:")]


def test_incomplete_technical_is_retried_then_succeeds_without_an_alert(daemon, monkeypatch, pushed):
    runs = _results(monkeypatch, INCOMPLETE, {"status": "complete"})
    dispatcher_daemon._run_dispatcher()

    _fire_tick(daemon)
    [retry] = _retries(daemon)
    assert retry["args"][3] == 1
    retry["fn"](*retry["args"])

    assert [run.kind for run in runs] == ["schedule", "manual"]
    assert runs[1].trigger_id.startswith("retry-incomplete:technical-review:")
    assert len(_retries(daemon)) == 1 and pushed == []


def test_technical_still_incomplete_after_its_retries_alerts_once_with_the_names(daemon, monkeypatch, pushed):
    _results(monkeypatch, INCOMPLETE, INCOMPLETE, INCOMPLETE)
    dispatcher_daemon._run_dispatcher()

    _fire_tick(daemon)
    for attempt in (1, 2):
        retry = _retries(daemon)[attempt - 1]
        retry["fn"](*retry["args"])

    assert len(_retries(daemon)) == len(dispatcher_daemon.INCOMPLETE_RETRY_SECONDS["technical-review"])
    [(title, body)] = pushed
    assert "technical-review" in title and "共 3 次" in title
    assert "GOOG" in body


def test_retries_end_before_the_chief_cycle():
    # 08:30 NY + ~3 min run, then the waits and two more short runs, must end before 09:45 NY.
    assert sum(dispatcher_daemon.INCOMPLETE_RETRY_SECONDS["technical-review"]) + 3 * 300 < 75 * 60


def test_a_failed_chief_daily_alerts(monkeypatch, pushed):
    monkeypatch.setattr("ats.workflow.ownership.install_product_health_gate", lambda: None)
    monkeypatch.setattr("ats.workflow.ownership.research_schedule_entries", lambda **kw: {})
    monkeypatch.setattr("ats.workflow.trade_schedules.trade_schedule_entries",
                        lambda *a, **kw: {"chief-daily": {**ENTRY, "task_ids": []}})
    monkeypatch.setattr("ats.workflow.trade_schedules.resumed_at", lambda workflow_id: None)

    def boom(*a, **kw):
        from ats.workflow.read_scope import ReadUnavailable

        raise ReadUnavailable("required decision input invalid: technical-review:GOOG:freshness_unconfirmed")

    monkeypatch.setattr("ats.workflow.trade_schedules.run_trade_workflow", boom)
    dispatcher_daemon._run_dispatcher(run_once=True)

    [(title, body)] = pushed
    assert "chief-daily 失败" in title
    assert "freshness_unconfirmed" in body


def test_a_blocked_chief_daily_alerts_with_its_gap_report(monkeypatch, pushed):
    monkeypatch.setattr("ats.workflow.ownership.install_product_health_gate", lambda: None)
    monkeypatch.setattr("ats.workflow.ownership.research_schedule_entries", lambda **kw: {})
    monkeypatch.setattr("ats.workflow.trade_schedules.trade_schedule_entries",
                        lambda *a, **kw: {"chief-daily": {**ENTRY, "task_ids": []}})
    monkeypatch.setattr("ats.workflow.trade_schedules.resumed_at", lambda workflow_id: None)
    monkeypatch.setattr("ats.workflow.trade_schedules.run_trade_workflow",
                        lambda *a, **kw: {"outcome": "research_incomplete", "gap_report": "缺 layer L1_app"})
    dispatcher_daemon._run_dispatcher(run_once=True)

    [(title, body)] = pushed
    assert "被阻断" in title and "L1_app" in body


def test_a_broken_channel_never_raises(monkeypatch):
    def broken(title, body):
        raise RuntimeError("feishu down")

    monkeypatch.setattr("ats.workflow.consumer_health.feishu_notify", broken)
    alerts.chief_blocked(None, {"gap_report": "x"})       # logged, not raised


def test_a_chief_daily_left_for_manual_review_alerts_with_its_reason(monkeypatch, pushed):
    monkeypatch.setattr("ats.workflow.ownership.install_product_health_gate", lambda: None)
    monkeypatch.setattr("ats.workflow.ownership.research_schedule_entries", lambda **kw: {})
    monkeypatch.setattr("ats.workflow.trade_schedules.trade_schedule_entries",
                        lambda *a, **kw: {"chief-daily": {**ENTRY, "task_ids": []}})
    monkeypatch.setattr("ats.workflow.trade_schedules.resumed_at", lambda workflow_id: None)
    monkeypatch.setattr("ats.workflow.trade_schedules.run_trade_workflow", lambda *a, **kw: {
        "outcome": "manual_review", "cycle_id": "chief-20261118-144500",
        "reason": "订单规范化拒绝（执行环境不可用）：execution_quote_source_timestamp_missing"})
    dispatcher_daemon._run_dispatcher(run_once=True)

    [(title, body)] = pushed
    assert "转人工复核" in title
    assert "chief-20261118-144500" in body and "execution_quote_source_timestamp_missing" in body
