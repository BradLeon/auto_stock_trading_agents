"""Context Memory contracts: trade logs and performance tracking."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

OrderStatus = Literal["pending", "submitted", "filled", "partial", "cancelled", "rejected", "error"]


class TradeLogEntry(BaseModel):
    """One execution record written by the Trader for every order attempt."""

    order_id: str
    cycle_id: str
    symbol: str
    action: str
    qty: float
    order_type: str = "limit"
    limit_price: float | None = None
    avg_fill_price: float | None = None
    status: OrderStatus = "pending"
    submitted_at: datetime | None = None
    filled_at: datetime | None = None
    rationale: str = ""
    error: str = ""


class Fill(BaseModel):
    """One executed fill from IBKR, carrying per-trade realized P&L."""

    exec_id: str
    symbol: str
    side: str = ""                    # BOT | SLD
    shares: float = 0.0
    price: float = 0.0
    time: str = ""
    realized_pnl: float | None = None  # populated on closing fills
    commission: float = 0.0
    order_id: str = ""


class PerformanceRecord(BaseModel):
    """Per-cycle performance snapshot, fed back to the Manager next cycle."""

    cycle_id: str
    as_of: datetime
    account_id: str = ""             # scope P&L series per account (paper vs live)
    net_liquidation: float = 0.0
    daily_pnl: float = 0.0
    cumulative_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    win_rate: float | None = Field(None, ge=0, le=1)
    profit_factor: float | None = None
    max_drawdown: float | None = None
    num_positions: int = 0
    notes: str = ""
