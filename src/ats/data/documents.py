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


def gather(symbol: str, docs_root: str | None = None) -> list[tuple[str, str]]:
    docs: list[tuple[str, str]] = []
    release = safe_fetch(lambda: _sec_8k_release(symbol), source=f"sec-8k:{symbol}", attempts=2)
    if release:
        docs.append(release)
    docs += _from_folder(symbol, docs_root)
    return docs


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
