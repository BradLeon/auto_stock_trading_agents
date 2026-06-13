"""Market data contracts produced by the ingest layer."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class Ticker(BaseModel):
    """An instrument in the tradable universe."""

    symbol: str
    name: str = ""
    sector: str = "unknown"
    exchange: str = "NASDAQ"


class OHLCV(BaseModel):
    """A single daily bar."""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketSnapshot(BaseModel):
    """Per-ticker market state assembled by the ingest layer for a cycle."""

    ticker: Ticker
    as_of: datetime
    last_price: float | None = None
    history: list[OHLCV] = Field(default_factory=list)
    # Derived technical indicators (sma_50, rsi_14, macd, ...) keyed by name.
    indicators: dict[str, float] = Field(default_factory=dict)
