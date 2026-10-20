"""PEAD score ledger: state transitions and one ledger per quarter.

The score-window routing these tests used to sit next to lived in the legacy scheduler,
removed on 2026-10-20; the Fundamental event workflow now scores a released print.
"""

from __future__ import annotations

from datetime import date

from ats.memory import get_store



# --------------------------------------------------------------------------- #
# Store-level ledger
# --------------------------------------------------------------------------- #
def test_score_state_transitions():
    store = get_store()
    assert store.score_state("GOOG", "Q2 2026") == "unscored"
    assert store.next_score_version("GOOG", "Q2 2026") == 1

    store.record_score_run(symbol="GOOG", fiscal_label="Q2 2026", version=1,
                           earnings_date=date(2026, 7, 22), has_transcript=False)
    assert store.score_state("GOOG", "Q2 2026") == "v1_no_transcript"
    assert store.next_score_version("GOOG", "Q2 2026") == 2

    store.record_score_run(symbol="GOOG", fiscal_label="Q2 2026", version=2,
                           earnings_date=date(2026, 7, 22), has_transcript=True,
                           transcript_source="tavily:investing.com")
    assert store.score_state("GOOG", "Q2 2026") == "final"
    assert store.latest_score_run("GOOG", "Q2 2026")["version"] == 2


def test_ledger_is_per_quarter():
    store = get_store()
    store.record_score_run(symbol="GOOG", fiscal_label="Q2 2026", version=1,
                           earnings_date=date(2026, 7, 22), has_transcript=True)
    assert store.score_state("GOOG", "Q3 2026") == "unscored"
