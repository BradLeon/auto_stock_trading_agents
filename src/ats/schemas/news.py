"""News + context-update contracts for the continuous PEAD monitor."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NewsParagraph(BaseModel):
    """One source paragraph from a structured news dataset."""

    paragraph_number: int
    highlight: str = ""
    paragraph: str = ""


class NewsItem(BaseModel):
    id: str                          # dedup key (provider id or url)
    source: str                      # finnhub | rss:<name> | x:<account>
    headline: str
    summary: str = ""
    url: str = ""
    published_at: datetime
    tickers: list[str] = Field(default_factory=list)
    publisher: str = ""
    report_date: str = ""
    article_type: str = ""
    paragraphs: list[NewsParagraph] = Field(default_factory=list)
    snapshot_updated_at: str = ""
    snapshot_lag_hours: float | None = None

    def one_line(self) -> str:
        return f"[{self.published_at:%Y-%m-%d} {self.source}] {self.headline}"

    def structured_body(self) -> str:
        blocks: list[str] = []
        for row in sorted(self.paragraphs, key=lambda item: item.paragraph_number):
            text = row.paragraph.strip()
            if not text:
                continue
            if row.highlight.strip():
                blocks.append(f"## {row.highlight.strip()}\n\n{text}")
            else:
                blocks.append(text)
        return "\n\n".join(blocks)


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
