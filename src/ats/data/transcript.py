"""Earnings-call transcript loader — pluggable, manual-file first.

Resolution order (`fetch`):
  1/2. explicit `source` arg — local file path, or http(s) URL to fetch
  3.   dropped file at var/transcripts/<SYM>_<fiscal>.txt
  4.   FMP /stable/earning-call-transcript  (paid tier; verified 402 on this key)
  5.   Tavily search of fool.com / investing.com, QUARTER-TARGETED
  6.   a transcript article already in our news feed

Returns (text, source_label). Empty text + "none" if nothing is available — the
actuals agent then works from reported financials alone and notes the gap.

Two hard-won rules govern the auto paths:
  * Select by REPORTING PERIOD, never by length. Every quarter's transcript page is
    ~60K chars, so length ranking is noise — it once put an 8-year-old Q4 2018
    Alphabet call first for a "latest transcript" query.
  * A confirmed wrong quarter is worse than nothing: scoring last quarter's call
    against this quarter's expectations yields a confident fictional miss. So we
    return empty and let the caller fall back or refuse.

Manual drops stay authoritative: a clean full transcript in var/transcripts/ beats
any scrape, and `extract_body` strips the page furniture those saves carry.
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


# Page furniture wrapping a scraped transcript. Measured on a real investing.com
# save (var/transcripts/GOOG_Q22026.txt): 34,377 chars of nav/summary BEFORE the
# body and 23,896 chars of ad pixels AFTER it — 52% of the file. Feeding that to
# the extractor spends the context budget on the wrong text (and the 40k clip used
# to leave only 7% of the actual transcript). fool.com wraps its bodies similarly.
# Ordered by specificity: the FIRST pattern that matches wins, so a stray
# "Operator:" in the marketing summary can't beat a real section heading.
_BODY_START = (
    r"##\s*\*{0,2}Full transcript",          # investing.com section heading
    r"\bPrepared Remarks:?\s*$",             # fool.com section heading
    r"\*\*Conference Call Operator\*\*",     # investing.com first speaker
    r"^\s*\*{0,2}Operator\*{0,2}\s*:",       # generic first speaker
)
# Earliest match after the body start wins.
_BODY_END = (
    r"_This article was generated with the support of AI",   # investing.com
    r"##\s*Latest comments",                                 # investing.com
    r"^\s*Risk Disclosure\s*:",                              # investing.com
    r"\bDuration:\s*\d+\s*minutes",                          # fool.com footer
)
_MIN_BODY_CHARS = 3000


def extract_body(text: str, source: str = "") -> tuple[str, str]:
    """Strip page furniture around a scraped transcript. Returns (body, note).

    Deliberately conservative: the trimmed result is accepted only if it still
    passes `looks_like_transcript` and isn't implausibly small, otherwise the input
    is returned unchanged. A bad marker should cost nothing — never make the input
    worse, since the alternative (dropping real transcript) is the expensive error.
    """
    raw = (text or "").strip()
    if len(raw) < _MIN_BODY_CHARS:
        return raw, ""

    start, start_tag = 0, ""
    for pat in _BODY_START:
        m = re.search(pat, raw, re.I | re.M)
        if m:
            start, start_tag = m.start(), pat
            break

    end, end_tag = len(raw), ""
    for pat in _BODY_END:
        m = re.search(pat, raw[start + _MIN_BODY_CHARS:], re.I | re.M)
        if m:
            pos = start + _MIN_BODY_CHARS + m.start()
            if pos < end:
                end, end_tag = pos, pat
    if not start_tag and not end_tag:
        return raw, ""

    body = raw[start:end].strip()
    floor = max(_MIN_BODY_CHARS, int(len(raw) * 0.05))
    ok, why = looks_like_transcript(body)
    if len(body) < floor or not ok:
        return raw, f"⚠️ chrome-strip 放弃（剥后 {len(body)} 字：{why}），保留原文 {len(raw)} 字"
    note = (f"chrome-strip: {len(raw)} → {len(body)} 字"
            f"（头 {start} / 尾 {len(raw) - end}）")
    return body, note


def _fetch_url(url: str) -> str:
    from .web import fetch_article_text

    text = fetch_article_text(url, min_chars=1, timeout=20)
    if not text:
        raise ValueError(f"no text from {url}")
    return text


def _accepted(symbol: str, fiscal_label: str, text: str, source: str, *,
              company_name: str = "", structured=None, store=None) -> tuple[str, str]:
    """Run every transcript source through the central admission gate."""
    if not text:
        return text, source
    from ..config import entity_meta
    from ..memory import get_store
    from . import admission, defeatbeta, fiscal
    from .document_types import CarrierFormat

    store = store or get_store()
    company_name = company_name or entity_meta(symbol).get("name", "")
    body, _ = extract_body(text, source)
    body_ok, _ = looks_like_transcript(body)
    manual = source.startswith("file:")
    claimed_entity = symbol if (
        manual or structured is not None
        or admission.mentions_entity(body, symbol, company_name)
    ) else ""
    if structured is not None:
        claimed_period = structured.label
    elif manual:
        # A manual override is explicitly named for this target period. Its provenance
        # is stronger than prose regexes, but an unresolved target still fails centrally.
        claimed_period = fiscal_label
    else:
        detected = fiscal.detect_period(body, source)
        claimed_period = f"Q{detected[1]} FY{detected[0]}" if detected else ""
    source_url = ""
    for prefix in ("url:", "tavily:"):
        if source.startswith(prefix):
            source_url = source[len(prefix):]
    if source.startswith("news:") and "http" in source:
        source_url = source[source.index("http"):]
    metadata = ({"paragraphs": structured.paragraphs} if structured is not None else {})
    candidate = admission.CandidateDocument(
        expected_entity=symbol, claimed_entity=claimed_entity,
        target_period=fiscal_label, claimed_period=claimed_period,
        expected_semantic="earnings_transcript", claimed_semantic="transcript",
        text=body, source=source, source_url=source_url,
        external_id=(f"defeatbeta:{structured.symbol}:{structured.report_date}"
                     if structured is not None else source_url or source),
        title=f"{symbol.upper()} {fiscal_label} earnings call transcript".strip(),
        published_at=str(structured.report_date) if structured is not None else "",
        carrier_format=(CarrierFormat.STRUCTURED_TEXT if structured is not None
                        else CarrierFormat.PLAIN_TEXT if manual else CarrierFormat.HTML),
        completeness="full" if body_ok else "unknown",
        min_chars=_MIN_TRANSCRIPT_CHARS, related_entities=(symbol,), metadata=metadata,
    )
    extensions = (defeatbeta.structured_transcript_issues,) if structured is not None else ()
    outcome = admission.admit(candidate, extensions=extensions, store=store)
    if not outcome.validation.accepted or outcome.document is None:
        return "", f"quarantined:{source}"
    if structured is not None:
        raw_path = defeatbeta.save_structure(structured, outcome.document)
        store.save_document_candidate(
            candidate, outcome.validation, raw_path=str(raw_path or ""),
            document_id=outcome.document.document_id,
        )
    return outcome.document.text, source


def fetch(symbol: str, fiscal_label: str = "", source: str | None = None,
          company_name: str = "", *, store=None) -> tuple[str, str]:
    # 1/2) explicit override
    if source:
        if source.startswith("http://") or source.startswith("https://"):
            try:
                return _accepted(symbol, fiscal_label, _fetch_url(source),
                                 f"url:{source}", company_name=company_name, store=store)
            except Exception:  # noqa: BLE001 - fall through
                pass
        else:
            p = Path(source)
            if p.exists():
                return _accepted(symbol, fiscal_label, p.read_text(encoding="utf-8"),
                                 f"file:{p}", company_name=company_name, store=store)

    # 3) dropped manual file — the user's habit; authoritative when present, and
    # a clean full transcript beats brittle scraping (which can return page chrome).
    mp = manual_path(symbol, fiscal_label)
    if mp.exists():
        return _accepted(symbol, fiscal_label, mp.read_text(encoding="utf-8"),
                         f"file:{mp}", company_name=company_name, store=store)

    # A manual/official accepted cache is an override. A weaker historical scrape may
    # not short-circuit the keyed structured source merely because it arrived first.
    from . import defeatbeta, fiscal, source_cache

    cached_docs = [source_cache.load(symbol, fiscal_label, kind)
                   for kind in ("earnings_transcript", "transcript")]
    cached_docs = [doc for doc in cached_docs if doc is not None]
    for cached in cached_docs:
        if (cached.source or "manual").lower().startswith(("file:", "manual", "official")):
            return cached.text, cached.source or "cache"
    for cached in cached_docs:
        if (cached.source or "").lower().startswith("defeatbeta"):
            return cached.text, cached.source

    # Structured primary source: only an exact, fully resolved fiscal period may enter.
    year, quarter = fiscal.parse_label(fiscal_label)
    if year and quarter:
        structured = defeatbeta.fetch(symbol, fiscal_year=year, fiscal_quarter=quarter)
        if structured is not None:
            accepted = _accepted(
                symbol, fiscal_label, structured.text, "defeatbeta",
                company_name=company_name, structured=structured, store=store,
            )
            if accepted[0]:
                return accepted
    for cached in cached_docs:
        if (cached.source or "").lower().startswith("fmp"):
            return cached.text, cached.source

    # 4) FMP auto-fetch (latest transcript) if a paid key is configured
    text, src = _fmp(symbol, fiscal_label)
    if text:
        accepted = _accepted(symbol, fiscal_label, text, src,
                             company_name=company_name, store=store)
        if accepted[0]:
            return accepted

    # 5) web search (Tavily) -> the fool.com / investing transcript page (free tier)
    text, src = _from_search(symbol, fiscal_label, company_name)
    if text:
        accepted = _accepted(symbol, fiscal_label, text, src,
                             company_name=company_name, store=store)
        if accepted[0]:
            return accepted

    # 6) secondary: a transcript article already in our news feed (if any)
    text, src = _from_news(symbol, fiscal_label=fiscal_label)
    if text:
        accepted = _accepted(symbol, fiscal_label, text, src,
                             company_name=company_name, store=store)
        if accepted[0]:
            return accepted

    return "", "none"


# --------------------------------------------------------------------------- #
# Web-search transcript fetch (Tavily)
# --------------------------------------------------------------------------- #
def _looks_transcript(res: dict) -> bool:
    return "transcript" in (res.get("url", "") + res.get("title", "")).lower()


def _tavily_search(key: str, query: str) -> list[dict]:
    try:
        import httpx

        # NOTE: deliberately no `time_range` — measured, it pulls in OLDER quarters
        # (transcript pages get re-crawled), which is the opposite of what we want.
        r = httpx.post("https://api.tavily.com/search", timeout=40, json={
            "api_key": key, "query": query,
            "include_domains": ["fool.com", "investing.com"],
            "include_raw_content": True, "max_results": 8})
        r.raise_for_status()
        return r.json().get("results", []) or []
    except Exception:  # noqa: BLE001
        return []


def _pick_candidate(results: list[dict], target: tuple[int | None, int | None]) -> dict | None:
    """Choose the result that REPORTS `target` (year, quarter).

    Selection is by reporting period, with length only as a tie-break. Ranking by
    length is what put an 8-year-old Q4 2018 Alphabet call at the top of a "latest
    transcript" search: every quarter's page is long, so length says nothing about
    recency. When the target quarter is known and nothing matches we return None
    rather than the longest page — a wrong quarter scored against this quarter's
    expectations produces a confident, entirely fictional miss.
    """
    from . import fiscal

    usable = [r for r in results
              if _looks_transcript(r)
              and len(r.get("raw_content") or r.get("content") or "") >= _MIN_TRANSCRIPT_CHARS]
    ty, tq = target
    if ty and tq:
        matched = [r for r in usable
                   if fiscal.detect_period(r.get("raw_content") or "", r.get("url", "")) == (ty, tq)]
        if not matched:
            return None
        return max(matched, key=lambda r: len(r.get("raw_content") or ""))
    # An unresolved target cannot safely select an automatic page. Keep every result
    # outside the trusted asset path until an earnings event supplies year + quarter.
    return None


def _from_search(symbol: str, fiscal_label: str = "",
                 company_name: str = "") -> tuple[str, str]:
    """Find + read THIS QUARTER's earnings-call transcript via Tavily web search.

    Restricts to free transcript sources and uses Tavily's extracted page text.
    Needs TAVILY_API_KEY (free tier); degrades to ('','') without it.
    """
    from ..config import get_config
    from . import fiscal

    key = get_config().secrets.tavily_api_key
    if not key:
        return "", ""

    target = fiscal.parse_label(fiscal_label)
    ty, tq = target
    if ty and tq:
        # Naming the quarter is what surfaces the right page at all: measured, the
        # bare "latest earnings call transcript" query returned five wrong quarters
        # for GOOG and no Q2 2026, while "<name> <SYM> Q2 2026 ..." returned it #1.
        who = f"{company_name} {symbol}".strip()
        queries = [f"{who} Q{tq} {ty} earnings call transcript",
                   f"{symbol} fiscal Q{tq} {ty} earnings call transcript"]
    else:
        queries = [f"{symbol} latest earnings call transcript"]

    for query in queries:
        pick = _pick_candidate(_tavily_search(key, query), target)
        if pick:
            content = pick.get("raw_content") or pick.get("content") or ""
            return content, f"tavily:{pick.get('url')}"
    return "", ""


# --------------------------------------------------------------------------- #
# News-driven transcript fetch
# --------------------------------------------------------------------------- #
_TRANSCRIPT_HINTS = ("earnings call transcript", "call transcript", "earnings transcript")
_MIN_TRANSCRIPT_CHARS = 2000          # a real transcript is long; skip stubs/paywalls
_PREFERRED = ("fool.com", "investing.com")


def _from_news(symbol: str, lookback_days: int = 10,
               fiscal_label: str = "") -> tuple[str, str]:
    """Locate the earnings-call transcript article in recent news and scrape it.

    Tight lookback so we get THIS quarter's transcript (published within days of
    the call), not last quarter's stale one. Degrades to ('','').
    """
    from datetime import datetime, timedelta, timezone

    from . import fiscal

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
    ty, tq = fiscal.parse_label(fiscal_label)
    for it in candidates:
        try:
            text = _fetch_url(it.url)
        except Exception:  # noqa: BLE001
            continue
        if len(text) < _MIN_TRANSCRIPT_CHARS:
            continue
        # Same rule as the search path: a recent article can still be about last
        # quarter (re-publications, round-ups). Reject a confirmed wrong quarter.
        if ty and tq and fiscal.detect_period(text, it.url) not in (None, (ty, tq)):
            continue
        return text, f"news:{it.source}:{it.url}"
    return "", ""


def _fmp(symbol: str, fiscal_label: str = "") -> tuple[str, str]:
    """FinancialModelingPrep latest earnings-call transcript (current /stable API).

    Transcripts are a PAID FMP feature. VERIFIED 2026-07: this key returns HTTP 402
    "Restricted Endpoint" for every symbol, so in practice this path always degrades
    to ('','') and the search / manual-drop paths do the work. Kept because a plan
    upgrade would light it up for free — don't spend time debugging it as if broken.
    """
    from ..config import get_config
    from . import fiscal

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
                src = f"fmp:{period}-{fy}"
                # limit=1 is "latest", which early in a print cycle is still LAST
                # quarter — reject a confirmed mismatch instead of scoring on it.
                ty, tq = fiscal.parse_label(fiscal_label)
                if ty and tq and fiscal.detect_period(content, src) not in (None, (ty, tq)):
                    return "", ""
                return content, src
    except Exception:  # noqa: BLE001 - degrade quietly
        return "", ""
    return "", ""
