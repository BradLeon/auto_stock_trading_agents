"""Topical news search via Tavily — for qualitative macro themes (geopolitics,
tariffs, trade/industrial policy) that no quantitative feed covers.

Mirrors the proven Tavily /search usage in transcript.py/documents.py. Never
raises; returns [] without a key or on failure.
"""

from __future__ import annotations

import logging

log = logging.getLogger("ats.data.websearch")

name = "websearch"


def search_news(query: str, *, max_results: int = 4, days: int = 14,
                max_chars: int = 2000) -> list[dict]:
    """Recent-news search. Returns [{title, url, content, published}] newest-first."""
    from ..config import get_config

    key = get_config().secrets.tavily_api_key
    if not key:
        log.info("websearch: no TAVILY_API_KEY — skipping %r", query)
        return []
    try:
        import httpx

        r = httpx.post("https://api.tavily.com/search", timeout=40, json={
            "api_key": key, "query": query, "topic": "news", "days": days,
            "include_raw_content": True, "max_results": max_results})
        r.raise_for_status()
        results = r.json().get("results", []) or []
    except Exception as exc:  # noqa: BLE001
        log.warning("websearch failed for %r: %s", query, exc)
        return []

    out = []
    for res in results:
        content = (res.get("content") or res.get("raw_content") or "").strip()
        out.append({
            "title": (res.get("title") or "").strip(),
            "url": res.get("url", ""),
            "content": content[:max_chars],
            "published": res.get("published_date", ""),
        })
    return out
