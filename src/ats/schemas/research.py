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
    completeness: str = "full"       # full | partial | teaser
    truncation_reason: str = ""
    mime_source: str = ""
    mailbox: str = ""
    folder: str = ""
    sender: str = ""
    uidvalidity: str = ""
    uid: int | None = None
    message_id: str = ""
    source_fields: dict[str, str] = Field(default_factory=dict)
    document_version_id: str = ""
    publication_id: str = ""
    interpretation_key: str = ""
    related_entities: list[str] = Field(default_factory=list)
    quality: dict = Field(default_factory=dict)


class Insight(BaseModel):
    """One extracted implication of an article for one universe ticker."""

    article_id: str
    ticker: str
    direction: str = "neutral"       # bullish | bearish | neutral
    impact_path: str = "direct"      # direct | supply_chain | competitive | demand | macro
    summary: str = ""
    evidence_quote: str = ""
    evidence_locator: dict = Field(default_factory=dict)
    evidence_context: list[dict] = Field(default_factory=list)  # verbatim supporting context + original locators
    confidence: float = Field(0.0, ge=0, le=1)
    materiality: float | None = Field(default=None, ge=0, le=1)
    source_fact: str = ""             # populated by target Information contract
    source_kind: str = "reported_fact"
    source_actor: str = ""
    relation_path: list[str] = Field(default_factory=list)
    association_type: str = "direct_update"
    association_reason: str = ""
    target_role: str = ""             # source role assessed once, independently of recap
    information_topic: str = ""
    information_quality: str = "substantive"
    input_truncated: bool | None = None  # system-owned, independent of document completeness
    visible_input_chars: int | None = None
