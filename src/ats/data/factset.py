"""FactSet Earnings Insight — weekly S&P 500 earnings/valuation backdrop.

The stable landing URL (factset.com/earningsinsight) 302-redirects to the current
week's date-coded PDF, so auto-download is a single redirect-following GET. Falls
back to the newest local PDF in the folder if the download fails (user-dropped
copies). Feeds the macro strategist's earnings/valuation regime. Never raises.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .base import safe_fetch

log = logging.getLogger("ats.data.factset")

name = "factset"

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def fetch_earnings_insight(cfg: dict) -> tuple[str, str]:
    """Return (commentary_text, source_label). ('', 'none') if unavailable."""
    if not cfg.get("enabled", True):
        return "", "disabled"
    folder = Path(cfg.get("folder", "") or "")

    path = None
    if cfg.get("download", True) and cfg.get("url"):
        path = safe_fetch(lambda: _download(cfg["url"], folder),
                          source="factset:download", attempts=2)
    if path is None:
        path = _newest_local(folder)
        if path is not None:
            log.info("factset: using local PDF %s (download unavailable)", path.name)
    if path is None or not path.is_file():
        return "", "none"

    text = safe_fetch(lambda: _extract(path, int(cfg.get("max_pages", 16))),
                      source=f"factset:read:{path.name}", attempts=1) or ""
    return text[:int(cfg.get("max_chars", 14000))], f"factset:{path.name}"


def _download(url: str, folder: Path) -> Path:
    import httpx

    r = httpx.get(url, headers={"User-Agent": _UA}, timeout=45, follow_redirects=True)
    r.raise_for_status()
    if "application/pdf" not in r.headers.get("content-type", "").lower():
        raise ValueError(f"not a pdf: {r.headers.get('content-type')}")
    fname = Path(str(r.url).split("?")[0]).name or "EarningsInsight_latest.pdf"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / fname
    p.write_bytes(r.content)
    log.info("factset: downloaded %s (%d KB)", fname, len(r.content) // 1024)
    return p


def _newest_local(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    pdfs = sorted(folder.glob("EarningsInsight_*.pdf"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    return pdfs[0] if pdfs else None


def _extract(path: Path, max_pages: int) -> str:
    """Extract the commentary pages (clean prose; chart/table pages garble in pypdf)."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = reader.pages[:max_pages]
    return "\n".join((pg.extract_text() or "") for pg in pages).strip()
