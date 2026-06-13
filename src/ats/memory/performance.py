"""Per-cycle performance computation.

PnL is meaningful once IBKR provides a live net-liquidation; with no portfolio we
still record the cycle (carrying the prior book value forward, zero daily PnL) so
the timeline is continuous.
"""

from __future__ import annotations

from datetime import datetime

from ..schemas.memory import PerformanceRecord, TradeLogEntry
from ..schemas.portfolio import PortfolioSnapshot


def compute(
    *,
    cycle_id: str,
    as_of: datetime,
    portfolio: PortfolioSnapshot | None,
    previous: PerformanceRecord | None,
    order_results: list[TradeLogEntry],
    fallback_net_liq: float,
) -> PerformanceRecord:
    if portfolio and portfolio.net_liquidation > 0:
        net_liq = portfolio.net_liquidation
        unrealized = sum(p.unrealized_pnl for p in portfolio.positions)
        num_positions = len(portfolio.positions)
    else:
        net_liq = previous.net_liquidation if previous else fallback_net_liq
        unrealized = 0.0
        num_positions = 0

    prev_net = previous.net_liquidation if previous else net_liq
    prev_cum = previous.cumulative_pnl if previous else 0.0
    daily_pnl = net_liq - prev_net

    return PerformanceRecord(
        cycle_id=cycle_id,
        as_of=as_of,
        net_liquidation=net_liq,
        daily_pnl=daily_pnl,
        cumulative_pnl=prev_cum + daily_pnl,
        unrealized_pnl=unrealized,
        num_positions=num_positions,
        notes=f"{sum(1 for t in order_results if t.status in ('filled', 'submitted'))} orders this cycle",
    )
