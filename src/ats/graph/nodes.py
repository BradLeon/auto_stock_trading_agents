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

from langgraph.types import Send, interrupt

from ..agents import analysts, manager as manager_agent, risk_validator
from ..config import get_config
from ..schemas.channel import ApprovalRequest
from ..schemas.decision import BossApproval, TradeDecision
from ..schemas.market import MarketSnapshot
from ..schemas.memory import TradeLogEntry
from ..schemas.risk import RiskGuardrails
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
    if state.live_data:
        from ..data import market_data

        snapshots = market_data.fetch_many(state.watchlist)
    else:
        snapshots = {
            t.symbol: MarketSnapshot(ticker=t, as_of=state.as_of, last_price=None)
            for t in state.watchlist
        }
    return {"market_data": snapshots}


# --------------------------------------------------------------------------- #
# Fan-out dispatcher (conditional edge from ingest)
# --------------------------------------------------------------------------- #
def fan_out(state: TradingState) -> list[Send]:
    common = {"as_of": state.as_of, "use_llm": state.use_llm}
    sends: list[Send] = [Send("macro_analyst", {**common})]
    for sector, brief in state.sectors.items():
        sends.append(Send("industry_analyst", {**common, "sector": sector, "brief": brief}))
    for t in state.watchlist:
        snap = state.market_data.get(t.symbol)
        sends.append(Send("fundamental_analyst", {**common, "symbol": t.symbol, "snapshot": snap}))
        sends.append(Send("technical_analyst", {**common, "symbol": t.symbol, "snapshot": snap}))
    return sends


# --------------------------------------------------------------------------- #
# Analysts (Send payload -> report). Delegate to LLM-backed agents; the agents
# fall back to neutral stubs when use_llm is False.
# --------------------------------------------------------------------------- #
def macro_analyst(payload: dict) -> dict:
    return {"macro_report": analysts.macro(payload["as_of"], payload["use_llm"])}


def industry_analyst(payload: dict) -> dict:
    return {"industry_reports": [analysts.industry(
        payload["sector"], payload.get("brief", ""), payload["as_of"], payload["use_llm"])]}


def fundamental_analyst(payload: dict) -> dict:
    return {"fundamental_reports": [analysts.fundamental(
        payload["symbol"], payload.get("snapshot"), payload["as_of"], payload["use_llm"])]}


def technical_analyst(payload: dict) -> dict:
    return {"technical_reports": [analysts.technical(
        payload["symbol"], payload.get("snapshot"), payload["as_of"], payload["use_llm"])]}


# --------------------------------------------------------------------------- #
# Risk manager (join point). STUB: echo config defaults as guardrails.
# --------------------------------------------------------------------------- #
def risk_manager(state: TradingState) -> dict:
    rc = get_config().app.risk
    return {"risk_guardrails": RiskGuardrails(
        as_of=state.as_of,
        max_position_pct=rc.max_position_pct,
        max_sector_pct=rc.max_sector_pct,
        max_gross_leverage=rc.max_gross_leverage,
        max_single_order_usd=rc.max_single_order_usd,
        cash_floor_pct=rc.cash_floor_pct,
        notes="[stub] no live portfolio yet; using config defaults.",
    )}


# --------------------------------------------------------------------------- #
# Manager (synthesis) + deterministic guardrail validator.
# --------------------------------------------------------------------------- #
def manager(state: TradingState) -> dict:
    cfg = get_config()
    gr = state.risk_guardrails
    net_liq = cfg.app.account.net_liquidation_usd

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
    to_execute = approval.effective_decisions(state.decisions)
    results: list[TradeLogEntry] = []
    for d in to_execute:
        if d.action == "hold":
            continue
        snap = state.market_data.get(d.symbol)
        price = snap.last_price if snap else None
        qty = _size_qty(d, price)
        results.append(TradeLogEntry(
            order_id=str(uuid.uuid4())[:8],
            cycle_id=state.cycle_id,
            symbol=d.symbol,
            action=d.action,
            qty=qty,
            order_type=d.order_type,
            limit_price=d.limit_price,
            status="filled" if state.dry_run else "submitted",
            submitted_at=_now(),
            filled_at=_now() if state.dry_run else None,
            rationale=d.rationale,
        ))
    return {"order_results": results}


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
