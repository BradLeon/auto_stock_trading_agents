"""Analyst report contracts. LLM nodes are forced to emit these via structured output."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Signal = Literal["bullish", "neutral", "bearish"]


class BaseReport(BaseModel):
    """Common fields shared by every analyst report."""

    author_role: str
    as_of: datetime
    signal: Signal = "neutral"
    conviction: float = Field(0.0, ge=0.0, le=1.0, description="0=no edge, 1=highest")
    thesis: str = ""
    key_risks: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class MacroReport(BaseReport):
    """Global market regime view. Exactly one per cycle."""

    author_role: str = "macro_analyst"
    scope: Literal["market"] = "market"
    rates: str = ""             # policy rate path, curve shape
    inflation: str = ""         # CPI / PCE trend
    employment: str = ""        # NFP / jobless claims
    geopolitics: str = ""       # tariffs, politics, military/geo risk
    market_breadth: str = ""    # SPX/NDX earnings, VIX, fear & greed
    vix: float | None = None
    fear_greed: int | None = Field(None, ge=0, le=100)


class IndustryReport(BaseReport):
    """Sector / supply-chain view. One per sector in the watchlist."""

    author_role: str = "industry_analyst"
    sector: str
    supply_chain_notes: str = ""   # bottlenecks, margin transmission up/down the chain


class FundamentalReport(BaseReport):
    """Per-ticker fundamental view."""

    author_role: str = "fundamental_analyst"
    symbol: str
    valuation: str = ""
    growth: str = ""
    profitability: str = ""
    catalysts: list[str] = Field(default_factory=list)


class TechnicalReport(BaseReport):
    """Per-ticker technical view."""

    author_role: str = "technical_analyst"
    symbol: str
    trend: str = ""
    support: float | None = None
    resistance: float | None = None
    indicators_summary: str = ""
