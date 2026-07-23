"""Earnings-call transcript loader — pluggable, manual-file first.

Resolution order:
  1. explicit `source` arg that is a local file path        -> read it
  2. explicit `source` arg that is an http(s) URL           -> fetch + strip tags
  3. dropped file at var/transcripts/<SYM>_<fiscal>.txt     -> read it
  4. (best-effort) a fool.com / investing.com URL passed in -> fetch

Returns (text, source_label). Empty text + "none" if nothing is available — the
actuals agent then works from reported financials alone and notes the gap.

This matches the user's habit of grabbing transcripts from fool.com /
investing.com manually: drop the text into var/transcripts/ and the loop ingests
it. Automated scraping of those sites is brittle and intentionally not relied on.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import REPO_ROOT


def _slug(fiscal_label: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", fiscal_label) or "latest"


def manual_path(symbol: str, fiscal_label: str) -> Path:
    return REPO_ROOT / "var" / "transcripts" / f"{symbol.upper()}_{_slug(fiscal_label)}.txt"


# Structural markers a real earnings-call transcript carries but a scraped page
# shell (nav chrome / paywall stub / truncated boilerplate) does not. Calibrated
# against a good fetch (investing.com: 6 hits) vs a bad one (benzinga shell: 0).
_TRANSCRIPT_MARKERS = (
    "operator", "question-and-answer", "q&a", "prepared remarks",
    "next question", "chief financial", "chief executive",
)


def looks_like_transcript(text: str) -> tuple[bool, str]:
    """Sanity gate before scoring: is `text` a real transcript body, or did the
    fetcher hand back page chrome / a truncated stub? Returns (ok, reason)."""
    body = (text or "").strip()
    if len(body) < 2000:
        return (False, f"transcript 正文过短（{len(body)} 字），疑似抓取失败/被截断")
    low = body.lower()
    hits = [m for m in _TRANSCRIPT_MARKERS if m in low]
    if len(hits) < 2:
        return (False, f"正文缺少电话会结构标记（命中 {len(hits)}：{hits}），疑似抓到页面外壳而非 transcript")
    return (True, f"transcript 正文校验通过（结构标记命中 {len(hits)}：{hits}）")


def _fetch_url(url: str) -> str:
    from .web import fetch_article_text

    text = fetch_article_text(url, min_chars=1, timeout=20)
    if not text:
        raise ValueError(f"no text from {url}")
    return text


def fetch(symbol: str, fiscal_label: str = "", source: str | None = None) -> tuple[str, str]:
    # 1/2) explicit override
    if source:
        if source.startswith("http://") or source.startswith("https://"):
            try:
                return _fetch_url(source), f"url:{source}"
            except Exception:  # noqa: BLE001 - fall through
                pass
        else:
            p = Path(source)
            if p.exists():
                return p.read_text(encoding="utf-8"), f"file:{p}"

    # 3) dropped manual file — the user's habit; authoritative when present, and
    # a clean full transcript beats brittle scraping (which can return page chrome).
    mp = manual_path(symbol, fiscal_label)
    if mp.exists():
        return mp.read_text(encoding="utf-8"), f"file:{mp}"

    # 4) FMP auto-fetch (latest transcript) if a paid key is configured
    text, src = _fmp(symbol)
    if text:
        return text, src

    # 5) web search (Tavily) -> the fool.com / investing transcript page (free tier)
    text, src = _from_search(symbol)
    if text:
        return text, src

    # 6) secondary: a transcript article already in our news feed (if any)
    text, src = _from_news(symbol)
    if text:
        return text, src

    return "", "none"


# --------------------------------------------------------------------------- #
# Web-search transcript fetch (Tavily)
# --------------------------------------------------------------------------- #
def _from_search(symbol: str) -> tuple[str, str]:
    """Find + read the latest earnings-call transcript via Tavily web search.

    Restricts to free transcript sources and uses Tavily's extracted page text.
    Needs TAVILY_API_KEY (free tier); degrades to ('','') without it.
    """
    from ..config import get_config

    key = get_config().secrets.tavily_api_key
    if not key:
        return "", ""
    try:
        import httpx

        r = httpx.post("https://api.tavily.com/search", timeout=40, json={
            "api_key": key,
            "query": f"{symbol} latest earnings call transcript",
            "include_domains": ["fool.com", "investing.com"],
            "include_raw_content": True, "max_results": 5})
        r.raise_for_status()
        results = r.json().get("results", []) or []
    except Exception:  # noqa: BLE001
        return "", ""

    def looks_transcript(res: dict) -> bool:
        return "transcript" in (res.get("url", "") + res.get("title", "")).lower()

    ranked = sorted(results, key=lambda x: (looks_transcript(x),
                                            len(x.get("raw_content") or "")), reverse=True)
    for res in ranked:
        content = res.get("raw_content") or res.get("content") or ""
        if looks_transcript(res) and len(content) >= _MIN_TRANSCRIPT_CHARS:
            return content, f"tavily:{res.get('url')}"
    return "", ""


# --------------------------------------------------------------------------- #
# News-driven transcript fetch
# --------------------------------------------------------------------------- #
_TRANSCRIPT_HINTS = ("earnings call transcript", "call transcript", "earnings transcript")
_MIN_TRANSCRIPT_CHARS = 2000          # a real transcript is long; skip stubs/paywalls
_PREFERRED = ("fool.com", "investing.com")


def _from_news(symbol: str, lookback_days: int = 10) -> tuple[str, str]:
    """Locate the earnings-call transcript article in recent news and scrape it.

    Tight lookback so we get THIS quarter's transcript (published within days of
    the call), not last quarter's stale one. Degrades to ('','').
    """
    from datetime import datetime, timedelta, timezone

    try:
        from .news import fetch_news

        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        items = fetch_news(symbol, since)
    except Exception:  # noqa: BLE001
        return "", ""

    candidates = [it for it in items if it.url and (
        any(h in it.headline.lower() for h in _TRANSCRIPT_HINTS)
        or "call-transcripts" in it.url.lower())]
    # Prefer free, transcript-friendly sources; newest first.
    candidates.sort(key=lambda it: (any(p in it.url.lower() for p in _PREFERRED),
                                    it.published_at), reverse=True)
    for it in candidates:
        try:
            text = _fetch_url(it.url)
        except Exception:  # noqa: BLE001
            continue
        if len(text) >= _MIN_TRANSCRIPT_CHARS:
            return text, f"news:{it.source}:{it.url}"
    return "", ""


def _fmp(symbol: str) -> tuple[str, str]:
    """FinancialModelingPrep latest earnings-call transcript (current /stable API).

    Transcripts are a PAID FMP feature: free/basic plans return 402 here, so this
    degrades quietly to ('','') and the manual drop / 'none' path takes over.
    """
    from ..config import get_config

    key = get_config().secrets.fmp_api_key
    if not key:
        return "", ""
    try:
        import httpx

        r = httpx.get("https://financialmodelingprep.com/stable/earning-call-transcript",
                      params={"symbol": symbol.upper(), "limit": 1, "apikey": key}, timeout=25)
        if r.status_code != 200:   # 402 restricted / 403 legacy / etc -> degrade
            return "", ""
        data = r.json()
        rows = data if isinstance(data, list) else [data]
        if rows and isinstance(rows[0], dict):
            row = rows[0]
            content = row.get("content") or row.get("transcript") or row.get("text")
            if content:
                period = row.get("period") or row.get("quarter")
                fy = row.get("fiscalYear") or row.get("year")
                return content, f"fmp:{period}-{fy}"
    except Exception:  # noqa: BLE001 - degrade quietly
        return "", ""
    return "", ""
