"""Score-window routing: which window scores which print, exactly once.

This replaces the dead `_pead_actions` score branch. The properties that matter:
  * an after-close print is scored the SAME evening (before the next open), not T+1;
  * a before-open print is scored that morning;
  * a print whose session is unknown is attempted in BOTH windows — and the score
    ledger makes the second attempt a no-op, so "try both" is safe;
  * nothing is scored until the release is actually confirmed;
  * a missed window can still catch up for a few days.
"""

from __future__ import annotations

from datetime import date

import pytest

from ats.data.earnings_calendar import EarningsPrint
from ats.memory import get_store
from ats.runtime import scheduler

SCHED = {"score_lookback_days": 4, "transcript_upgrade_days": 4}


def _print(d=date(2026, 7, 22), session="amc", *, eps_actual=9.11, src="yf-clock"):
    return EarningsPrint(symbol="GOOG", date=d, session=session, session_source=src,
                         quarter=2, year=2026, eps_actual=eps_actual)


def plan(window, today, print_, state="unscored", sched=None):
    """-> (action, why); action is "score" | "promote" | ""."""
    return scheduler._score_plan(window, today, print_, state, sched or SCHED)


# --------------------------------------------------------------------------- #
# Session -> window
# --------------------------------------------------------------------------- #
def test_amc_print_scores_the_same_evening():
    """The GOOG case: 7/22 after close -> the 20:00 window that night.

    Under the old routing this waited for T+1 at 10:30 ET, i.e. an hour AFTER the
    next open — the drift it was trying to trade had already happened.
    """
    action, why = plan("amc", date(2026, 7, 22), _print(session="amc"))
    assert action == "score", why
    assert "首打" in why


def test_amc_print_not_scored_by_the_morning_window_same_day():
    action, why = plan("bmo", date(2026, 7, 22), _print(session="amc"))
    assert action == ""
    assert "不匹配" in why


def test_bmo_print_scores_that_morning():
    action, why = plan("bmo", date(2026, 7, 29), _print(d=date(2026, 7, 29), session="bmo"))
    assert action == "score", why


def test_bmo_print_not_scored_by_the_evening_window():
    action, _ = plan("amc", date(2026, 7, 29), _print(d=date(2026, 7, 29), session="bmo"))
    assert action == ""


def test_dmh_print_treated_like_after_close():
    action, _ = plan("amc", date(2026, 7, 22), _print(session="dmh"))
    assert action == "score"


@pytest.mark.parametrize("window", ["amc", "bmo"])
def test_unknown_session_is_attempted_in_both_windows(window):
    """Finnhub's `hour` is blank for SKHY/CRDO/MRVL — guessing would mis-time them."""
    action, why = plan(window, date(2026, 7, 22), _print(session="unknown", src="none"))
    assert action == "score", why


# --------------------------------------------------------------------------- #
# Idempotency / state
# --------------------------------------------------------------------------- #
def test_final_state_short_circuits():
    action, why = plan("amc", date(2026, 7, 22), _print(), state="final")
    assert action == ""
    assert "终版" in why


def test_v1_is_retried_for_an_upgrade_inside_the_window():
    action, why = plan("amc", date(2026, 7, 24), _print(), state="v1_no_transcript")
    assert action == "score", why
    assert "升级 v2" in why


def test_v1_is_promoted_after_the_upgrade_window():
    """The transcript never came: promote the v1 (no LLM) so the Chief can finally
    act on it, rather than leaving the quarter silently unscored forever."""
    action, why = plan("amc", date(2026, 7, 28), _print(), state="v1_no_transcript",
                       sched={"score_lookback_days": 30, "transcript_upgrade_days": 4})
    assert action == "promote"
    assert "提升为终版" in why


# --------------------------------------------------------------------------- #
# Windowing
# --------------------------------------------------------------------------- #
def test_no_print_does_nothing():
    action, why = plan("amc", date(2026, 7, 22), None)
    assert action == "" and "无近期财报" in why


def test_future_print_does_nothing():
    action, why = plan("amc", date(2026, 7, 20), _print(d=date(2026, 7, 22)))
    assert action == "" and "未来" in why


def test_missed_window_can_catch_up_next_day_in_either_window():
    """If the box was asleep at 20:00, T+1 should still score rather than skip."""
    for window in ("amc", "bmo"):
        action, why = plan(window, date(2026, 7, 23), _print(session="amc"))
        assert action == "score", f"{window}: {why}"
        assert "补打" in why


def test_stale_print_is_abandoned():
    action, why = plan("amc", date(2026, 8, 5), _print(session="amc"))
    assert action == "" and "超出补打窗口" in why


# --------------------------------------------------------------------------- #
# Release confirmation
# --------------------------------------------------------------------------- #
def test_actual_eps_confirms_the_release():
    ok, why = scheduler._confirm_reported("GOOG", _print(eps_actual=9.11))
    assert ok and "已公布" in why


def test_8k_filed_on_the_print_date_confirms_the_release(monkeypatch):
    """The vendors lag hours behind the release; the 8-K lands within minutes, which
    is what makes scoring the same evening possible."""
    from ats.data import documents

    monkeypatch.setattr(documents, "sec_8k_release",
                        lambda s, **k: {"label": "8-K", "text": "x" * 3000,
                                   "filed": date(2026, 7, 22)})
    ok, why = scheduler._confirm_reported("GOOG", _print(eps_actual=None))
    assert ok and "8-K" in why


def test_stale_8k_does_not_confirm(monkeypatch):
    """An 8-K filed BEFORE the print date is last quarter's release — scoring on it
    would invent a surprise out of stale numbers."""
    from ats.data import documents

    monkeypatch.setattr(documents, "sec_8k_release",
                        lambda s, **k: {"label": "8-K", "text": "x" * 3000,
                                   "filed": date(2026, 4, 29)})
    ok, why = scheduler._confirm_reported("GOOG", _print(eps_actual=None))
    assert not ok and "早于财报日" in why


def test_no_evidence_at_all_defers(monkeypatch):
    from ats.data import documents

    monkeypatch.setattr(documents, "sec_8k_release", lambda s, **k: None)
    ok, why = scheduler._confirm_reported("GOOG", _print(eps_actual=None))
    assert not ok and "8-K" in why


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
