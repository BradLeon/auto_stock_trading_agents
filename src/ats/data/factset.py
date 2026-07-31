"""FactSet Earnings Insight — weekly S&P 500 earnings/valuation backdrop.

The stable landing URL (factset.com/earningsinsight) 302-redirects to the current
week's date-coded PDF, so auto-download is a single redirect-following GET. Falls
back to the newest local PDF in the folder if the download fails (user-dropped
copies). Feeds the macro strategist's earnings/valuation regime. Never raises.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from ..schemas.macro_strategy import EarningsBackdrop
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


# --------------------------------------------------------------------------- #
# Key Metrics parsing
# --------------------------------------------------------------------------- #
# The aggregate numbers the macro framework needs live in the page-1 "Key Metrics"
# bullets as clean prose — NOT in the chart/table pages that pypdf garbles. So no
# table-extraction or vision dependency is needed; a validated regex pass is enough.
#
# Two things make this less trivial than it looks, both observed in real files:
#   · wording shifts with the earnings season — "estimated" before the quarter
#     reports vs "blended" after; the Scorecard bullet is absent until companies
#     start reporting; guidance counts reset at the quarter roll.
#   · pypdf sometimes injects a space INSIDE a number ("was 1 8.8%"). A naive
#     `[\d.]+` then silently captures "8.8" — a wrong number, which is worse than
#     a missing one. `_num` strips internal spaces and every field is range-checked.

_PLAUSIBLE = {                       # field -> (low, high), inclusive
    "growth_pct": (-100.0, 300.0),
    "prior_growth_pct": (-100.0, 300.0),
    "pct_reported": (0.0, 100.0),
    "pct_eps_beat": (0.0, 100.0),
    "pct_revenue_beat": (0.0, 100.0),
    "fwd_pe": (5.0, 60.0),
    "fwd_pe_5y_avg": (5.0, 60.0),
    "fwd_pe_10y_avg": (5.0, 60.0),
}


def _num(raw: str | None) -> float | None:
    """Parse a number that may contain pypdf's stray internal spaces."""
    if raw is None:
        return None
    try:
        return float(re.sub(r"\s+", "", raw))
    except ValueError:
        return None


def _quarter(raw: str | None) -> str:
    """"Q 2 2026" (pypdf splits it) -> "Q2 2026"."""
    if not raw:
        return ""
    m = re.match(r"Q\s*(\d)\s*(\d{4})", raw)
    return f"Q{m.group(1)} {m.group(2)}" if m else re.sub(r"\s+", " ", raw).strip()


def _find(flat: str, pattern: str) -> str | None:
    m = re.search(pattern, flat, re.I)
    return m.group(1) if m else None


# Digits possibly broken up by pypdf, e.g. "1 8.8" or "20.1".
_D = r"(-?[\d\s]*\d(?:\s*\.\s*\d+)?)"


def parse_key_metrics(text: str, *, source: str = "") -> EarningsBackdrop:
    """Extract the page-1 Key Metrics into a validated EarningsBackdrop.

    Per-field degradation: a field that is absent or implausible is dropped with a
    note, and the fields that did parse are kept. Only a total failure to find the
    anchor valuation figure marks the whole thing `degraded`.
    """
    out = EarningsBackdrop(source=source)
    if not text:
        out.degraded = True
        out.notes.append("no text")
        return out
    flat = re.sub(r"\s+", " ", text)

    raw_date = _find(flat, r"([A-Z][a-z]+ \d{1,2}, \d{4})")
    if raw_date:
        try:
            out.report_date = datetime.strptime(raw_date, "%B %d, %Y").date()
        except ValueError:
            pass

    out.quarter = _quarter(_find(flat, r"Earnings Growth: For (Q\s*\d\s*\d{4})"))

    basis = _find(flat, r"the (estimated|blended) \(year-over-year\) earnings growth rate")
    out.growth_basis = (basis or "").lower()
    out.growth_pct = _num(_find(
        flat, r"(?:estimated|blended) \(year-over-year\) earnings growth rate "
              rf"for the S&P 500 is {_D}\s*%"))

    out.prior_growth_pct = _num(_find(
        flat, r"estimated \(year-over-year\) earnings growth rate for the S&P 500 "
              rf"for Q\s*\d\s*\d{{4}} was {_D}\s*%"))
    out.prior_as_of = _find(flat, r"Earnings Revisions: On ([A-Z][a-z]+ \d{1,2})") or ""

    sectors = _find(flat, r"(\w+) sectors are (?:expected to report|reporting) higher earnings")
    out.sectors_higher = _WORD_NUM.get((sectors or "").lower())
    if out.sectors_higher is None and (sectors or "").isdigit():
        out.sectors_higher = int(sectors)
    out.revision_direction = (_find(flat, r"due to (upward|downward) revisions") or "").lower()

    out.guidance_quarter = _quarter(_find(flat, r"Earnings Guidance: For (Q\s*\d\s*\d{4})"))
    neg = _find(flat, r"(\d+) S&P 500 companies have issued negative EPS guidance")
    pos = _find(flat, r"(\d+) S&P 500 companies have issued positive EPS guidance")
    out.guidance_negative = int(neg) if neg else None
    out.guidance_positive = int(pos) if pos else None

    out.pct_reported = _num(_find(flat, rf"with {_D}\s*% of S&P 500 companies reporting"))
    out.pct_eps_beat = _num(_find(
        flat, rf"{_D}\s*% of S&P 500 companies have reported a positive EPS surprise"))
    out.pct_revenue_beat = _num(_find(
        flat, rf"{_D}\s*% of S&P 500 companies has? reported a positive revenue surprise"))

    out.fwd_pe = _num(_find(
        flat, rf"forward 12\s*-?\s*month P/E ratio for the S&P 500 is {_D}"))
    out.fwd_pe_5y_avg = _num(_find(flat, rf"the 5-year average \({_D}\)"))
    out.fwd_pe_10y_avg = _num(_find(flat, rf"the 10-year average \({_D}\)"))

    _validate(out)
    if out.fwd_pe is None:
        out.degraded = True
        out.notes.append("forward P/E not found — 版式可能已变，退回散文模式")
    return out


_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11}


def _validate(bd: EarningsBackdrop) -> None:
    """Drop implausible values. A wrong number is worse than a missing one — a
    garbled parse must never reach the model looking like a fact."""
    for field, (lo, hi) in _PLAUSIBLE.items():
        val = getattr(bd, field)
        if val is not None and not (lo <= val <= hi):
            bd.notes.append(f"{field}={val} 超出合理区间 [{lo}, {hi}]，已丢弃")
            setattr(bd, field, None)
