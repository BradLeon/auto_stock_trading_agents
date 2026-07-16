"""Fiscal-period parsing, canonical file tags, and transcript-period verification.

Two jobs:
  1. Turn a config `fiscal_label` ("Q2 FY2026", "Q2 2026", "Q4 FY2025") into a
     structured (year, quarter) and a canonical filename tag ("2026Q2") so every
     PEAD document names the exact fiscal quarter it covers — easy per-company
     history browsing/sorting.
  2. Guard the score path: confirm the fetched earnings-call transcript actually
     REPORTS the target quarter, not merely mentions it as forward guidance. This
     is what caught us — a stale Q1 2026 transcript (which contains Q2 guidance)
     was scored against Q2 expectations, producing a spurious miss.
"""

from __future__ import annotations

import re

_WORD_Q = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def parse_label(label: str) -> tuple[int | None, int | None]:
    """('Q2 FY2026') -> (2026, 2); ('Q FY2026') -> (2026, None); ('') -> (None, None)."""
    if not label:
        return (None, None)
    s = label.strip()
    ym = re.search(r"(20\d\d)", s)
    year = int(ym.group(1)) if ym else None
    qm = re.search(r"Q\s*([1-4])", s, re.I)          # "Q2", "Q 2" — NOT "Q FY..."
    quarter = int(qm.group(1)) if qm else None
    if quarter is None:
        wm = re.search(r"(first|second|third|fourth)\s+quarter", s, re.I)
        if wm:
            quarter = _WORD_Q[wm.group(1).lower()]
    return (year, quarter)


def canonical_tag(label: str) -> str:
    """Filename-safe tag that surfaces the fiscal quarter: '2026Q2'.
    Falls back to year-only, then to a sanitized label, so nothing ever crashes."""
    year, quarter = parse_label(label)
    if year and quarter:
        return f"{year}Q{quarter}"
    if year:
        return str(year)
    return re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-") or "latest"


# --------------------------------------------------------------------------- #
# Transcript reporting-period detection
# --------------------------------------------------------------------------- #
# Order matters: the source label (URL slug / fmp period) names the REPORTING
# quarter directly and is immune to in-body guidance mentions, so try it first.
_SLUG_QY = re.compile(r"q([1-4])[-_\s]?(?:fy)?[-_\s]?(20\d\d)", re.I)   # q2-2026, Q2FY2026
_SLUG_YQ = re.compile(r"(20\d\d)[-_\s]?q([1-4])", re.I)                 # 2026-q2, 2026Q2
_WORD_QY = re.compile(r"(first|second|third|fourth)[-\s]+quarter[^.]{0,14}?(20\d\d)", re.I)


def _find_period(hay: str) -> tuple[int, int] | None:
    if not hay:
        return None
    m = _SLUG_QY.search(hay)
    if m:
        return (int(m.group(2)), int(m.group(1)))
    m = _SLUG_YQ.search(hay)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = _WORD_QY.search(hay)
    if m:
        return (int(m.group(2)), _WORD_Q[m.group(1).lower()])
    return None


def detect_period(text: str, source: str = "") -> tuple[int, int] | None:
    """Best-effort (year, quarter) that a transcript primarily REPORTS.
    Prefers the source label (URL slug / fmp period); falls back to the head of
    the body. Returns None when it can't tell."""
    for hay in (source or "", (text or "")[:2500]):
        p = _find_period(hay)
        if p:
            return p
    return None


def verify_transcript(label: str, text: str, source: str = "") -> tuple[bool, str]:
    """Policy gate for scoring. Returns (ok, reason).

    - target quarter not encoded in the label -> can't compare, allow (skip).
    - transcript period undetectable          -> allow, but reason flags the gap.
    - CONFIRMED mismatch (period != target)   -> reject; caller must refuse to score.
    """
    ty, tq = parse_label(label)
    if tq is None:
        return (True, f"目标季未在 fiscal_label='{label}' 中编码，跳过报告期核对")
    period = detect_period(text, source)
    if period is None:
        return (True, f"⚠️ transcript 报告期无法从来源/正文识别（source={source!r}），"
                      f"未能核对是否={ty}Q{tq}，谨慎放行")
    py, pq = period
    if (py, pq) == (ty, tq):
        return (True, f"报告期核对通过：{py}Q{pq}")
    return (False, f"transcript 报告期 {py}Q{pq} ≠ 目标季 {ty}Q{tq}")
