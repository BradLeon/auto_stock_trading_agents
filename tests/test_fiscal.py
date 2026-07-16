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

    # undetectable period -> allowed but flagged (⚠️), never a silent pass
    ok, why = fiscal.verify_transcript("Q2 FY2026", "plain body", "none")
    assert ok is True and "⚠️" in why

    # target quarter not encoded -> can't compare, skip the check
    ok, why = fiscal.verify_transcript("Q FY2026", "body", "tavily:...q1-2026")
    assert ok is True and "跳过" in why
