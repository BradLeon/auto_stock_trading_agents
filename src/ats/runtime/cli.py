"""CLI runner: drive the chief decision graph, pausing at the Boss-approval interrupt.

    ats chief run           # dry-run, interactive Boss prompt
    ats trader buy NVDA 5   # manual order — same graph, same risk gate + approval
    ats pead score COHR --chief   # earnings-event trade, chief收口 immediately

The graph is transport-agnostic: it interrupts, this runner asks the configured
BossChannel for a verdict, then resumes with Command(resume=...). Async channels
(Feishu) checkpoint at the interrupt and `ats serve` resumes on callback.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from langgraph.types import Command

from ..channel import get_channel
from ..config import get_config
from ..graph.checkpoint import get_checkpointer
from ..schemas.channel import ApprovalRequest, Notification


def run_decision_graph(state, *, channel="cli") -> dict:
    """Run the chief decision graph through its approval interrupt.

    Sync channels (CLI) loop interrupt->verdict->resume in-process. Async
    channels (Feishu) send the card and return — the checkpointed thread is
    resumed later by `ats serve` via resume_cycle(). `channel` is a kind
    string or an already-built BossChannel.
    """
    from ..channel import get_channel as _get_channel   # late bind (tests patch it)
    from ..graph.chief import build_chief_graph

    ch = _get_channel(channel) if isinstance(channel, str) else channel
    is_async = getattr(ch, "is_async", False)
    # Async channels must survive across processes -> persistent checkpointer.
    app = build_chief_graph(checkpointer=get_checkpointer(persist=is_async))
    cfg_run = {"configurable": {"thread_id": state.cycle_id}}
    print(f"▶ {state.cycle_id} (source={state.source}, dry_run={state.dry_run})")

    result = app.invoke(state, config=cfg_run)
    if "__interrupt__" not in result:
        return result

    if is_async:
        req = ApprovalRequest.model_validate(result["__interrupt__"][0].value)
        ch.send_approval_request(req, thread_id=state.cycle_id)
        print(f"⏸ {state.cycle_id} awaiting Boss approval via "
              f"{getattr(ch, 'kind', 'async channel')}. Run `ats serve` to handle the callback.")
        return result

    # Sync (CLI): drive interrupts to completion in-process.
    while "__interrupt__" in result:
        req = ApprovalRequest.model_validate(result["__interrupt__"][0].value)
        if hasattr(ch, "push"):
            ch.push(Notification(kind="approval_request", title="Decisions pending review",
                                 body=f"{len(req.decisions)} proposed trade(s)"))
        approval = ch.request_approval(req)
        result = app.invoke(Command(resume=approval.model_dump(mode="json")), config=cfg_run)
    return result


def run_pead(symbol: str, phase: str, *, dry_run: bool = True, auto: bool = False,
             offline: bool = False, use_llm: bool = True, transcript: str | None = None,
             channel: str = "cli", chief: bool = False) -> dict:
    """Run one PEAD phase (prep | score). v0.2: score produces a RECOMMENDATION
    persisted in the dossier (no interrupt) — the Chief makes the trade call.
    Pass chief=True to run the Chief immediately after a score completes."""
    from ..graph.pead import build_pead_graph
    from ..graph.pead_state import PeadState

    sym = symbol.upper()
    app = build_pead_graph(checkpointer=get_checkpointer(persist=False))
    now = datetime.now(timezone.utc)
    state = PeadState(symbol=sym, phase=phase, as_of=now, dry_run=dry_run, use_llm=use_llm,
                      use_broker=not offline, live_data=not offline, transcript_source=transcript)
    cfg_run = {"configurable": {"thread_id": f"pead-{sym}-{phase}-{now:%Y%m%d%H%M%S}"}}
    print(f"▶ PEAD {phase} {sym}")

    result = app.invoke(state, config=cfg_run)
    _pead_report(sym, phase, result)
    if phase == "score":
        if chief:
            run_chief(dry_run=dry_run, channel=channel, auto=auto, offline=offline,
                      source="pead-chief")
        else:
            print("→ 建议已入档；运行 `ats chief run` 收口交易决策")
    return result


def _pead_report(symbol: str, phase: str, result: dict) -> None:
    print("\n" + "=" * 70)
    if phase == "prep":
        es = result.get("expectation_set")
        ms = result.get("market_setup")
        print(f"PEAD PREP COMPLETE — {symbol}")
        if es:
            print(f"Narrative: {es.narrative[:240]}")
            if es.focus_ranking:
                print("Focus: " + " > ".join(es.focus_ranking[:5]))
            print(f"Expectations rows: {len(es.expectations)}  | consensus EPS={es.consensus_eps} "
                  f"Rev={es.consensus_revenue}")
        if ms:
            print(f"Setup: run-up vs sector {ms.run_up_vs_sector_pct}% · EM {ms.expected_move_pct}% "
                  f"· ATM IV {ms.atm_iv}% · dist-to-high {ms.dist_to_ath_pct}%")
        print(f"Signal chain: {len(result.get('signal_chain', []))} names")
    else:
        sc = result.get("scorecard")
        recs = result.get("decisions", [])
        if sc:
            print(f"PEAD SCORE COMPLETE — {symbol}  Scorecard {sc.total:+.2f} "
                  f"(门槛 {sc.threshold:+.1f}) — {sc.band}")
        print(f"决策情景: {result.get('decision_band', '—')} · 建议 {len(recs)} 条")
        for d in recs:
            size = f"${d.notional_usd:,.0f}" if d.notional_usd else (f"{d.qty:.0f}股" if d.qty else "")
            print(f"  • 建议 {d.action} {d.symbol} {size}")
    print("=" * 70)


def run_pead_monitor(symbol: str, *, use_llm: bool = True) -> dict:
    """Run one continuous-monitor pass: ingest events, update the living dossier."""
    from ..agents.pead import monitor
    from ..config import load_pead_global

    g = load_pead_global()
    update = monitor.run(symbol.upper(), use_llm=use_llm,
                         lookback_days=g["monitor"]["lookback_days"])
    print(f"📡 monitor {symbol.upper()} — materiality {update.materiality:.2f} · "
          f"{update.event_summary}")
    if update.narrative_delta:
        print(f"   Δ thesis: {update.narrative_delta}")
    for ec in update.expectation_changes:
        print(f"   Δ {ec.dim_key}: {ec.change}")

    mon = g["monitor"]
    if (mon.get("push_context_updates") and update.materiality >= mon["materiality_threshold"]):
        try:
            get_channel("feishu").push(Notification(
                kind="info", title=f"PEAD context update — {symbol.upper()} "
                f"(materiality {update.materiality:.2f})",
                body=update.event_summary + ("\nΔ " + update.narrative_delta
                                             if update.narrative_delta else "")))
            print("   → pushed Feishu info card")
        except Exception as exc:  # noqa: BLE001 - push is best-effort
            print(f"   (Feishu push skipped: {exc})")
    return {"update": update}


def run_pead_watch(*, use_llm: bool = True) -> None:
    from ..config import load_pead_global

    for sym in load_pead_global().get("targets", []):
        run_pead_monitor(sym, use_llm=use_llm)


def events_list(*, days: int | None = None) -> int:
    from datetime import date, timedelta

    from ..config import load_events

    events = load_events()
    if days is not None:
        today = date.today()
        events = [e for e in events if today <= e.date <= today + timedelta(days=days)]
        if not events:
            print(f"(未来 {days} 天无日历事件 — 检查 config/events.yaml 是否需要补充下季度日期)")
            return 0
    if not events:
        print("(config/events.yaml 为空)")
        return 0
    for e in sorted(events, key=lambda e: e.date):
        print(f"  {e.date} [{e.kind:13}] {e.label} -> {', '.join(e.triggers)}")
    if days is None and all(e.date < date.today() for e in events):
        print("⚠️ 日历中全部事件已过期 — 请补充下季度 FOMC/BLS 日期")
    return 0


def run_chief(*, execute: bool = True, dry_run: bool = True, channel: str = "cli",
              use_llm: bool = True, auto: bool = False, offline: bool = False,
              source: str = "chief") -> int:
    """One Chief decision run through the decision graph: assemble all artifacts
    -> decide -> risk gate -> persist -> Boss approval -> trade -> persist."""
    from ..graph.chief_state import ChiefDecisionState

    now = datetime.now(timezone.utc)
    state = ChiefDecisionState(cycle_id=f"chief-{now:%Y%m%d-%H%M%S}", as_of=now,
                               source=source, dry_run=dry_run, use_llm=use_llm,
                               use_broker=not offline, auto_approve=auto, execute=execute)
    run_decision_graph(state, channel=channel)
    return 0


def chief_show() -> int:
    from ..memory import get_store

    run = get_store().last_chief_run()
    if run is None:
        print("(no chief run yet — `ats chief run`)")
        return 0
    print(f"=== chief {run['cycle_id']} @ {run['as_of'][:16]} ===\n{run['manager_summary']}")
    for d in run["decisions"]:
        print(f"  {d['action']} {d['symbol']} ${d.get('notional_usd') or 0:,.0f} — "
              f"{(d.get('rationale') or '')[:70]}")
    return 0


def chief_probe(*, offline: bool = False) -> int:
    from ..agents.chief import assemble

    ctx = assemble.build(live_broker=not offline)
    print(f"=== chief context stats: {ctx.stats()} ===\n")
    print(ctx.as_context())
    return 0


def risk_report(*, write_report: bool = False, offline: bool = False) -> int:
    from ..memory import get_store
    from ..risk import assess as risk_assess, report as risk_report_mod
    from ..trader import portfolio as tport

    if offline:
        stored = get_store().latest_risk_review()
        if stored is None:
            print("❌ No stored risk review found — run once with TWS connected.")
            return 1
        age = (stored.as_of.replace(tzinfo=None) if stored.as_of.tzinfo
               else stored.as_of)
        from datetime import datetime, timezone
        age_days = (datetime.now(timezone.utc) - stored.as_of).days
        print(f"⚠️  offline mode — showing stored review ({age_days}d old, as of {stored.as_of:%Y-%m-%d})")
        print(risk_report_mod.render(stored))
        return 0

    pf = tport.snapshot()
    if pf is None:
        from ..config import get_config
        _port = get_config().app.broker.port or get_config().secrets.ibkr_port
        print(f"❌ IBKR unavailable — start TWS with API enabled (port {_port}). Use --offline to show stored review.")
        return 1
    risk_assess.enrich_beta(pf)
    risk_assess.enrich_options(pf)
    review = risk_assess.assess(pf)
    get_store().save_risk_review(review)
    print(risk_report_mod.render(review))
    if write_report:
        from ..config import load_macro_config
        try:
            out_dir = load_macro_config().output_dir
        except Exception:  # noqa: BLE001
            out_dir = ""
        path = risk_report_mod.write(review, out_dir)
        print(f"📝 {path}" if path else "(report dir unset — skipped)")
    return 0


def risk_memo() -> int:
    """Risk-officer narrative memo: deterministic 6-layer assess -> LLM memo -> Obsidian doc.
    Read-only: snapshots the portfolio, never submits orders."""
    from ..agents.risk_officer import report as memo_report, review as memo_review

    memo = memo_review.run()
    if memo is None:
        from ..config import get_config
        _port = get_config().app.broker.port or get_config().secrets.ibkr_port
        print(f"❌ IBKR unavailable — start TWS with API enabled (port {_port}).")
        return 1
    print(memo_report.render(memo))
    from ..config import load_macro_config
    try:
        out_dir = load_macro_config().output_dir
    except Exception:  # noqa: BLE001
        out_dir = ""
    path = memo_report.write(memo, out_dir)
    print(f"\n📝 {path}" if path else "\n(report dir unset — skipped)")
    return 0


def risk_check(symbol: str | None = None) -> int:
    """Dry-run the risk gate over stored decisions (shows block/clip without ordering)."""
    from ..memory import get_store
    from ..risk import checks as risk_checks
    from ..schemas.decision import TradeDecision
    from ..trader import portfolio as tport

    rows = get_store().recent_decisions(symbol, limit=20)
    if not rows:
        print("(no stored decisions to check)")
        return 0
    seen, decisions = set(), []
    for r in rows:
        if r["symbol"] in seen:
            continue
        seen.add(r["symbol"])
        decisions.append(TradeDecision(symbol=r["symbol"], action=r["action"],
                                       notional_usd=r.get("notional_usd"),
                                       limit_price=r.get("limit_price"),
                                       rationale=r.get("rationale") or ""))
    pf = tport.snapshot()
    approved, notes, _ = risk_checks.pre_trade(decisions, pf)
    print(f"=== Risk check: {len(decisions)} decisions → {len(approved)} pass ===")
    for n in notes:
        print(f"  {n}")
    return 0


def trader_portfolio(*, offline: bool = False) -> int:
    from ..trader import portfolio as tp

    pf = tp.snapshot()
    if pf is None:
        if offline:
            # Fall back to most recent stored performance snapshot for display
            from ..memory import get_store
            from datetime import datetime, timezone
            ph = get_store().performance_history(limit=1)
            if ph:
                r = ph[0]
                age_days = (datetime.now(timezone.utc) - r.as_of).days
                print(f"⚠️  offline mode — showing stored snapshot ({age_days}d old, as of {r.as_of:%Y-%m-%d})")
                print(f"=== Portfolio {r.account_id} @ {r.as_of:%Y-%m-%d} (stored) ===")
                print(f"NetLiq ${r.net_liquidation:,.0f} · dailyP&L ${r.daily_pnl:,.0f} "
                      f"· cumP&L ${r.cumulative_pnl:,.0f} · positions {r.num_positions}")
                return 0
            print("❌ No stored snapshot found — connect TWS and run `ats trader snapshot`.")
            return 1
        from ..config import get_config
        _port = get_config().app.broker.port or get_config().secrets.ibkr_port
        print(f"❌ IBKR unavailable — start TWS/Gateway with API enabled (port {_port}). Use --offline for stored data.")
        return 1
    print(f"=== Portfolio {pf.account_id} @ {pf.as_of:%Y-%m-%d %H:%M} ===")
    print(f"NetLiq ${pf.net_liquidation:,.0f} · cash ${pf.cash:,.0f} · leverage {pf.leverage:.2f}x "
          f"· dailyP&L ${pf.daily_pnl:,.0f} · realized ${pf.realized_pnl:,.0f}")
    if not pf.positions:
        print("(no open positions)")
    for p in pf.positions:
        print(f"  {p.symbol:6} {p.qty:+.0f} @ {p.avg_cost:.2f}  mv=${p.market_value:,.0f} "
              f"w={p.weight*100:.1f}% uPnL=${p.unrealized_pnl:,.0f}")
    return 0


def trader_snapshot() -> int:
    from ..trader import performance as tperf

    r = tperf.record_snapshot()
    if r is None:
        print("❌ IBKR unavailable — snapshot skipped.")
        return 1
    print(f"📸 snapshot {r.as_of:%Y-%m-%d} · NetLiq ${r.net_liquidation:,.0f} · "
          f"dayP&L ${r.daily_pnl:,.0f} · cumP&L ${r.cumulative_pnl:,.0f} · positions {r.num_positions}")
    return 0


def trader_perf(days: int = 30, *, write_report: bool = False) -> int:
    from ..trader import performance as tperf

    rep = tperf.report(days)
    a = rep["analytics"]
    print(f"=== Performance (last {a['window_days']} snapshots) ===")
    print(f"NetLiq ${a['start_nav'] or 0:,.0f} → ${a['end_nav'] or 0:,.0f} · "
          f"return {a['total_return_pct']}% · cumP&L ${a['cumulative_pnl'] or 0:,.0f}")
    print(f"maxDD {a['max_drawdown_pct']}% · winRate {a['win_rate']} · "
          f"profitFactor {a['profit_factor']} · closedTrades {a['closed_trades']}")
    for name, b in a["benchmarks"].items():
        print(f"  vs {name}: {b['return_pct']}% (alpha {b['alpha_pct']}%)")
    if write_report:
        _write_perf_report(rep)
    return 0


def _write_perf_report(rep: dict) -> None:
    from ..config import load_macro_config

    try:
        out_dir = load_macro_config().output_dir
    except Exception:  # noqa: BLE001
        out_dir = ""
    if not out_dir:
        print("(report dir unset — skipped)")
        return
    from datetime import datetime, timezone
    from pathlib import Path

    a = rep["analytics"]
    lines = [f"# 🤖 组合绩效 — {datetime.now(timezone.utc):%Y-%m-%d}", "",
             f"- NetLiq: ${a['start_nav'] or 0:,.0f} → ${a['end_nav'] or 0:,.0f}",
             f"- 收益率: {a['total_return_pct']}% · 累计P&L: ${a['cumulative_pnl'] or 0:,.0f}",
             f"- 最大回撤: {a['max_drawdown_pct']}%",
             f"- 胜率: {a['win_rate']} · 盈亏比: {a['profit_factor']} · 平仓交易: {a['closed_trades']}"]
    for name, b in a["benchmarks"].items():
        lines.append(f"- vs {name}: {b['return_pct']}% (alpha {b['alpha_pct']}%)")
    p = Path(out_dir) / f"组合绩效-{datetime.now(timezone.utc):%Y-%m-%d}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"📝 {p}")


def trader_orders() -> int:
    from ..broker import IBKRBroker, IBKRUnavailable

    try:
        oo = IBKRBroker().open_orders()
    except IBKRUnavailable as exc:
        print(f"❌ IBKR unavailable: {exc}")
        return 1
    if not oo:
        print("(no open orders)")
        return 0
    print("=== Open orders ===")
    for o in oo:
        print(f"  #{o['order_id']} {o['action']} {o['symbol']} x{o['qty']:.0f} {o['type']} [{o['status']}]")
    return 0


def trader_cancel(symbol: str | None = None) -> int:
    from ..broker import IBKRBroker, IBKRUnavailable

    try:
        cancelled = IBKRBroker().cancel_all(symbol)
    except IBKRUnavailable as exc:
        print(f"❌ IBKR unavailable: {exc}")
        return 1
    print(f"cancelled {len(cancelled)} order(s): {cancelled}" if cancelled else "(no open orders to cancel)")
    return 0


def trader_fills(symbol: str | None = None) -> int:
    from ..memory import get_store

    rows = get_store().recent_fills(symbol, limit=30)
    if not rows:
        print("(no fills recorded yet)")
        return 0
    print(f"=== Fills{' ' + symbol if symbol else ''} ===")
    for f in rows:
        rp = f"realized ${f['realized_pnl']:,.0f}" if f.get("realized_pnl") is not None else ""
        print(f"  {f['time'][:16]} {f['side']} {f['symbol']} {f['shares']:.0f}@{f['price']:.2f} {rp}")
    return 0


def trader_execute(symbol: str | None = None, *, channel: str = "cli", dry_run: bool = False) -> int:
    from ..memory import get_store
    from ..schemas.decision import TradeDecision
    from ..trader import execute as texec

    rows = get_store().recent_decisions(symbol, limit=20)
    if not rows:
        print("(no stored decisions to execute — use `ats trader buy/sell` for manual orders)")
        return 0
    seen, decisions = set(), []
    for r in rows:                     # newest first; one per symbol
        if r["symbol"] in seen:
            continue
        seen.add(r["symbol"])
        decisions.append(TradeDecision(
            symbol=r["symbol"], action=r["action"], notional_usd=r.get("notional_usd"),
            limit_price=r.get("limit_price"), conviction=r.get("conviction") or 0.0,
            rationale=r.get("rationale") or ""))
    texec.execute(decisions, source="stored-decisions", channel=channel, dry_run=dry_run)
    return 0


def trader_manual(action: str, symbol: str, qty: float, *, limit: float | None = None,
                  channel: str = "cli", dry_run: bool = False) -> int:
    from ..trader import execute as texec

    texec.manual(symbol, action, qty, order_type="limit" if limit else "market",
                 limit_price=limit, channel=channel, dry_run=dry_run)
    return 0


def _print_quadrant(review) -> None:
    """Show the deterministic layer — this is what `--no-llm` exists to verify."""
    if not review.axis_inputs and review.quadrant == "transition":
        return
    print(f"   {review.quadrant_line()}")
    if review.quadrant_reason:
        print(f"      理由: {review.quadrant_reason}")
    for a in review.axis_inputs:
        val = "n/a" if a.value is None else a.value
        print(f"      · {a.label or a.key}: {val} (判据 {a.threshold}) → {a.score:+.2f}")
    dec = review.decomposition
    if dec is not None and dec.d_real_bp is not None:
        print(f"      · 利率分解 {dec.window_days}d: Δ名义 {dec.d_nominal_bp:+.0f}bp"
              f" = Δ实际 {dec.d_real_bp:+.0f} + Δ通胀补偿 {dec.d_breakeven_bp:+.0f}"
              f" → {dec.classification}")
        if dec.real_yield_cause:
            print(f"      · 成因: {dec.real_yield_cause}")
    for s in review.shock_vs_trend:
        print(f"      · {s}")
    stale = [r.key for r in review.indicators if r.stale]
    if stale:
        print(f"      · ⚠️ 数据过旧/缺失: {', '.join(stale)}")


def run_technical_review(name: str = "technical", *, live_data: bool = True,
                        write_report: bool = True) -> int:
    """Deterministic technical readings (no LLM). Advisory input to the Chief."""
    from ..agents.technical import review as tech_review

    r = tech_review.run(name, live_data=live_data, write_report=write_report)
    print(f"📐 technical {name} — 策略 {r.strategy} · {r.summary_line()}")
    for note in r.notes:
        print(f"   ⚠️  {note}")
    live = [x for x in r.readings if not x.stale]
    for x in sorted(live, key=lambda v: (v.target_exposure, v.symbol)):
        mark = " ←变化" if x.changed else ""
        print(f"   {x.one_line()}{mark}")
    stale = [x.symbol for x in r.readings if x.stale]
    if stale:
        print(f"   （未评估: {', '.join(stale)}）")
    return 0


def technical_show(name: str = "technical") -> int:
    from ..memory import get_store

    store = get_store()
    latest = store.latest_technical_review(name)
    if latest is None:
        print(f"(no technical review for {name} yet — run `ats technical review`)")
        return 0
    print(f"=== technical {name} @ {latest.as_of:%Y-%m-%d} · 策略 {latest.strategy} ===")
    print(latest.chief_block(4000))
    print("\nHistory:")
    for row in store.recent_technical_reviews(name):
        print(f"  {row['as_of'][:10]}  {row['summary'][:80]}")
    return 0


def technical_probe(name: str = "technical", *, live_data: bool = True) -> int:
    """Resolve the universe and compute readings WITHOUT persisting or reporting."""
    from ..agents.technical import review as tech_review

    r = tech_review.run(name, live_data=live_data, persist=False, write_report=False)
    print(f"=== technical probe: {len(r.readings)} readings, "
          f"strategy={r.strategy}, fingerprint={r.fingerprint} ===")
    for note in r.notes:
        print(f"  note: {note}")
    print()
    print(r.chief_block(4000))
    return 0


def run_macro_review(name: str = "macro", *, use_llm: bool = True,
                     live_data: bool = True, write_report: bool = True):
    """One weekly macro strategist review: regime + rate path + sector tilts."""
    from ..agents.macro import report, review as macro_review
    from ..config import load_macro_config

    from datetime import datetime, timezone

    started = datetime.now(timezone.utc)
    review = macro_review.run(name, use_llm=use_llm, live_data=live_data)
    # run() returns the PRIOR stored review when the LLM call fails, so a failed
    # run is otherwise indistinguishable from a good one — and writing a report
    # for it would overwrite that older day's file under its own date.
    stale = use_llm and review.as_of < started
    if stale:
        print(f"⚠️  macro {name}: LLM 失败，以下为 {review.as_of:%Y-%m-%d %H:%M} 的旧评审"
              f"（未写报告、未更新存档）")
    print(f"🌐 macro {name} — {review.regime}")
    _print_quadrant(review)
    if review.rate_path:
        print(f"   利率路径: {review.rate_path}")
    for t in review.sector_tilts:
        print(f"   {t.stance} {t.sector}: {t.rationale[:80]}")
    if review.asset_implications:
        print(f"   资产含义: {review.asset_implications}")
    if write_report and use_llm and review.sector_tilts and not stale:
        path = report.write(review, load_macro_config(name))
        print(f"   📝 {path}" if path else "   (report dir unset — skipped)")
    return review


def macro_show(name: str = "macro") -> int:
    from ..memory import get_store

    store = get_store()
    latest = store.latest_macro_review(name)
    if latest is None:
        print(f"(no macro review for {name} yet — run `ats macro review`)")
        return 0
    print(f"=== macro review {name} @ {latest.as_of:%Y-%m-%d} ===")
    print(f"Regime: {latest.regime}")
    _print_quadrant(latest)
    if latest.falsifier:
        print(f"证伪条件: {latest.falsifier}")
    print(f"利率路径: {latest.rate_path}\n\n{latest.summary}\n")
    for t in latest.sector_tilts:
        print(f"  {t.stance} {t.sector}")
    print("\nHistory:")
    for r in store.recent_macro_reviews(name):
        print(f"  {r['as_of'][:10]}  {r['regime'][:70]}")
    return 0


def macro_probe(name: str = "macro", *, live_data: bool = True) -> int:
    from ..agents.macro import assemble
    from ..config import load_macro_config

    mc = assemble.build(load_macro_config(name), live_data=live_data)
    print(f"=== macro context stats: {mc.stats()} ===\n")
    print(mc.as_context())
    return 0


def run_cross_section(name: str = "ai_hardware", layer: str = "all",
                      *, structure: bool = False, write_report: bool = True) -> int:
    """Cross-sectional selection + sizing within a chain layer (WHO / HOW MUCH).
    --structure blends the KB-grounded structure analyst (tech_tenor/moat_pricing)."""
    from ..agents.sector import cross_section
    from ..config import load_sector_config

    cfg = load_sector_config(name)
    keys = [layer] if layer != "all" else [ly.key for ly in cfg.layers if ly.tickers]
    for key in keys:
        try:
            rows, basket = cross_section.run_layer(name, key, persist=True, structure=structure)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ {key}: {exc}")
            continue
        label = next((ly.label for ly in cfg.layers if ly.key == key), key)
        print(f"\n=== {label}  [{key}]{'  +结构层' if basket.structural else ''} ===")
        print(cross_section.format_table(rows, basket.layer_cap))
        if write_report:
            path = cross_section.write_report(rows, basket, cfg)
            if path:
                print(f"📝 {path}")
    return 0


def run_sector_review(name: str = "ai_hardware", *, use_llm: bool = True,
                      live_data: bool = True, write_report: bool = True):
    """One weekly sector review: L1-L6 assessment + company calls."""
    from ..agents.sector import report, review as sector_review
    from ..config import load_sector_config

    review = sector_review.run(name, use_llm=use_llm, live_data=live_data)
    print(f"🏭 sector {name} — {review.regime}")
    for a in review.layers:
        print(f"   {a.label}: 景气 {a.boom_score:.0f} [{a.signal}] {a.supply_demand}")
    if review.rotation_advice:
        print(f"   轮动: {review.rotation_advice}")
    for c in review.company_calls:
        print(f"   {c.stance} {c.symbol} ({c.conviction:.2f}): {c.rationale[:80]}")
    if write_report and use_llm and review.company_calls:
        path = report.write(review, load_sector_config(name))
        print(f"   📝 {path}" if path else "   (report dir unset — skipped)")
    return review


def _evidence_sources(store, *, entity: str = "") -> int:
    """Primary-source coverage: who the roster declares vs whose documents we hold.

    A declared witness we can never fetch is worse than an undeclared one — it sits in
    `未发声` forever and quietly inflates the coverage denominator, making the evidence
    look thinner than it is. This is the report that says which ones to demote.
    """
    from ..config import canonical_entity, entity_meta, load_sector_config
    from ..data import source_cache

    cfg = load_sector_config(entity or "ai_hardware")
    root = source_cache.root()
    print(f"信息源目录：{root or '(未配置 docs_root)'}\n")

    for layer in cfg.layers:
        if not layer.witness_roster:
            continue
        print(f"=== {layer.label} ({layer.key}) ===")
        for role in ("peer", "upstream", "downstream", "reference"):
            names = layer.witness_roster.get(role, [])
            if not names:
                continue
            print(f"  [{role}]")
            for raw in names:
                sym = canonical_entity(raw)
                docs = source_cache.inventory(sym)
                bad = [d for d in store.documents(entity=sym, ok_only=False) if not d["ok"]]
                meta = entity_meta(sym)
                if docs:
                    newest = max(docs, key=lambda d: d.fetched_at or "")
                    mark, detail = "✅", (f"{len(docs)} 份 · 最新 {newest.period or '期间未知'}"
                                         f" · {len(newest.text)//1000}k 字符")
                elif bad:
                    mark, detail = "⚠️ ", f"抓到过但被闸拦下：{bad[0]['note']}"
                else:
                    mark, detail = "❌", "无原始文件"
                market = meta.get("market", "?")
                print(f"    {mark} {sym:<12}{meta.get('name', ''):<34}{market:<6}{detail}")
        print()
    return 0


def _evidence_probe(symbol: str, runs: int = 5) -> int:
    """Run the SAME extraction N times and print the spread.

    Exists because a single run tells you nothing about this failure mode: on
    2026-08-07 the same document and prompt returned [0, 33, 36] observations through
    OpenRouter, so any one-shot check would have "confirmed" whichever answer it drew.
    Variance is the measurement.
    """
    from ..agents.evidence import observer
    from ..config import get_config
    from ..data import source_cache

    sym = symbol.upper()
    docs = source_cache.inventory(sym)
    if not docs:
        print(f"❌ {sym} 没有缓存文档 —— 先跑 `ats evidence observe {sym}`")
        return 1
    doc = max(docs, key=lambda d: len(d.text))
    rc = get_config().app.llm.for_role("evidence_observer")
    print(f"{sym} · {doc.path.name} · {len(doc.text)} 字符")
    print(f"provider={rc.provider} model={rc.model} max_tokens={rc.max_tokens}\n")

    counts = []
    for i in range(1, runs + 1):
        obs, failure = observer.extract(sym, f"probe-{i}", doc.text)
        counts.append(len(obs))
        print(f"  第 {i} 次 → {len(obs):>3} 条" + (f"  failure={failure}" if failure else ""))
    lo, hi = min(counts), max(counts)
    print(f"\n结果 {counts} · 极差 {hi - lo} · 空结果 {counts.count(0)}/{runs}")
    print("稳定" if lo > 0 and hi - lo <= max(3, hi * 0.2) else "⚠️ 不稳定")
    return 0


def run_evidence(action: str, symbol: str | None = None, *, file: str = "",
                 entity: str = "", limit: int = 30, accept: bool = False,
                 reviewer: str = "", note: str = "") -> int:
    """Chain evidence: observe one company's latest print, or inspect what's stored.

    Read-only with respect to trading — this path can never place an order.
    """
    from ..agents.evidence import observer
    from ..memory import get_store

    store = get_store()
    if action == "sources":
        return _evidence_sources(store, entity=entity)
    if action == "probe":
        if not symbol:
            print("❌ probe 需要标的：ats evidence probe NVDA --limit 5")
            return 1
        return _evidence_probe(symbol, runs=limit if limit and limit <= 20 else 5)
    if action == "show":
        rows = store.observations(entity=entity or None, limit=limit)
        if not rows:
            print("(暂无证据观测 — 等观察名单的下一次财报，或用 `ats evidence observe` 手动跑)")
            return 0
        print(f"{'实体':<10}{'指标':<22}{'类型':<18}{'立场':<12}{'方向':<7}{'期间'}")
        print("-" * 84)
        for r in rows:
            print(f"{r['entity']:<10}{r['metric']:<22}{r['observation_type']:<18}"
                  f"{r['stance']:<12}{r['direction']:<7}{r['period'] or '—'}")
            print(f"    {r['evidence_span'][:76]}")
        fails = store.observation_failures(limit=5)
        if fails:
            print("\n最近抽取失败（保留而非记成'零观测'）：")
            for f in fails:
                print(f"  {f['entity']} · {f['document_id']}: {f['reason']}")
        return 0

    if action in ("propose", "proposals", "review"):
        from ..chain import induction
        from ..config import load_pead_global

        if action == "proposals":
            rows = store.claim_proposals(limit=limit)
            if not rows:
                print("(暂无待确认命题 —— 未归属观测还没积累到触发门槛)")
                return 0
            for r in rows:
                mark = {"pending": "⏳", "accepted": "✅", "rejected": "🚫"}.get(r["status"], "·")
                print(f"{mark} {r['id']}  [{r['status']}]  {r['statement']}")
                if r.get("rationale"):
                    print(f"     理由：{r['rationale']}")
            return 0

        if action == "review":
            if not symbol:
                print("❌ 需要 proposal id：ats evidence review <id> --accept|--reject")
                return 1
            status = "accepted" if accept else "rejected"
            if not store.set_proposal_status(symbol, status, reviewer=reviewer or "boss",
                                             rationale=note):
                print(f"❌ 未找到 proposal {symbol}")
                return 1
            print(f"{'✅ 已采纳' if accept else '🚫 已拒绝'} {symbol}")
            if accept:
                print("提醒：命题仍需你手工写进 config/sectors/<name>.yaml 的 claims: —— "
                      "坐标系只有人能扩。触发它的那批观测已冻结，不会用于印证它自己。")
            return 0

        ind_cfg = load_pead_global().get("induction", {})
        if not ind_cfg.get("enabled", True):
            print("(induction.enabled=false — 归纳已关闭)")
            return 0
        proposal, reason = induction.induce(store, cfg=ind_cfg)
        print(f"触发判定：{reason}")
        if proposal is None:
            return 0
        rows_by_id = {r["id"]: r for r in store.observations(limit=500)}
        print()
        print(induction.as_card(proposal, rows_by_id))
        return 0

    if action == "report":
        from ..chain import report as chain_report
        from ..config import load_pead_global, load_sector_config

        cfg = load_sector_config(entity or "ai_hardware")
        path = chain_report.write(cfg, store, as_of=datetime.now(timezone.utc),
                                  ind_cfg=load_pead_global().get("induction", {}))
        print(f"📝 {path}" if path else "(report dir unset — skipped)")
        return 0

    if action == "collect":
        # Fetch every configured third-party series into the ledger. Separate from
        # `sources`, which reports primary-document coverage and touches no network.
        from ..chain import sources as chain_sources

        saved = chain_sources.collect(store)
        if not saved:
            print("（config/sources.yaml 里没有配置第三方源）")
            return 0
        # -1 = the source could not be reached this round (true gap). 0 = it was
        # reached but every point was already in the ledger (data is current, not
        # stale). Printing both as "取不到数据" is the exact bug this distinguishes:
        # a monthly source fetched twice in one month is 0-and-fine, not dead.
        for sid, n in sorted(saved.items()):
            if n < 0:
                print(f"  ⚠️  {sid:<28}取不到数据，已记成缺口而非沉默")
            elif n == 0:
                print(f"  🟡 {sid:<28}0 条新观测　—— 已抓到最新数据，只是与上次相同")
            else:
                print(f"  ✅ {sid:<28}{n} 条新观测")
        return 0

    if action == "articles":
        # The prose half of `collect`. Separate command because it costs a model call
        # per article and takes minutes, while `collect` is arithmetic over a series.
        from ..chain import articles as chain_articles
        from ..data import research as research_data

        # Newsletter acquisition is a source-stage operation shared by every consumer.
        # The SemiAnalysis adapter below only discovers already-stored assets.
        if not entity or entity == "semianalysis":
            research_data.ingest_configured(store=store)

        stats = chain_articles.collect_articles(store, source_ids={entity} if entity else None)
        if not stats:
            print("（config/sources.yaml 里没有配置 article_sources）")
            return 0
        for sid, st in sorted(stats.items()):
            if st.unreachable:
                print(f"  ⚠️  {sid:<24}取不到文章列表，已记成缺口而非沉默")
                continue
            print(f"  📰 {sid:<24}扫 {st.scanned} 篇 · 命中 {st.matched} 篇 · "
                  f"新抽取 {st.ingested} 篇 / {st.observations} 条观测")
            if st.unreadable:
                # Never fold this into the headline: a widening paywall must not read
                # as the publisher having gone quiet.
                print(f"     🚫 {st.unreadable} 篇取不到正文（付费或模板变更），已记成缺口")
            for t in st.titles:
                print(f"     · {t[:88]}")
        return 0

    if action == "kbreview":
        # The same six detectors the weekly report runs, on demand. Worth its own
        # command because the report costs a full re-assessment to regenerate, while
        # the question "did my KB edit clear that finding?" should cost nothing.
        from ..chain import kb_review
        from ..config import load_sector_config

        cfg = load_sector_config(entity or "ai_hardware")
        findings = kb_review.review(cfg, store, now=datetime.now(timezone.utc))
        print("\n".join(kb_review.as_section(findings)))
        return 0

    if action == "claims":
        from ..chain import corroborate as corr
        from ..config import load_sector_config

        cfg = load_sector_config(entity or "ai_hardware")
        from ..chain.sources import source_entities_for as _source_entities

        ccfg = cfg.review.get("corroboration", {})
        any_claim = False
        for layer in cfg.layers:
            if not layer.claims:
                continue
            any_claim = True
            print(f"\n=== {layer.label} ({layer.key}) ===")
            rows_by_entity = {}
            for claim in layer.claims:
                ents = {w.entity.upper() for w in claim.witnesses}
                ents |= claim.expected_witnesses() | set(claim.entities)
                ents |= _source_entities(claim)
                for e in ents:
                    rows_by_entity.setdefault(e, store.observations(entity=e, limit=200))
            for a in corr.assess_layer(layer, rows_by_entity, cfg=ccfg):
                claim = next(c for c in layer.claims if c.id == a.claim_id)
                # 「◐」 = one-sided AND too few independent filers -> unconfirmed,
                # NOT conflicting. 「仅自述」 = it did resolve, on one vantage point.
                mark = ("◐" if a.unresolved_reason == "single_stance"
                        else {"supportive": "✅", "contradicted": "⛔", "mixed": "⚠️",
                              "resolved": "📊", "unknown": "· "}.get(a.verdict, "· "))
                basis = "（仅自述）" if a.basis == "self_reported" else ""
                print(f"{mark} {a.claim_id:20} {a.verdict:13}{basis} 覆盖 {a.coverage:6} "
                      f"证据簇 {a.evidence_clusters} · 立场 {a.stance_classes} 类")
                print(f"     {claim.statement}")
                if a.entity_readings:
                    # A cross-section's answer IS the per-company table; there is no
                    # single support/refute count to print.
                    stand = {"strong": "强", "neutral": "中", "weak": "弱",
                             "unknown": "—"}
                    basis = {"corroborated": "有交叉印证", "self_reported": "仅自述",
                             "thin": "证据薄"}
                    for r in a.entity_readings:
                        print(f"       {r.entity:<12}{stand.get(r.standing, r.standing):<3}"
                              f"{basis.get(r.basis, r.basis):<8}"
                              f"{r.evidence_clusters} 簇/{r.stance_classes} 类  {r.reason}")
                else:
                    print(f"     支持 {a.support_score:.0f} / 反驳 {a.refute_score:.0f}"
                          + (f" · 异议 {','.join(a.dissenters)}" if a.dissenters else ""))
                if a.silent_witnesses:
                    # Silence is a gap, not neutrality — name who did not speak.
                    print(f"     未发声：{','.join(a.silent_witnesses)}")
                if a.note:
                    print(f"     {a.note}")
        if not any_claim:
            print("(该行业配置里还没有 claims —— 见 docs/CHAIN_EVIDENCE.md)")
        return 0

    if not symbol:
        print("❌ observe 需要标的：ats evidence observe MU")
        return 1
    sym = symbol.upper()
    if file:
        text, src, doc_id = Path(file).read_text(encoding="utf-8"), file, f"{sym}:{Path(file).name}"
    else:
        from ..data import earnings_calendar

        pr = earnings_calendar.last_print(sym, back_days=45)
        doc_id = f"{sym}:{pr.date:%Y%m%d}" if pr and pr.date else f"{sym}:manual"
        text, src, note = observer.fetch_document(sym, print_=pr, store=store)
        if note:
            print(f"（{note}）")
    if not text.strip():
        print(f"❌ 取不到 {sym} 的财报稿/纪要 — 可用 --file 指定本地文档")
        return 1
    res = observer.observe_document(sym, doc_id, text, source_url=src)
    if res["failure"]:
        print(f"⚠️  {sym}: 抽取失败 — {res['failure']}（已记录，不记为零观测）")
    else:
        print(f"✅ {sym}: {res['new']} 条新观测 / 共 {res['saved']} 条（doc={doc_id}）")

    # The earnings RELEASE is a second document, extracted separately — the call
    # narrates, the release tabulates, and folding them together would lose which
    # source a number came from.
    if not file:
        report_date = str(getattr(pr, "date", "") or "")
        rtext, rsrc, rnote = observer.fetch_release(sym, report_date=report_date,
                                                    store=store)
        if rnote:
            print(f"（财报稿：{rnote}）")
        if rtext.strip():
            rid = f"{sym}:release:{report_date or 'manual'}"
            rres = observer.observe_document(sym, rid, rtext, source_url=rsrc)
            if rres["failure"]:
                print(f"⚠️  {sym} 财报稿抽取失败 — {rres['failure']}")
            else:
                print(f"✅ {sym} 财报稿: {rres['new']} 条新观测 / 共 {rres['saved']} 条")
    return 0


def sector_show(name: str = "ai_hardware") -> int:
    from ..memory import get_store

    store = get_store()
    latest = store.latest_sector_review(name)
    if latest is None:
        print(f"(no sector review for {name} yet — run `ats sector review {name}`)")
        return 0
    print(f"=== sector review {name} @ {latest.as_of:%Y-%m-%d} ===")
    print(f"Regime: {latest.regime}\n\n{latest.summary}\n")
    for a in latest.layers:
        print(f"  {a.label}: 景气 {a.boom_score:.0f} [{a.signal}]")
    print("\nHistory:")
    for r in store.recent_sector_reviews(name):
        print(f"  {r['as_of'][:10]}  {r['regime'][:70]}")
    return 0


def sector_probe(name: str = "ai_hardware", *, live_data: bool = True) -> int:
    """Assemble the review context without spending an LLM call; print stats + prompt."""
    from ..agents.sector import assemble
    from ..config import load_sector_config

    sc = assemble.build(load_sector_config(name), live_data=live_data)
    print(f"=== sector context stats: {sc.stats()} ===\n")
    print(sc.as_context())
    return 0


def run_pead_research(*, use_llm: bool = True) -> list:
    """One research pass: ingest newsletters, extract per-ticker insights."""
    from ..agents.pead import research
    from ..data import research as research_data
    from ..memory import get_store

    research_data.ingest_configured(store=get_store())
    insights = research.run(use_llm=use_llm)
    if not insights:
        print("📰 research — no new articles / no insights")
        return []
    print(f"📰 research — {len(insights)} insights:")
    for i in insights:
        print(f"   [{i.direction}/{i.impact_path}] {i.ticker} ({i.confidence:.2f}): {i.summary}")
    return insights


def run_pead_score_window(window: str, *, dry_run: bool = True, use_llm: bool = True,
                          as_of: str | None = None, chief: bool = True,
                          plan_only: bool = False) -> int:
    """Run one PEAD score window by hand (the scheduler runs the same function).

    `--as-of` rewinds only the calendar/state layer, so a past print can be replayed;
    it does NOT rewind Tavily or SEC results.
    """
    from datetime import datetime

    from .scheduler import ET, pead_score_window

    moment = None
    if as_of:
        moment = datetime.fromisoformat(as_of)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=ET)

    label = "计划" if plan_only else ("试运行" if dry_run else "实盘")
    print(f"=== PEAD {window} 打分窗口（{label}"
          f"{f'，as_of={moment:%Y-%m-%d %H:%M %Z}' if moment else ''}）===")
    outcomes = pead_score_window(window, dry_run=dry_run, use_llm=use_llm, as_of=moment,
                                 chief=chief, plan_only=plan_only)
    if not outcomes:
        print("  （非交易日，或打分窗口被关闭）")
        return 0
    for sym, why in outcomes.items():
        mark = "▶" if why.startswith("GO") else "·"
        print(f"  {mark} {sym:6} {why}")
    n = sum(1 for w in outcomes.values() if w.startswith("GO"))
    print(f"\n  {n}/{len(outcomes)} 个标的{'待打分' if plan_only else '已打分'}")
    return 0


def run_transcript_probe(symbols: list[str] | None = None, quarters: int = 4) -> int:
    """Audit transcript retrieval across recent quarters — no LLM, read-only.

    For each target we walk the fiscal label BACKWARDS (label arithmetic, so it works
    for offset fiscal years like NVDA's FY2027 as well as calendar-quarter filers)
    and check that the fetched transcript REPORTS the quarter we asked for.

    Acceptance: zero wrong-quarter picks. A miss is tolerable — scoring then falls
    back to the earnings release — but a wrong quarter silently produces a fictional
    surprise, so it must never happen.
    """
    from ..config import load_pead_config, load_pead_global
    from ..data import fiscal, transcript

    targets = [s.upper() for s in (symbols or load_pead_global().get("targets", []))]
    rows, wrong, missing, skipped = [], 0, 0, []

    for sym in targets:
        cfg = load_pead_config(sym)
        year, quarter = fiscal.parse_label(cfg.fiscal_label)
        if not (year and quarter):
            skipped.append(f"{sym}（fiscal_label={cfg.fiscal_label!r} 无季度）")
            continue
        for back in range(quarters):
            q, y = quarter - back, year
            while q <= 0:
                q += 4
                y -= 1
            label = f"Q{q} {y}"
            text, src = transcript._from_search(sym, label, cfg.company_name)
            got = fiscal.detect_period(text, src) if text else None
            body = transcript.extract_body(text, src)[0] if text else ""
            if not text:
                status, missing = "—— 未找到", missing + 1
            elif got == (y, q):
                status = "✅ 正确"
            else:
                status, wrong = f"❌ 错季 {got}", wrong + 1
            rows.append((sym, label, status, len(text), len(body), src[:64]))
            print(f"  {sym:6} {label:10} {status:16} raw={len(text):7} body={len(body):7} {src[:64]}")

    print(f"\n=== transcript 检索审计：{len(rows)} 次查询 ===")
    print(f"  ✅ 正确 {len(rows) - wrong - missing}   ❌ 错季 {wrong}   —— 未找到 {missing}")
    if skipped:
        print(f"  ⏭  跳过（label 无季度，待 Stage B 派生）：{', '.join(skipped)}")
    print("  验收标准：错季 = 0" + ("  → 通过 ✅" if wrong == 0 else "  → 未通过 ❌"))
    return 1 if wrong else 0


def pead_show(symbol: str) -> int:
    from ..memory import get_store

    store = get_store()
    recent = store.recent_dossiers(symbol.upper(), limit=1)
    if not recent:
        print(f"(no PEAD dossier for {symbol.upper()} yet — run `ats pead prep {symbol.upper()}`)")
        return 0
    d = store.get_dossier(symbol.upper(), recent[0]["fiscal_label"])
    print(f"=== PEAD dossier {d.symbol} {d.fiscal_label} (phase={d.phase}) ===")
    if d.expectation_set:
        print(f"\n[Narrative]\n{d.expectation_set.narrative}")
        print(f"\n[Valuation] {d.expectation_set.valuation}")
    if d.market_setup:
        m = d.market_setup
        print(f"\n[Setup] run-up vs sector {m.run_up_vs_sector_pct}% · EM {m.expected_move_pct}% "
              f"· ATM IV {m.atm_iv}% · skew {m.iv_skew}")
    if d.scorecard:
        print(f"\n[Scorecard] 总分 {d.scorecard.total:+.2f} (门槛 {d.scorecard.threshold:+.1f}) — "
              f"{d.scorecard.band}")
        for ln in d.scorecard.lines:
            print(f"  {ln.dim_key:14} score {ln.score:+.2f} × {ln.weight:.0%} = {ln.weighted:+.3f}  "
                  f"{ln.note[:60]}")
    if d.decision_summary:
        print(f"\n[Decision] {d.decision_summary}")

    # Score-run ledger: which window produced it, whether it had a transcript, and how
    # far behind the print it was — the audit trail for the score windows.
    rows = store.conn.execute(
        "SELECT * FROM pead_score_runs WHERE symbol = ? AND fiscal_label = ? "
        "ORDER BY version", (symbol.upper(), d.fiscal_label)).fetchall()
    if rows:
        print("\n[打分台账]")
        for r in rows:
            lat = f"{r['latency_hours']:.1f}h" if r["latency_hours"] is not None else "?"
            print(f"  v{r['version']}  {r['scored_at'][:19]}  window={r['window'] or '-':4} "
                  f"纪要={'✅' if r['has_transcript'] else '❌'} "
                  f"终版={'✅' if r['final'] else '❌'} 距财报={lat} "
                  f"总分={r['total'] if r['total'] is not None else '-'}")
        period = store.get_period(symbol.upper(), rows[-1]["earnings_date"])
        if period:
            print(f"  财报: {period['earnings_date']} {period['session']}"
                  f"（来源 {period['session_source']}）· label 来自 {period['label_source']}")
    return 0


def resume_cycle(thread_id: str, approval, channel=None) -> dict:
    """Resume a checkpointed decision run with the Boss verdict (webhook path).

    All approval interrupts live on the chief decision graph (`chief-*` /
    `trader-*` thread ids), so it is always the graph to rebuild here.
    """
    from ..graph.chief import build_chief_graph

    if approval.reviewed_at is None:
        approval.reviewed_at = datetime.now(timezone.utc)
    app = build_chief_graph(checkpointer=get_checkpointer(persist=True))
    cfg_run = {"configurable": {"thread_id": thread_id}}
    result = app.invoke(Command(resume=approval.model_dump(mode="json")), config=cfg_run)

    if channel is not None:
        orders = result.get("order_results", [])
        channel.push(Notification(
            kind="fill_report", title=f"{thread_id}: {approval.status}",
            body=f"{len(orders)} order(s) processed"))
    return result


def thetadata_probe(symbol: str) -> int:
    """Hit the local ThetaData terminal and dump the response shape (schema check)."""
    from ..data import options

    try:
        raw = options.thetadata_raw(symbol.upper())
    except Exception as exc:  # noqa: BLE001
        print(f"❌ ThetaData unreachable: {exc}")
        print("   Start it: put creds in var/thetadata/creds.txt, run ./scripts/start_thetadata.sh")
        return 1
    rows = raw if isinstance(raw, list) else (raw.get("response") if isinstance(raw, dict) else [])
    print(f"✅ ThetaData responded ({len(rows)} option-EOD rows).")
    # Confirm the parser end-to-end (Expected Move / IV / skew).
    setup = options.fetch(symbol.upper())
    print(f"   setup: EM {setup.get('expected_move_pct')}% · ATM IV {setup.get('atm_iv')}% · "
          f"skew {setup.get('iv_skew')} · exp {setup.get('expiration')} · src {setup.get('source')}")
    return 0


def ibkr_probe() -> int:
    """Connectivity check: print account summary + positions, or a clear error."""
    from ..broker import IBKRBroker, IBKRUnavailable

    cfg = get_config()
    broker = IBKRBroker(sector_by_symbol={t.symbol: t.sector for t in cfg.app.tickers})
    try:
        pf = broker.get_portfolio()
    except IBKRUnavailable as exc:
        print(f"❌ IBKR unavailable: {exc}")
        print("   Start TWS/IB Gateway, enable API (port 7497 paper), trust 127.0.0.1.")
        return 1
    print(f"✅ Connected. account={pf.account_id or '?'}  "
          f"NetLiq=${pf.net_liquidation:,.0f}  cash=${pf.cash:,.0f}  leverage={pf.leverage:.2f}x")
    if not pf.positions:
        print("   (no open positions)")
    for p in pf.positions:
        print(f"   {p.symbol:6} {p.qty:>8.0f} @ {p.avg_cost:>8.2f}  mv=${p.market_value:>12,.0f}  "
              f"w={p.weight:.1%}  uPnL=${p.unrealized_pnl:,.0f}")
    return 0


def _setup_logging() -> None:
    import logging

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger("ats").setLevel(logging.INFO)  # our own logs at INFO, third-party quiet


def run_data(action: str, value: str = "", *, source: str = "", series: str = "",
             entity: str = "", since: str = "", as_of: str = "", limit: int = 20,
             vintages: bool = False) -> int:
    """Inspect stable data products without knowing their backing tables."""
    import json

    from ..data_platform import get_data_products

    products = get_data_products()
    if action == "health":
        result = products.health()
    elif action == "series":
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None
        result = products.indicator_series(
            source_id=source or None, series=series or None, entity=entity or None,
            since=since or None, as_of=cutoff, include_vintages=vintages,
        )
    elif action == "search":
        result = products.search_documents(
            value, entity=entity or None, source_contains=source or None,
            published_since=since or None, limit=limit,
        )
    elif action == "company":
        result = products.company_research_package(value)
    elif action == "claim":
        result = products.claim_evidence_package(value, limit=limit)
    elif action == "lineage":
        result = products.lineage(value)
    else:
        raise ValueError(f"unknown data action: {action}")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="ats", description="Multi-agent trading cycle runner")
    sub = parser.add_subparsers(dest="command", required=True)
    data = sub.add_parser("data", help="统一数据产品 (health / series / search / company / claim / lineage)")
    data.add_argument("action", choices=["health", "series", "search", "company", "claim",
                                         "lineage"])
    data.add_argument("value", nargs="?", default="",
                      help="search 查询词 / company 实体 / claim 命题 / lineage 投影 ID")
    data.add_argument("--source", default="", help="series: source ID；search: 来源过滤")
    data.add_argument("--series", default="", help="series: 指标名称")
    data.add_argument("--entity", default="", help="实体过滤")
    data.add_argument("--since", default="", help="最早期间或发布日期")
    data.add_argument("--as-of", default="", help="series: 历史可见时点（ISO 8601）")
    data.add_argument("--limit", type=int, default=20)
    data.add_argument("--vintages", action="store_true", help="series: 包含所有修订版本")
    sub.add_parser("ibkr", help="probe IBKR paper connectivity (account + positions)")
    srv = sub.add_parser("serve", help="run the approval webhook (Feishu callbacks)")
    srv.add_argument("--host", default="0.0.0.0")
    srv.add_argument("--port", type=int, default=8000)
    sch = sub.add_parser("schedule", help="run cycles on a daily NYSE-session cron")
    sch.add_argument("--live", action="store_true", help="execute (IBKR paper); default dry-run")
    sch.add_argument("--now", action="store_true", help="run one cycle immediately, then exit")
    sch.add_argument("--window", choices=["amc", "bmo"],
                     help="run one PEAD score window immediately, then exit")
    td = sub.add_parser("thetadata", help="probe the local ThetaData terminal (inspect schema)")
    td.add_argument("symbol")
    se = sub.add_parser("sector", help="sector review 行业分析 (review / show / probe)")
    se.add_argument("action",
                    choices=["review", "show", "probe", "crosssection", "kbperturb"])
    se.add_argument("name", nargs="?", default="ai_hardware")
    se.add_argument("--layer", default="all", help="crosssection: layer key (e.g. L3_dc_infra) or 'all'")
    se.add_argument("--structure", action="store_true", help="crosssection: blend KB structure analyst")
    se.add_argument("--mode", default="poison", choices=["poison", "ablate", "control"],
                    help="kbperturb: 倒序判据(poison) / 删掉判据(ablate) / "
                         "同一份笔记跑两次测噪声底(control)")
    se.add_argument("--no-llm", action="store_true", help="assemble + stub review, no LLM")
    se.add_argument("--offline", action="store_true", help="skip yfinance (store/static only)")
    se.add_argument("--no-report", action="store_true", help="skip the Obsidian report file")
    evi = sub.add_parser("evidence", help="产业链证据 (observe / show / sources) —— 只读，绝不下单")
    evi.add_argument("action",
                     choices=["observe", "show", "claims", "report", "sources",
                              "probe", "propose", "proposals", "review", "kbreview",
                              "collect", "articles"])
    evi.add_argument("symbol", nargs="?", help="observe: 标的，如 MU")
    evi.add_argument("--file", default="", help="observe: 用本地文档而不是自动抓取")
    evi.add_argument("--entity", default="", help="show: 只看某实体 / claims: 行业名")
    evi.add_argument("--limit", type=int, default=30, help="show/proposals: 条数")
    evi.add_argument("--accept", action="store_true", help="review: 采纳（默认拒绝）")
    evi.add_argument("--reviewer", default="", help="review: 审阅人")
    evi.add_argument("--note", default="", help="review: 理由")
    ev = sub.add_parser("events", help="事件日历 (list / upcoming)")
    ev.add_argument("action", choices=["list", "upcoming"])
    ev.add_argument("--days", type=int, default=30, help="upcoming window")
    ch = sub.add_parser("chief", help="chief 首席统一决策 (run / show / probe)")
    ch.add_argument("action", choices=["run", "show", "probe"])
    ch.add_argument("--live", action="store_true", help="execute for real (default dry-run)")
    ch.add_argument("--yes", action="store_true", help="auto-approve (non-interactive)")
    ch.add_argument("--no-llm", action="store_true")
    ch.add_argument("--offline", action="store_true", help="skip live broker read")
    ch.add_argument("--no-execute", action="store_true", help="decide only, don't call trader")
    ch.add_argument("--channel", choices=["cli", "feishu", "feishu_bot"], default="cli")
    rk = sub.add_parser("risk", help="risk officer 风控 (report / memo / check)")
    rk.add_argument("action", choices=["report", "memo", "check"])
    rk.add_argument("symbol", nargs="?", help="check: filter stored decisions by ticker")
    rk.add_argument("--report", action="store_true", help="report: also write an Obsidian file")
    rk.add_argument("--offline", action="store_true", help="show stored review without IBKR")
    jr = sub.add_parser("journal",
                        help="交易日志 (doctor / reconcile / episodes / mark / invalidate / review / "
                             "calibrate / reflect / ledger / score)")
    jr.add_argument("action", choices=["doctor", "reconcile", "episodes", "mark",
                                       "invalidate", "review", "calibrate", "reflect",
                                       "ledger", "score"])
    jr.add_argument("--dry-run", action="store_true",
                    help="reconcile: 只读，打印将要写入什么")
    jr.add_argument("--month", help="ledger: YYYY-MM（默认本月）")
    jr.add_argument("--backfill", action="store_true",
                    help="score: 先用已打分的 dossier 回填预测")
    jr.add_argument("--symbol", help="episodes: 只看这个标的")
    jr.add_argument("--no-llm", action="store_true",
                    help="invalidate: 只算 horizon_overdue_days，不调 LLM 判定失效；"
                        "reflect: 只出确定性证据+当前需要处理清单，不调 LLM 生成假设")
    jr.add_argument("--quarterly", action="store_true",
                    help="calibrate: 按季度出报告（默认按月）")
    tr = sub.add_parser("trader", help="IBKR trader: portfolio / perf / snapshot / fills / execute / buy / sell")
    tr.add_argument("action", choices=["portfolio", "perf", "snapshot", "fills", "orders",
                                       "cancel", "execute", "buy", "sell"])
    tr.add_argument("symbol", nargs="?", help="ticker (execute/fills optional; buy/sell required)")
    tr.add_argument("qty", nargs="?", type=float, help="shares (buy/sell)")
    tr.add_argument("--limit", type=float, help="limit price (buy/sell); omit for market")
    tr.add_argument("--days", type=int, default=30, help="perf window (snapshots)")
    tr.add_argument("--report", action="store_true", help="perf: also write an Obsidian report")
    tr.add_argument("--channel", choices=["cli", "feishu", "feishu_bot"], default="cli",
                    help="approval channel for orders")
    tr.add_argument("--offline", action="store_true", help="portfolio: show stored snapshot without IBKR")
    tr.add_argument("--dry-run", action="store_true", help="go through approval but place no orders")
    te = sub.add_parser("technical",
                        help="technical analyst 技术面 (review / show / probe) — 确定性无 LLM")
    te.add_argument("action", choices=["review", "show", "probe"])
    te.add_argument("name", nargs="?", default="technical")
    te.add_argument("--offline", action="store_true", help="skip broker + price fetch")
    te.add_argument("--no-report", action="store_true", help="skip the Obsidian report file")
    ma = sub.add_parser("macro", help="macro strategist 宏观分析 (review / show / probe)")
    ma.add_argument("action", choices=["review", "show", "probe"])
    ma.add_argument("name", nargs="?", default="macro")
    ma.add_argument("--no-llm", action="store_true", help="assemble + stub review, no LLM")
    ma.add_argument("--offline", action="store_true", help="skip FRED/yfinance/Tavily")
    ma.add_argument("--no-report", action="store_true", help="skip the Obsidian report file")
    pe = sub.add_parser("pead",
                        help="PEAD earnings workflow (prep / score / show / monitor / watch / research)")
    pe.add_argument("action", choices=["prep", "score", "show", "monitor", "watch", "research",
                                       "transcriptprobe", "scorewindow"])
    pe.add_argument("symbol", nargs="?", help="ticker (omit for `watch` / `research`)")
    pe.add_argument("--quarters", type=int, default=4,
                    help="transcriptprobe: how many recent quarters per target")
    pe.add_argument("--window", choices=["amc", "bmo"],
                    help="scorewindow: which window to run")
    pe.add_argument("--as-of", dest="as_of",
                    help="scorewindow: ISO datetime; rewinds the calendar/state layer only")
    pe.add_argument("--plan-only", action="store_true",
                    help="scorewindow: print the routing decision without scoring")
    pe.add_argument("--no-chief", action="store_true",
                    help="scorewindow: score but don't run the Chief / push approval")
    pe.add_argument("--transcript", help="path or URL to the earnings-call transcript (score)")
    pe.add_argument("--live", action="store_true", help="execute (IBKR paper); default dry-run")
    pe.add_argument("--yes", action="store_true", help="auto-approve (non-interactive)")
    pe.add_argument("--offline", action="store_true", help="skip live data + IBKR (local only)")
    pe.add_argument("--no-llm", action="store_true", help="skip LLM (stub agents)")
    pe.add_argument("--channel", choices=["cli", "feishu", "feishu_bot"], default="cli",
                    help="approval channel when --chief executes")
    pe.add_argument("--chief", action="store_true",
                    help="score: run the Chief immediately after the recommendation persists")
    args = parser.parse_args(argv)

    if args.command == "data":
        if args.action in {"search", "company", "claim", "lineage"} and not args.value:
            parser.error(f"data {args.action} requires VALUE")
        return run_data(args.action, args.value, source=args.source, series=args.series,
                        entity=args.entity, since=args.since, as_of=args.as_of,
                        limit=args.limit, vintages=args.vintages)
    if args.command == "ibkr":
        return ibkr_probe()
    if args.command == "serve":
        from .server import serve

        serve(host=args.host, port=args.port)
        return 0
    if args.command == "schedule":
        from .scheduler import start

        start(dry_run=not args.live, run_once=args.now, window=args.window)
        return 0
    if args.command == "thetadata":
        return thetadata_probe(args.symbol)
    if args.command == "sector":
        if args.action == "show":
            return sector_show(args.name)
        if args.action == "probe":
            return sector_probe(args.name, live_data=not args.offline)
        if args.action == "kbperturb":
            # Layer 2 of KB validation: the audit shows the analyst CITES the criteria,
            # this shows whether they PRODUCED the score. Never persists, never touches
            # the production notes.
            from ..agents.sector import kb_perturb

            if args.layer in ("", "all"):
                print("kbperturb 需要指定 --layer（一次只扰动一层）")
                return 2
            base, other = kb_perturb.run(args.name, args.layer, mode=args.mode)
            print()
            print(kb_perturb.render(base, other, mode=args.mode))
            return 0

        if args.action == "crosssection":
            return run_cross_section(args.name, args.layer, structure=args.structure,
                                     write_report=not args.no_report)
        run_sector_review(args.name, use_llm=not args.no_llm,
                          live_data=not args.offline, write_report=not args.no_report)
        return 0
    if args.command == "evidence":
        return run_evidence(args.action, args.symbol, file=args.file,
                            entity=args.entity, limit=args.limit, accept=args.accept,
                            reviewer=args.reviewer, note=args.note)
    if args.command == "events":
        return events_list(days=args.days if args.action == "upcoming" else None)
    if args.command == "chief":
        if args.action == "show":
            return chief_show()
        if args.action == "probe":
            return chief_probe(offline=args.offline)
        return run_chief(execute=not args.no_execute, dry_run=not args.live,
                         channel=args.channel, use_llm=not args.no_llm,
                         auto=args.yes, offline=args.offline)
    if args.command == "risk":
        if args.action == "report":
            return risk_report(write_report=args.report, offline=getattr(args, "offline", False))
        if args.action == "memo":
            return risk_memo()
        return risk_check(args.symbol)
    if args.command == "journal":
        if args.action == "reconcile":
            from ..trader import reconcile

            return reconcile.run(dry_run=args.dry_run)
        if args.action == "episodes":
            from ..journal import episodes as episodes_mod
            from ..memory import get_store

            rc = episodes_mod.run()
            for ep in get_store().list_episodes(symbol=args.symbol or "", limit=50):
                print(f"  {ep.symbol:6} {ep.direction:5} {ep.status:6} "
                      f"origin={ep.origin:12} realized={ep.realized_pnl} "
                      f"entry={ep.avg_entry} exit={ep.avg_exit}")
            return rc
        if args.action == "mark":
            from ..journal import marks

            return marks.run()
        if args.action == "invalidate":
            from ..journal import invalidation

            return invalidation.run(use_llm=not args.no_llm)
        if args.action == "review":
            from ..journal import episode_report

            return episode_report.run()
        if args.action == "calibrate":
            from ..journal import calibration

            return calibration.run(quarterly=args.quarterly)
        if args.action == "reflect":
            from ..journal import critic

            return critic.run(use_llm=not args.no_llm)
        if args.action == "ledger":
            from ..journal import report as journal_report

            return journal_report.run(args.month or "")
        if args.action == "score":
            from ..journal import predictions

            return predictions.run(backfill=args.backfill)
        from ..journal import doctor

        return doctor.run()
    if args.command == "trader":
        if args.action == "portfolio":
            return trader_portfolio(offline=getattr(args, "offline", False))
        if args.action == "snapshot":
            return trader_snapshot()
        if args.action == "perf":
            return trader_perf(args.days, write_report=args.report)
        if args.action == "fills":
            return trader_fills(args.symbol)
        if args.action == "orders":
            return trader_orders()
        if args.action == "cancel":
            return trader_cancel(args.symbol)
        if args.action == "execute":
            return trader_execute(args.symbol, channel=args.channel, dry_run=args.dry_run)
        # buy / sell — manual order (symbol + qty required)
        if not args.symbol or args.qty is None:
            parser.error(f"trader {args.action} requires SYMBOL and QTY")
        return trader_manual(args.action, args.symbol, args.qty, limit=args.limit,
                             channel=args.channel, dry_run=args.dry_run)
    if args.command == "technical":
        if args.action == "show":
            return technical_show(args.name)
        if args.action == "probe":
            return technical_probe(args.name, live_data=not args.offline)
        return run_technical_review(args.name, live_data=not args.offline,
                                    write_report=not args.no_report)
    if args.command == "macro":
        if args.action == "show":
            return macro_show(args.name)
        if args.action == "probe":
            return macro_probe(args.name, live_data=not args.offline)
        run_macro_review(args.name, use_llm=not args.no_llm,
                         live_data=not args.offline, write_report=not args.no_report)
        return 0
    if args.command == "pead":
        if args.action == "watch":
            run_pead_watch(use_llm=not args.no_llm)
            return 0
        if args.action == "research":
            run_pead_research(use_llm=not args.no_llm)
            return 0
        if args.action == "transcriptprobe":
            return run_transcript_probe([args.symbol] if args.symbol else None,
                                        quarters=args.quarters)
        if args.action == "scorewindow":
            if not args.window:
                parser.error("pead scorewindow requires --window amc|bmo")
            return run_pead_score_window(args.window, dry_run=not args.live,
                                         use_llm=not args.no_llm, as_of=args.as_of,
                                         chief=not args.no_chief, plan_only=args.plan_only)
        if not args.symbol:
            parser.error("pead %s requires a symbol" % args.action)
        if args.action == "show":
            return pead_show(args.symbol)
        if args.action == "monitor":
            run_pead_monitor(args.symbol, use_llm=not args.no_llm)
            return 0
        run_pead(args.symbol, args.action, dry_run=not args.live, auto=args.yes,
                 offline=args.offline, use_llm=not args.no_llm, transcript=args.transcript,
                 channel=args.channel, chief=getattr(args, "chief", False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
