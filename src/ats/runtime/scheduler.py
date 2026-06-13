"""Daily scheduler: trigger a trading cycle on NYSE sessions only.

Pairs with an async channel (Feishu): at the configured time the job runs the
analysis, sends an approval card, and returns — the Boss approves from their
phone and the `ats serve` webhook resumes execution. With the CLI channel a
scheduled run would block on terminal input, so a warning is emitted.

    ats schedule            # start the daemon (cron from config/settings.yaml)
    ats schedule --now      # run one cycle immediately (skips if not a session)
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from ..config import get_config

log = logging.getLogger("ats.scheduler")


def is_trading_session(day: date | datetime | None = None) -> bool:
    """True if `day` (default today) is a NYSE trading session."""
    import pandas_market_calendars as mcal

    d = (day or datetime.now()).date() if isinstance(day, datetime) else (day or _today())
    iso = d.isoformat()
    sched = mcal.get_calendar("XNYS").schedule(start_date=iso, end_date=iso)
    return not sched.empty


def _today() -> date:
    return datetime.now().date()


def run_if_session(*, dry_run: bool = True) -> bool:
    """Run one cycle if today is a session. Returns True if it ran."""
    from ..channel import get_channel
    from .cli import run_cycle

    if not is_trading_session():
        log.info("not a trading session today (%s); skipping", _today())
        return False

    channel = get_channel()
    if getattr(channel, "is_async", False) is False:
        log.warning("scheduled run with a blocking channel (%s) will wait on input; "
                    "use channel.kind=feishu for unattended approval",
                    getattr(channel, "kind", "cli"))
    log.info("starting scheduled cycle (dry_run=%s)", dry_run)
    run_cycle(dry_run=dry_run, channel=channel)
    return True


def start(*, dry_run: bool = True, run_once: bool = False) -> None:
    cfg = get_config().app.schedule
    if run_once:
        run_if_session(dry_run=dry_run)
        return

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    hour, minute = (int(x) for x in cfg.run_at.split(":"))
    scheduler = BlockingScheduler(timezone=cfg.timezone)
    scheduler.add_job(
        lambda: run_if_session(dry_run=dry_run),
        CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=cfg.timezone),
        id="daily_cycle", misfire_grace_time=3600,
    )
    log.info("scheduler started: %s %s (mon-fri, NYSE sessions only)", cfg.run_at, cfg.timezone)
    print(f"⏰ scheduling daily cycle at {cfg.run_at} {cfg.timezone} (NYSE sessions). Ctrl-C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler stopped")
