"""Deterministic guardrail enforcement.

Runs AFTER the Manager LLM. It only ever tightens — clipping sizes, dropping
disallowed adds, injecting forced trims, and scaling the book back under the
cash floor / leverage cap. Every adjustment is recorded for the Boss to see.

Position-aware checks (current sector weights) activate once a live IBKR
portfolio is wired in; until then sector concentration is enforced on the
*proposed* buys only (best-effort).
"""

from __future__ import annotations

from ..schemas.decision import TradeDecision
from ..schemas.portfolio import PortfolioSnapshot
from ..schemas.risk import RiskGuardrails

_BUY_ACTIONS = {"buy", "add"}
_SELL_ACTIONS = {"trim", "sell"}


def apply_guardrails(
    decisions: list[TradeDecision],
    guardrails: RiskGuardrails,
    *,
    sector_by_symbol: dict[str, str],
    net_liquidation: float,
    portfolio: PortfolioSnapshot | None = None,
) -> tuple[list[TradeDecision], list[str]]:
    notes: list[str] = []
    gr = guardrails
    out: list[TradeDecision] = []

    held = {p.symbol for p in portfolio.positions} if portfolio else set()

    for d in decisions:
        d = d.model_copy(deep=True)

        # 1) do-not-add list: drop buys/adds entirely.
        if d.action in _BUY_ACTIONS and d.symbol in gr.no_add_list:
            notes.append(f"DROP {d.action} {d.symbol}: on do-not-add list")
            continue

        # 2) per-order notional cap.
        if d.notional_usd and d.notional_usd > gr.max_single_order_usd:
            notes.append(f"CLIP {d.symbol}: order ${d.notional_usd:,.0f} -> ${gr.max_single_order_usd:,.0f}")
            d.notional_usd = gr.max_single_order_usd

        # 3) per-name weight cap.
        if d.target_weight and d.target_weight > gr.max_position_pct:
            notes.append(f"CLIP {d.symbol}: weight {d.target_weight:.0%} -> {gr.max_position_pct:.0%}")
            d.target_weight = gr.max_position_pct
        # If weight given but no notional, derive a notional from the (capped) weight.
        if d.action in _BUY_ACTIONS and d.notional_usd is None and d.target_weight:
            d.notional_usd = min(d.target_weight * net_liquidation, gr.max_single_order_usd)

        out.append(d)

    # 4) forced trims: ensure each appears as a sell/trim.
    present = {d.symbol for d in out}
    for sym in gr.forced_trim:
        if sym not in present:
            out.append(TradeDecision(symbol=sym, action="trim", order_type="market",
                                     conviction=1.0, rationale="[guardrail] forced trim"))
            notes.append(f"INJECT trim {sym}: forced by risk")

    # 5) sector concentration on proposed buys (best-effort without live portfolio).
    out, sector_notes = _enforce_sector_cap(out, gr, sector_by_symbol, net_liquidation)
    notes.extend(sector_notes)

    # 6) cash floor / gross leverage: scale all buys down proportionally if the
    #    deployed notional exceeds the deployable budget.
    out, budget_notes = _enforce_budget(out, gr, net_liquidation)
    notes.extend(budget_notes)

    return out, notes


def _enforce_sector_cap(decisions, gr, sector_by_symbol, net_liq):
    notes: list[str] = []
    cap = gr.max_sector_pct * net_liq
    by_sector: dict[str, float] = {}
    for d in decisions:
        if d.action in _BUY_ACTIONS and d.notional_usd:
            by_sector.setdefault(sector_by_symbol.get(d.symbol, "unknown"), 0.0)
            by_sector[sector_by_symbol.get(d.symbol, "unknown")] += d.notional_usd

    for sector, total in by_sector.items():
        if total > cap and total > 0:
            scale = cap / total
            notes.append(f"SCALE sector {sector}: ${total:,.0f} -> ${cap:,.0f} (x{scale:.2f})")
            for d in decisions:
                if d.action in _BUY_ACTIONS and d.notional_usd and \
                        sector_by_symbol.get(d.symbol, "unknown") == sector:
                    d.notional_usd *= scale
    return decisions, notes


def _enforce_budget(decisions, gr, net_liq):
    notes: list[str] = []
    deployable = net_liq * min(gr.max_gross_leverage, 1.0 - gr.cash_floor_pct)
    buys = [d for d in decisions if d.action in _BUY_ACTIONS and d.notional_usd]
    total = sum(d.notional_usd for d in buys)
    if total > deployable and total > 0:
        scale = deployable / total
        notes.append(f"SCALE book: buys ${total:,.0f} -> ${deployable:,.0f} (cash floor/leverage, x{scale:.2f})")
        for d in buys:
            d.notional_usd *= scale
    return decisions, notes
