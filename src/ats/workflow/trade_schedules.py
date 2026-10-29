"""The trade path's scheduled workflows: `clerk-daily` and `chief-daily` (design D5).

They share the Dispatcher's schedule authority (owner, claims, planned-instant identity) with the research
workflows but are not research tasks: one runs the Clerk's ledger pipeline, the other starts a Chief decision
cycle and leaves it waiting for the Boss. Both run only on NYSE trading sessions.

`enabled: false` (the starting state) means nothing here registers, plans or expects anything.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from ..agent.task_projection import ProjectionScope
from ..config import REPO_ROOT

ET = ZoneInfo("America/New_York")

#: workflow -> the consumer whose read route and health must pass before it may be resumed.
TRADE_WORKFLOW_CONSUMER = {"clerk-daily": "clerk", "chief-daily": "chief"}


def _root(config_dir) -> Path:
    return Path(config_dir or os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))


def load_trade_schedules(config_dir=None) -> dict[str, dict[str, Any]]:
    path = _root(config_dir) / "workflow" / "trade_schedules.yaml"
    if not path.is_file():                  # a config directory without trade workflows (older drills, tests)
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    workflows = payload.get("workflows", {}) or {}
    unknown = set(workflows) - set(TRADE_WORKFLOW_CONSUMER)
    if unknown:
        raise ValueError(f"unknown trade workflow(s): {sorted(unknown)}")
    for workflow_id, value in workflows.items():
        cron = value.get("cron", {}) or {}
        if not all(key in cron for key in ("hour", "minute", "timezone")):
            raise ValueError(f"trade schedule {workflow_id!r} has incomplete cron settings")
    return workflows


def is_trading_day(day: date) -> bool:
    import pandas_market_calendars as mcal

    iso = day.isoformat()
    return not mcal.get_calendar("XNYS").schedule(start_date=iso, end_date=iso).empty


def enabled_entries(config_dir=None) -> dict[str, dict[str, Any]]:
    """The workflows switched on in the schedule file, before the owner is consulted."""
    out = {}
    for workflow_id, value in load_trade_schedules(config_dir).items():
        if value.get("enabled", False):
            out[workflow_id] = {**value, "task_ids": (workflow_id,),
                                "scope": ProjectionScope.model_validate(value["scope"])}
    return out


def trade_schedule_entries(config_dir=None) -> dict[str, dict[str, Any]]:
    """The workflows the dispatcher should register: switched on AND owned by the dispatcher.

    A workflow whose owner is still `legacy` (paused) is left out even when its schedule says enabled, the
    same rule the research workflows follow.
    """
    from .schedule_runtime import ScheduleAuthorityError, current_owner

    out = {}
    for workflow_id, entry in enabled_entries(config_dir).items():
        try:
            owner = current_owner(workflow_id, entry["scope"].model_dump(mode="json"))
        except ScheduleAuthorityError:
            continue                                # no owner row yet: it has never been resumed
        if owner == "dispatcher":
            out[workflow_id] = entry
    return out


def resumed_at(workflow_id: str) -> datetime | None:
    """When the workflow was last resumed (owner log), so a restart never replays ticks from before that."""
    from .schedule_runtime import owner_log

    stamps = [datetime.fromisoformat(row["at"]) for row in owner_log(workflow_id)
              if row.get("action") == "switch" and row.get("to_owner") == "dispatcher"]
    return max(stamps) if stamps else None


def planned_trading_ticks(cron: dict, start: datetime, end: datetime):
    """Cron instants in [start, end] that fall on a NYSE trading session (local date of the workflow's zone)."""
    from apscheduler.triggers.cron import CronTrigger

    engine = CronTrigger(**cron)
    point = engine.get_next_fire_time(None, start)
    zone = ZoneInfo(cron["timezone"])
    while point is not None and point <= end:
        if is_trading_day(point.astimezone(zone).date()):
            yield point
        point = engine.get_next_fire_time(point, point)


# --- what a wake does ------------------------------------------------------------------------------ #
def _clerk_daily(entry: dict) -> dict:
    from ..execution import clerk

    out = clerk.clerk_run(dry_run=False)
    return {"status": out.get("status"), "run_id": out.get("run_id")}


def _chief_daily(entry: dict) -> dict:
    from ..runtime.cli import start_chief_cycle

    cycle_id, result = start_chief_cycle(
        execute=True, dry_run=False, channel="pending", auto=False, source="scheduled",
        fundamental_mode=entry.get("fundamental_mode", "routine"),
        decision_profile=entry.get("decision_profile", "ai_hardware"))
    if "__interrupt__" in result:
        outcome = "pending_approval"
    elif result.get("gap_report"):
        outcome = "research_incomplete"
    else:
        outcome = str(result.get("cycle_status") or "ended")
    return {"cycle_id": cycle_id, "outcome": outcome}


_RUNNERS = {"clerk-daily": _clerk_daily, "chief-daily": _chief_daily}


def run_trade_workflow(workflow_id: str, entry: dict, trigger, *, now: datetime | None = None) -> dict:
    """One scheduled wake of a trade workflow: a closed market does nothing, an open one runs under the claim.

    The claim is keyed by workflow, scope and the planned instant, so a restart that replays the same tick finds
    the first run's result instead of running again. Each workflow runs in its own claim: a Chief failure leaves
    the Clerk's tick untouched, and the reverse.
    """
    from .schedule_runtime import execute

    planned = datetime.fromisoformat(trigger.scheduled_for) if trigger.scheduled_for else (now or datetime.now(ET))
    if not is_trading_day(planned.astimezone(ET).date()):
        return {"status": "skipped", "reason": "market_closed"}
    runner = _RUNNERS[workflow_id]
    return execute(workflow_id, entry["scope"].model_dump(mode="json"), trigger, lambda: runner(entry),
                   owner="dispatcher", actor="dispatcher-" + uuid.uuid4().hex)
