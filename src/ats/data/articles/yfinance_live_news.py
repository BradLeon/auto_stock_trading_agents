"""Live Yahoo Finance news candidates with a strict issuer-title admission gate.

``yfinance.Ticker.news`` is an aggregation and recommendation surface, not an issuer
newswire.  Its ticker association is therefore treated only as discovery input.  A
candidate is usable for an issuer only when its *title* independently names that
issuer; the report retains rejected recommendations so an operator can audit recall
without allowing unrelated stories into the shared document corpus.

This deliberately differs from :mod:`ats.data.yahoo_news`, which reads defeatbeta's
day-level Yahoo mirror with paragraph arrays.  The two sources have different latency,
history and provenance and must never share a source id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import hashlib
import html
import logging
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ...schemas.chain import ArticleRef
from .entity_association import entity_aliases as _entity_aliases
from .entity_association import title_mentions_entity as _title_mentions_entity


log = logging.getLogger("ats.data.articles.yfinance_live_news")
URL_PREFIX = "yfinance-live-news"
_DEFAULT_TIMEOUT_SECONDS = 20


@dataclass
class _Record:
    yahoo_id: str
    canonical_url: str
    title: str
    publisher: str
    published_at: datetime | None
    summary: str = ""
    queried_entities: set[str] = field(default_factory=set)
    title_verified_entities: set[str] = field(default_factory=set)
    association_rejected_entities: set[str] = field(default_factory=set)


_records: dict[str, _Record] = {}


def _canonical_url(value: str) -> str:
    parsed = urlsplit((value or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return ""
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(),
                       parsed.path.rstrip("/"), "", ""))


def _slug(yahoo_id: str, url: str) -> str:
    raw = yahoo_id or url
    safe = re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-").lower()
    return safe[:100] or hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _nested(raw: dict[str, Any]) -> dict[str, Any]:
    content = raw.get("content") if isinstance(raw, dict) else None
    return content if isinstance(content, dict) else (raw if isinstance(raw, dict) else {})


def _published(raw: dict[str, Any]) -> datetime | None:
    value = raw.get("pubDate") or raw.get("providerPublishTime") or raw.get("published_at")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _row(raw: dict[str, Any]) -> tuple[str, str, str, str, datetime | None, str] | None:
    content = _nested(raw)
    yahoo_id = str(content.get("id") or raw.get("uuid") or "").strip()
    title = str(content.get("title") or raw.get("title") or "").strip()
    canonical = content.get("canonicalUrl") or content.get("clickThroughUrl") or raw.get("link")
    if isinstance(canonical, dict):
        canonical = canonical.get("url")
    url = _canonical_url(str(canonical or ""))
    provider = content.get("provider")
    publisher = (str(provider.get("displayName") or provider.get("name") or "")
                 if isinstance(provider, dict) else str(content.get("publisher") or ""))
    summary = str(content.get("summary") or raw.get("summary") or "").strip()
    if not title or not url or not (yahoo_id or url):
        return None
    return yahoo_id or hashlib.sha256(url.encode("utf-8")).hexdigest(), url, title, publisher, _published(content), summary


def _ticker_news(symbol: str) -> list[dict[str, Any]]:
    import yfinance as yf

    return list(yf.Ticker(symbol).news or [])


def discover_with_status(*, pages: int = 1, symbols: list[str] | None = None,
                         now: datetime | None = None, **_) -> tuple[list[ArticleRef], dict]:
    """Discover current Yahoo candidates for PEAD targets; do not fetch bodies."""
    del pages  # Yahoo controls the current-result count; it is not a pageable archive.
    from ...data.pead_official_disclosures import active_pead_targets

    requested = [str(symbol).upper() for symbol in (symbols or active_pead_targets())]
    requested = list(dict.fromkeys(requested))
    _records.clear()
    # Yahoo occasionally returns a different content id for the same canonical story
    # from two ticker feeds.  Conversely, the same Yahoo id can gain a different
    # click-through URL.  Either identity must collapse to one candidate.
    by_yahoo_id: dict[str, str] = {}
    by_canonical_url: dict[str, str] = {}
    failures: list[dict[str, str]] = []
    succeeded_symbols = 0
    retrieved_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    for symbol in requested:
        try:
            raw_rows = _ticker_news(symbol)
        except Exception as exc:  # noqa: BLE001 - one issuer must not erase the ledger
            log.warning("yfinance live news: %s unavailable: %s", symbol, exc)
            failures.append({"symbol": symbol, "error": f"{type(exc).__name__}:{exc}"})
            continue
        succeeded_symbols += 1
        for raw in raw_rows:
            parsed = _row(raw)
            if parsed is None:
                continue
            yahoo_id, url, title, publisher, published_at, summary = parsed
            id_slug = by_yahoo_id.get(yahoo_id)
            url_slug = by_canonical_url.get(url)
            slug = id_slug or url_slug or _slug(yahoo_id, url)
            if id_slug and url_slug and id_slug != url_slug:
                # Preserve the id-keyed record and merge the URL-keyed record into
                # it.  This is rare, but prevents a future request order from
                # producing two documents for one Yahoo identity.
                record = _records[id_slug]
                duplicate = _records.pop(url_slug)
                record.queried_entities.update(duplicate.queried_entities)
                record.title_verified_entities.update(duplicate.title_verified_entities)
                record.association_rejected_entities.update(
                    duplicate.association_rejected_entities)
                for key, value in tuple(by_yahoo_id.items()):
                    if value == url_slug:
                        by_yahoo_id[key] = id_slug
                for key, value in tuple(by_canonical_url.items()):
                    if value == url_slug:
                        by_canonical_url[key] = id_slug
            record = _records.get(slug)
            if record is None:
                record = _records[slug] = _Record(
                    yahoo_id=yahoo_id, canonical_url=url, title=title, publisher=publisher,
                    published_at=published_at, summary=summary)
            by_yahoo_id[yahoo_id] = slug
            by_canonical_url[url] = slug
            record.queried_entities.add(symbol)
            if _title_mentions_entity(title, symbol):
                record.title_verified_entities.add(symbol)
            else:
                record.association_rejected_entities.add(symbol)

    refs = [ArticleRef(url=record.canonical_url, slug=slug, title=record.title,
                       published_at=record.published_at.date() if record.published_at else None)
            for slug, record in _records.items()]
    refs.sort(key=lambda ref: (ref.published_at or date.min, ref.slug), reverse=True)
    return refs, {
        "status": "succeeded" if succeeded_symbols else "unreachable",
        "queried_entities": requested, "entities_succeeded": succeeded_symbols,
        "failed_slices": failures, "retrieved_at": retrieved_at.isoformat(),
        "discovery_method": "yfinance.Ticker.news",
    }


def discover(*, pages: int = 1, symbols: list[str] | None = None, **kwargs) -> list[ArticleRef]:
    return discover_with_status(pages=pages, symbols=symbols, **kwargs)[0]


def provenance(ref: ArticleRef) -> dict[str, str]:
    record = _records.get(ref.slug)
    if record is None:
        return {"native_id": ref.slug, "canonical_url": _canonical_url(ref.url),
                "entity_association": "association_rejected"}
    association = "title_verified" if record.title_verified_entities else "association_rejected"
    return {
        "native_id": record.yahoo_id,
        "canonical_url": record.canonical_url,
        "publisher": record.publisher,
        "published_at_exact": record.published_at.isoformat() if record.published_at else "",
        "queried_entities": ", ".join(sorted(record.queried_entities)),
        "title_verified_entities": ", ".join(sorted(record.title_verified_entities)),
        "association_rejected_entities": ", ".join(sorted(record.association_rejected_entities)),
        "entity_association": association,
    }


def _normalised(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", html.unescape(value or "").lower())


def _title_anchor_present(text: str, title: str) -> bool:
    expected = _normalised(title)
    actual = _normalised(text)
    return bool(expected and len(expected) >= 12 and expected in actual)


def extract_body(markup: str, *, title: str) -> str:
    """Extract an article/main container only, then prove it carries the headline."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(markup or "", "html.parser")
    containers = [*soup.select("article"), *soup.select("main"),
                  *soup.select("[role='main']")]
    seen: set[str] = set()
    for container in containers:
        text = " ".join(container.stripped_strings)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        if _title_anchor_present(text, title):
            return text
    return ""


def _get(url: str) -> str:
    import httpx

    response = httpx.get(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; ATS research data collector/1.0)",
    }, timeout=_DEFAULT_TIMEOUT_SECONDS, follow_redirects=True)
    response.raise_for_status()
    return response.text


def fetch_body(url: str) -> str:
    """Fetch a direct article body; never fall back to page-wide text or Tavily."""
    canonical = _canonical_url(url)
    record = next((row for row in _records.values() if row.canonical_url == canonical), None)
    if record is None or not record.title_verified_entities:
        return ""
    try:
        return extract_body(_get(canonical), title=record.title)
    except Exception as exc:  # noqa: BLE001 - the acceptance ledger owns the gap
        log.info("yfinance live news: body unavailable for %s: %s", canonical, exc)
        return ""


__all__ = ["discover", "discover_with_status", "extract_body", "fetch_body", "provenance"]
