"""PEAD earnings-proximity scheduling (v0.2: the score branch no longer interrupts —
the Chief owns trade decisions — so the old async score-resume test was removed)."""

from datetime import date, datetime, timezone

from ats.runtime import scheduler

NOW = datetime.now(timezone.utc)
SCHED = {"prep_days_before": 3, "score_after": True}


# --- E: earnings-proximity routing ----------------------------------------- #
def test_actions_far_from_earnings_is_monitor_only():
    assert scheduler._pead_actions(date(2026, 6, 1), date(2026, 8, 1), "amc", SCHED) == ["monitor"]


def test_actions_within_prep_window_adds_prep():
    assert scheduler._pead_actions(date(2026, 6, 1), date(2026, 6, 3), "amc", SCHED) \
        == ["monitor", "prep"]


def test_actions_never_score_regardless_of_session():
    """The daily cycle only monitors/preps — scoring moved to the score windows.

    These cases used to assert `["monitor", "score"]`, but they fed `_pead_actions` a
    PAST earnings_date, which its real source (next_earnings, filtered to >= today)
    can never produce. The assertions passed while the branch was dead in production.
    Routing by observed print is covered in tests/test_score_state.py.

    `today == ed` (days_to == 0) also exercises the bmo/amc same-day prep boundary —
    see test_actions_same_day_prep_is_session_aware for that in detail; this test only
    asserts "score" never appears, regardless of what happens with "prep".
    """
    for today, ed, hour in [(date(2026, 6, 2), date(2026, 6, 1), "amc"),
                            (date(2026, 6, 1), date(2026, 6, 1), "amc"),
                            (date(2026, 6, 1), date(2026, 6, 1), "bmo"),
                            (date(2026, 6, 1), date(2026, 6, 1), "")]:
        assert "score" not in scheduler._pead_actions(today, ed, hour, SCHED)


def test_actions_same_day_prep_is_session_aware():
    """Found via KLAC 2026-07-29: an amc print due LATER TODAY is still hours away at
    the 10:30 ET daily-cycle run, so prep is still useful — but the print already
    happened once EARNINGS_DATE < today (checked elsewhere), and a bmo print has
    already opened for trading by 10:30 ET, so prep after the fact is moot."""
    same_day = date(2026, 6, 1)
    assert scheduler._pead_actions(same_day, same_day, "amc", SCHED) == ["monitor", "prep"]
    assert scheduler._pead_actions(same_day, same_day, "dmh", SCHED) == ["monitor", "prep"]
    assert scheduler._pead_actions(same_day, same_day, "", SCHED) == ["monitor", "prep"]
    assert scheduler._pead_actions(same_day, same_day, "bmo", SCHED) == ["monitor"]


def test_actions_no_earnings_date_is_monitor_only():
    assert scheduler._pead_actions(date(2026, 6, 1), None, "", SCHED) == ["monitor"]


# --- score branch runs straight through (no interrupt / no checkpoint pause) --
def test_score_runs_to_completion_without_interrupt():
    from ats.graph.checkpoint import get_checkpointer
    from ats.graph.pead import build_pead_graph
    from ats.graph.pead_state import PeadState

    app = build_pead_graph(checkpointer=get_checkpointer(persist=False))
    state = PeadState(symbol="COHR", phase="score", as_of=NOW, use_llm=False,
                      use_broker=False, live_data=False)
    res = app.invoke(state, config={"configurable": {"thread_id": "t-score-async"}})
    assert "__interrupt__" not in res
    assert res.get("scorecard") is not None
