"""Newsletter/research contracts — full articles from subscribed high-signal
sources and the per-ticker insights extracted from them."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Article(BaseModel):
    id: str                          # dedup key: imap:<Message-ID> | substack:<entry id>
    source: str                      # newsletter:<name> | substack:<name>
    title: str
    url: str = ""
    body: str = ""
    published_at: datetime


class Insight(BaseModel):
    """One extracted implication of an article for one universe ticker."""

    article_id: str
    ticker: str
    direction: str = "neutral"       # bullish | bearish | neutral
    impact_path: str = "direct"      # direct | supply_chain | competitive | demand | macro
    summary: str = ""
    evidence_quote: str = ""
    confidence: float = Field(0.0, ge=0, le=1)
