"""PEAD continuous monitor — event ingest, dedup, dossier update (no network)."""

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
    monkeypatch.setattr(news_src, "fetch_news", lambda sym, since, until=None: _news(sym))
    upd = monitor.run("COHR", use_llm=False)
    assert upd.materiality == 0.0
    assert get_store().count_events("COHR") == 2
    # Dossier auto-created.
    assert get_store().get_dossier("COHR", "Q FY2026") is not None


def test_monitor_dedups_on_second_run(monkeypatch):
    monkeypatch.setattr(news_src, "fetch_news", lambda sym, since, until=None: _news(sym))
    monitor.run("COHR", use_llm=False)
    monitor.run("COHR", use_llm=False)          # same events again
    assert get_store().count_events("COHR") == 2  # not 4


def test_monitor_llm_material_update_appends_to_narrative(monkeypatch):
    monkeypatch.setattr(news_src, "fetch_news", lambda sym, since, until=None: _news(sym))
    monkeypatch.setattr(triage, "score_items", lambda *a, **k: {})  # triage miss -> pass-through
    view = ContextUpdateView(materiality=0.8, event_summary="hyperscaler capex up",
                             narrative_delta="upstream CapEx raised → optical demand up",
                             expectation_changes=[])
    monkeypatch.setattr(monitor, "run_structured", lambda *a, **k: view)

    upd = monitor.run("COHR", use_llm=True)
    assert upd.materiality == 0.8
    d = get_store().get_dossier("COHR", "Q FY2026")
    assert "upstream CapEx raised" in d.expectation_set.narrative


def test_monitor_no_fresh_events_is_zero_materiality(monkeypatch):
    monkeypatch.setattr(news_src, "fetch_news", lambda sym, since, until=None: [])
    upd = monitor.run("COHR", use_llm=True)       # no events -> short-circuits before LLM
    assert upd.materiality == 0.0
