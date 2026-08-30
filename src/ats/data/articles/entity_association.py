"""Deterministic issuer-title association shared by article adapters.

Ticker feeds are discovery mechanisms, not proof that every returned story belongs to
that issuer.  This module deliberately uses only registered entity names and aliases
with word boundaries: it is cheap, auditable, and avoids silently turning a semantic
guess into a document-admission decision.
"""

from __future__ import annotations

import re


def entity_aliases(symbol: str) -> list[str]:
    """Return strong, registered aliases for an issuer, longest first."""
    from ...config import canonical_entity, entity_meta

    canonical = canonical_entity(symbol).upper()
    meta = entity_meta(canonical)
    values = [canonical, str(meta.get("name") or ""), *(meta.get("aliases") or [])]
    # A one-character alias is too ambiguous for deterministic admission.  Unknown
    # entities still retain their ticker as the strongest identity.
    return sorted({str(value).strip() for value in values if len(str(value).strip()) >= 2},
                  key=len, reverse=True)


def title_mentions_entity(title: str, symbol: str) -> bool:
    """Whether a title independently names the queried issuer."""
    for alias in entity_aliases(symbol):
        if re.search(rf"(?<![A-Z0-9]){re.escape(alias.upper())}(?![A-Z0-9])",
                     (title or "").upper()):
            return True
    return False


_SOURCE_SUFFIX = re.compile(
    r"\s+--\s+(?:WSJ|Barron'?s(?:\.com)?|MarketWatch|IBD|"
    r"Tech Stocks(?:\s+--\s+MarketWatch)?|Need to Know(?:\s+--\s+MarketWatch)?)$",
    re.IGNORECASE,
)


def normalised_title(title: str) -> str:
    """Stable headline key without known wire source suffixes or continuation marks."""
    value = _SOURCE_SUFFIX.sub("", title or "")
    value = re.sub(r"\s+-\d+-$", "", value)
    return re.sub(r"\s+", " ", value).strip().lower()


__all__ = ["entity_aliases", "normalised_title", "title_mentions_entity"]
