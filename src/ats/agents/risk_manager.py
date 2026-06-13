"""Risk manager: turn config thresholds + the live portfolio into guardrails.

Deterministic by design — guardrails are safety-critical, so we do not delegate
them to an LLM. When a portfolio is present we tighten further: names already at
the position cap become forced trims; sectors already at the cap go on the
do-not-add list. With no portfolio we return the plain config thresholds.
"""

from __future__ import annotations

from datetime import datetime

from ..config import RiskConfig
from ..schemas.portfolio import PortfolioSnapshot
from ..schemas.risk import RiskGuardrails


def assess(
    *,
    as_of: datetime,
    risk_cfg: RiskConfig,
    portfolio: PortfolioSnapshot | None,
    sector_by_symbol: dict[str, str],
) -> RiskGuardrails:
    gr = RiskGuardrails(
        as_of=as_of,
        max_position_pct=risk_cfg.max_position_pct,
        max_sector_pct=risk_cfg.max_sector_pct,
        max_gross_leverage=risk_cfg.max_gross_leverage,
        max_single_order_usd=risk_cfg.max_single_order_usd,
        cash_floor_pct=risk_cfg.cash_floor_pct,
    )
    if portfolio is None:
        gr.notes = "No live portfolio; using config thresholds."
        return gr

    # Names over the position cap -> must trim.
    over_position = [p.symbol for p in portfolio.positions if p.weight > gr.max_position_pct]
    # Sectors at/over the cap -> cannot add to any name in them.
    hot_sectors = {s for s, w in portfolio.exposure.by_sector.items() if w >= gr.max_sector_pct}
    no_add = sorted({sym for sym, sec in sector_by_symbol.items() if sec in hot_sectors}
                    | set(over_position))

    gr.forced_trim = sorted(over_position)
    gr.no_add_list = no_add
    gr.notes = (
        f"NetLiq ${portfolio.net_liquidation:,.0f}, leverage {portfolio.leverage:.2f}x, "
        f"{len(portfolio.positions)} positions. "
        f"Hot sectors: {sorted(hot_sectors) or 'none'}. "
        f"Over-cap names: {over_position or 'none'}."
    )
    return gr
