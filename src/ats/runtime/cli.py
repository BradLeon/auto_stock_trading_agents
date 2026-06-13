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


def resume_cycle(thread_id: str, approval, channel=None) -> dict:
    """Resume a checkpointed cycle with the Boss verdict (called by the webhook)."""
    from datetime import datetime, timezone

    if approval.reviewed_at is None:
        approval.reviewed_at = datetime.now(timezone.utc)
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


def main(argv: list[str] | None = None) -> int:
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
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
