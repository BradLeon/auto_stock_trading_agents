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
from zoneinfo import ZoneInfo

from ..config import get_config

log = logging.getLogger("ats.scheduler")

# All scheduler date arithmetic is in market time — see _today_et().
ET = ZoneInfo("America/New_York")


def is_trading_session(day: date | datetime | None = None) -> bool:
    """True if `day` (default today, ET) is a NYSE trading session."""
    import pandas_market_calendars as mcal

    d = day.date() if isinstance(day, datetime) else (day or _today())
    iso = d.isoformat()
    sched = mcal.get_calendar("XNYS").schedule(start_date=iso, end_date=iso)
    return not sched.empty


def _now_et() -> datetime:
    return datetime.now(ET)


def _today_et() -> date:
    """The market's calendar date, not the machine's.

    This used to be `datetime.now().date()` — the naive LOCAL date. On an Asia-based
    machine that is a day AHEAD of ET whenever the job fires after ~12:00 ET, which
    silently mis-dated everything downstream: the NYSE session check, the events
    calendar, and the Monday gates for the macro/sector reviews. At the old
    run_at 16:15 (= 04:15 next day in Asia) it was always wrong; the current 10:30
    (= 22:30 same day) happens to line up; the 20:00 ET amc score window would be
    wrong again. So date arithmetic is pinned to ET.
    """
    return _now_et().date()


# Kept as an alias: `_today` reads naturally at the call sites and several tests
# monkeypatch it to pin "today".
_today = _today_et


def _pead_actions(today: date, earnings_date: date | None, hour: str,
                  sched_cfg: dict) -> list[str]:
    """Decide what to run for a PEAD target in the daily cycle (pure routing).

    Monitor always; prep when the next print is within prep_days_before.

    Scoring is NOT here. It used to be, as
        score_offset = 0 if hour in ("bmo","dmh") else 1
        if (today - earnings_date).days == score_offset: actions.append("score")
    which could never fire: `earnings_date` came from next_earnings(), which filters
    to dates >= today, so (today - earnings_date).days was always <= 0 and never 1.
    The branch was dead in production for every after-close print — and the unit test
    hid it by passing a past date that the real source cannot produce. Scoring now
    lives in `_score_plan` / `pead_score_window`, triggered by an OBSERVED print.
    """
    actions = ["monitor"]
    if earnings_date:
        days_to = (earnings_date - today).days
        if 0 < days_to <= sched_cfg.get("prep_days_before", 3):
            actions.append("prep")
    return actions


# --------------------------------------------------------------------------- #
# Score windows: one before the open (bmo prints), one after the close (amc)
# --------------------------------------------------------------------------- #
def _score_plan(window: str, today: date, print_, state: str, sched_cfg: dict) -> tuple[bool, str]:
    """Should `window` attempt to score this print today? Pure — no I/O.

    `print_` is an EarningsPrint (or None). `state` is store.score_state(...).
    Sessions map to windows as follows, and a print whose session is UNKNOWN is
    attempted in BOTH windows — measured, Finnhub's `hour` is blank for 5 of 13
    targets, so guessing would mis-time those. Double attempts are safe because
    `state` short-circuits once a score exists.
    """
    if print_ is None:
        return (False, "无近期财报")
    age = (today - print_.date).days
    if age < 0:
        return (False, f"财报在未来（{print_.date}）")
    if age > sched_cfg.get("score_lookback_days", 4):
        return (False, f"财报已过 {age} 天，超出补打窗口")
    if state == "final":
        return (False, "已有含纪要的终版打分")
    if state == "v1_no_transcript" and age > sched_cfg.get("transcript_upgrade_days", 4):
        return (False, f"纪要窗口已过（{age} 天），v1 定稿")

    session = print_.session or "unknown"
    if session == "bmo":
        ok = window == "bmo"
    elif session in ("amc", "dmh"):
        # Same day only the after-close window makes sense; from T+1 either window
        # may catch up (e.g. the machine was asleep at 20:00).
        ok = window == "amc" if age == 0 else True
    else:
        ok = True                      # unknown session -> try both windows
    if not ok:
        return (False, f"session={session} 不匹配 {window} 窗口")

    verb = "补打" if age else "首打"
    return (True, f"{verb}（{print_.date} {session}/{print_.session_source}, state={state}）")


def _confirm_reported(symbol: str, print_) -> tuple[bool, str]:
    """Has this print ACTUALLY been released? Guards against scoring too early.

    Two independent pieces of evidence:
      1. an actual EPS/revenue in the calendar feeds — authoritative, but the vendors
         can lag hours behind the release;
      2. a SEC 8-K filed on/after the print date — lands within minutes of the
         release, so this is what makes same-evening scoring possible.

    Neither -> skip and let the next window retry. Never accept an 8-K filed BEFORE
    the print date: that is last quarter's release, and scoring on it would invent a
    surprise out of stale numbers.
    """
    if print_.reported:
        return (True, f"已公布（eps_actual={print_.eps_actual}）")
    from ..data import documents
    from ..data.base import safe_fetch

    rel = safe_fetch(lambda: documents.sec_8k_release(symbol),
                     source=f"sec-8k:{symbol}", attempts=2)
    if not rel:
        return (False, "无实际 EPS，且未取到 8-K")
    filed = rel.get("filed")
    if filed is None:
        return (False, "8-K 无申报日期，无法确认是本季")
    if filed < print_.date:
        return (False, f"最新 8-K 申报于 {filed}，早于财报日 {print_.date}（上一季）")
    return (True, f"8-K 申报于 {filed}（≥ 财报日 {print_.date}）")


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
            except Exception as exc:  # noqa: BLE001 - one target must not break the rest
                log.warning("PEAD %s %s failed: %s", sym, action, exc)
        ran[sym] = actions
    return ran


def pead_score_window(window: str, *, dry_run: bool = True, use_llm: bool = True,
                      as_of: datetime | None = None, chief: bool = True,
                      plan_only: bool = False) -> dict:
    """Score every target whose print has landed but hasn't been scored yet.

    Runs twice a day (see config/pead.yaml schedule.score_windows): the `amc` window
    after the close for that evening's prints, the `bmo` window late morning for
    before-open prints. Cheap by default — the expensive LLM path is only entered for
    a target that actually has an unscored, confirmed print.

    Returns {symbol: reason} for every target, so the log is a full audit of what was
    considered, not just what ran.
    """
    from ..config import load_pead_config, load_pead_global
    from ..data import earnings_calendar, period
    from ..memory import get_store
    from .cli import run_chief, run_pead

    g = load_pead_global()
    sched = g.get("schedule", {})
    if not sched.get("score_after", True):
        log.info("score windows disabled (schedule.score_after=false)")
        return {}

    now = as_of or _now_et()
    today = now.date()
    if not is_trading_session(today):
        log.info("not a trading session (%s); skipping PEAD %s score window", today, window)
        return {}

    store = get_store()
    outcomes: dict[str, str] = {}
    scored: list[str] = []

    for sym in g.get("targets", []):
        try:
            cfg = load_pead_config(sym)
            pr = earnings_calendar.last_print(
                sym, as_of=today, back_days=sched.get("score_lookback_days", 4) + 3)
            label = period.resolve_fiscal_label(
                sym, pr, config_label=cfg.fiscal_label, store=store)[0] if pr else cfg.fiscal_label
            state = store.score_state(sym, label) if label else "unscored"
            go, why = _score_plan(window, today, pr, state, sched)
            if go:
                # Confirm even under --plan-only: it is read-only and cheap, and a
                # preview that skipped it would not reflect what the real run does.
                ok, ev = _confirm_reported(sym, pr)
                if not ok:
                    go, why = False, f"待确认发布：{ev}"
                else:
                    why = f"{why} · {ev}"
            log.info("PEAD-score[%s] %s: %s -> %s", window, sym,
                     "GO" if go else "skip", why)
            outcomes[sym] = ("GO · " if go else "") + why
            if not go or plan_only:
                continue
            # Pin the resolved label so the Chief reads the same key tomorrow.
            period.resolve_and_cache(sym, pr, config_label=cfg.fiscal_label, store=store)
            run_pead(sym, "score", dry_run=dry_run, use_llm=use_llm, chief=False)
            scored.append(sym)
        except Exception as exc:  # noqa: BLE001 - one target must not break the window
            log.warning("PEAD-score[%s] %s failed: %s", window, sym, exc)
            outcomes[sym] = f"失败：{exc}"

    # One Chief cycle for the whole window, not one per symbol — a single approval
    # card. The Chief consumes the score, so the regular daily cascade then correctly
    # treats it as background instead of re-proposing the same trade.
    if scored and chief and not plan_only and sched.get("chief_after_score", True):
        try:
            run_chief(dry_run=dry_run, channel=get_config().app.channel.kind,
                      source="pead-chief")
        except Exception as exc:  # noqa: BLE001 - chief must not break the window
            log.warning("PEAD-score[%s] chief run failed: %s", window, exc)
    elif scored:
        log.info("PEAD-score[%s] scored %s; chief skipped", window, ",".join(scored))
    return outcomes


def _daily(*, dry_run: bool) -> None:
    _macro_weekly()      # top-down cascade: macro -> sector -> (daily) pead
    _sector_weekly()
    _event_triggers()    # FOMC/CPI/行业会议 -> extra analyst runs, cascade into today
    pead_daily(dry_run=dry_run)
    _intel_digest()      # surface today's intel: Obsidian .md + Feishu card
    _perf_snapshot()
    _perf_risk_digest()  # surface perf + 6-layer risk: Obsidian .md + Feishu card
    _chief_daily(dry_run=dry_run)   # LAST: the Chief reads everything fresh and decides


def _intel_digest() -> None:
    try:
        from .digest import intel_digest

        p = intel_digest()
        if p:
            log.info("intel digest -> %s", p)
    except Exception as exc:  # noqa: BLE001 - digest must not break the daily job
        log.warning("intel digest failed: %s", exc)


def _perf_risk_digest() -> None:
    try:
        from .digest import perf_risk_digest

        p = perf_risk_digest()
        if p:
            log.info("perf/risk digest -> %s", p)
    except Exception as exc:  # noqa: BLE001 - digest must not break the daily job
        log.warning("perf/risk digest failed: %s", exc)


def _perf_snapshot() -> None:
    """Record a portfolio/performance snapshot each trading day (independent of the
    daily cycle), so a continuous NetLiq/P&L curve exists even if only PEAD runs.
    Also records a risk snapshot and pushes an alert on de-risk state."""
    if not is_trading_session():
        return
    try:
        from ..trader import performance as tperf

        r = tperf.record_snapshot()
        if r is not None:
            log.info("perf snapshot: NetLiq $%.0f dayP&L $%.0f", r.net_liquidation, r.daily_pnl)
    except Exception as exc:  # noqa: BLE001 - snapshot must not break the daily job
        log.warning("perf snapshot failed: %s", exc)
    try:
        from ..memory import get_store
        from ..risk import assess as risk_assess
        from ..trader import portfolio as tport

        pf = tport.snapshot()
        if pf is not None:
            risk_assess.enrich_beta(pf)
            risk_assess.enrich_options(pf)
            review = risk_assess.assess(pf)
            get_store().save_risk_review(review)
            log.info("risk snapshot: state=%s breaches=%d", review.risk_state, len(review.breaches))
            if review.risk_state != "normal":
                _push_risk_alert(review)
    except Exception as exc:  # noqa: BLE001 - risk snapshot must not break the daily job
        log.warning("risk snapshot failed: %s", exc)


def _push_risk_alert(review) -> None:
    try:
        from ..channel import get_channel
        from ..config import load_pead_global
        from ..schemas.channel import Notification

        if not load_pead_global()["monitor"].get("push_context_updates", False):
            return
        get_channel("feishu").push(Notification(
            kind="error" if review.risk_state == "derisk" else "info",
            title=f"风控告警 — {review.risk_state} ({len(review.breaches)} 破限)",
            body=review.regime_block()))
    except Exception as exc:  # noqa: BLE001 - push is best-effort
        log.info("risk alert push skipped: %s", exc)


def _event_triggers() -> list[str]:
    """Fire analyst refreshes for today's calendar events (config/events.yaml).
    Returns the fired event labels (testable)."""
    from ..config import load_events, load_pead_global
    from .cli import run_macro_review, run_pead_monitor, run_sector_review

    fired: list[str] = []
    today = _today()
    for ev in load_events():
        if ev.date != today:
            continue
        log.info("event trigger: %s (%s) -> %s", ev.label, ev.kind, ev.triggers)
        for trig in ev.triggers:
            try:
                if trig == "macro":
                    run_macro_review(load_pead_global()["macro_review"]["name"])
                elif trig == "sector":
                    for name in load_pead_global()["sector_review"]["sectors"]:
                        run_sector_review(name)
                elif trig.startswith("sector:"):
                    run_sector_review(trig.split(":", 1)[1])
                elif trig.startswith("pead:"):
                    run_pead_monitor(trig.split(":", 1)[1])
                else:
                    log.warning("unknown event trigger %r on %s", trig, ev.label)
                    continue
                fired.append(f"{ev.label}->{trig}")
            except Exception as exc:  # noqa: BLE001 - one trigger must not break the job
                log.warning("event trigger %s (%s) failed: %s", ev.label, trig, exc)
    return fired


def _chief_daily(*, dry_run: bool) -> None:
    """Daily decision收口: the Chief reads all fresh artifacts and (via the trader's
    single approval gate) proposes/executes trades. Quiet days -> zero decisions."""
    if not is_trading_session():
        return
    try:
        from .cli import run_chief

        run_chief(dry_run=dry_run, channel=get_config().app.channel.kind,
                  source="scheduled")
    except Exception as exc:  # noqa: BLE001 - chief must not break the daily job
        log.warning("chief daily run failed: %s", exc)


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
