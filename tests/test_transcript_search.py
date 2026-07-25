"""Quarter-targeted transcript selection.

Replays the ACTUAL Tavily result set observed for GOOG on 2026-07-25 (the day after
Alphabet's Q2 2026 print). Ranking those five results by raw_content length — the
old behaviour — puts an 8-year-old Q4 2018 call first. Scoring last quarter's (or
2018's) call against this quarter's expectations produces a confident, fictional
miss, so a wrong quarter must lose to returning nothing at all.
"""

from __future__ import annotations

import pytest

from ats.data import transcript

BODY = ("Operator: Good afternoon. Prepared Remarks follow. "
        "Chief Executive Officer speaking. Next question please. "
        "Chief Financial Officer here. Question-and-Answer session. ") * 60

FOOL = "https://www.fool.com/earnings/call-transcripts"

# Observed live: five results, none of them Q2 2026, longest is Q4 2018.
OBSERVED_MISS = [
    {"url": f"{FOOL}/2025/10/30/alphabet-goog-q3-2025-earnings-call-transcript",
     "title": "Alphabet (GOOG) Q3 2025 Earnings Call Transcript", "raw_content": BODY * 2},
    {"url": f"{FOOL}/2025/07/23/alphabet-googl-q2-2025-earnings-call-transcript",
     "title": "Alphabet GOOGL Q2 2025 Earnings Call Transcript", "raw_content": BODY * 3},
    {"url": f"{FOOL}/2026/04/29/alphabet-googl-q1-2026-earnings-call-transcript",
     "title": "Alphabet (GOOGL) Q1 2026 Earnings Call Transcript", "raw_content": BODY * 4},
    {"url": f"{FOOL}/2018/10/26/alphabet-inc-goog-googl-q3-2018-earnings-conf.aspx",
     "title": "Alphabet Inc. (GOOG) Q3 2018 Earnings Conference Call Transcript",
     "raw_content": BODY * 5},
    # The length winner under the old ranking:
    {"url": f"{FOOL}/2019/02/04/alphabet-inc-goog-googl-q4-2018-earnings-conf.aspx",
     "title": "Alphabet Inc. (GOOG) Q4 2018 Earnings Conference Call Transcript",
     "raw_content": BODY * 6},
]

HIT = {"url": ("https://www.investing.com/news/transcripts/"
               "earnings-call-transcript-alphabet-beats-q2-2026-estimates"),
       "title": "Earnings call transcript: Alphabet beats Q2 2026 estimates",
       "raw_content": BODY}          # deliberately the SHORTEST of the set


@pytest.fixture(autouse=True)
def _tavily_key(monkeypatch):
    """Pin a key so selection is what's under test, not local .env presence."""
    from types import SimpleNamespace

    from ats import config

    monkeypatch.setattr(config, "get_config",
                        lambda: SimpleNamespace(secrets=SimpleNamespace(tavily_api_key="k")))


def _stub(monkeypatch, results):
    monkeypatch.setattr(transcript, "_tavily_search", lambda key, query: list(results))


def test_wrong_quarter_loses_to_nothing(monkeypatch):
    """THE regression: no Q2 2026 available -> empty, NOT the long Q4 2018 page."""
    _stub(monkeypatch, OBSERVED_MISS)
    text, src = transcript._from_search("GOOG", "Q2 2026", "Alphabet")
    assert (text, src) == ("", "")


def test_target_quarter_wins_even_when_shortest(monkeypatch):
    _stub(monkeypatch, OBSERVED_MISS + [HIT])
    text, src = transcript._from_search("GOOG", "Q2 2026", "Alphabet")
    assert text == BODY
    assert "q2-2026" in src.lower()


def test_query_names_the_quarter(monkeypatch):
    """Naming the quarter is what surfaces the page at all (measured)."""
    seen: list[str] = []

    def spy(key, query):
        seen.append(query)
        return []

    monkeypatch.setattr(transcript, "_tavily_search", spy)
    transcript._from_search("GOOG", "Q2 2026", "Alphabet")
    assert seen, "no search was issued"
    assert "Q2 2026" in seen[0]
    assert "Alphabet" in seen[0]


def test_unparseable_label_falls_back_to_longest(monkeypatch):
    """'Q FY2026' encodes no quarter — can't verify, so keep the old behaviour and
    let the downstream period guard be the safety net."""
    _stub(monkeypatch, OBSERVED_MISS)
    text, _ = transcript._from_search("SKHY", "Q FY2026", "SK Hynix")
    assert text == BODY * 6


def test_short_pages_are_never_candidates(monkeypatch):
    _stub(monkeypatch, [{"url": f"{FOOL}/2026/07/23/alphabet-q2-2026-earnings-call-transcript",
                         "title": "Alphabet Q2 2026 Earnings Call Transcript",
                         "raw_content": "paywall stub"}])
    assert transcript._from_search("GOOG", "Q2 2026", "Alphabet") == ("", "")
