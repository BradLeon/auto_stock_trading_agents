"""LLM-facing output schemas.

These capture ONLY the analytical fields the model should produce. System
bookkeeping (as_of, author_role, symbol/sector) is attached in code afterwards,
so the model is never asked to invent timestamps or echo identifiers.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ..schemas.reports import Signal


def _as_list(v):
    """Coerce a stray string/None into a list — LLMs sometimes flatten arrays."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    return v


class AnalystView(BaseModel):
    # No numeric (min/max) constraints here: some providers reject them in the
    # structured-output JSON schema. Conviction is clamped to [0,1] in code.
    signal: Signal = Field(description="bullish | neutral | bearish")
    conviction: float = Field(description="0=no edge .. 1=highest conviction")
    thesis: str = Field(description="2-4 sentence reasoning for the signal")
    key_risks: list[str] = Field(default_factory=list, description="what would invalidate the thesis")
    sources: list[str] = Field(default_factory=list, description="data points/refs used")

    @field_validator("key_risks", "sources", mode="before")
    @classmethod
    def _coerce_lists(cls, v):
        return _as_list(v)


class MacroView(AnalystView):
    rates: str = ""
    inflation: str = ""
    employment: str = ""
    geopolitics: str = ""
    market_breadth: str = ""


class IndustryView(AnalystView):
    supply_chain_notes: str = ""


class FundamentalView(AnalystView):
    valuation: str = ""
    growth: str = ""
    profitability: str = ""
    catalysts: list[str] = Field(default_factory=list)

    @field_validator("catalysts", mode="before")
    @classmethod
    def _coerce_catalysts(cls, v):
        return _as_list(v)


class TechnicalView(AnalystView):
    trend: str = ""
    support: float | None = None
    resistance: float | None = None
    indicators_summary: str = ""
