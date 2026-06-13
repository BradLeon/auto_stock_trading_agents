"""Manager agent: synthesize all analyst reports + risk guardrails into trades.

The LLM proposes decisions; a deterministic validator (risk_validator) then hard
-clips them against the guardrails. The Manager is never trusted to self-comply.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..schemas.decision import TradeDecision
from ..schemas.market import MarketSnapshot
from ..schemas.reports import (
    FundamentalReport,
    IndustryReport,
    MacroReport,
    TechnicalReport,
)
from ..schemas.risk import RiskGuardrails
from .base import run_structured
from .outputs import DecisionView, ManagerOutput

log = logging.getLogger("ats.agents")


def _fmt_guardrails(gr: RiskGuardrails) -> str:
    return (
        f"- max single position: {gr.max_position_pct:.0%} of book\n"
        f"- max sector exposure: {gr.max_sector_pct:.0%}\n"
        f"- max gross leverage: {gr.max_gross_leverage:.2f}x\n"
        f"- max single order: ${gr.max_single_order_usd:,.0f}\n"
        f"- cash floor: {gr.cash_floor_pct:.0%}\n"
        f"- do-not-add: {gr.no_add_list or 'none'}\n"
        f"- forced trim: {gr.forced_trim or 'none'}"
    )


def _fmt_ticker_block(
    symbol: str,
    fundamentals: dict[str, FundamentalReport],
    technicals: dict[str, TechnicalReport],
    market: dict[str, MarketSnapshot],
) -> str:
    f = fundamentals.get(symbol)
    t = technicals.get(symbol)
    snap = market.get(symbol)
    price = f"{snap.last_price:.2f}" if snap and snap.last_price else "n/a"
    lines = [f"### {symbol} (last {price})"]
    if f:
        lines.append(f"  Fundamental: {f.signal} (conv {f.conviction:.2f}) — {f.thesis}")
    if t:
        lvl = f" | support {t.support} resistance {t.resistance}" if t.support else ""
        lines.append(f"  Technical: {t.signal} (conv {t.conviction:.2f}) — {t.trend}{lvl}")
    return "\n".join(lines)


def _build_context(
    *,
    macro: MacroReport | None,
    industry_reports: list[IndustryReport],
    fundamental_reports: list[FundamentalReport],
    technical_reports: list[TechnicalReport],
    guardrails: RiskGuardrails,
    market_data: dict[str, MarketSnapshot],
    net_liquidation: float,
) -> str:
    f_by = {r.symbol: r for r in fundamental_reports}
    t_by = {r.symbol: r for r in technical_reports}
    symbols = sorted(set(f_by) | set(t_by) | set(market_data))

    parts = [f"Book size (net liquidation): ${net_liquidation:,.0f}", ""]
    if macro:
        parts.append(f"## Macro regime: {macro.signal} (conv {macro.conviction:.2f})\n{macro.thesis}")
    if industry_reports:
        parts.append("## Industry")
        for r in industry_reports:
            parts.append(f"- {r.sector}: {r.signal} (conv {r.conviction:.2f}) — {r.thesis}")
    parts.append("\n## Names")
    for s in symbols:
        parts.append(_fmt_ticker_block(s, f_by, t_by, market_data))
    parts.append("\n## Risk guardrails (hard limits; a validator enforces these after you)")
    parts.append(_fmt_guardrails(guardrails))
    parts.append(
        "\nPropose trades for a swing/position horizon. Size by conviction and "
        "confluence of macro + fundamental + technical. Prefer limit orders near "
        "technical support for buys. Only act where there is a clear edge; otherwise "
        "hold. Stay within the guardrails — proposals that violate them will be clipped."
    )
    return "\n".join(parts)


def _stub_decisions(fundamental_reports: list[FundamentalReport], cap: float) -> list[TradeDecision]:
    """Deterministic offline/no-LLM path: small buy per bullish fundamental name."""
    out = []
    for r in fundamental_reports:
        if r.signal == "bullish":
            out.append(TradeDecision(
                symbol=r.symbol, action="buy", notional_usd=min(10000.0, cap),
                order_type="limit", conviction=0.6,
                rationale="[stub] bullish fundamentals within guardrails.",
            ))
    return out


def decide(
    *,
    as_of: datetime,
    macro: MacroReport | None,
    industry_reports: list[IndustryReport],
    fundamental_reports: list[FundamentalReport],
    technical_reports: list[TechnicalReport],
    guardrails: RiskGuardrails,
    market_data: dict[str, MarketSnapshot],
    net_liquidation: float,
    use_llm: bool,
    feedback: str = "",
) -> tuple[list[TradeDecision], str]:
    """Return (proposed decisions, manager summary). Pre-validation."""
    if not use_llm:
        return _stub_decisions(fundamental_reports, guardrails.max_single_order_usd), "[stub] manager"

    ctx = _build_context(
        macro=macro, industry_reports=industry_reports,
        fundamental_reports=fundamental_reports, technical_reports=technical_reports,
        guardrails=guardrails, market_data=market_data, net_liquidation=net_liquidation,
    )
    if feedback:
        ctx = f"## Track record (most recent)\n{feedback}\n\n{ctx}"
    try:
        out: ManagerOutput = run_structured("manager", ManagerOutput, ctx, skill_slug="manager")
    except Exception as exc:  # noqa: BLE001 - degrade to no-trade rather than abort
        log.warning("manager failed: %s", exc)
        return [], "[fallback] manager unavailable; no trades proposed."

    decisions = [_to_decision(d) for d in out.decisions if d.action != "hold"]
    return decisions, out.summary


def _to_decision(d: DecisionView) -> TradeDecision:
    return TradeDecision(
        symbol=d.symbol,
        action=d.action,
        target_weight=d.target_weight,
        notional_usd=d.notional_usd,
        order_type=d.order_type,
        limit_price=d.limit_price,
        conviction=max(0.0, min(1.0, float(d.conviction))),
        rationale=d.rationale,
    )
