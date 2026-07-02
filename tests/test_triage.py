"""News triage — scoring persistence, noise filtering, degradation (no network)."""

from datetime import datetime, timezone

from ats.agents.pead import monitor, triage
from ats.agents.pead.outputs import ContextUpdateView, TriageBatchView, TriageItemView
from ats.data import news as news_src
from ats.memory import get_store
from ats.schemas.news import NewsItem

NOW = datetime.now(timezone.utc)


def _news(symbol):
    if symbol == "COHR":
        return [
            NewsItem(id="hot", source="finnhub", headline="COHR raises guidance",
                     url="https://x.test/hot", published_at=NOW),
            NewsItem(id="meh", source="finnhub", headline="peer in-line quarter",
                     published_at=NOW),
            NewsItem(id="noise", source="rss:feed", headline="10 stocks to watch",
                     published_at=NOW),
        ]
    return []


def _scores(**kw):
    return lambda *a, **k: kw


def test_triage_scores_persisted_and_noise_filtered(monkeypatch):
    monkeypatch.setattr(news_src, "fetch_news", lambda sym, since, until=None: _news(sym))
    monkeypatch.setattr(triage, "score_items", _scores(
        hot=(0.9, "guidance"), meh=(0.5, "analyst"), noise=(0.1, "noise")))
    monkeypatch.setattr(triage, "enrich", lambda items, **k: [])

    captured = {}

    def fake_llm(role, schema, ctx, **k):
        captured["ctx"] = ctx
        return ContextUpdateView(materiality=0.7, event_summary="guidance up")

    monkeypatch.setattr(monitor, "run_structured", fake_llm)
    upd = monitor.run("COHR", use_llm=True)

    assert upd.materiality == 0.7
    # Scores persisted.
    rows = {r["id"]: r for r in get_store().recent_events("COHR", limit=10)}
    assert rows["hot"]["triage_score"] == 0.9
    assert rows["hot"]["triage_category"] == "guidance"
    assert rows["noise"]["triage_score"] == 0.1
    # Noise excluded from the manager-LLM context; material items included.
    assert "COHR raises guidance" in captured["ctx"]
    assert "peer in-line quarter" in captured["ctx"]
    assert "10 stocks to watch" not in captured["ctx"]


def test_triage_all_noise_skips_manager_llm(monkeypatch):
    monkeypatch.setattr(news_src, "fetch_news", lambda sym, since, until=None: _news(sym))
    monkeypatch.setattr(triage, "score_items", _scores(
        hot=(0.1, "noise"), meh=(0.1, "noise"), noise=(0.0, "noise")))

    def boom(*a, **k):
        raise AssertionError("manager LLM must not be called when all noise")

    monkeypatch.setattr(monitor, "run_structured", boom)
    upd = monitor.run("COHR", use_llm=True)
    assert upd.materiality == 0.0
    assert "all triaged as noise" in upd.event_summary


def test_triage_failure_degrades_to_passthrough(monkeypatch):
    monkeypatch.setattr(news_src, "fetch_news", lambda sym, since, until=None: _news(sym))
    monkeypatch.setattr(triage, "score_items", lambda *a, **k: {})   # LLM failed

    captured = {}

    def fake_llm(role, schema, ctx, **k):
        captured["ctx"] = ctx
        return ContextUpdateView(materiality=0.2, event_summary="routine")

    monkeypatch.setattr(monitor, "run_structured", fake_llm)
    upd = monitor.run("COHR", use_llm=True)
    assert upd.materiality == 0.2
    # No scores -> everything passes through (current behavior).
    assert "10 stocks to watch" in captured["ctx"]


def test_fulltext_bodies_reach_llm_context(monkeypatch):
    monkeypatch.setattr(news_src, "fetch_news", lambda sym, since, until=None: _news(sym))
    monkeypatch.setattr(triage, "score_items", _scores(
        hot=(0.9, "guidance"), meh=(0.4, "analyst"), noise=(0.1, "noise")))
    import ats.data.web as web
    monkeypatch.setattr(web, "fetch_article_text",
                        lambda url, **k: "FULL BODY TEXT of the guidance article")

    captured = {}

    def fake_llm(role, schema, ctx, **k):
        captured["ctx"] = ctx
        return ContextUpdateView(materiality=0.8, event_summary="x")

    monkeypatch.setattr(monitor, "run_structured", fake_llm)
    monitor.run("COHR", use_llm=True)
    assert "FULL BODY TEXT of the guidance article" in captured["ctx"]


def test_score_items_batches_and_maps_idx(monkeypatch):
    items = [NewsItem(id=f"i{n}", source="finnhub", headline=f"h{n}", published_at=NOW)
             for n in range(3)]
    view = TriageBatchView(items=[
        TriageItemView(idx=0, materiality=0.8, category="capex"),
        TriageItemView(idx=2, materiality=0.1, category="noise"),
        TriageItemView(idx=99, materiality=1.0, category="bogus"),   # out of range -> dropped
    ])
    monkeypatch.setattr(triage, "run_structured", lambda *a, **k: view)
    scores = triage.score_items("COHR", "thesis", items)
    assert scores == {"i0": (0.8, "capex"), "i2": (0.1, "noise")}


def test_migration_adds_triage_columns():
    cols = {r["name"] for r in get_store().conn.execute("PRAGMA table_info(pead_events)")}
    assert "triage_score" in cols and "triage_category" in cols
