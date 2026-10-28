"""Deterministic guardrail review: boundaries are reported, never rewritten.

The old `apply_guardrails` (silently clipped, dropped, injected and scaled
proposals) was retired and deleted on 2026-10-27; only the read-only review
remains. Position-aware checks (current sector weights) activate once a live
IBKR portfolio is wired in; until then sector concentration is checked on the
*proposed* buys only (best-effort).
"""

from __future__ import annotations

from ..schemas.decision import TradeDecision
from ..schemas.portfolio import PortfolioSnapshot
from ..schemas.risk import RiskGuardrails

_BUY_ACTIONS = {"buy", "add"}


def review_guardrails(
    decisions: list[TradeDecision],
    guardrails: RiskGuardrails,
    *,
    sector_by_symbol: dict[str, str],
    net_liquidation: float,
    portfolio: PortfolioSnapshot | None = None,
) -> tuple[list[TradeDecision], list[str]]:
    """Read-only review of a recommendation batch against the L1/L2 guardrails.

    Phase B (design D7): six checks expressed as BOUNDARY notes instead of
    silent rewrites — the input decisions are
    returned unmodified, nothing is dropped, injected or scaled. The PEAD
    recommendation path uses this: recommendations are proposals, and the
    Chief's own graph runs the full decision-level review downstream, so a
    clipped recommendation here would just hide the boundary it hit.
    """
    notes: list[str] = []
    gr = guardrails
    for d in decisions:
        if d.action in _BUY_ACTIONS and d.symbol in gr.no_add_list:
            notes.append(f"BOUNDARY DROP {d.action} {d.symbol}: on do-not-add list")
        if d.notional_usd and d.notional_usd > gr.max_single_order_usd:
            notes.append(f"BOUNDARY {d.symbol}: order ${d.notional_usd:,.0f} "
                         f"exceeds per-order cap ${gr.max_single_order_usd:,.0f}")
        if d.target_weight and d.target_weight > gr.max_position_pct:
            notes.append(f"BOUNDARY {d.symbol}: weight {d.target_weight:.0%} "
                         f"exceeds per-name cap {gr.max_position_pct:.0%}")

    # Forced trims: recommended, never injected as synthetic decisions.
    present = {d.symbol for d in decisions}
    for sym in gr.forced_trim:
        if sym not in present:
            notes.append(f"BOUNDARY trim {sym}: forced by risk (建议补一笔 trim)")

    cap = gr.max_sector_pct * net_liquidation
    by_sector: dict[str, float] = {}
    for d in decisions:
        if d.action in _BUY_ACTIONS and d.notional_usd:
            key = sector_by_symbol.get(d.symbol, "unknown")
            by_sector[key] = by_sector.get(key, 0.0) + d.notional_usd
    for sector, total in by_sector.items():
        if total > cap > 0:
            notes.append(f"BOUNDARY sector {sector}: ${total:,.0f} exceeds "
                         f"sector cap ${cap:,.0f}")

    deployable = net_liquidation * min(gr.max_gross_leverage, 1.0 - gr.cash_floor_pct)
    total = sum(d.notional_usd for d in decisions
                if d.action in _BUY_ACTIONS and d.notional_usd)
    if total > deployable > 0:
        notes.append(f"BOUNDARY book: buys ${total:,.0f} exceed deployable "
                     f"${deployable:,.0f} (cash floor/leverage)")
    return decisions, notes
