"""News aggregation for the continuous PEAD monitor.

Three adapters, each degrading independently (one dead source never kills the
others): Finnhub company-news (structured, free), curated RSS (feedparser, keyword
-matched to a ticker), and an X/social stub (X API is restricted — interface only).
`fetch_news` aggregates + dedups by id.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..config import get_config, load_news_sources
from ..schemas.news import NewsItem
from .base import safe_fetch

log = logging.getLogger("ats.data.news")
name = "news"


def fetch_news(symbol: str, since: datetime, until: datetime | None = None, *,
               store=None, consumer: str = "pead_monitor") -> list[NewsItem]:
    """Read released news only; scheduled ingestion owns source acquisition.

    A consumer must never fall back to an ad-hoc provider request: doing so bypasses
    entity admission, deduplication and the platform lineage recorded at ingestion.
    """
    until = until or datetime.now(timezone.utc)
    from .products.unstructured import platform_news_items

    return platform_news_items(entity=symbol, since=since, until=until)


def external_id(item: NewsItem) -> str:
    """Cross-provider identity: canonical URL when present, provider id otherwise."""
    if not item.url:
        return f"news:{item.id}"
    parts = urlsplit(item.url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_") and k.lower() not in {
                           "guccounter", "guce_referrer", "guce_referrer_sig"}])
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def _catalog(items: list[NewsItem], *, store=None) -> None:
    """Save the provider-visible headline/summary before any triage decision."""
    from . import document_assets

    if store is None:
        from .stores.unstructured import get_data_ingestion_store

        store = get_data_ingestion_store()

    for item in items:
        identity = external_id(item)
        structured_body = item.structured_body()
        body = structured_body or item.headline.strip()
        if not structured_body and item.summary.strip():
            body += "\n\n" + item.summary.strip()
        if not body:
            continue
        published = item.published_at.isoformat()
        existing = store.document_by_external_id(identity)
        if existing is None:
            existing = store.document_by_story(item.headline, published)
        # A complete copy already held through another provider is the canonical asset;
        # retain this provider as an alias instead of re-indexing the same story.
        if existing is not None and int(existing.get("chars") or 0) >= 800:
            store.link_document_entities(existing["document_id"], item.tickers)
            metadata = {
                "publisher": item.publisher, "report_date": item.report_date,
                "article_type": item.article_type,
                "snapshot_updated_at": item.snapshot_updated_at,
                "snapshot_lag_hours": item.snapshot_lag_hours,
                "paragraphs": [row.model_dump() for row in item.paragraphs],
            }
            store.save_document_alias(
                existing["document_id"], source=item.source, source_url=item.url,
                external_id=identity, title=item.headline, published_at=published,
                metadata=metadata)
            continue
        doc = document_assets.ingest(
            entity="NEWS", key=document_assets.stable_key(identity, prefix="news"),
            doc_type="news_item", text=body,
            source=(f"{item.source}:structured" if structured_body
                    else f"{item.source}:metadata"),
            source_url=item.url, external_id=identity, title=item.headline,
            published_at=published,
            related_entities=tuple(item.tickers), min_chars=1, store=store,
            carrier_format="structured_dataset" if structured_body else "api_json",
        )
        if doc is not None and structured_body and item.source == "yahoo:defeatbeta":
            from . import yahoo_news

            yahoo_news.save_structure(item, doc)
        if doc is not None:
            store.save_document_alias(
                doc.document_id, source=item.source, source_url=item.url,
                external_id=identity, title=item.headline, published_at=published,
                metadata={"publisher": item.publisher, "report_date": item.report_date})


def acquire_body(item: NewsItem, *, store=None) -> str:
    """Return shared full text, fetching it at most once after metadata ingestion."""
    if item.source.startswith("platform:"):
        from .products.unstructured import _read_version_text
        from .stores.unstructured import get_platform_unstructured_repository

        repository = get_platform_unstructured_repository()
        try:
            version = repository.latest_document_version(item.id)
            return _read_version_text(version or {})
        finally:
            repository.close()
    from .stores.unstructured import get_data_ingestion_store
    from . import document_assets
    from .web import fetch_article_text

    store = store or get_data_ingestion_store()
    identity = external_id(item)
    row, cached = document_assets.read_external(identity, store=store)
    # Metadata versions are intentionally short. Once a real article body exists,
    # every target and Workflow reuses it without another HTTP request.
    if row and len(cached) >= 800:
        store.link_document_entities(row["document_id"], item.tickers)
        return cached
    structured = item.structured_body()
    if structured:
        return structured
    if not item.url:
        return ""
    body = fetch_article_text(item.url)
    if not body:
        return ""
    doc = document_assets.ingest(
        entity="NEWS", key=document_assets.stable_key(identity, prefix="news"),
        doc_type="news_item", text=body, source=f"{item.source}:fulltext",
        source_url=item.url, external_id=identity, title=item.headline,
        published_at=item.published_at.isoformat(), related_entities=tuple(item.tickers),
        min_chars=1, store=store,
    )
    return doc.text if doc else body


# --------------------------------------------------------------------------- #
# Finnhub
# --------------------------------------------------------------------------- #
def _finnhub(symbol: str, since: datetime, until: datetime) -> list[NewsItem]:
    import httpx

    key = get_config().secrets.finnhub_api_key
    if not key:
        raise ValueError("no FINNHUB_API_KEY")
    r = httpx.get("https://finnhub.io/api/v1/company-news",
                  params={"symbol": symbol, "from": since.strftime("%Y-%m-%d"),
                          "to": until.strftime("%Y-%m-%d"), "token": key}, timeout=20)
    r.raise_for_status()
    out = []
    for d in r.json():
        ts = d.get("datetime")
        if not ts:
            continue
        pub = datetime.fromtimestamp(ts, tz=timezone.utc)
        if pub < since:
            continue
        out.append(NewsItem(id=f"finnhub:{d.get('id')}", source="finnhub",
                            headline=d.get("headline", ""), summary=d.get("summary", ""),
                            url=d.get("url", ""), published_at=pub, tickers=[symbol]))
    return out


# --------------------------------------------------------------------------- #
# RSS (keyword-matched to the ticker)
# --------------------------------------------------------------------------- #
def _rss(symbol: str, since: datetime, cfg: dict) -> list[NewsItem]:
    feeds = cfg.get("rss", []) or []
    keywords = [k.lower() for k in (cfg.get("keywords_by_ticker", {}) or {}).get(symbol.upper(), [])]
    out: list[NewsItem] = []
    for feed in feeds:
        parsed = safe_fetch(lambda f=feed: _parse_feed(f, symbol, since, keywords),
                            source=f"rss:{feed.get('name')}", attempts=1)
        if parsed:
            out += parsed
    return out


def _parse_feed(feed: dict, symbol: str, since: datetime, keywords: list[str]) -> list[NewsItem]:
    import feedparser

    parsed = feedparser.parse(feed["url"])
    out = []
    for e in parsed.entries:
        pub = _entry_dt(e)
        if pub and pub < since:
            continue
        title, summary = e.get("title", ""), _clean(e.get("summary", ""))
        if keywords and not any(k in (title + " " + summary).lower() for k in keywords):
            continue
        out.append(NewsItem(id=e.get("id") or e.get("link", title), source=f"rss:{feed.get('name')}",
                            headline=title, summary=summary[:800], url=e.get("link", ""),
                            published_at=pub or datetime.now(timezone.utc), tickers=[symbol]))
    return out


def _entry_dt(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def _clean(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html or "")).strip()


# --------------------------------------------------------------------------- #
# X / social (stub — X API is restricted; interface only for now)
# --------------------------------------------------------------------------- #
def _x(symbol: str, since: datetime, cfg: dict) -> list[NewsItem]:
    accounts = cfg.get("x_accounts", []) or []
    if accounts:
        log.info("X/social tracking configured for %s but adapter is a stub (needs X API)", accounts)
    return []
