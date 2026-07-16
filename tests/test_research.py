"""Newsletter research — ingestion dedup, insight extraction, event injection
(no network)."""

from datetime import datetime, timezone

import ats.data.research as research_src
from ats.agents.pead import research
from ats.agents.pead.outputs import InsightBatchView, InsightItemView
from ats.memory import get_store
from ats.schemas.research import Article

NOW = datetime.now(timezone.utc)


def _pin_universe(monkeypatch):
    """Pin the research universe so operational config/pead.yaml target changes
    don't break these tests (they assert on the COHR/LITE optical group)."""
    import ats.config as _config

    real = _config.load_pead_global
    monkeypatch.setattr(_config, "load_pead_global",
                        lambda: {**real(), "targets": ["COHR", "LITE", "AAOI"]})

ARTICLE = Article(id="imap:msg1", source="newsletter:SemiAnalysis",
                  title="Meta to rent out idle compute", url="https://s.test/p/meta",
                  body="Meta plans to rent out idle GPU capacity as a cloud service...",
                  published_at=NOW)


def _view():
    return InsightBatchView(article_gist="Meta compute rental", insights=[
        InsightItemView(ticker="TSM", direction="bearish", impact_path="supply_chain",
                        summary="less net-new foundry demand", evidence_quote="rent out idle",
                        confidence=0.9),
        InsightItemView(ticker="ZZZZ", direction="bullish", impact_path="direct",
                        summary="not in universe", confidence=0.9),
        InsightItemView(ticker="LITE", direction="neutral", impact_path="demand",
                        summary="weak read-through", confidence=0.3),
    ])


def test_research_extracts_filters_and_injects(monkeypatch):
    _pin_universe(monkeypatch)
    monkeypatch.setattr(research_src, "fetch_articles", lambda since: [ARTICLE])
    monkeypatch.setattr(research, "run_structured", lambda *a, **k: _view())

    insights = research.run(use_llm=True)

    # ZZZZ (not in universe) dropped; TSM + LITE kept.
    assert {i.ticker for i in insights} == {"TSM", "LITE"}
    stored = get_store().recent_insights()
    assert {r["ticker"] for r in stored} == {"TSM", "LITE"}

    # TSM (conf 0.9 >= 0.6, upstream of COHR) -> synthetic event under COHR with
    # pre-seeded triage score; LITE insight (conf 0.3) injects nothing.
    events = {r["id"]: r for r in get_store().recent_events("COHR", limit=10)}
    key = "insight:imap:msg1:TSM"
    assert key in events
    assert events[key]["triage_score"] == 0.9
    assert events[key]["triage_category"] == "research"
    assert "[bearish/supply_chain] TSM" in events[key]["headline"]
    assert not any("LITE" in r["id"] for r in events.values())


def test_research_dedups_articles_on_second_run(monkeypatch):
    _pin_universe(monkeypatch)
    monkeypatch.setattr(research_src, "fetch_articles", lambda since: [ARTICLE])
    monkeypatch.setattr(research, "run_structured", lambda *a, **k: _view())
    research.run(use_llm=True)
    assert research.run(use_llm=True) == []          # article already seen
    assert len(get_store().recent_insights()) == 2   # not 4


def test_research_llm_failure_still_marks_article_seen(monkeypatch):
    monkeypatch.setattr(research_src, "fetch_articles", lambda since: [ARTICLE])

    def boom(*a, **k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(research, "run_structured", boom)
    assert research.run(use_llm=True) == []
    assert get_store().article_seen("imap:msg1")


def test_build_universe_maps_chain_members():
    card, mapping = research._build_universe(["COHR"])
    assert "COHR (target)" in card
    assert "TSM (upstream of COHR)" in card
    assert mapping["COHR"] == ["COHR"]
    assert "COHR" in mapping["TSM"]


def test_extract_body_multipart_quoted_printable():
    import email

    raw = (
        "From: a@b.c\r\nTo: d@e.f\r\nSubject: =?utf-8?q?Meta_compute?=\r\n"
        "MIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary=XYZ\r\n\r\n"
        "--XYZ\r\nContent-Type: text/plain; charset=utf-8\r\n"
        "Content-Transfer-Encoding: quoted-printable\r\n\r\nplain fallback\r\n"
        "--XYZ\r\nContent-Type: text/html; charset=utf-8\r\n"
        "Content-Transfer-Encoding: quoted-printable\r\n\r\n"
        "<html><body><p>Meta rents =E2=80=94 idle compute</p>"
        '<a href="https://x.substack.com/p/meta-post?utm=1">View in browser</a>'
        "</body></html>\r\n--XYZ--\r\n"
    )
    msg = email.message_from_bytes(raw.encode())
    text, html = research_src._extract_body(msg)
    assert "Meta rents — idle compute" in text
    assert "<p>" not in text
    assert research_src._web_link(html) == "https://x.substack.com/p/meta-post"
    assert research_src._decode_header(msg["Subject"]) == "Meta compute"
