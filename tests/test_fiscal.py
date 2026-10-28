"""Fiscal-period parsing, canonical filename tags, and the transcript period guard."""

from ats.data import fiscal


def test_parse_label_extracts_year_and_quarter():
    assert fiscal.parse_label("Q2 FY2026") == (2026, 2)
    assert fiscal.parse_label("Q2 2026") == (2026, 2)
    assert fiscal.parse_label("Q4 FY2025") == (2025, 4)
    assert fiscal.parse_label("Q2 FY2027") == (2027, 2)
    # legacy label with no quarter encoded
    assert fiscal.parse_label("Q FY2026") == (2026, None)
    assert fiscal.parse_label("") == (None, None)


def test_canonical_tag_surfaces_quarter():
    assert fiscal.canonical_tag("Q2 FY2026") == "2026Q2"
    assert fiscal.canonical_tag("Q4 FY2025") == "2025Q4"
    assert fiscal.canonical_tag("Q FY2026") == "2026"          # year-only fallback
    assert fiscal.canonical_tag("") == "latest"


def test_detect_period_prefers_source_slug():
    # The URL slug / fmp period names the REPORTING quarter directly and is immune
    # to in-body next-quarter guidance mentions.
    assert fiscal.detect_period("body", "url:...stays-hot-in-q2-2026-93CH") == (2026, 2)
    assert fiscal.detect_period("body", "tavily:...tsmcs-q1-2026-shows") == (2026, 1)
    assert fiscal.detect_period("body", "fmp:Q2-2026") == (2026, 2)
    assert fiscal.detect_period("body", "news:fool:...tsm-q4-2022-ear") == (2022, 4)
    # falls back to body prose when source has no period
    assert fiscal.detect_period("TSMC second quarter 2026 earnings call", "none") == (2026, 2)
    assert fiscal.detect_period("no period here", "none") is None


def test_verify_transcript_rejects_wrong_quarter():
    # The exact bug: a Q1 transcript scored against Q2 target must be rejected.
    ok, why = fiscal.verify_transcript("Q2 FY2026", "body", "tavily:...q1-2026")
    assert ok is False and "≠" in why

    ok, why = fiscal.verify_transcript("Q2 FY2026", "body", "url:...q2-2026")
    assert ok is True

    # An automatic candidate with an undetectable period is quarantined.
    ok, why = fiscal.verify_transcript("Q2 FY2026", "plain body", "none")
    assert ok is False and "period unresolved" in why

    # An incomplete target cannot authorize an automatic candidate either.
    ok, why = fiscal.verify_transcript("Q FY2026", "body", "tavily:...q1-2026")
    assert ok is False and "period unresolved" in why


def test_catalog_declared_period_is_authoritative_when_the_text_cannot_be_read():
    """defeatbeta transcripts open with 'September Quarter 2025 Earnings Conference Call'; the catalog still knows
    fiscal year and quarter as fields.  Without this the guard aborted every event review on such a transcript."""
    body = "## Prepared Remarks\n\n**Operator:** Good day, and welcome to the September Quarter 2026 call."
    assert fiscal.detect_period(body, "") is None
    ok, why = fiscal.verify_transcript("Q1 FY2027", body, "platform:defeatbeta", declared_period="Q1 FY2027")
    assert ok and "目录声明" in why
    ok, why = fiscal.verify_transcript("Q1 FY2027", body, "platform:defeatbeta", declared_period="Q4 FY2026")
    assert not ok and "≠" in why
    # an incomplete declaration falls back to reading the text, exactly as before
    ok, why = fiscal.verify_transcript("Q1 FY2027", body, "platform:defeatbeta", declared_period="Q FY2027")
    assert not ok and "无法" in why
