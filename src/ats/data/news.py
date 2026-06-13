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

from ..config import get_config, load_news_sources
from ..schemas.news import NewsItem
from .base import safe_fetch

log = logging.getLogger("ats.data.news")
name = "news"


def fetch_news(symbol: str, since: datetime, until: datetime | None = None) -> list[NewsItem]:
    until = until or datetime.now(timezone.utc)
    sources_cfg = load_news_sources()

    items: list[NewsItem] = []
    fh = safe_fetch(lambda: _finnhub(symbol, since, until), source=f"finnhub:{symbol}")
    if fh:
        items += fh
    items += _rss(symbol, since, sources_cfg)
    items += _x(symbol, since, sources_cfg)

    # Dedup by id, keep newest first.
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x.published_at, reverse=True):
        if it.id in seen:
            continue
        seen.add(it.id)
        out.append(it)
    return out


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
