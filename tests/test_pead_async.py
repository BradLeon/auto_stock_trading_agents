"""PEAD score branch runs straight through: the Chief owns trade decisions, so scoring never interrupts."""

from datetime import date, datetime, timezone


NOW = datetime.now(timezone.utc)


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
