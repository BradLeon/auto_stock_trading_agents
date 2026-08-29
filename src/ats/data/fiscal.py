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
from datetime import date, datetime
from html import unescape

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
# Two-digit fiscal year — the form US investor decks put on their cover ("Q2 FY26",
# often with the newline PDF extraction leaves behind). `fy` is required: a bare
# "Q2 26" is far too easy to hit by accident inside a table. Without this an
# 11-month-old NVIDIA deck read as "period undetectable" and was waved through.
_SLUG_QY2 = re.compile(r"q([1-4])\s*fy\s*(\d\d)\b", re.I)


def _find_period(hay: str) -> tuple[int, int] | None:
    if not hay:
        return None
    m = _SLUG_QY.search(hay)
    if m:
        return (int(m.group(2)), int(m.group(1)))
    m = _SLUG_QY2.search(hay)
    if m:
        return (2000 + int(m.group(2)), int(m.group(1)))
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

    - target quarter not encoded in the label -> reject as unresolved.
    - transcript period undetectable          -> reject as unresolved.
    - CONFIRMED mismatch (period != target)   -> reject; caller must refuse to score.
    """
    ty, tq = parse_label(label)
    if ty is None or tq is None:
        return (False, f"目标季未在 fiscal_label='{label}' 中完整编码，period unresolved")
    period = detect_period(text, source)
    if period is None:
        return (False, f"transcript 报告期无法从来源/正文识别（source={source!r}），"
                       "period unresolved")
    py, pq = period
    if (py, pq) == (ty, tq):
        return (True, f"报告期核对通过：{py}Q{pq}")
    return (False, f"transcript 报告期 {py}Q{pq} ≠ 目标季 {ty}Q{tq}")


# --------------------------------------------------------------------------- #
# Earnings-release reporting-period verification
# --------------------------------------------------------------------------- #
# A release is not a transcript: its headline commonly names the reported quarter,
# while the body also names the prior-year comparison and the next-quarter outlook.
# Consequently `detect_period()`'s intentionally simple first-match rule must not be
# reused here.  These patterns collect *all* explicit quarter/year pairs and the
# scorer below decides which pair is the primary disclosure period.
_RELEASE_QY = re.compile(r"\bq\s*([1-4])\s*(?:fy\s*)?(20\d\d)\b", re.I)
_RELEASE_YQ = re.compile(r"\b(20\d\d)\s*(?:fy\s*)?q\s*([1-4])\b", re.I)
_RELEASE_WORD_QY = re.compile(
    r"\b(first|second|third|fourth)\s+(?:fiscal\s+)?quarter"
    r"(?:(?![.;]).){0,100}?\b(20\d\d)\b",
    re.I,
)
_RELEASE_FY_WORD_Q = re.compile(
    r"\b(?:fiscal\s+)?(20\d\d)\s+"
    r"(first|second|third|fourth)\s+quarter\b",
    re.I,
)
_RELEASE_QUARTER_ENDED_DATE = re.compile(
    r"\bquarter\s+ended\s+([A-Za-z]+)\s+(\d{1,2}),?\s+(20\d\d)\b", re.I)


def _release_period_candidates(text: str, source: str = "") -> list[tuple[int, int, int, str]]:
    """Return scored ``(year, quarter, score, evidence)`` release-period candidates."""
    raw = unescape(f"{source or ''}\n{(text or '')[:12000]}")
    normalized = re.sub(r"[\t\r ]+", " ", raw)
    # Keep sentences short enough that a comparison/guidance qualifier only affects
    # the period it describes. Newlines are meaningful in release headlines.
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+|\n+", normalized)
                 if item.strip()]
    candidates: list[tuple[int, int, int, str]] = []
    # Regulatory 6-K tables explicitly label the reported column "Current Period".
    # This local structural signal outranks comparison columns even when HTML table
    # extraction flattens the whole header into one sentence.
    for match in re.finditer(
            r"\bcurrent\s+period\b(?:(?![.;]).){0,180}?"
            r"\b(first|second|third|fourth)\s+quarter\b"
            r"(?:(?![.;]).){0,60}?\b(20\d\d)\b",
            normalized, re.I):
        candidates.append((
            int(match.group(2)), _WORD_Q[match.group(1).lower()], 14, match.group(0)))

    def score_sentence(sentence: str, index: int) -> int:
        low = sentence.lower()
        score = 2
        if index < 5:
            score += 3
        if any(marker in low for marker in (
                "reports", "results", "announces", "announced", "financial results")):
            score += 5
        if "quarter ended" in low or "fiscal quarter" in low:
            score += 2
        if any(marker in low for marker in (
                "guidance", "outlook", "expects", "forecast", "projection")):
            score -= 9
        if any(marker in low for marker in (
                "compared", "corresponding", "prior-year", "prior year", "year-ago")):
            score -= 7
        return score

    for index, sentence in enumerate(sentences):
        found: list[tuple[int, int, str]] = []
        for match in _RELEASE_QY.finditer(sentence):
            found.append((int(match.group(2)), int(match.group(1)), match.group(0)))
        for match in _RELEASE_YQ.finditer(sentence):
            found.append((int(match.group(1)), int(match.group(2)), match.group(0)))
        for match in _RELEASE_WORD_QY.finditer(sentence):
            found.append((int(match.group(2)), _WORD_Q[match.group(1).lower()], match.group(0)))
        for match in _RELEASE_FY_WORD_Q.finditer(sentence):
            found.append((int(match.group(1)), _WORD_Q[match.group(2).lower()], match.group(0)))
        sentence_score = score_sentence(sentence, index)
        for year, quarter, evidence in found:
            candidates.append((year, quarter, sentence_score, evidence))

        # Some issuers put the year in a dateline immediately after a quarter-only
        # headline: "Fourth Quarter Results. July 29, 2026."  Bind those two only
        # when the first sentence is explicitly a results/reporting headline.
        if not found and index + 1 < len(sentences):
            word_match = re.search(
                r"\b(first|second|third|fourth)\s+(?:fiscal\s+)?quarter\b",
                sentence, re.I)
            next_year = re.search(r"\b(20\d\d)\b", sentences[index + 1])
            if word_match and next_year and any(
                    marker in sentence.lower() for marker in ("results", "reports")):
                candidates.append((
                    int(next_year.group(1)), _WORD_Q[word_match.group(1).lower()],
                    sentence_score, f"{word_match.group(0)} / {next_year.group(0)}",
                ))
    return candidates


def verify_release_period(label: str, text: str, source: str = "", *,
                          event_date: str | date | None = None) -> tuple[bool, str]:
    """Verify that an earnings release's *primary* reporting period matches ``label``.

    Unlike transcript verification, this considers every explicit period and gives
    precedence to results/reporting headlines while discounting comparison and
    guidance sentences. This prevents a stale Q1 release containing Q2 guidance from
    being admitted as Q2, without rejecting releases that also mention Q2 2025.
    """
    target = parse_label(label)
    if None in target:
        return (False, f"目标季未在 fiscal_label='{label}' 中完整编码，period unresolved")
    candidates = _release_period_candidates(text, source)
    if not candidates:
        # Some issuers name the result only by its period end-date.  That date
        # cannot encode a fiscal quarter by itself, so use it only with the
        # independently resolved release event and a bounded report-to-release lag.
        if event_date:
            raw_event_date = event_date.isoformat() if isinstance(event_date, date) else str(event_date)
            try:
                released = date.fromisoformat(raw_event_date[:10])
            except ValueError:
                released = None
            target_year, _target_quarter = target
            normalized = unescape(f"{source or ''}\n{text or ''}")[:12000]
            for match in _RELEASE_QUARTER_ENDED_DATE.finditer(normalized):
                try:
                    ended = datetime.strptime(
                        f"{match.group(1)} {match.group(2)} {match.group(3)}",
                        "%B %d %Y").date()
                except ValueError:
                    continue
                interval = (released - ended).days if released else -1
                if ended.year == target_year and 0 <= interval <= 100:
                    return (True, f"主报告期按期末日与事件绑定核对通过：{ended.isoformat()}")
        return (False, "earnings release 主报告期无法识别，period unresolved")
    ty, tq = target
    target_scores = [score for year, quarter, score, _ in candidates
                     if (year, quarter) == (ty, tq)]
    best_target = max(target_scores, default=-999)
    best_period = max(candidates, key=lambda item: item[2])
    # Seven requires either a results/reporting signal, or an early fiscal-quarter
    # statement. A target mentioned only in guidance/comparison remains well below it.
    # Some release headlines put "Q2 2026 results" and Q3 outlook in one flattened
    # HTML sentence. The target remains the best/only detected period but its score is
    # reduced by the outlook qualifier. A positive target that is still the best
    # candidate is primary; guidance-only targets remain negative and lose to the
    # actually reported period.
    if best_target >= 1 and best_target >= best_period[2]:
        return (True, f"主报告期核对通过：{ty}Q{tq}")
    py, pq, score, _ = best_period
    return (False, f"earnings release 主报告期 {py}Q{pq} ≠ 目标季 {ty}Q{tq}"
                   f"（target_score={best_target}, primary_score={score}）")
