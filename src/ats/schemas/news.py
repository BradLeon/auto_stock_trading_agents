"""News + context-update contracts for the continuous PEAD monitor."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    id: str                          # dedup key (provider id or url)
    source: str                      # finnhub | rss:<name> | x:<account>
    headline: str
    summary: str = ""
    url: str = ""
    published_at: datetime
    tickers: list[str] = Field(default_factory=list)

    def one_line(self) -> str:
        return f"[{self.published_at:%Y-%m-%d} {self.source}] {self.headline}"


class ExpectationChange(BaseModel):
    dim_key: str = ""
    change: str = ""                 # how the expectation shifts and why


class ContextUpdate(BaseModel):
    """Incremental update the monitor applies to a dossier from new events."""

    symbol: str
    as_of: datetime
    materiality: float = Field(0.0, ge=0, le=1, description="0=noise, 1=thesis-changing")
    event_summary: str = ""          # what happened since last update
    narrative_delta: str = ""        # what changes in the thesis (empty if nothing)
    expectation_changes: list[ExpectationChange] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
