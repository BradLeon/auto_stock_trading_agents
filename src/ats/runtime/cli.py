"""CLI runner: execute one trading cycle, pausing at the Boss-approval interrupt.

    ats run                 # dry-run, interactive Boss prompt
    ats run --yes           # dry-run, auto-approve (unattended smoke test)
    ats run --live          # paper execution path (Phase 7+); still IBKR paper

The graph is transport-agnostic: it interrupts, this runner asks the configured
BossChannel for a verdict, then resumes with Command(resume=...).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from langgraph.types import Command

from ..channel import CLIChannel, get_channel
from ..config import get_config
from ..schemas.channel import ApprovalRequest, Notification
from ..graph.build import build_graph
from ..graph.checkpoint import get_checkpointer
from ..graph.state import TradingState


def _initial_state(cfg, *, dry_run: bool, live_data: bool, use_llm: bool, use_broker: bool) -> TradingState:
    now = datetime.now(timezone.utc)
    sectors = {
        sector: cfg.app.sectors.get(sector).supply_chain if cfg.app.sectors.get(sector) else ""
        for sector in cfg.app.sectors_in_use
    }
    return TradingState(
        cycle_id=f"cycle-{now:%Y%m%d-%H%M%S}",
        as_of=now,
        dry_run=dry_run,
        live_data=live_data,
        use_llm=use_llm,
        use_broker=use_broker,
        watchlist=cfg.app.tickers,
        sectors=sectors,
    )


def _make_channel(cfg, auto: bool):
    if cfg.app.channel.kind == "cli":
        return CLIChannel(auto=auto)
    return get_channel()


def run_cycle(*, dry_run: bool = True, auto: bool = False, offline: bool = False,
              use_llm: bool = True, channel=None) -> dict:
    cfg = get_config()
    channel = channel or _make_channel(cfg, auto)
    is_async = getattr(channel, "is_async", False)
    # Async channels (Feishu) must survive across processes -> persistent checkpointer.
    app = build_graph(checkpointer=get_checkpointer(persist=is_async))

    state = _initial_state(cfg, dry_run=dry_run, live_data=not offline, use_llm=use_llm,
                           use_broker=not offline)
    cfg_run = {"configurable": {"thread_id": state.cycle_id}}
    print(f"▶ running {state.cycle_id} (dry_run={dry_run}) over {[t.symbol for t in state.watchlist]}")

    result = app.invoke(state, config=cfg_run)

    if "__interrupt__" not in result:
        _report(channel, result)
        return result

    req = ApprovalRequest.model_validate(result["__interrupt__"][0].value)

    # Async: send the card and exit; the webhook resumes this thread later.
    if is_async:
        channel.send_approval_request(req, thread_id=state.cycle_id)
        label = getattr(channel, "kind", "async channel")
        print(f"⏸ {state.cycle_id} awaiting Boss approval via {label}. "
              f"Run `ats serve` to handle the callback.")
        return result

    # Sync (CLI): drive interrupts to completion in-process.
    while "__interrupt__" in result:
        req = ApprovalRequest.model_validate(result["__interrupt__"][0].value)
        channel.push(Notification(kind="approval_request", title="Decisions pending review",
                                  body=f"{len(req.decisions)} proposed trade(s)"))
        approval = channel.request_approval(req)
        result = app.invoke(Command(resume=approval.model_dump(mode="json")), config=cfg_run)

    _report(channel, result)
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
            run_chief(dry_run=dry_run, channel=channel, auto=auto, offline=offline)
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
              use_llm: bool = True, auto: bool = False, offline: bool = False) -> int:
    """One Chief decision run: read all artifacts -> decide -> (optionally) execute."""
    from ..agents.chief import decide as chief_decide
    from ..memory import get_store

    result = chief_decide.run(use_llm=use_llm, live_broker=not offline)
    get_store().save_chief_run(cycle_id=result.cycle_id, as_of=result.as_of,
                               summary=result.summary, decisions=result.decisions)
    print(f"👔 chief {result.cycle_id}\n{result.summary}")
    for d in result.decisions:
        size = f"${d.notional_usd:,.0f}" if d.notional_usd else (
            f"w={d.target_weight:.0%}" if d.target_weight else "?")
        print(f"   {d.action.upper()} {d.symbol} {size} conv={d.conviction:.2f} — {d.rationale[:70]}")
    if not result.decisions:
        print("   (无行动 — 零决策)")
        return 0
    if execute:
        from ..trader import execute as texec

        texec.execute(result.decisions, source="chief", channel=channel,
                      dry_run=dry_run, auto=auto, event_data=_pead_event_data())
    return 0


def _pead_event_data() -> dict[str, dict]:
    """Expected Move per PEAD target from the freshest dossiers (for the L6 risk gate)."""
    from ..config import load_pead_config, load_pead_global
    from ..memory import get_store

    out: dict[str, dict] = {}
    for sym in load_pead_global().get("targets", []):
        try:
            d = get_store().get_dossier(sym.upper(), load_pead_config(sym).fiscal_label)
            if d and d.market_setup and d.market_setup.expected_move_pct:
                out[sym.upper()] = {"expected_move_pct": d.market_setup.expected_move_pct}
        except Exception:  # noqa: BLE001
            continue
    return out


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


def risk_report(*, write_report: bool = False) -> int:
    from ..memory import get_store
    from ..risk import assess as risk_assess, report as risk_report_mod
    from ..trader import portfolio as tport

    pf = tport.snapshot()
    if pf is None:
        print("❌ IBKR unavailable — start TWS (port 7497).")
        return 1
    risk_assess.enrich_beta(pf)
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


def trader_portfolio() -> int:
    from ..trader import portfolio as tp

    pf = tp.snapshot()
    if pf is None:
        print("❌ IBKR unavailable — start TWS/Gateway with API enabled (port 7497).")
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


def run_macro_review(name: str = "macro", *, use_llm: bool = True,
                     live_data: bool = True, write_report: bool = True):
    """One weekly macro strategist review: regime + rate path + sector tilts."""
    from ..agents.macro import report, review as macro_review
    from ..config import load_macro_config

    review = macro_review.run(name, use_llm=use_llm, live_data=live_data)
    print(f"🌐 macro {name} — {review.regime}")
    if review.rate_path:
        print(f"   利率路径: {review.rate_path}")
    for t in review.sector_tilts:
        print(f"   {t.stance} {t.sector}: {t.rationale[:80]}")
    if review.asset_implications:
        print(f"   资产含义: {review.asset_implications}")
    if write_report and use_llm and review.sector_tilts:
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
    print(f"Regime: {latest.regime}\n利率路径: {latest.rate_path}\n\n{latest.summary}\n")
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

    insights = research.run(use_llm=use_llm)
    if not insights:
        print("📰 research — no new articles / no insights")
        return []
    print(f"📰 research — {len(insights)} insights:")
    for i in insights:
        print(f"   [{i.direction}/{i.impact_path}] {i.ticker} ({i.confidence:.2f}): {i.summary}")
    return insights


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
    return 0


def resume_cycle(thread_id: str, approval, channel=None) -> dict:
    """Resume a checkpointed run with the Boss verdict (called by the webhook).

    Routes by thread_id prefix: `pead:` -> PEAD graph, else the daily cycle graph.
    """
    if approval.reviewed_at is None:
        approval.reviewed_at = datetime.now(timezone.utc)
    if thread_id.startswith("pead:"):
        from ..graph.pead import build_pead_graph

        app = build_pead_graph(checkpointer=get_checkpointer(persist=True))
    else:
        app = build_graph(checkpointer=get_checkpointer(persist=True))
    cfg_run = {"configurable": {"thread_id": thread_id}}
    result = app.invoke(Command(resume=approval.model_dump(mode="json")), config=cfg_run)

    if channel is not None:
        orders = result.get("order_results", [])
        channel.push(Notification(
            kind="fill_report", title=f"{thread_id}: {approval.status}",
            body=f"{len(orders)} order(s) processed"))
    return result


def _report(channel, result: dict) -> None:
    orders = result.get("order_results", [])
    approval = result.get("approval")
    status = getattr(approval, "status", None) or (approval or {}).get("status") if approval else "—"
    print("\n" + "=" * 70)
    print(f"CYCLE COMPLETE — approval={status} · orders={len(orders)}")
    try:
        from ..memory import get_store

        perf = get_store().last_performance()
        if perf:
            print(f"Performance: NetLiq ${perf.net_liquidation:,.0f} · "
                  f"daily ${perf.daily_pnl:,.0f} · cumulative ${perf.cumulative_pnl:,.0f}")
    except Exception:  # noqa: BLE001 - reporting only
        pass
    for o in orders:
        sym = getattr(o, "symbol", None) or o.get("symbol")
        act = getattr(o, "action", None) or o.get("action")
        st = getattr(o, "status", None) or o.get("status")
        oid = getattr(o, "order_id", None) or o.get("order_id")
        print(f"  • {act:4} {sym:6} [{st}] order={oid}")
    print("=" * 70)
    if orders:
        channel.push(Notification(kind="fill_report", title="Execution done",
                                  body=f"{len(orders)} order(s) processed"))


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

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("ats").setLevel(logging.INFO)  # our own logs at INFO, third-party quiet


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="ats", description="Multi-agent trading cycle runner")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one trading cycle")
    run.add_argument("--live", action="store_true", help="execute (still IBKR paper); default dry-run")
    run.add_argument("--yes", action="store_true", help="auto-approve (non-interactive)")
    run.add_argument("--offline", action="store_true", help="skip live data + IBKR (local only)")
    run.add_argument("--no-llm", action="store_true", help="skip LLM calls (neutral stub reports)")
    run.add_argument("--channel", choices=["cli", "feishu"], help="override approval channel")
    sub.add_parser("ibkr", help="probe IBKR paper connectivity (account + positions)")
    srv = sub.add_parser("serve", help="run the approval webhook (Feishu callbacks)")
    srv.add_argument("--host", default="0.0.0.0")
    srv.add_argument("--port", type=int, default=8000)
    sch = sub.add_parser("schedule", help="run cycles on a daily NYSE-session cron")
    sch.add_argument("--live", action="store_true", help="execute (IBKR paper); default dry-run")
    sch.add_argument("--now", action="store_true", help="run one cycle immediately, then exit")
    td = sub.add_parser("thetadata", help="probe the local ThetaData terminal (inspect schema)")
    td.add_argument("symbol")
    se = sub.add_parser("sector", help="sector review 行业分析 (review / show / probe)")
    se.add_argument("action", choices=["review", "show", "probe"])
    se.add_argument("name", nargs="?", default="ai_hardware")
    se.add_argument("--no-llm", action="store_true", help="assemble + stub review, no LLM")
    se.add_argument("--offline", action="store_true", help="skip yfinance (store/static only)")
    se.add_argument("--no-report", action="store_true", help="skip the Obsidian report file")
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
    rk = sub.add_parser("risk", help="risk officer 风控 (report / check)")
    rk.add_argument("action", choices=["report", "check"])
    rk.add_argument("symbol", nargs="?", help="check: filter stored decisions by ticker")
    rk.add_argument("--report", action="store_true", help="report: also write an Obsidian file")
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
    tr.add_argument("--dry-run", action="store_true", help="go through approval but place no orders")
    ma = sub.add_parser("macro", help="macro strategist 宏观分析 (review / show / probe)")
    ma.add_argument("action", choices=["review", "show", "probe"])
    ma.add_argument("name", nargs="?", default="macro")
    ma.add_argument("--no-llm", action="store_true", help="assemble + stub review, no LLM")
    ma.add_argument("--offline", action="store_true", help="skip FRED/yfinance/Tavily")
    ma.add_argument("--no-report", action="store_true", help="skip the Obsidian report file")
    pe = sub.add_parser("pead",
                        help="PEAD earnings workflow (prep / score / show / monitor / watch / research)")
    pe.add_argument("action", choices=["prep", "score", "show", "monitor", "watch", "research"])
    pe.add_argument("symbol", nargs="?", help="ticker (omit for `watch` / `research`)")
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

    if args.command == "run":
        channel = get_channel(args.channel) if args.channel else None
        run_cycle(dry_run=not args.live, auto=args.yes, offline=args.offline,
                  use_llm=not args.no_llm, channel=channel)
        return 0
    if args.command == "ibkr":
        return ibkr_probe()
    if args.command == "serve":
        from .server import serve

        serve(host=args.host, port=args.port)
        return 0
    if args.command == "schedule":
        from .scheduler import start

        start(dry_run=not args.live, run_once=args.now)
        return 0
    if args.command == "thetadata":
        return thetadata_probe(args.symbol)
    if args.command == "sector":
        if args.action == "show":
            return sector_show(args.name)
        if args.action == "probe":
            return sector_probe(args.name, live_data=not args.offline)
        run_sector_review(args.name, use_llm=not args.no_llm,
                          live_data=not args.offline, write_report=not args.no_report)
        return 0
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
            return risk_report(write_report=args.report)
        return risk_check(args.symbol)
    if args.command == "trader":
        if args.action == "portfolio":
            return trader_portfolio()
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
