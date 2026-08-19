"""Official document sources for fundamental analysis.

  (a) Earnings release  -> auto from the latest SEC 8-K Exhibit 99.1 (canonical,
      free, no IR-site scraping).
  (b) Investor presentation / other -> read from a local folder (<docs_root>/<SYM>/),
      where you drop the PDFs you download from the IR site. PDFs parsed with pypdf.

Returns a list of (label, text). Each source degrades independently.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from pathlib import Path

from .base import safe_fetch

log = logging.getLogger("ats.data.documents")
name = "documents"

_MIN_DOC_CHARS = 1000


_RELEASE_KW = ("earning", "release", "press", "8-k", "8k")
_DECK_KW = ("presentation", "investor", "deck", "slide")


def gather(symbol: str, docs_root: str | None = None, *, period: str = "",
           store=None) -> list[tuple[str, str]]:
    """Earnings release + investor presentation + any extra curated docs.

    Priority per doc type: a curated file in <docs_root>/<SYM>/ (most precise) wins;
    otherwise auto-fetch — release from SEC 8-K, deck via Tavily. No duplicates.
    """
    folder = _from_folder(symbol, docs_root)
    used: set[str] = set()
    docs: list[tuple[str, str]] = []
    new_docs: list[tuple[str, str]] = []
    metadata: dict[str, dict] = {}

    f_release = _classify(folder, _RELEASE_KW)
    if f_release:
        docs.append(f_release); new_docs.append(f_release); used.add(f_release[0])
    else:
        cached = _cached(symbol, period, "release")
        if cached:
            docs.append(cached)
        else:
            rel = safe_fetch(lambda: sec_8k_release(symbol), source=f"sec-8k:{symbol}", attempts=2)
            if rel:
                item = (rel["label"], rel["text"])
                docs.append(item); new_docs.append(item)
                metadata[rel["label"]] = rel

    f_deck = _classify(folder, _DECK_KW)
    if f_deck:
        docs.append(f_deck); new_docs.append(f_deck); used.add(f_deck[0])
    else:
        cached = _cached(symbol, period, "deck")
        if cached:
            docs.append(cached)
        else:
            deck = safe_fetch(lambda: _tavily_deck(symbol), source=f"tavily-deck:{symbol}", attempts=1)
            if deck:
                docs.append(deck); new_docs.append(deck)

    extras = [d for d in folder if d[0] not in used]
    docs += extras                                  # extra curated docs (e.g. a saved 10-K)
    new_docs += extras
    _persist(symbol, period, new_docs, metadata=metadata, store=store)
    return docs


def _cached(symbol: str, period: str, doc_type: str) -> tuple[str, str] | None:
    if not period:
        return None
    from . import source_cache

    doc = source_cache.load(symbol, period, doc_type)
    if not doc:
        return None
    return (doc.title or f"cached {doc_type} ({period})", doc.text)


def _persist(symbol: str, period: str, docs: list[tuple[str, str]], *,
             metadata: dict[str, dict] | None = None, store=None) -> None:
    """Register accepted official documents while preserving the tuple API."""
    from . import document_assets

    metadata = metadata or {}
    for label, text in docs:
        meta = metadata.get(label, {})
        low = label.lower()
        if any(k in low for k in _RELEASE_KW):
            doc_type = "release"
        elif any(k in low for k in _DECK_KW):
            doc_type = "deck"
        elif any(k in low for k in ("10-k", "10-q", "6-k", "filing", "annual report")):
            doc_type = "filing"
        else:
            doc_type = "announcement"
        url_match = re.search(r"(?:tavily:)?(https?://[^)\s]+)", label)
        source_url = meta.get("source_url") or (url_match.group(1) if url_match else "")
        source = ("sec" if label.startswith("SEC ") else
                  "tavily" if "tavily:" in label else
                  "manual" if label.startswith("doc:") else "documents")
        # Release/deck are one-per-period roles. Extra filings and announcements use
        # their own stable label so several documents for the same quarter coexist.
        key = period if period and doc_type in {"release", "deck"} else \
            document_assets.stable_key(f"{period}|{label}", prefix=doc_type)
        published = ""
        filed = re.search(r"\((\d{4}-\d{2}-\d{2})\)", label)
        if filed:
            published = filed.group(1)
        document_assets.ingest(
            entity=symbol, key=key, doc_type=doc_type, text=text, source=source,
            source_url=source_url,
            external_id=meta.get("accession") or source_url or f"{symbol}:{label}",
            title=label, published_at=published, related_entities=(symbol,), store=store,
        )


def _classify(folder: list[tuple[str, str]], keywords: tuple[str, ...]) -> tuple[str, str] | None:
    for label, text in folder:
        if any(k in label.lower() for k in keywords):
            return (label, text)
    return None


# --------------------------------------------------------------------------- #
# (a) SEC 8-K Exhibit 99.1 earnings release
# --------------------------------------------------------------------------- #
def _headers() -> dict:
    from ..config import get_config

    return {"User-Agent": get_config().secrets.sec_edgar_user_agent}


def _sec_8k_release(symbol: str) -> tuple[str, str] | None:
    """(label, text) for `gather()`. See sec_8k_release for the dated form."""
    rel = sec_8k_release(symbol)
    return (rel["label"], rel["text"]) if rel else None


def sec_8k_release(symbol: str) -> dict | None:
    """Latest 8-K Exhibit 99.1 as {label, text, filed: date|None}.

    The filing DATE is what makes this usable as a "has the print happened" probe:
    an 8-K lands within minutes of the release, well before the data vendors
    populate an actual EPS. Callers must still check the date against the expected
    print — the newest 8-K may be an unrelated filing, or last quarter's release.
    """
    import httpx

    from .fundamentals import _ticker_to_cik

    cik = _ticker_to_cik().get(symbol.upper())
    if not cik:
        return None
    sub = httpx.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                    headers=_headers(), timeout=20)
    sub.raise_for_status()
    recent = sub.json().get("filings", {}).get("recent", {})
    forms, accns, dates = (recent.get("form", []), recent.get("accessionNumber", []),
                           recent.get("filingDate", []))
    accn = filed = None
    for form, a, d in zip(forms, accns, dates):
        if form == "8-K":
            accn, filed = a.replace("-", ""), d
            break
    if not accn:
        return None

    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn}"
    idx = httpx.get(f"{base}/index.json", headers=_headers(), timeout=20)
    idx.raise_for_status()
    files = [it for it in idx.json().get("directory", {}).get("item", [])
             if it.get("name", "").lower().endswith(".htm")]
    # The press release is the largest 'ex99' exhibit (e.g. d...dex991.htm).
    ex99 = [f for f in files if "ex99" in f["name"].lower()]
    pick = max(ex99 or files, key=lambda f: int(f.get("size", 0)), default=None)
    if not pick:
        return None
    doc = httpx.get(f"{base}/{pick['name']}", headers=_headers(), timeout=20)
    doc.raise_for_status()
    text = _text(doc.text)
    if len(text) < _MIN_DOC_CHARS:
        return None
    filed_date = None
    if filed:
        try:
            filed_date = date.fromisoformat(filed)
        except ValueError:
            pass
    return {"label": f"SEC 8-K earnings release ({filed})", "text": text,
            "filed": filed_date, "source_url": f"{base}/{pick['name']}",
            "accession": accn}


_XBRL_MARKERS = ("IDEA: XBRL DOCUMENT", "Namespace Prefix: dei_", "X - Definition")


def strip_xbrl_boilerplate(text: str) -> str:
    """Drop the SEC XBRL viewer's element dictionary from a rendered filing.

    An 8-K cover page rendered through EDGAR's viewer is ~7k characters of
    "X - Definition / Namespace Prefix: dei_ / Data Type: xbrli:..." with no financial
    content. Two costs, one of them silent: the observer pays to read it, and — worse —
    it fills the head of the document, which is the only window `fiscal.detect_period`
    examines. That is how a stale NVIDIA deck concatenated after one of these cover
    pages came back as "period undetectable" instead of "eleven months old".
    """
    body = text or ""
    if "X - Definition" in body:
        body = body[:body.index("X - Definition")]
    return body.strip()


def _text(html: str) -> str:
    t = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return strip_xbrl_boilerplate(re.sub(r"\s+", " ", t).strip())


# --------------------------------------------------------------------------- #
# Investor presentation via Tavily web search (generalizes across companies)
# --------------------------------------------------------------------------- #
def _tavily_deck(symbol: str) -> tuple[str, str] | None:
    import httpx

    from ..config import get_config

    key = get_config().secrets.tavily_api_key
    if not key:
        return None
    r = httpx.post("https://api.tavily.com/search", timeout=40, json={
        "api_key": key, "query": f"{symbol} latest quarterly investor presentation slides pdf",
        "include_raw_content": True, "max_results": 6})
    r.raise_for_status()
    results = r.json().get("results", []) or []

    def is_deck(x: dict) -> bool:
        return "presentation" in (x.get("url", "") + x.get("title", "")).lower()

    # Prefer an actual .pdf deck over an IR landing page; then longer content.
    ranked = sorted(results, key=lambda x: (is_deck(x), x.get("url", "").lower().endswith(".pdf"),
                                            len(x.get("raw_content") or "")), reverse=True)
    for res in ranked:
        if not is_deck(res):
            continue
        url = res.get("url", "")
        content = res.get("raw_content") or ""
        if len(content) < _MIN_DOC_CHARS and url.lower().endswith(".pdf"):
            content = _download_pdf_text(url)        # Tavily didn't extract -> fetch the PDF
        if len(content) >= _MIN_DOC_CHARS:
            return (f"investor presentation (tavily:{url})", content)
    return None


def _download_pdf_text(url: str) -> str:
    import io

    import httpx

    try:
        from pypdf import PdfReader

        r = httpx.get(url, headers={"User-Agent": _BROWSER_UA}, timeout=40, follow_redirects=True)
        r.raise_for_status()
        reader = PdfReader(io.BytesIO(r.content))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:  # noqa: BLE001
        return ""


_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


# --------------------------------------------------------------------------- #
# (b) Local folder (<docs_root>/<SYM>/) — investor decks etc.
# --------------------------------------------------------------------------- #
def _docs_root(override: str | None) -> str:
    if override:
        return override
    if os.environ.get("ATS_DOCS_ROOT"):
        return os.environ["ATS_DOCS_ROOT"]
    from ..config import load_pead_global

    return load_pead_global().get("docs_root", "") or ""


def _from_folder(symbol: str, docs_root: str | None) -> list[tuple[str, str]]:
    root = _docs_root(docs_root)
    if not root:
        return []
    folder = Path(root) / symbol.upper()
    if not folder.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for p in sorted(folder.iterdir()):
        if _is_auto_cached(p):
            # source_cache writes into this same folder by design (one directory, one
            # reader, so a hand-dropped correction wins). But gather() must skip what we
            # fetched ourselves: this function's output is what gets cached, so reading
            # our own cache back would re-ingest it every run and grow the file without
            # bound. Hand-dropped files have no frontmatter and are still read.
            continue
        text = safe_fetch(lambda p=p: _read_doc(p), source=f"doc:{p.name}", attempts=1)
        if text and len(text) >= _MIN_DOC_CHARS:
            out.append((f"doc:{p.name}", text))
    return out


def _is_auto_cached(path: Path) -> bool:
    """True for files written by data.source_cache (frontmatter with a `source:` line)."""
    if path.suffix.lower() not in (".md", ".txt"):
        return False
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            if fh.readline().strip() != "---":
                return False
            for _ in range(12):
                line = fh.readline()
                if not line or line.strip() == "---":
                    return False
                if line.startswith("source:") and line.split(":", 1)[1].strip():
                    return True
    except OSError:
        return False
    return False


def _read_doc(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    if suffix in (".txt", ".md", ".htm", ".html"):
        raw = path.read_text(encoding="utf-8", errors="ignore")
        return _text(raw) if suffix in (".htm", ".html") else raw
    return ""
