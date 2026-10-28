"""Deterministic issuer mentions shared by article adapters.

Ticker feeds are discovery mechanisms, not proof that every returned story belongs to
that issuer. This module uses registered names/aliases and case-sensitive ticker
evidence with word boundaries: it is auditable and avoids turning a semantic
guess into a document-admission decision.
"""

from __future__ import annotations

import re


def entity_aliases(symbol: str) -> list[str]:
    """Return strong, registered aliases for an issuer, longest first."""
    from ..config import canonical_entity, entity_meta

    canonical = canonical_entity(symbol).upper()
    meta = entity_meta(canonical)
    values = [canonical, str(meta.get("name") or ""), *(meta.get("aliases") or [])]
    # A one-character alias is too ambiguous for deterministic admission.  Unknown
    # entities still retain their ticker as the strongest identity.
    return sorted({str(value).strip() for value in values if len(str(value).strip()) >= 2},
                  key=lambda value: (-len(value), value.casefold(), value))


def title_mentions_entity(title: str, symbol: str) -> bool:
    """Whether a title independently names the queried issuer."""
    return bool(entity_mentions(title, symbol))


def entity_mentions(text: str, symbol: str) -> list[dict]:
    """Registered literal mentions with original offsets; tickers are case-sensitive.

    Short symbols need a stock marker or nearby financial context. Query contracts
    and product names are deliberately not evidence of an issuer mention.
    """
    mentions = []
    canonical = symbol.upper()
    for alias in entity_aliases(symbol):
        ticker = alias.upper() == canonical
        flags = 0 if ticker else re.IGNORECASE
        pattern = rf"(?<![\w]){re.escape(alias)}(?![\w])"
        for match in re.finditer(pattern, text or "", flags):
            if canonical == "ORCL" and alias.casefold() == "oracle" and re.match(
                    r"\s+of\s+(?:omaha|delphi)\b", text[match.end():], re.I):
                continue
            if ticker and len(canonical) <= 2:
                before = text[max(0, match.start()-24):match.start()]
                around = text[max(0, match.start()-48):match.end()+48]
                if not (before.endswith("$") or re.search(
                    r"(?:NASDAQ|NYSE)\s*:\s*$", before, re.I) or re.search(
                    r"\b(?:shares?|stock|ticker|earnings)\b", around, re.I)):
                    continue
            mentions.append({"entity": canonical, "alias": alias,
                             "start": match.start(), "end": match.end(),
                             "kind": "ticker" if ticker else "company_alias"})
    return sorted(mentions, key=lambda row: (
        row["start"], -row["end"], row["kind"], row["alias"]))


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


__all__ = ["entity_aliases", "entity_mentions", "normalised_title", "title_mentions_entity"]
