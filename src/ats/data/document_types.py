"""Stable business semantics for shared document assets.

The legacy catalog used names such as ``deck`` and ``article`` for a mixture of
business meaning and storage representation.  New code uses the semantic values
below and records the carrier separately.  Mapping helpers keep historical rows
queryable without rewriting their identifiers or paths.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path


class DocumentSemantic(StrEnum):
    RESEARCH_ARTICLE = "research_article"
    NEWS_ITEM = "news_item"
    COMPANY_RELEASE = "company_release"
    INVESTOR_PRESENTATION = "investor_presentation"
    EARNINGS_TRANSCRIPT = "earnings_transcript"
    REGULATORY_FILING = "regulatory_filing"


class CarrierFormat(StrEnum):
    HTML = "html"
    PDF = "pdf"
    EMAIL = "email"
    STRUCTURED_TEXT = "structured_text"
    PLAIN_TEXT = "plain_text"
    UNKNOWN = "unknown"


LEGACY_TO_SEMANTIC: dict[str, DocumentSemantic] = {
    "article": DocumentSemantic.RESEARCH_ARTICLE,
    "news": DocumentSemantic.NEWS_ITEM,
    "release": DocumentSemantic.COMPANY_RELEASE,
    "deck": DocumentSemantic.INVESTOR_PRESENTATION,
    "transcript": DocumentSemantic.EARNINGS_TRANSCRIPT,
    "filing": DocumentSemantic.REGULATORY_FILING,
}
SEMANTIC_TO_LEGACY: dict[DocumentSemantic, str] = {
    semantic: legacy for legacy, semantic in LEGACY_TO_SEMANTIC.items()
}


def semantic_type(value: str | DocumentSemantic) -> DocumentSemantic:
    """Return the canonical semantic for either a legacy or current value."""
    if isinstance(value, DocumentSemantic):
        return value
    normalized = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in LEGACY_TO_SEMANTIC:
        return LEGACY_TO_SEMANTIC[normalized]
    return DocumentSemantic(normalized)


def legacy_type(value: str | DocumentSemantic) -> str:
    """Return the one historical catalog value corresponding to a semantic."""
    return SEMANTIC_TO_LEGACY[semantic_type(value)]


def compatible_type_values(value: str | DocumentSemantic) -> tuple[str, str]:
    """Values a query should include while old and new catalog rows coexist."""
    semantic = semantic_type(value)
    return semantic.value, SEMANTIC_TO_LEGACY[semantic]


def infer_carrier_format(path: str | Path) -> CarrierFormat:
    """Infer only the file carrier; never infer business semantics from a suffix."""
    suffix = Path(path).suffix.lower()
    if suffix in {".htm", ".html"}:
        return CarrierFormat.HTML
    if suffix == ".pdf":
        return CarrierFormat.PDF
    if suffix in {".eml", ".msg"}:
        return CarrierFormat.EMAIL
    if suffix in {".json", ".jsonl", ".parquet"}:
        return CarrierFormat.STRUCTURED_TEXT
    if suffix in {".md", ".txt"}:
        return CarrierFormat.PLAIN_TEXT
    return CarrierFormat.UNKNOWN
