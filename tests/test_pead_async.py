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


def test_actions_amc_scores_next_day():
    # After-close print on 6/1 -> score 6/2 (T+1).
    assert scheduler._pead_actions(date(2026, 6, 2), date(2026, 6, 1), "amc", SCHED) \
        == ["monitor", "score"]
    assert scheduler._pead_actions(date(2026, 6, 1), date(2026, 6, 1), "amc", SCHED) == ["monitor"]


def test_actions_bmo_scores_same_day():
    # Before-open print on 6/1 -> score same day 6/1.
    assert scheduler._pead_actions(date(2026, 6, 1), date(2026, 6, 1), "bmo", SCHED) \
        == ["monitor", "score"]


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
