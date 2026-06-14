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
             channel: str = "cli") -> dict:
    """Run one PEAD phase (prep | score). Score pauses at HITL approval.

    channel="cli": synchronous terminal approval. channel="feishu": async — the
    score run checkpoints, sends a card, and exits; `ats serve` resumes the
    `pead:<sym>:<fiscal>` thread on the Boss's tap. The deterministic risk clip
    runs BEFORE the interrupt, so trades pass risk before approval is requested.
    """
    from ..config import load_pead_config
    from ..graph.pead import build_pead_graph
    from ..graph.pead_state import PeadState

    sym = symbol.upper()
    is_async = phase == "score" and channel in ("feishu", "feishu_bot")
    ch = get_channel(channel) if channel in ("feishu", "feishu_bot") else CLIChannel(auto=auto)
    app = build_pead_graph(checkpointer=get_checkpointer(persist=is_async))
    now = datetime.now(timezone.utc)
    fiscal = load_pead_config(sym).fiscal_label
    state = PeadState(symbol=sym, phase=phase, as_of=now, dry_run=dry_run, use_llm=use_llm,
                      use_broker=not offline, live_data=not offline, transcript_source=transcript)
    thread_id = (f"pead:{sym}:{fiscal}".replace(" ", "") if is_async
                 else f"pead-{sym}-{phase}-{now:%Y%m%d%H%M%S}")
    cfg_run = {"configurable": {"thread_id": thread_id}}
    print(f"▶ PEAD {phase} {sym}")

    result = app.invoke(state, config=cfg_run)

    if "__interrupt__" in result and is_async:
        req = ApprovalRequest.model_validate(result["__interrupt__"][0].value)
        ch.send_approval_request(req, thread_id=thread_id)
        print(f"⏸ {thread_id} awaiting Feishu approval. Run `ats serve` to handle the callback.")
        return result

    while "__interrupt__" in result:
        req = ApprovalRequest.model_validate(result["__interrupt__"][0].value)
        ch.push(Notification(kind="approval_request", title=f"PEAD {sym} decision",
                             body=req.context_summary))
        approval = ch.request_approval(req)
        result = app.invoke(Command(resume=approval.model_dump(mode="json")), config=cfg_run)

    _pead_report(sym, phase, result)
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
        orders = result.get("order_results", [])
        if sc:
            print(f"PEAD SCORE COMPLETE — {symbol}  Scorecard {sc.total:+.2f} "
                  f"(门槛 {sc.threshold:+.1f}) — {sc.band}")
        print(f"决策情景: {result.get('decision_band', '—')} · orders={len(orders)}")
        for o in orders:
            print(f"  • {o.action} {o.symbol} {o.qty:.0f} [{o.status}]")
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
    pe = sub.add_parser("pead", help="PEAD earnings workflow (prep / score / show / monitor / watch)")
    pe.add_argument("action", choices=["prep", "score", "show", "monitor", "watch"])
    pe.add_argument("symbol", nargs="?", help="ticker (omit for `watch`)")
    pe.add_argument("--transcript", help="path or URL to the earnings-call transcript (score)")
    pe.add_argument("--live", action="store_true", help="execute (IBKR paper); default dry-run")
    pe.add_argument("--yes", action="store_true", help="auto-approve (non-interactive)")
    pe.add_argument("--offline", action="store_true", help="skip live data + IBKR (local only)")
    pe.add_argument("--no-llm", action="store_true", help="skip LLM (stub agents)")
    pe.add_argument("--channel", choices=["cli", "feishu", "feishu_bot"], default="cli",
                    help="score approval channel (feishu/feishu_bot = async via webhook)")
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
    if args.command == "pead":
        if args.action == "watch":
            run_pead_watch(use_llm=not args.no_llm)
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
                 channel=args.channel)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
