"""Shared web-page text fetching — httpx + HTML strip, Tavily /extract fallback.

`fetch_article_text` is the one place that turns a URL into plain text for LLM
consumption (news bodies, newsletter posts, transcript pages). Never raises.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger("ats.data.web")

_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def strip_html(html: str) -> str:
    import html as html_mod

    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_article_text(url: str, *, min_chars: int = 800, timeout: int = 25) -> str:
    """URL -> plain text. httpx+strip primary; Tavily /extract fallback for
    paywall/JS shells (< min_chars). Returns "" if still too short. Never raises."""
    text = ""
    try:
        import httpx

        r = httpx.get(url, headers={"User-Agent": _BROWSER_UA}, timeout=timeout,
                      follow_redirects=True)
        r.raise_for_status()
        text = strip_html(r.text)
    except Exception as exc:  # noqa: BLE001
        log.debug("direct fetch failed for %s: %s", url, exc)

    if len(text) < min_chars:
        tavily = _tavily_extract(url, timeout=timeout)
        if len(tavily) > len(text):
            text = tavily

    return text if len(text) >= min_chars else ""


def _tavily_extract(url: str, *, timeout: int = 25) -> str:
    from ..config import get_config

    key = get_config().secrets.tavily_api_key
    if not key:
        return ""
    try:
        import httpx

        r = httpx.post("https://api.tavily.com/extract", timeout=timeout,
                       json={"api_key": key, "urls": [url]})
        r.raise_for_status()
        results = r.json().get("results", []) or []
        return (results[0].get("raw_content") or "").strip() if results else ""
    except Exception as exc:  # noqa: BLE001
        log.debug("tavily extract failed for %s: %s", url, exc)
        return ""
