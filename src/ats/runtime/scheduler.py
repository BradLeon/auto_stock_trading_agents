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
    silently mis-dated everything downstream: the NYSE session check and the events
    calendar. At the old run_at 16:15 (= 04:15 next day in Asia) it was always wrong;
    the current 10:30 (= 22:30 same day) happens to line up; the 20:00 ET amc score
    window would be wrong again. So date arithmetic for anything NYSE-related is
    pinned to ET. (The weekly macro/sector review's own day gate is NOT here — see
    `_today_weekly()` — because that job was deliberately un-pinned from ET.)
    """
    return _now_et().date()


# Kept as an alias: `_today` reads naturally at the call sites and several tests
# monkeypatch it to pin "today".
_today = _today_et


def _today_weekly() -> date:
    """Calendar date for the weekly macro/sector review's own day gate.

    Deliberately NOT `_today_et()`: that job neither trades nor needs a NYSE session,
    so tying its "which day is it" concept to the ET calendar was only ever inherited
    from the shared helper. It actively breaks once the trigger's own timezone
    (schedule.weekly_review_tz) disagrees with ET on the calendar day — e.g. Beijing
    Saturday 08:40 is still Friday evening in New York, so `_today_et().weekday()`
    would read 4 (Friday), the weekday gate would silently return without running,
    and the job would look like it fired but produce nothing. Exactly the failure
    mode `_today_et()` itself exists to prevent, just with the roles reversed.
    """
    from ..config import get_config

    tz = ZoneInfo(get_config().app.schedule.weekly_review_tz)
    return datetime.now(tz).date()


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

    Same-day (days_to == 0) is prep-eligible UNLESS the print is bmo. A bmo print
    has already opened for trading by the time the 10:30 ET cycle runs — prep after
    the fact is moot. An amc/dmh/unknown-session print same-day is still hours away
    (10:30 ET is well before a 20:00 ET close), so prep is still useful. Found
    2026-07-29: KLAC is amc and printed same-day, but the old unconditional
    `0 < days_to` excluded days_to == 0 regardless of session, so a same-day amc
    name lost its very last legitimate prep window even with a perfectly healthy
    scheduler — this was never actually about the daemon-downtime misfire issue.
    """
    actions = ["monitor"]
    if earnings_date:
        days_to = (earnings_date - today).days
        min_days = 1 if hour == "bmo" else 0
        if min_days <= days_to <= sched_cfg.get("prep_days_before", 3):
            actions.append("prep")
    return actions


# --------------------------------------------------------------------------- #
# Score windows: one before the open (bmo prints), one after the close (amc)
# --------------------------------------------------------------------------- #
def _score_plan(window: str, today: date, print_, state: str,
                sched_cfg: dict) -> tuple[str, str]:
    """What should `window` do about this print today? Pure — no I/O.

    Returns (action, why) where action is:
      "score"   — run the scoring pipeline
      "promote" — the transcript never came; make the existing v1 the final answer
                  (bookkeeping only, no LLM) so the Chief can finally act on it
      ""        — nothing

    `print_` is an EarningsPrint (or None); `state` is store.score_state(...).
    A print whose session is UNKNOWN is attempted in BOTH windows — measured,
    Finnhub's `hour` is blank for 5 of 13 targets, so guessing would mis-time them.
    Double attempts are safe: `state` short-circuits once a score exists.
    """
    if print_ is None:
        return ("", "无近期财报")
    age = (today - print_.date).days
    if age < 0:
        return ("", f"财报在未来（{print_.date}）")
    if state == "final":
        return ("", "已有终版打分")

    if state == "v1_no_transcript":
        # Retry for the transcript, then give up and let the v1 stand.
        if age > sched_cfg.get("transcript_upgrade_days", 4):
            return ("promote", f"纪要 {age} 天未到，v1 提升为终版（可行动）")
    elif age > sched_cfg.get("score_lookback_days", 4):
        return ("", f"财报已过 {age} 天，超出补打窗口")

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
        return ("", f"session={session} 不匹配 {window} 窗口")

    verb = "升级 v2" if state == "v1_no_transcript" else ("补打" if age else "首打")
    return ("score", f"{verb}（{print_.date} {session}/{print_.session_source}, state={state}）")


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

    # Staged rollout: the resident daemon runs `ats schedule --live`, so without this
    # the windows would place real orders the moment they are registered. They stay
    # dry until schedule.score_windows_live is turned on explicitly — the PEAD decide
    # step still emits order_type="market", and a market order raised at 20:00 ET
    # queues into the next open, which on a post-earnings gap is the worst possible
    # fill. Flip this on only once overnight orders are priced as limits.
    if not dry_run and not sched.get("score_windows_live", False):
        log.info("score window %s: forcing dry-run (schedule.score_windows_live=false)", window)
        dry_run = True

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
            action, why = _score_plan(window, today, pr, state, sched)
            if action == "score":
                # Confirm even under --plan-only: it is read-only and cheap, and a
                # preview that skipped it would not reflect what the real run does.
                ok, ev = _confirm_reported(sym, pr)
                if not ok:
                    action, why = "", f"待确认发布：{ev}"
                else:
                    why = f"{why} · {ev}"
            log.info("PEAD-score[%s] %s: %s -> %s", window, sym, action or "skip", why)
            outcomes[sym] = (f"{action.upper()} · " if action else "") + why
            if not action or plan_only:
                continue

            if action == "promote":
                if store.promote_score_run(sym, label):
                    scored.append(sym)      # now actionable -> let the Chief see it
                continue

            # Pin the resolved label so the Chief reads the same key tomorrow.
            period.resolve_and_cache(sym, pr, config_label=cfg.fiscal_label, store=store)
            run_pead(sym, "score", dry_run=dry_run, use_llm=use_llm, chief=False)
            # Stamp which window scored it and how far behind the print we were, so the
            # cost of fixed windows is measurable rather than assumed.
            latency = round((now - pr.at).total_seconds() / 3600, 2) if pr.at else None
            store.stamp_score_run(sym, label, window=window, latency_hours=latency)
            # Only a FINAL score reaches the Chief. A transcript-less v1 is withheld
            # while we retry, so the Chief responds once, to the best evidence —
            # this is what keeps score_consumption's "act once per earnings" intact
            # without needing to un-consume anything.
            if store.is_score_final(sym, label):
                scored.append(sym)
            else:
                log.info("PEAD-score[%s] %s: v1 缺纪要，暂不惊动 Chief（等升级或到期提升）",
                         window, sym)
                outcomes[sym] += " · v1 缺纪要，暂不交给 Chief"
        except Exception as exc:  # noqa: BLE001 - one target must not break the window
            log.warning("PEAD-score[%s] %s failed: %s", window, sym, exc)
            outcomes[sym] = f"失败：{exc}"

    # Evidence-only tier. Deliberately a SEPARATE loop over `observe`, never merged
    # into the targets loop above: these names must not enter `scored`, must not
    # trigger a Chief cycle, and must never reach the order path. They only leave
    # chain observations behind (docs/CHAIN_EVIDENCE.md).
    if not plan_only:
        # Evidence covers EVERY name a claim can call as a witness — targets included.
        # Restricting it to `observe` left the two most important witnesses uncollected:
        # SKHY (the subject we hold) and NVDA (the customer whose testimony is the only
        # cross-stance evidence about SK's share). Both are targets, so the share claim
        # could only ever see competitor readings — which gate 3 correctly refuses to
        # act on — leaving it permanently unknown.
        _observe_window(window, today, sched, outcomes,
                        list(g.get("targets", [])) + list(g.get("observe", [])))

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


def _observe_window(window: str, today, sched: dict, outcomes: dict, names) -> None:
    """Read the earnings of evidence-only names into chain observations.

    Same double confirmation as scoring (`last_print` has an actual EPS AND an 8-K
    filed on/after the print date) — without it we would read last quarter's numbers
    as if they were new, which is exactly how a stale figure becomes false
    corroboration for a live claim.

    Runs over targets AND observe names: being tradable does not make a company's
    filing useless as evidence about the chain. The difference stays where it belongs —
    targets are additionally scored and may reach the order path; this function never
    does either.

    Hard boundary: no scoring, no dossier, no Chief, no orders. This function may
    only write to the evidence tables.
    """
    from ..agents.evidence import observer
    from ..data import earnings_calendar
    from ..memory import get_store

    if not names:
        return
    store = get_store()
    for sym in dict.fromkeys(str(s).upper() for s in names):     # dedupe, keep order
        try:
            pr = earnings_calendar.last_print(
                sym, as_of=today, back_days=sched.get("score_lookback_days", 4) + 3)
            if pr is None or not pr.date:
                continue
            ok, ev = _confirm_reported(sym, pr)
            if not ok:
                log.info("observe[%s] %s: 待确认发布：%s", window, sym, ev)
                continue
            doc_id = f"{sym}:{pr.date:%Y%m%d}"
            if store.has_observations_for_document(doc_id):
                # Already extracted. Both windows attempt an unknown-session print and
                # the lookback spans several days, so without this the same filing
                # would be re-fetched and re-read every attempt.
                continue
            # Period-guarded fetch (see observer.fetch_document): the fiscal label must
            # reach the search, and the result must be verified, or we extract from
            # another company's or another quarter's filing.
            text, src, note = observer.fetch_document(sym, print_=pr, store=store)
            if note:
                log.info("observe[%s] %s: %s", window, sym, note)
            from ..data import document_assets

            asset = document_assets.identify(text, entity=sym, store=store)
            if asset and store.has_observations_for_document(asset["document_id"]):
                continue
            res = observer.observe_document(sym, doc_id, text, source_url=src,
                                            period=str(pr.date))
            outcomes[f"{sym} (observe)"] = (
                f"证据 {res['new']} 新 / {res['saved']} 条" if not res["failure"]
                else f"证据抽取失败：{res['failure']}")
            log.info("observe[%s] %s: %s", window, sym, outcomes[f"{sym} (observe)"])
        except Exception as exc:  # noqa: BLE001 - one name must not break the window
            log.warning("observe[%s] %s failed: %s", window, sym, exc)


def _technical_daily() -> None:
    """Deterministic technical readings, before the Chief reads them.

    Cheap (one batched price download, no LLM), so it runs every session even
    though the strategy's own deadband means most days imply no action.
    """
    from .cli import run_technical_review

    try:
        run_technical_review()
    except Exception as exc:  # noqa: BLE001 - an analyst must not break the cycle
        log.warning("technical review failed: %s", exc)


def _daily(*, dry_run: bool) -> None:
    # Macro/sector weekly review moved to its own Saturday job (see _weekly_review /
    # the "weekly_review" cron job) — they don't touch the broker or need a trading
    # session, so tying them to the mon-fri trading-day cascade was never necessary,
    # and running them over the weekend keeps Monday's cascade free of the extra load.
    _event_triggers()    # FOMC/CPI/行业会议 -> extra analyst runs, cascade into today
    pead_daily(dry_run=dry_run)
    _technical_daily()
    _intel_digest()      # surface today's intel: Obsidian .md + Feishu card
    _perf_snapshot()
    _perf_risk_digest()  # surface perf + 6-layer risk: Obsidian .md + Feishu card
    _journal_marks()     # score elapsed prediction horizons + rewrite the ledger
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
            state = review.directive.state if review.directive else review.risk_state
            log.info("risk snapshot: state=%s breaches=%d", state, len(review.breaches))
            if state not in ("NORMAL", "normal"):
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
        state = review.directive.state if review.directive else review.risk_state
        get_channel("feishu").push(Notification(
            kind="error" if state in ("DATA_INVALID", "EMERGENCY", "derisk") else "info",
            title=f"风控告警 — {state} ({len(review.breaches)} 破限)",
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


def _journal_marks() -> None:
    """Score elapsed prediction horizons and regenerate the ledger.

    Price-only (yfinance), no broker and no LLM — safe to run every session.
    """
    if not get_config().app.journal.enabled:
        return
    try:
        from ..journal import predictions

        log.info("journal predictions: %s", predictions.score_open_predictions())
    except Exception as exc:  # noqa: BLE001 - the journal observes, never blocks
        log.warning("journal prediction scoring failed: %s", exc)
    try:
        from ..journal import report as journal_report

        p = journal_report.write_ledger()
        if p:
            log.info("journal ledger -> %s", p)
    except Exception as exc:  # noqa: BLE001
        log.warning("journal ledger failed: %s", exc)


def _journal_reconcile() -> None:
    """Backfill today's execution outcomes onto the trade record."""
    if not is_trading_session():
        return
    try:
        from ..trader import reconcile

        s = reconcile.reconcile()
        log.info("journal reconcile: %s", {k: v for k, v in s.items() if k != "errors"})
        for e in s.get("errors", []):
            log.warning("journal reconcile: %s", e)
    except Exception as exc:  # noqa: BLE001 - must never break the daemon
        log.warning("journal reconcile failed: %s", exc)


def _macro_weekly() -> None:
    """Weekly macro strategist review (Saturdays by default). Runs BEFORE the sector
    review so this week's macro regime feeds it (cascade 宏观→行业→个股)."""
    from ..config import load_pead_global
    from .cli import run_macro_review

    mr = load_pead_global()["macro_review"]
    if not mr["enabled"] or _today_weekly().weekday() != mr["weekday"]:
        return
    try:
        run_macro_review(mr["name"])
    except Exception as exc:  # noqa: BLE001 - review must not break the weekly job
        log.warning("macro review failed: %s", exc)


def _sector_weekly() -> None:
    """Weekly sector review (Saturdays by default). Runs the weekend after the week's
    monitors land, so Monday's cascade opens with the freshest injection already in
    place; run `ats sector review` manually if it matters sooner."""
    from ..config import load_pead_global
    from .cli import run_sector_review

    sr = load_pead_global()["sector_review"]
    if not sr["enabled"] or _today_weekly().weekday() != sr["weekday"]:
        return
    for name in sr["sectors"]:
        try:
            run_sector_review(name)
        except Exception as exc:  # noqa: BLE001 - review must not break the weekly job
            log.warning("sector review %s failed: %s", name, exc)
        # Cross-section right after the review, on the same fresh data. Without this it
        # only ever ran when someone typed `ats sector crosssection` by hand, so the
        # deterministic who/how-much layer effectively never updated.
        if sr.get("cross_section", True):
            _cross_section_weekly(name)


def _cross_section_weekly(name: str) -> None:
    """Rank every layer that declares a cohort, with the structure overlay on.

    Structure is on by default here: it is what carries the chain-evidence into
    `moat_pricing`. Failures are per-layer — one layer's data outage must not cost
    the others their ranking.
    """
    from ..agents.sector import cross_section
    from ..config import load_sector_config
    from ..memory import get_store

    try:
        cfg = load_sector_config(name)
    except Exception as exc:  # noqa: BLE001
        log.warning("cross-section skipped for %s: %s", name, exc)
        return
    review = get_store().latest_sector_review(name)
    for layer in cfg.layers:
        if not layer.tickers:
            continue
        # Skip what the review, minutes earlier in this same job, called bearish. Sizing
        # names inside a layer we just recommended cutting is work whose output nobody
        # should act on. Uses the existing `signal` field rather than inventing a
        # threshold; layers with no assessment still run.
        assessment = review.layer_assessment(layer.key) if review else None
        if assessment is not None and assessment.signal == "bearish":
            log.info("cross-section %s/%s skipped — review called it bearish", name, layer.key)
            continue
        try:
            cross_section.run_layer(name, layer.key, persist=True, structure=True)
        except Exception as exc:  # noqa: BLE001 - one layer must not break the rest
            log.warning("cross-section %s/%s failed: %s", name, layer.key, exc)
    # One Obsidian report per week, after every layer has been ranked, so the claim
    # verdicts and the basket it produced describe the same run.
    # Refresh third-party series BEFORE the report renders, or the report shows last
    # month's customs print next to this week's filings. `collect()` had no production
    # caller at all — the configured sources only ever ran when someone invoked it from
    # a REPL, so they were silently frozen while looking configured.
    # Articles first, series second: both land in the ledger the report then reads, and
    # article ingestion is the slower, more failure-prone half (one model call per piece
    # against a site whose template can move), so it gets its own guard.
    try:
        from ..chain import articles as chain_articles
        from ..data import research as research_data

        # One acquisition stage feeds both PEAD and chain consumers. The adapter for
        # subscribed research reads the shared catalog and never reconnects to IMAP.
        research_data.ingest_configured(store=get_store())

        for sid, stat in chain_articles.collect_articles(get_store()).items():
            if stat.unreachable:
                log.warning("article source %s: unreachable this round (recorded as a gap)", sid)
            else:
                log.info("article source %s: scanned %d, matched %d, ingested %d "
                         "(%d new observations, %d unreadable)", sid, stat.scanned,
                         stat.matched, stat.ingested, stat.observations, stat.unreadable)
    except Exception as exc:  # noqa: BLE001 - a publisher outage must not break the job
        log.warning("article source collection failed: %s", exc)

    try:
        from ..chain import sources as chain_sources

        saved = chain_sources.collect(get_store())
        # -1 per id = could not reach it this round (a gap); 0 = reached, nothing new.
        log.info("third-party sources: %s", saved or "(none configured)")
    except Exception as exc:  # noqa: BLE001 - a source outage must not break the job
        log.warning("third-party source collection failed: %s", exc)

    try:
        from ..chain import report as chain_report
        from ..config import load_pead_global

        path = chain_report.write(cfg, get_store(), as_of=_now_et(),
                                  ind_cfg=load_pead_global().get("induction", {}))
        log.info("chain evidence report: %s", path or "(output_dir unset — skipped)")
    except Exception as exc:  # noqa: BLE001 - the report must not break the job
        log.warning("chain evidence report failed for %s: %s", name, exc)


def _weekly_review() -> None:
    """Saturday-only job: macro then sector review, decoupled from the mon-fri
    trading-day cascade (see `_daily`'s comment for why). Neither node places trades,
    so there's no dry_run to thread through here."""
    _macro_weekly()
    _sector_weekly()


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
        if event.code == EVENT_JOB_MISSED:
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


def start(*, dry_run: bool = True, run_once: bool = False, window: str | None = None) -> None:
    from ..config import load_pead_global

    cfg = get_config().app.schedule
    if window:
        pead_score_window(window, dry_run=dry_run)
        return
    if run_once:
        _daily(dry_run=dry_run)
        return

    from apscheduler.executors.pool import ThreadPoolExecutor
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    hour, minute = (int(x) for x in cfg.run_at.split(":"))
    # This host SLEEPS. Measured 2026-07-27: the daemon had been alive 2.5 days with
    # 0.45s of CPU — every job had been skipped, because the Mac was asleep at the
    # trigger and the old 1-hour grace expired before it woke. The 20:00 ET after-close
    # window is the worst case: it lands at 09:00 local, i.e. squarely in the night.
    # A late score is still useful — 20:00 ET + 6h is 02:00 ET, still ~7h before the
    # next open — so the grace is wide enough to survive an overnight sleep. Beyond
    # that the T+1 catch-up path in _score_plan takes over.
    grace = int(cfg.misfire_grace_hours * 3600)
    # One worker on purpose: these jobs are long sequential LLM pipelines that all
    # write the same sqlite connection, so letting two run concurrently would risk
    # interleaved writes for no gain. A job that comes due while another is running
    # waits, and misfire_grace_time decides whether it still fires.
    scheduler = BlockingScheduler(timezone=cfg.timezone,
                                  executors={"default": ThreadPoolExecutor(1)})
    _attach_job_logging(scheduler)
    scheduler.add_job(
        lambda: _daily(dry_run=dry_run),
        CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=cfg.timezone),
        id="daily_cycle", misfire_grace_time=grace,
    )
    # Macro/sector weekly review: own job, own time/timezone (schedule.weekly_review_at/
    # _tz) — it never trades and doesn't need a NYSE session, so there's no reason to
    # tie it to the daily cascade's ET clock. See _today_weekly() for why the day-gate
    # inside _macro_weekly/_sector_weekly must track this same timezone, not ET.
    w_hour, w_minute = (int(x) for x in cfg.weekly_review_at.split(":"))
    scheduler.add_job(
        lambda: _weekly_review(),
        CronTrigger(day_of_week="sat", hour=w_hour, minute=w_minute,
                    timezone=cfg.weekly_review_tz),
        id="weekly_review", misfire_grace_time=grace,
    )

    # PEAD score windows — triggered by an observed print, so their job is to check
    # cheaply and usually do nothing. See config/pead.yaml schedule.score_windows.
    windows = load_pead_global().get("schedule", {}).get("score_windows", {}) or {}
    for name, hhmm in sorted(windows.items()):
        try:
            w_hour, w_minute = (int(x) for x in str(hhmm).split(":"))
        except ValueError:
            log.warning("bad score_windows[%s]=%r; skipping", name, hhmm)
            continue
        scheduler.add_job(
            # `name=name` binds the loop variable per job — a bare closure would give
            # every job the last window's name.
            lambda name=name: pead_score_window(name, dry_run=dry_run),
            CronTrigger(day_of_week="mon-fri", hour=w_hour, minute=w_minute,
                        timezone=cfg.timezone),
            id=f"pead_score_{name}", misfire_grace_time=grace,
        )

    # Post-close reconciliation. Its own job on purpose: reqExecutions only returns
    # the CURRENT day's executions, so a session it misses is lost for good — it must
    # not be able to fail just because an LLM step earlier in the cascade did.
    jcfg = get_config().app.journal
    if jcfg.enabled:
        r_hour, r_minute = (int(x) for x in jcfg.reconcile_at.split(":"))
        scheduler.add_job(
            lambda: _journal_reconcile(),
            CronTrigger(day_of_week="mon-fri", hour=r_hour, minute=r_minute,
                        timezone=cfg.timezone),
            # Same grace as the PEAD jobs, not a separate hardcoded value: a missed
            # reconcile is unrecoverable (see comment above), so it needs the wide
            # window at least as much as the score windows do — this predates the
            # misfire_grace_hours config and was never updated when that landed.
            id="journal_reconcile", misfire_grace_time=grace,
        )

    win_desc = ", ".join(f"{n}@{h}" for n, h in sorted(windows.items())) or "none"
    log.info("scheduler started: daily %s %s; PEAD score windows %s "
             "(mon-fri, NYSE sessions only)", cfg.run_at, cfg.timezone, win_desc)
    print(f"⏰ daily cycle at {cfg.run_at} {cfg.timezone}; PEAD score windows: {win_desc} "
          f"(NYSE sessions{'' if not dry_run else ', dry-run'}). Ctrl-C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler stopped")
