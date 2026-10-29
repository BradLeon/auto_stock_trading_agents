"""Research dispatcher daemon (`ats schedule`, launchd `com.ats.dispatcher`).

Wakes the research workflows on their schedules (`config/workflow/research_schedules.yaml`)
and on published calendar events, and runs them through the Dispatcher. It never enters
the Chief decision cycle and never touches the broker.

This replaces the legacy serial scheduler (the daily cascade, weekly review, PEAD score
windows, FactSet weekly ingest, Chief daily and journal reconcile jobs), removed on
2026-10-20. The trade-path jobs (Chief daily decision, Clerk reconcile) get a Dispatcher
schedule when the trade path is switched.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("ats.dispatcher")

ET = ZoneInfo("America/New_York")

# The scheduler sleeps on a monotonic timer, which does not advance while the Mac is asleep: a job due at
# 20:00 that the machine slept through fired 2.6 hours late (2026-10-21), although it had been fully awake
# long before. Due-ness itself is judged against the wall clock, so all that is needed is that the scheduler
# wakes up often. This job does nothing; it only bounds how long a sleep can hide an overdue job.
HEARTBEAT_JOB_ID = "heartbeat"
HEARTBEAT_SECONDS = 30


def _now_et() -> datetime:
    return datetime.now(ET)


def is_trading_session(day: date | datetime | None = None) -> bool:
    """True if `day` (default today in New York) is a NYSE trading session."""
    import pandas_market_calendars as mcal

    d = day.date() if isinstance(day, datetime) else (day or _now_et().date())
    iso = d.isoformat()
    sched = mcal.get_calendar("XNYS").schedule(start_date=iso, end_date=iso)
    return not sched.empty


def _attach_job_logging(scheduler) -> None:
    """Log every fire, miss and error.

    Without this we are blind in the exact way that matters: for 2.5 days the daemon
    logged "scheduler started" and then nothing, and a missed job looks identical to a
    quiet day. APScheduler reports misses on its own logger, which our config leaves at
    WARNING on the root handler — reachable in principle, but nothing ever surfaced it
    next to our own lines. Now a miss is loud and a late run says how late it was.
    """
    from apscheduler.events import (
        EVENT_JOB_ERROR,
        EVENT_JOB_EXECUTED,
        EVENT_JOB_MISSED,
    )

    def _on_event(event) -> None:
        from ..workflow.schedule_runtime import observe

        if event.job_id == HEARTBEAT_JOB_ID:     # every 30 seconds: no log line, no ledger row
            return
        if event.scheduled_run_time:
            observe("wake_seen", event.job_id, {"planned": event.scheduled_run_time.astimezone(timezone.utc).isoformat(),
                                              "code": event.code})
        if event.code == EVENT_JOB_MISSED:
            observe("wake_misfire", event.job_id, {"scheduled_for": event.scheduled_run_time.isoformat()})
            log.error("job %s MISSED its %s run — machine asleep past the grace window? "
                      "(nothing ran)", event.job_id, event.scheduled_run_time)
            return
        late = ""
        if event.scheduled_run_time:
            secs = (_now_et() - event.scheduled_run_time).total_seconds()
            if secs > 300:
                late = f"（迟 {secs / 3600:.1f} 小时）"
        if event.code == EVENT_JOB_ERROR:
            log.error("job %s raised%s: %s", event.job_id, late, event.exception)
        else:
            log.info("job %s finished%s", event.job_id, late)

    scheduler.add_listener(_on_event,
                           EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED)


def _add_heartbeat(scheduler) -> None:
    """Wake the scheduler every HEARTBEAT_SECONDS of awake time (see HEARTBEAT_JOB_ID).

    The beat runs on its own executor so a workflow that occupies the single worker for hours cannot make
    beats queue up behind it, and it has no misfire limit so a long sleep never reports it as missed.
    """
    from apscheduler.executors.pool import ThreadPoolExecutor as BeatExecutor
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler.add_executor(BeatExecutor(1), alias="heartbeat")
    scheduler.add_job(lambda: None, IntervalTrigger(seconds=HEARTBEAT_SECONDS), id=HEARTBEAT_JOB_ID,
                      executor="heartbeat", coalesce=True, max_instances=1, misfire_grace_time=None)


def start(*, run_once: bool = False) -> None:
    """Start the research dispatcher daemon (or, with `run_once`, dispatch every enabled workflow once)."""
    from ..execution.broker_write_guard import startup

    startup(caller="ats.runtime.dispatcher_daemon.start")
    from ..workflow.cutover import assert_runtime_startup

    assert_runtime_startup(caller="ats.runtime.dispatcher_daemon.start")
    from ..workflow.schedule_runtime import reload_authority

    reload_authority()
    _run_dispatcher(run_once=run_once)


def _run_dispatcher(*, run_once: bool = False) -> None:
    """Scheduled and calendar-event wake-ups; workflow ownership is checked per workflow ID."""
    from ..workflow.ownership import research_schedule_entries, run_owned_workflow
    from ..workflow.run_contracts import TriggerContext
    from ..workflow.triggers import load_trigger_policies, reconcile_schedule, trigger_key

    from ..workflow.ownership import install_product_health_gate

    install_product_health_gate()
    from ..workflow.trade_schedules import (is_trading_day, resumed_at, run_trade_workflow,
                                            trade_schedule_entries)

    entries = research_schedule_entries()
    trade_entries = trade_schedule_entries()      # empty while both trade workflows are switched off
    entries = {**entries, **trade_entries}
    resumed = {workflow_id: resumed_at(workflow_id) for workflow_id in trade_entries}
    policies = load_trigger_policies()

    def dispatch_tick(workflow_id: str, entry: dict, planned: datetime) -> None:
        context = TriggerContext(kind="schedule", workflow_id=workflow_id,
                                 schedule_id=workflow_id,
                                 scheduled_for=planned.isoformat())
        from ..workflow.schedule_runtime import observe

        observe("wake", workflow_id, {"owner": "dispatcher", "trigger": context.model_dump(mode="json")})
        try:
            if workflow_id in trade_entries:
                result = run_trade_workflow(workflow_id, entry, context, now=planned)
                log.info("workflow %s -> %s", workflow_id, result.get("status") or result.get("outcome"))
                return
            result = run_owned_workflow(
                workflow_id, scope=entry["scope"], trigger=context,
                request={"requested_tasks": list(entry["task_ids"]),
                         "task_inputs": {"schedule_id": workflow_id}}, now=_now_et())
            log.info("workflow %s -> %s", workflow_id, result.get("status"))
        except Exception:  # noqa: BLE001
            log.exception("workflow %s failed", workflow_id)

    if run_once:
        now = datetime.now(timezone.utc)
        for workflow_id, entry in entries.items():
            dispatch_tick(workflow_id, entry, now)
        if not entries:
            print("当前没有启用的 dispatcher 工作流。")
        return

    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
    from ..workflow.schedule_executor import PlannedThreadPoolExecutor as ThreadPoolExecutor
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone="UTC", executors={"default": ThreadPoolExecutor(1)})
    _attach_job_logging(scheduler)
    _add_heartbeat(scheduler)
    job_to_workflow = {}

    def on_wake(event) -> None:
        workflow_id = job_to_workflow.get(event.job_id)
        if not workflow_id or workflow_id not in entries:
            return
        planned = event.scheduled_run_time
        if planned is None:
            return
        if event.code == EVENT_JOB_MISSED:
            from ..workflow.schedule_runtime import observe

            observe("wake_misfire", workflow_id, {"owner": "dispatcher", "scheduled_for": planned.isoformat()})
            log.warning("wake %s misfired at %s; Trigger Ledger will catch up or skip",
                        workflow_id, planned)
            dispatch_tick(workflow_id, entries[workflow_id], planned)

    scheduler.add_listener(on_wake, EVENT_JOB_EXECUTED | EVENT_JOB_MISSED)
    # The in-memory cron engine forgets ticks across process restarts. Enumerate only
    # the configured bounded lookback, compare with the durable ledger, and dispatch
    # eligible gaps using their original planned instants.
    from datetime import timedelta
    from ..workflow.ownership import workflow_store_for_owner

    startup_now = datetime.now(timezone.utc)
    for workflow_id, entry in entries.items():
        cron = entry["cron"]
        job_id = f"phase_e:{workflow_id}"
        job_to_workflow[job_id] = workflow_id
        trigger = CronTrigger(
            day_of_week=cron.get("day_of_week", "*"), hour=int(cron["hour"]),
            minute=int(cron["minute"]), timezone=cron["timezone"])
        policy = policies.get(workflow_id, policies["default"])
        start_at = startup_now - timedelta(seconds=max(
            policy.max_lookback_seconds, policy.misfire_grace_seconds, 1))
        ticks = []
        planned = trigger.get_next_fire_time(None, start_at)
        while planned is not None and planned <= startup_now:
            if workflow_id in trade_entries and (
                    not is_trading_day(planned.astimezone(ET).date())
                    or (resumed.get(workflow_id) and planned < resumed[workflow_id])):
                # A closed market has no tick; a tick from before the workflow was resumed was never its to run.
                planned = trigger.get_next_fire_time(planned, planned)
                continue
            context = TriggerContext(kind="schedule", workflow_id=workflow_id,
                                     schedule_id=workflow_id, scheduled_for=planned.isoformat())
            frozen_request = {
                "requested_tasks": list(entry["task_ids"]),
                "scope": entry["scope"].model_dump(mode="json"),
                "task_inputs": {"schedule_id": workflow_id},
                "profile_id": "", "enter_decision_cycle": False,
            }
            ticks.append((context, frozen_request))
            planned = trigger.get_next_fire_time(planned, planned)
        if workflow_id in trade_entries:
            # Missed ticks inside the grace window are replayed under their original planned instants; the claim
            # keyed by workflow + scope + instant returns the first run's result, so a restart never runs one twice.
            for context, _ in ticks:
                dispatch_tick(workflow_id, entry, datetime.fromisoformat(context.scheduled_for))
            decisions = []
        else:
            decisions = reconcile_schedule(workflow_store_for_owner(workflow_id, scope=entry["scope"]),
                                           expected=ticks, now=startup_now, policy=policy)
        for decision in decisions:
            if decision["status"] == "planned":
                key = decision["trigger_key"]
                item = next((ctx for ctx, _ in ticks if trigger_key(ctx) == key), None)
                if item is not None:
                    dispatch_tick(workflow_id, entry, datetime.fromisoformat(item.scheduled_for))
        def run_tick(workflow_id=workflow_id, entry=entry):
            from ..workflow.schedule_runtime import _WAKE

            dispatch_tick(workflow_id, entry, datetime.fromisoformat(_WAKE.get().scheduled_for))

        scheduler.add_job(run_tick, trigger, id=job_id, coalesce=False,
                          max_instances=1, misfire_grace_time=None)

    from ..config import REPO_ROOT
    import os
    import yaml

    cfg_root = Path(os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))
    calendar_cfg = yaml.safe_load((cfg_root / "data" / "schedule_calendar.yaml").read_text(
        encoding="utf-8")) or {}
    refresh_schedule = (calendar_cfg.get("refresh", {}) or {}).get("schedule", {}) or {}
    if refresh_schedule.get("enabled", False):
        def consume_calendar() -> None:
            # Source publication/queue leases retain their existing owner. This
            # research daemon consumes published versions without refreshing them.
            from ..workflow.ownership import (dispatch_planned_calendar_events,
                dispatch_released_calendar_events, reconcile_calendar_trigger_versions,
                record_due_calendar_material_waits)

            for operation in (reconcile_calendar_trigger_versions,
                              dispatch_planned_calendar_events,
                              record_due_calendar_material_waits,
                              dispatch_released_calendar_events):
                try:
                    operation()
                except Exception:
                    log.exception("published calendar consumption failed: %s", operation.__name__)

        cron = CronTrigger(day_of_week="*", hour=refresh_schedule.get("hour", 6),
                           minute=str(refresh_schedule.get("minute", 10)),
                           timezone=refresh_schedule.get("timezone", "UTC"))
        scheduler.add_job(consume_calendar, cron, id="phase_e:calendar-consume", coalesce=True,
                          max_instances=1, misfire_grace_time=None)

    log.info("dispatcher started: workflows=%s; calendar_consume=%s",
             ",".join(entries) or "none", bool(refresh_schedule.get("enabled", False)))
    print(f"⏰ dispatcher workflows: {', '.join(entries) or 'none'}; "
          f"published calendar consume: {'enabled' if refresh_schedule.get('enabled', False) else 'disabled'}")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("dispatcher stopped")
