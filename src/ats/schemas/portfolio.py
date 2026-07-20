"""Portfolio contracts sourced from IBKR (read by the risk manager)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Position(BaseModel):
    symbol: str
    sector: str = "unknown"
    sec_type: str = "STK"            # IBKR secType: STK | OPT | FUT | …
    qty: float
    avg_cost: float
    market_price: float
    market_value: float
    unrealized_pnl: float = 0.0
    weight: float = Field(0.0, description="market_value / net_liquidation")
    beta: float | None = None

    # --- option contract fields (secType=OPT only; None for equities) --------
    strike: float | None = None
    right: str | None = None                 # 'C' | 'P'
    expiry: str | None = None                # YYYYMMDD (IBKR lastTradeDateOrContractMonth)
    multiplier: float | None = None          # shares per contract (usually 100)
    underlying: str | None = None            # underlying symbol (= symbol for IBKR OPT)
    # --- IBKR-supplied model greeks (per contract; None → BSM fallback) ------
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None
    iv: float | None = None                  # implied vol (decimal, e.g. 0.45)
    underlying_price: float | None = None     # spot from IBKR modelGreeks.undPrice
    greeks_source: str | None = None         # 'ibkr' | 'bsm' | None


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
    # --- IBKR-authoritative margin (None → Reg-T estimation fallback in assess) --
    init_margin: float | None = None
    maint_margin: float | None = None
    excess_liquidity: float | None = None
    buying_power: float | None = None
    available_funds: float | None = None
    margin_source: str | None = None  # 'ibkr' | 'regt_est' | None
