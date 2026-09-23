"""PEAD monitor bridge — ingestion, dedup, brief hand-off (no network).

Phase D: the monitor no longer writes the dossier (narrative/expectations) —
its analysis output is an InformationBrief projection via the information
analyst. These tests pin the NEW contract.
"""

from datetime import datetime, timezone

from ats.agents.pead import monitor, triage
from ats.agents.pead.outputs import ContextUpdateView
from ats.data import news as news_src
from ats.memory import get_store
from ats.schemas.news import NewsItem

NOW = datetime.now(timezone.utc)


def _news(symbol):
    if symbol == "COHR":
        return [NewsItem(id="n1", source="finnhub", headline="NVDA raises CapEx", published_at=NOW),
                NewsItem(id="n2", source="rss:SemiAnalysis", headline="1.6T ramp", published_at=NOW)]
    return []


def test_monitor_no_llm_stores_events(monkeypatch):
    monkeypatch.setattr(news_src, "fetch_news", lambda sym, since, until=None, consumer="pead_monitor": _news(sym))
    upd = monitor.run("COHR", use_llm=False)
    assert upd.materiality == 0.0
    assert get_store().count_events("COHR") == 2
    # Phase D: the monitor no longer creates or mutates a dossier.
    assert get_store().get_dossier("COHR", "Q FY2026") is None


def test_monitor_dedups_on_second_run(monkeypatch):
    monkeypatch.setattr(news_src, "fetch_news", lambda sym, since, until=None, consumer="pead_monitor": _news(sym))
    monitor.run("COHR", use_llm=False)
    monitor.run("COHR", use_llm=False)          # same events again
    assert get_store().count_events("COHR") == 2  # not 4


def test_monitor_llm_material_update_does_not_touch_dossier(monkeypatch):
    monkeypatch.setattr(news_src, "fetch_news", lambda sym, since, until=None, consumer="pead_monitor": _news(sym))
    monkeypatch.setattr(triage, "score_items", lambda *a, **k: {})  # triage miss -> pass-through
    view = ContextUpdateView(materiality=0.8, event_summary="hyperscaler capex up",
                             narrative_delta="upstream CapEx raised → optical demand up",
                             expectation_changes=[])
    monkeypatch.setattr("ats.agents.information.documents.run_structured", lambda *a, **k: view)

    upd = monitor.run("COHR", use_llm=True)
    assert upd.materiality == 0.8
    # The narrative delta goes to the BRIEF projection, never the dossier.
    store = get_store()
    assert store.get_dossier("COHR", "Q FY2026") is None
    briefs = store.task_projection_envelopes(agent_role="information_brief")
    assert len(briefs) == 1
    assert "upstream CapEx raised" in briefs[0]["payload"]["summary"]


def test_monitor_no_fresh_events_is_zero_materiality(monkeypatch):
    monkeypatch.setattr(news_src, "fetch_news", lambda sym, since, until=None, consumer="pead_monitor": [])
    upd = monitor.run("COHR", use_llm=True)       # no events -> short-circuits before LLM
    assert upd.materiality == 0.0


def test_monitor_persists_expectation_changes_only_as_brief(monkeypatch):
    from ats.agents.pead.outputs import ContextUpdateView, ExpectationChangeView

    monkeypatch.setattr(news_src, "fetch_news", lambda sym, since, until=None, consumer="pead_monitor": _news(sym))
    monkeypatch.setattr(triage, "score_items", lambda *a, **k: {})
    view = ContextUpdateView(
        materiality=0.8, event_summary="capex divergence",
        narrative_delta="demand no longer uniformly up",
        expectation_changes=[ExpectationChangeView(
            dim_key="hyperscaler_capex_demand", change="downgrade conviction")])
    monkeypatch.setattr("ats.agents.information.documents.run_structured",
                        lambda *a, **k: view)

    monitor.run("COHR", use_llm=True)
    store = get_store()
    # No dossier write, no narrative merge — expectation changes stay in the brief.
    assert store.get_dossier("COHR", "Q FY2026") is None
    briefs = store.task_projection_envelopes(agent_role="information_brief")
    assert briefs and "demand no longer uniformly up" in briefs[0]["payload"]["summary"]
