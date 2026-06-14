"""PEAD v2 D+E: async webhook resume routing + earnings-proximity scheduling."""

from datetime import date, datetime, timezone

from ats.runtime import scheduler

NOW = datetime.now(timezone.utc)
SCHED = {"prep_days_before": 3, "score_after": True}


# --- E: earnings-proximity routing ----------------------------------------- #
def test_actions_far_from_earnings_is_monitor_only():
    assert scheduler._pead_actions(date(2026, 6, 1), date(2026, 8, 1), SCHED) == ["monitor"]


def test_actions_within_prep_window_adds_prep():
    assert scheduler._pead_actions(date(2026, 6, 1), date(2026, 6, 3), SCHED) == ["monitor", "prep"]


def test_actions_day_after_earnings_adds_score():
    assert scheduler._pead_actions(date(2026, 6, 2), date(2026, 6, 1), SCHED) == ["monitor", "score"]


def test_actions_no_earnings_date_is_monitor_only():
    assert scheduler._pead_actions(date(2026, 6, 1), None, SCHED) == ["monitor"]


# --- D: webhook resume routes to the PEAD graph ---------------------------- #
def test_resume_routes_pead_thread_to_pead_graph():
    from langgraph.types import Command  # noqa: F401  (ensures langgraph import path)

    from ats.graph.checkpoint import get_checkpointer
    from ats.graph.pead import build_pead_graph
    from ats.graph.pead_state import PeadState
    from ats.runtime.cli import resume_cycle
    from ats.schemas.decision import BossApproval

    thread = "pead:COHR:QFY2026"
    app = build_pead_graph(checkpointer=get_checkpointer(persist=True))
    state = PeadState(symbol="COHR", phase="score", as_of=NOW, use_llm=False,
                      use_broker=False, live_data=False)
    res = app.invoke(state, config={"configurable": {"thread_id": thread}})
    assert "__interrupt__" in res

    # The webhook calls resume_cycle; the pead: prefix must route to the PEAD graph.
    out = resume_cycle(thread, BossApproval(status="approved"))
    assert out.get("scorecard") is not None        # completed via the PEAD graph
    assert out.get("order_results") == []          # no-llm => zero score => no trade
