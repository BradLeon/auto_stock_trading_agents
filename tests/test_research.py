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


def _batch(*articles):
    return research_src.AcquisitionBatch(tuple(articles))


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
    monkeypatch.setattr(research_src, "fetch_batch", lambda since, **k: _batch(ARTICLE))
    monkeypatch.setattr(research, "run_structured", lambda *a, **k: _view())
    research_src.ingest(NOW, store=get_store())

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
    monkeypatch.setattr(research_src, "fetch_batch", lambda since, **k: _batch(ARTICLE))
    monkeypatch.setattr(research, "run_structured", lambda *a, **k: _view())
    research_src.ingest(NOW, store=get_store())
    research.run(use_llm=True)
    assert research.run(use_llm=True) == []          # article already seen
    assert len(get_store().recent_insights()) == 2   # not 4


def test_research_llm_failure_still_marks_article_seen(monkeypatch):
    monkeypatch.setattr(research_src, "fetch_batch", lambda since, **k: _batch(ARTICLE))
    research_src.ingest(NOW, store=get_store())

    def boom(*a, **k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(research, "run_structured", boom)
    assert research.run(use_llm=True) == []
    assert get_store().article_seen("imap:msg1")


def test_shared_ingestion_feeds_both_consumers_without_refetch(monkeypatch):
    from ats.data.articles import semianalysis

    calls = []

    def _fetch(since):
        calls.append(since)
        return [ARTICLE]

    monkeypatch.setattr(research_src, "fetch_batch",
                        lambda since, **k: _batch(*_fetch(since)))
    research_src.ingest(NOW, store=get_store())

    # Both consumers now read disk/catalog only; discovery must not touch the fetcher.
    monkeypatch.setattr(research_src, "fetch_batch",
                        lambda since, **k: (_ for _ in ()).throw(AssertionError("refetched")))
    assert research_src.stored_articles(NOW, source_match="SemiAnalysis")
    refs = semianalysis.discover(lookback_days=1, source_match="SemiAnalysis")
    assert len(refs) == 1
    assert semianalysis.fetch_body(refs[0].url) == ARTICLE.body
    assert len(calls) == 1


def test_pead_research_reader_excludes_news_but_can_return_partial_for_policy(monkeypatch, tmp_path):
    full = tmp_path / "full.md"
    partial = tmp_path / "partial.md"
    news = tmp_path / "news.md"
    full.write_text("complete research body", encoding="utf-8")
    partial.write_text("preview body", encoding="utf-8")
    news.write_text("wire headline body", encoding="utf-8")

    class Reader:
        def documents(self, **_kwargs):
            return [
                {"external_id": "trendforce:1", "source": "trendforce",
                 "title": "DRAM outlook", "source_url": "https://trendforce.test/1",
                 "local_path": str(full), "published_at": NOW.isoformat(), "completeness": "full"},
                {"external_id": "semi:1", "source": "newsletter:SemiAnalysis",
                 "title": "Preview", "source_url": "https://semi.test/1",
                 "local_path": str(partial), "published_at": NOW.isoformat(), "completeness": "partial"},
                {"external_id": "ibkr:1", "source": "ibkr_news",
                 "title": "MSFT wire", "source_url": "https://wire.test/1",
                 "local_path": str(news), "published_at": NOW.isoformat(), "completeness": "full"},
            ]

        def close(self):
            pass

    monkeypatch.setattr("ats.data.products.get_unstructured_read_router",
                        lambda **_kwargs: Reader())

    full_only = research_src.stored_articles(NOW, store=object())
    assert [article.id for article in full_only] == ["trendforce:1"]
    with_partial = research_src.stored_articles(NOW, store=object(), allow_incomplete=True)
    assert [article.id for article in with_partial] == ["trendforce:1", "semi:1"]


def test_pead_research_allows_only_semianalysis_partial_previews():
    full = ARTICLE.model_copy(update={"completeness": "full"})
    semi_preview = ARTICLE.model_copy(update={"completeness": "partial"})
    trendforce_preview = ARTICLE.model_copy(update={
        "source": "trendforce", "completeness": "partial"})
    teaser = ARTICLE.model_copy(update={"completeness": "teaser"})

    assert research_src.is_pead_research_article(full) is True
    assert research_src.is_pead_research_article(semi_preview) is True
    assert research_src.is_pead_research_article(trendforce_preview) is False
    assert research_src.is_pead_research_article(teaser) is False


def test_research_uses_stable_document_id_for_pre_metadata_article(monkeypatch):
    _pin_universe(monkeypatch)
    migrated = ARTICLE.model_copy(update={"id": "SEMIANALYSIS:imap-123:research_article"})
    monkeypatch.setattr(research_src, "stored_articles", lambda *_args, **_kwargs: [migrated])
    monkeypatch.setattr(research, "run_structured", lambda *a, **k: _view())
    store = get_store()
    from ats.data import document_assets

    document_assets.ingest(
        entity="SEMIANALYSIS", key="imap-123", doc_type="research_article",
        text=migrated.body, source=migrated.source, source_url=migrated.url,
        external_id="", title=migrated.title, published_at=migrated.published_at.isoformat(),
        min_chars=1, store=store)

    research.run(use_llm=True, since=NOW)

    assert store.document_processing(
        document_id="SEMIANALYSIS:imap-123:research_article", consumer="pead",
        processor_version=research.PROCESSOR_VERSION)


def test_stored_articles_recovers_pre_metadata_migration_records(monkeypatch, tmp_path):
    historical = tmp_path / "historical.md"
    historical.write_text("migrated SemiAnalysis body", encoding="utf-8")

    class Reader:
        def documents(self, **kwargs):
            # The old migration populated stable document identity and fetch lineage,
            # but did not backfill article-native ID or published_at.
            assert "published_since" not in kwargs
            return [{
                "document_id": "SEMIANALYSIS:imap-123:article",
                "source": "semianalysis",
                "local_path": str(historical),
                "fetched_at": NOW.isoformat(),
                "external_id": None,
                "published_at": None,
            }]

        def close(self):
            pass

    monkeypatch.setattr("ats.data.products.get_unstructured_read_router",
                        lambda **_kwargs: Reader())

    articles = research_src.stored_articles(NOW, store=object())

    assert len(articles) == 1
    assert articles[0].id == "SEMIANALYSIS:imap-123:article"
    assert articles[0].published_at == NOW


def test_cross_carrier_duplicate_prefers_imap_body_and_records_duplicate_count(monkeypatch):
    imap = ARTICLE.model_copy(update={
        "id": "imap:<same-post@test>", "url": "https://semianalysis.com/p/same-post?utm=email",
        "body": "complete email body " * 200, "message_id": "<same-post@test>",
    })
    rss = ARTICLE.model_copy(update={
        "id": "substack:same-post", "source": "substack:SemiAnalysis",
        "url": "https://semianalysis.com/p/same-post", "body": "RSS teaser",
        "completeness": "teaser",
    })
    monkeypatch.setattr(research_src, "_imap_batch", lambda *a, **k: _batch(imap))
    monkeypatch.setattr(research_src, "_substack_rss", lambda *a, **k: [rss])

    batch = research_src.fetch_batch(NOW)

    assert batch.candidate_count == 2 and batch.duplicate_count == 1
    assert len(batch.articles) == 1
    assert batch.articles[0].id == "imap:<same-post@test>"


def test_rss_failure_is_reported_as_a_transport_gap(monkeypatch):
    monkeypatch.setattr(research_src, "_imap_batch", lambda *a, **k: _batch())
    monkeypatch.setattr(research_src, "_substack_rss",
                        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("feed down")))

    batch = research_src.fetch_batch(NOW)

    assert batch.complete is False
    assert batch.transport_status["rss"]["status"] == "partial"
    assert batch.transport_status["rss"]["failed_feeds"][0]["feed"] == "SemiAnalysis"


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
