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
from pathlib import Path

from .base import safe_fetch

log = logging.getLogger("ats.data.documents")
name = "documents"

_MIN_DOC_CHARS = 1000


_RELEASE_KW = ("earning", "release", "press", "8-k", "8k")
_DECK_KW = ("presentation", "investor", "deck", "slide")


def gather(symbol: str, docs_root: str | None = None) -> list[tuple[str, str]]:
    """Earnings release + investor presentation + any extra curated docs.

    Priority per doc type: a curated file in <docs_root>/<SYM>/ (most precise) wins;
    otherwise auto-fetch — release from SEC 8-K, deck via Tavily. No duplicates.
    """
    folder = _from_folder(symbol, docs_root)
    used: set[str] = set()
    docs: list[tuple[str, str]] = []

    f_release = _classify(folder, _RELEASE_KW)
    if f_release:
        docs.append(f_release); used.add(f_release[0])
    else:
        rel = safe_fetch(lambda: _sec_8k_release(symbol), source=f"sec-8k:{symbol}", attempts=2)
        if rel:
            docs.append(rel)

    f_deck = _classify(folder, _DECK_KW)
    if f_deck:
        docs.append(f_deck); used.add(f_deck[0])
    else:
        deck = safe_fetch(lambda: _tavily_deck(symbol), source=f"tavily-deck:{symbol}", attempts=1)
        if deck:
            docs.append(deck)

    docs += [d for d in folder if d[0] not in used]   # extra curated docs (e.g. a saved 10-K)
    return docs


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
    return (f"SEC 8-K earnings release ({filed})", text)


def _text(html: str) -> str:
    t = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


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
        text = safe_fetch(lambda p=p: _read_doc(p), source=f"doc:{p.name}", attempts=1)
        if text and len(text) >= _MIN_DOC_CHARS:
            out.append((f"doc:{p.name}", text))
    return out


def _read_doc(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    if suffix in (".txt", ".md", ".htm", ".html"):
        raw = path.read_text(encoding="utf-8", errors="ignore")
        return _text(raw) if suffix in (".htm", ".html") else raw
    return ""
