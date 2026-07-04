"""Approval-gated order execution. ANY order to the broker requires human
confirmation; every attempt persists its full context. Deterministic, no LLM.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..broker import IBKRBroker, IBKRUnavailable
from ..memory import get_store
from ..schemas.channel import ApprovalRequest
from ..schemas.decision import TradeDecision
from ..schemas.memory import TradeLogEntry

log = logging.getLogger("ats.trader.execute")

_LIVE_PORTS = {7496, 4001}   # TWS/Gateway live; 7497/4002 are paper


def _now() -> datetime:
    return datetime.now(timezone.utc)


def execute(decisions: list[TradeDecision], *, source: str, channel: str = "cli",
            dry_run: bool = False, auto: bool = False) -> list[TradeLogEntry]:
    """Size, request human approval, place approved orders, persist with context."""
    from ..channel import get_channel
    from ..config import get_config

    decisions = [d for d in decisions if d.action != "hold"]
    if not decisions:
        print("(no actionable decisions)")
        return []

    secrets = get_config().secrets
    env = get_config().app.environment
    is_live = secrets.ibkr_port in _LIVE_PORTS or env == "live"
    banner = (f"account={secrets.ibkr_account or '(default)'} @ {secrets.ibkr_host}:{secrets.ibkr_port} "
              f"[{'⚠️ LIVE ACCOUNT' if is_live else 'paper'}]")
    sized = [(d, _size(d)) for d in decisions]
    lines = "\n".join(f"  {d.action.upper()} {d.symbol} x{q:.0f} "
                      f"{'@ ' + str(d.limit_price) if d.limit_price else '(mkt)'} — {d.rationale[:60]}"
                      for d, q in sized)
    summary = f"{banner}\nsource={source}\nOrders:\n{lines}"
    if is_live:
        summary = "⚠️⚠️ 实盘账户，请务必确认 ⚠️⚠️\n" + summary

    req = ApprovalRequest(cycle_id=f"trader-{_now():%Y%m%d%H%M%S}", as_of=_now(),
                          decisions=decisions, context_summary=summary)
    approval = get_channel(channel if not auto else "cli").request_approval(req) if not auto \
        else _auto_approve(req)

    approved = approval.effective_decisions(decisions)
    approved_syms = {d.symbol for d in approved}
    context = json.dumps({
        "source": source, "approval_status": approval.status,
        "reviewer": getattr(approval, "reviewer", ""),
        "decisions": [d.model_dump(mode="json") for d in decisions]}, ensure_ascii=False)

    if dry_run or not approved:
        print(f"→ {approval.status}: no orders placed (dry_run={dry_run})")
        entries = [TradeLogEntry(order_id="", cycle_id=req.cycle_id, symbol=d.symbol,
                                 action=d.action, qty=q, order_type=d.order_type,
                                 limit_price=d.limit_price, status="cancelled",
                                 submitted_at=_now(), rationale=d.rationale,
                                 error=f"not executed ({approval.status})")
                   for d, q in sized]
        get_store().save_trades(entries, cycle_id=req.cycle_id, source=source, context=context)
        return entries

    to_place = [(d, q) for d, q in sized if d.symbol in approved_syms and q > 0]
    try:
        broker = IBKRBroker()
        entries = broker.place_orders(to_place, req.cycle_id)
        fills = broker.get_fills()
    except IBKRUnavailable as exc:
        print(f"❌ IBKR unavailable: {exc}")
        entries = [TradeLogEntry(order_id="", cycle_id=req.cycle_id, symbol=d.symbol,
                                 action=d.action, qty=q, status="error", submitted_at=_now(),
                                 rationale=d.rationale, error=str(exc)) for d, q in to_place]
        get_store().save_trades(entries, cycle_id=req.cycle_id, source=source, context=context)
        return entries

    get_store().save_trades(entries, cycle_id=req.cycle_id, source=source, context=context)
    get_store().upsert_fills(fills)
    for e in entries:
        print(f"   {e.action} {e.symbol} x{e.qty:.0f} [{e.status}]"
              + (f" @ {e.avg_fill_price}" if e.avg_fill_price else ""))
    return entries


def manual(symbol: str, action: str, qty: float, *, order_type: str = "limit",
           limit_price: float | None = None, channel: str = "cli", dry_run: bool = False,
           auto: bool = False) -> list[TradeLogEntry]:
    d = TradeDecision(symbol=symbol.upper(), action=action, qty=qty, order_type=order_type,
                      limit_price=limit_price, rationale="manual order")
    return execute([d], source="manual", channel=channel, dry_run=dry_run, auto=auto)


def _size(d: TradeDecision) -> float:
    if d.qty:
        return float(abs(d.qty))
    if d.notional_usd:
        px = _last_price(d.symbol)
        if px:
            return float(round(d.notional_usd / px))
    return 0.0


def _last_price(symbol: str) -> float | None:
    try:
        from ..data import market_data
        from ..schemas.market import Ticker

        snap = market_data.fetch_snapshot(Ticker(symbol=symbol))
        return snap.history[-1].close if snap.history else None
    except Exception:  # noqa: BLE001
        return None


def _auto_approve(req: ApprovalRequest):
    from ..schemas.decision import BossApproval

    return BossApproval(status="approved", reviewer="auto", reviewed_at=_now())
