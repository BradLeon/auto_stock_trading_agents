"""Portfolio contracts sourced from IBKR (read by the risk manager)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Position(BaseModel):
    symbol: str
    sector: str = "unknown"
    qty: float
    avg_cost: float
    market_price: float
    market_value: float
    unrealized_pnl: float = 0.0
    weight: float = Field(0.0, description="market_value / net_liquidation")


class ExposureBreakdown(BaseModel):
    by_sector: dict[str, float] = Field(default_factory=dict)   # sector -> weight
    by_ticker: dict[str, float] = Field(default_factory=dict)   # symbol -> weight


class PortfolioSnapshot(BaseModel):
    as_of: datetime
    account_id: str = ""
    net_liquidation: float = 0.0
    cash: float = 0.0
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    leverage: float = 0.0
    daily_pnl: float = 0.0            # account-level, from IBKR reqPnL
    realized_pnl: float = 0.0
    positions: list[Position] = Field(default_factory=list)
    exposure: ExposureBreakdown = Field(default_factory=ExposureBreakdown)
