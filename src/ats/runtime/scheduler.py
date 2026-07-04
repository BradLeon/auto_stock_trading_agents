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


def _pead_actions(today: date, earnings_date: date | None, hour: str,
                  sched_cfg: dict) -> list[str]:
    """Decide what to run for a PEAD target today (pure routing).

    Always monitor; prep when earnings is within prep_days_before; score after the
    print — same day for before-open (bmo/dmh) prints, next session for after-close
    (amc, or unknown).
    """
    actions = ["monitor"]
    if earnings_date:
        days_to = (earnings_date - today).days
        if 0 < days_to <= sched_cfg.get("prep_days_before", 3):
            actions.append("prep")
        score_offset = 0 if hour in ("bmo", "dmh") else 1   # bmo: same day; amc/unknown: T+1
        if sched_cfg.get("score_after", True) and (today - earnings_date).days == score_offset:
            actions.append("score")
    return actions


def pead_daily(*, dry_run: bool = True, use_llm: bool = True) -> dict:
    """Per-target: monitor (context update), plus prep/score by earnings proximity."""
    from ..config import load_pead_global
    from ..data import earnings_calendar
    from .cli import run_pead, run_pead_monitor, run_pead_research

    g = load_pead_global()
    if not g.get("monitor", {}).get("enabled", True):
        return {}
    if not is_trading_session():
        log.info("not a trading session; skipping PEAD daily")
        return {}

    # Newsletter research first, so injected insight-events reach today's monitors.
    if g.get("research", {}).get("enabled", True):
        try:
            run_pead_research(use_llm=use_llm)
        except Exception as exc:  # noqa: BLE001 - research must not break the monitors
            log.warning("PEAD research failed: %s", exc)

    today = _today()
    ran: dict[str, list[str]] = {}
    for sym in g.get("targets", []):
        ev = earnings_calendar.next_earnings(sym)
        ed = ev["date"] if ev else None
        hour = ev["hour"] if ev else ""
        actions = _pead_actions(today, ed, hour, g.get("schedule", {}))
        log.info("PEAD %s: earnings=%s (%s) -> %s", sym, ed, hour or "?", actions)
        for action in actions:
            try:
                if action == "monitor":
                    run_pead_monitor(sym, use_llm=use_llm)
                elif action == "prep":
                    run_pead(sym, "prep", dry_run=dry_run, use_llm=use_llm)
                elif action == "score":
                    run_pead(sym, "score", dry_run=dry_run, use_llm=use_llm, channel="feishu")
            except Exception as exc:  # noqa: BLE001 - one target must not break the rest
                log.warning("PEAD %s %s failed: %s", sym, action, exc)
        ran[sym] = actions
    return ran


def _daily(*, dry_run: bool) -> None:
    run_if_session(dry_run=dry_run)
    _macro_weekly()      # top-down cascade: macro -> sector -> (daily) pead
    _sector_weekly()
    pead_daily(dry_run=dry_run)


def _macro_weekly() -> None:
    """Weekly macro strategist review (Mondays by default). Runs BEFORE the sector
    review so this week's macro regime feeds it (cascade 宏观→行业→个股)."""
    from ..config import load_pead_global
    from .cli import run_macro_review

    mr = load_pead_global()["macro_review"]
    if not mr["enabled"] or _today().weekday() != mr["weekday"]:
        return
    try:
        run_macro_review(mr["name"])
    except Exception as exc:  # noqa: BLE001 - review must not break the daily job
        log.warning("macro review failed: %s", exc)


def _sector_weekly() -> None:
    """Weekly sector review (Mondays by default). Lands after today's monitors, so
    the freshest injection reaches Tuesday-onward runs; run `ats sector review`
    manually if it matters intraday."""
    from ..config import load_pead_global
    from .cli import run_sector_review

    sr = load_pead_global()["sector_review"]
    if not sr["enabled"] or _today().weekday() != sr["weekday"]:
        return
    for name in sr["sectors"]:
        try:
            run_sector_review(name)
        except Exception as exc:  # noqa: BLE001 - review must not break the daily job
            log.warning("sector review %s failed: %s", name, exc)


def start(*, dry_run: bool = True, run_once: bool = False) -> None:
    cfg = get_config().app.schedule
    if run_once:
        _daily(dry_run=dry_run)
        return

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    hour, minute = (int(x) for x in cfg.run_at.split(":"))
    scheduler = BlockingScheduler(timezone=cfg.timezone)
    scheduler.add_job(
        lambda: _daily(dry_run=dry_run),
        CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=cfg.timezone),
        id="daily_cycle", misfire_grace_time=3600,
    )
    log.info("scheduler started: %s %s (mon-fri, NYSE sessions only; daily cycle + PEAD)",
             cfg.run_at, cfg.timezone)
    print(f"⏰ scheduling daily cycle + PEAD at {cfg.run_at} {cfg.timezone} (NYSE sessions). "
          f"Ctrl-C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler stopped")
