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


def _initial_state(cfg, *, dry_run: bool, live_data: bool, use_llm: bool) -> TradingState:
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
    app = build_graph(checkpointer=get_checkpointer(persist=False))

    state = _initial_state(cfg, dry_run=dry_run, live_data=not offline, use_llm=use_llm)
    cfg_run = {"configurable": {"thread_id": state.cycle_id}}
    print(f"▶ running {state.cycle_id} (dry_run={dry_run}) over {[t.symbol for t in state.watchlist]}")

    result = app.invoke(state, config=cfg_run)

    # Drive HITL interrupts until the graph completes.
    while "__interrupt__" in result:
        req = ApprovalRequest.model_validate(result["__interrupt__"][0].value)
        channel.push(Notification(kind="approval_request", title="Decisions pending review",
                                  body=f"{len(req.decisions)} proposed trade(s)"))
        approval = channel.request_approval(req)
        result = app.invoke(Command(resume=approval.model_dump(mode="json")), config=cfg_run)

    _report(channel, result)
    return result


def _report(channel, result: dict) -> None:
    orders = result.get("order_results", [])
    approval = result.get("approval")
    status = getattr(approval, "status", None) or (approval or {}).get("status") if approval else "—"
    print("\n" + "=" * 70)
    print(f"CYCLE COMPLETE — approval={status} · orders={len(orders)}")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ats", description="Multi-agent trading cycle runner")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one trading cycle")
    run.add_argument("--live", action="store_true", help="execute (still IBKR paper); default dry-run")
    run.add_argument("--yes", action="store_true", help="auto-approve (non-interactive)")
    run.add_argument("--offline", action="store_true", help="skip live data fetch (stub snapshots)")
    run.add_argument("--no-llm", action="store_true", help="skip LLM calls (neutral stub reports)")
    args = parser.parse_args(argv)

    if args.command == "run":
        run_cycle(dry_run=not args.live, auto=args.yes, offline=args.offline, use_llm=not args.no_llm)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
