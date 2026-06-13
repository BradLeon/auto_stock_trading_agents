"""Graph node implementations.

Phase 2 ships STUB bodies that return plausible fake data so the full topology
(ingest → parallel analysts → risk → manager → HITL → trader → persist) runs
end-to-end. Phase 3+ replaces each stub's internals with real data sources and
LLM-backed agents (see agents/). The graph wiring in build.py does not change.

Node input convention:
  * ingest / risk_manager / manager / boss_review / trader / persist receive the
    full `TradingState` (Pydantic instance).
  * analyst nodes are invoked via Send and receive a small dict payload carrying
    just their assignment (symbol/sector + snapshot).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import logging

from langgraph.types import Send, interrupt

from ..agents import analysts, manager as manager_agent, risk_manager as risk_agent, risk_validator
from ..broker import IBKRBroker, IBKRUnavailable
from ..config import get_config

log = logging.getLogger("ats.graph")


def _broker(state: TradingState) -> IBKRBroker:
    return IBKRBroker(sector_by_symbol={t.symbol: t.sector for t in state.watchlist})
from ..schemas.channel import ApprovalRequest
from ..schemas.decision import BossApproval, TradeDecision
from ..schemas.market import MarketSnapshot
from ..schemas.memory import TradeLogEntry
from .state import TradingState


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #
def ingest(state: TradingState) -> dict:
    """Assemble per-ticker market snapshots (yfinance OHLCV + indicators).

    Offline mode (live_data=False) returns empty snapshots so tests stay
    hermetic. Other data sources (fundamentals, macro, news, social) land in
    later milestones; each degrades to None on failure.
    """
    if not state.live_data:
        snapshots = {
            t.symbol: MarketSnapshot(ticker=t, as_of=state.as_of, last_price=None)
            for t in state.watchlist
        }
        return {"market_data": snapshots}

    from ..data import fundamentals as fund_src, macro as macro_src, market_data

    snapshots = market_data.fetch_many(state.watchlist)
    macro_data = macro_src.fetch()
    fundamentals = {t.symbol: fund_src.fetch(t.symbol) for t in state.watchlist}
    return {"market_data": snapshots, "macro_data": macro_data, "fundamentals": fundamentals}


# --------------------------------------------------------------------------- #
# Fan-out dispatcher (conditional edge from ingest)
# --------------------------------------------------------------------------- #
def fan_out(state: TradingState) -> list[Send]:
    common = {"as_of": state.as_of, "use_llm": state.use_llm}
    sends: list[Send] = [Send("macro_analyst", {**common, "macro_data": state.macro_data})]
    for sector, brief in state.sectors.items():
        sends.append(Send("industry_analyst", {**common, "sector": sector, "brief": brief}))
    for t in state.watchlist:
        snap = state.market_data.get(t.symbol)
        fund = state.fundamentals.get(t.symbol)
        sends.append(Send("fundamental_analyst",
                          {**common, "symbol": t.symbol, "snapshot": snap, "fundamentals": fund}))
        sends.append(Send("technical_analyst", {**common, "symbol": t.symbol, "snapshot": snap}))
    return sends


# --------------------------------------------------------------------------- #
# Analysts (Send payload -> report). Delegate to LLM-backed agents; the agents
# fall back to neutral stubs when use_llm is False.
# --------------------------------------------------------------------------- #
def macro_analyst(payload: dict) -> dict:
    return {"macro_report": analysts.macro(
        payload.get("macro_data"), payload["as_of"], payload["use_llm"])}


def industry_analyst(payload: dict) -> dict:
    return {"industry_reports": [analysts.industry(
        payload["sector"], payload.get("brief", ""), payload["as_of"], payload["use_llm"])]}


def fundamental_analyst(payload: dict) -> dict:
    return {"fundamental_reports": [analysts.fundamental(
        payload["symbol"], payload.get("snapshot"), payload.get("fundamentals"),
        payload["as_of"], payload["use_llm"])]}


def technical_analyst(payload: dict) -> dict:
    return {"technical_reports": [analysts.technical(
        payload["symbol"], payload.get("snapshot"), payload["as_of"], payload["use_llm"])]}


# --------------------------------------------------------------------------- #
# Risk manager (join point). STUB: echo config defaults as guardrails.
# --------------------------------------------------------------------------- #
def risk_manager(state: TradingState) -> dict:
    cfg = get_config()
    portfolio = None
    if state.use_broker:
        try:
            portfolio = _broker(state).get_portfolio()
        except IBKRUnavailable as exc:
            log.warning("portfolio read skipped: %s", exc)
    guardrails = risk_agent.assess(
        as_of=state.as_of,
        risk_cfg=cfg.app.risk,
        portfolio=portfolio,
        sector_by_symbol={t.symbol: t.sector for t in state.watchlist},
    )
    return {"risk_guardrails": guardrails, "portfolio": portfolio}


# --------------------------------------------------------------------------- #
# Manager (synthesis) + deterministic guardrail validator.
# --------------------------------------------------------------------------- #
def manager(state: TradingState) -> dict:
    cfg = get_config()
    gr = state.risk_guardrails
    net_liq = cfg.app.account.net_liquidation_usd
    if state.portfolio and state.portfolio.net_liquidation > 0:
        net_liq = state.portfolio.net_liquidation

    proposed, summary = manager_agent.decide(
        as_of=state.as_of,
        macro=state.macro_report,
        industry_reports=state.industry_reports,
        fundamental_reports=state.fundamental_reports,
        technical_reports=state.technical_reports,
        guardrails=gr,
        market_data=state.market_data,
        net_liquidation=net_liq,
        use_llm=state.use_llm,
    )

    sector_by_symbol = {t.symbol: t.sector for t in state.watchlist}
    decisions, adjustments = risk_validator.apply_guardrails(
        proposed, gr, sector_by_symbol=sector_by_symbol, net_liquidation=net_liq,
        portfolio=state.portfolio,
    )
    return {"decisions": decisions, "manager_summary": summary, "risk_adjustments": adjustments}


# --------------------------------------------------------------------------- #
# Boss review (HITL). The graph only does interrupt plumbing; the CLI/Feishu
# runner owns the actual channel interaction and resumes with the verdict.
# --------------------------------------------------------------------------- #
def boss_review(state: TradingState) -> dict:
    request = ApprovalRequest(
        cycle_id=state.cycle_id,
        as_of=state.as_of,
        decisions=state.decisions,
        context_summary=_summarize(state),
    )
    verdict = interrupt(request.model_dump(mode="json"))
    return {"approval": BossApproval.model_validate(verdict)}


def _summarize(state: TradingState) -> str:
    macro = state.macro_report
    lines = []
    if macro:
        lines.append(f"Macro: {macro.signal} (conv {macro.conviction:.2f}) — {macro.thesis}")
    for r in state.fundamental_reports:
        lines.append(f"Fund {r.symbol}: {r.signal} (conv {r.conviction:.2f})")
    if state.risk_guardrails:
        gr = state.risk_guardrails
        lines.append(f"Risk: pos<={gr.max_position_pct:.0%} sector<={gr.max_sector_pct:.0%} "
                     f"order<=${gr.max_single_order_usd:,.0f}")
    if state.manager_summary:
        lines.append(f"\nManager: {state.manager_summary}")
    if state.risk_adjustments:
        lines.append("Guardrail adjustments:")
        lines.extend(f"  - {a}" for a in state.risk_adjustments)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Trader (pure execution). STUB: marks orders filled in dry-run.
# --------------------------------------------------------------------------- #
def trader(state: TradingState) -> dict:
    approval = state.approval
    if approval is None:
        return {"order_results": []}
    to_execute = [d for d in approval.effective_decisions(state.decisions) if d.action != "hold"]
    sized = [(d, _size_qty(d, _price(state, d.symbol))) for d in to_execute]

    # Live path: submit to IBKR paper. Otherwise simulate fills (dry-run).
    if not state.dry_run and state.use_broker:
        try:
            return {"order_results": _broker(state).place_orders(sized, state.cycle_id)}
        except IBKRUnavailable as exc:
            log.warning("execution aborted, IBKR unavailable: %s", exc)
            return {"order_results": [_errored(state, d, qty, str(exc)) for d, qty in sized]}

    return {"order_results": [_simulated(state, d, qty) for d, qty in sized]}


def _price(state: TradingState, symbol: str) -> float | None:
    snap = state.market_data.get(symbol)
    return snap.last_price if snap else None


def _simulated(state: TradingState, d: TradeDecision, qty: float) -> TradeLogEntry:
    return TradeLogEntry(
        order_id=str(uuid.uuid4())[:8], cycle_id=state.cycle_id, symbol=d.symbol,
        action=d.action, qty=qty, order_type=d.order_type, limit_price=d.limit_price,
        status="filled", submitted_at=_now(), filled_at=_now(), rationale=d.rationale,
    )


def _errored(state: TradingState, d: TradeDecision, qty: float, error: str) -> TradeLogEntry:
    return TradeLogEntry(
        order_id="", cycle_id=state.cycle_id, symbol=d.symbol, action=d.action, qty=qty,
        order_type=d.order_type, limit_price=d.limit_price, status="error",
        submitted_at=_now(), error=error, rationale=d.rationale,
    )


def _size_qty(d: TradeDecision, price: float | None) -> float:
    """Translate a decision into a share quantity (paper sizing).

    Real IBKR order placement lands in a later phase; this converts notional ->
    shares using the last price so the log carries a concrete size.
    """
    if d.qty:
        return d.qty
    if d.notional_usd and price:
        return float(round(d.notional_usd / price))
    return 0.0


# --------------------------------------------------------------------------- #
# Persist (Context Memory write). STUB: no-op.
# --------------------------------------------------------------------------- #
def persist(state: TradingState) -> dict:
    # Phase 8 writes reports/decisions/trade_log/performance to memory here.
    return {}
