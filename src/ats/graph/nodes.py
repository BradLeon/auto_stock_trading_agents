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

from ..config import get_config
from ..schemas.channel import ApprovalRequest
from ..schemas.decision import BossApproval, TradeDecision
from ..schemas.market import MarketSnapshot
from ..schemas.memory import TradeLogEntry
from ..schemas.reports import (
    FundamentalReport,
    IndustryReport,
    MacroReport,
    TechnicalReport,
)
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
    sends: list[Send] = [Send("macro_analyst", {"as_of": state.as_of})]
    for sector, brief in state.sectors.items():
        sends.append(Send("industry_analyst", {"sector": sector, "brief": brief, "as_of": state.as_of}))
    for t in state.watchlist:
        snap = state.market_data.get(t.symbol)
        sends.append(Send("fundamental_analyst", {"symbol": t.symbol, "snapshot": snap, "as_of": state.as_of}))
        sends.append(Send("technical_analyst", {"symbol": t.symbol, "snapshot": snap, "as_of": state.as_of}))
    return sends


# --------------------------------------------------------------------------- #
# Analysts (Send payload -> report). STUBs.
# --------------------------------------------------------------------------- #
def macro_analyst(payload: dict) -> dict:
    return {"macro_report": MacroReport(
        as_of=payload["as_of"], signal="neutral", conviction=0.4,
        thesis="[stub] balanced regime; rates steady, VIX moderate.",
        market_breadth="[stub]", vix=15.0, fear_greed=55,
    )}


def industry_analyst(payload: dict) -> dict:
    return {"industry_reports": [IndustryReport(
        as_of=payload["as_of"], sector=payload["sector"], signal="bullish", conviction=0.5,
        thesis=f"[stub] {payload['sector']} demand healthy.",
        supply_chain_notes=payload.get("brief", ""),
    )]}


def fundamental_analyst(payload: dict) -> dict:
    return {"fundamental_reports": [FundamentalReport(
        as_of=payload["as_of"], symbol=payload["symbol"], signal="bullish", conviction=0.6,
        thesis=f"[stub] {payload['symbol']} fundamentals solid.",
    )]}


def technical_analyst(payload: dict) -> dict:
    return {"technical_reports": [TechnicalReport(
        as_of=payload["as_of"], symbol=payload["symbol"], signal="neutral", conviction=0.45,
        thesis=f"[stub] {payload['symbol']} consolidating.", trend="sideways",
    )]}


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
# Manager (synthesis). STUB: small buy for bullish-fundamental names.
# --------------------------------------------------------------------------- #
def manager(state: TradingState) -> dict:
    gr = state.risk_guardrails
    cap = gr.max_single_order_usd if gr else 25000
    bullish = {r.symbol for r in state.fundamental_reports if r.signal == "bullish"}
    decisions: list[TradeDecision] = []
    for t in state.watchlist:
        if t.symbol in bullish:
            decisions.append(TradeDecision(
                symbol=t.symbol, action="buy", notional_usd=min(10000.0, cap),
                order_type="limit", conviction=0.6,
                rationale="[stub] bullish fundamentals within guardrails.",
            ))
    return {"decisions": decisions}


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
        results.append(TradeLogEntry(
            order_id=str(uuid.uuid4())[:8],
            cycle_id=state.cycle_id,
            symbol=d.symbol,
            action=d.action,
            qty=d.qty or 0.0,
            order_type=d.order_type,
            limit_price=d.limit_price,
            status="filled" if state.dry_run else "submitted",
            submitted_at=_now(),
            filled_at=_now() if state.dry_run else None,
            rationale=d.rationale,
        ))
    return {"order_results": results}


# --------------------------------------------------------------------------- #
# Persist (Context Memory write). STUB: no-op.
# --------------------------------------------------------------------------- #
def persist(state: TradingState) -> dict:
    # Phase 8 writes reports/decisions/trade_log/performance to memory here.
    return {}
