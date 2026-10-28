from datetime import datetime, timezone

from ats.data import research
from ats.data.pipelines.unstructured.article_ingest import _semianalysis_materials


NOW = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)


def test_target_semianalysis_transport_does_not_read_legacy_registry(monkeypatch):
    observed = {}
    monkeypatch.setattr("ats.config.load_news_sources", lambda: (_ for _ in ()).throw(
        AssertionError("legacy newsletter registry was consulted")))

    def imap(_since, cfg, *, store):
        observed["imap"] = cfg
        return research.AcquisitionBatch((), complete=True,
            transport_status={"imap": {"status": "succeeded"}})

    def rss(_since, feeds):
        observed["rss"] = feeds
        return [], {"status": "succeeded", "feeds": len(feeds), "failed_feeds": []}

    monkeypatch.setattr(research, "_imap_batch", imap)
    monkeypatch.setattr(research, "_rss_batch", rss)
    source = type("Source", (), {"id": "semianalysis"})()
    policy = {"policy": {"lookback_days": 30},
              "request_budget": {"max_items_per_run": 32}, "transport": {
        "imap": {"senders": [{"email": "semianalysis@substack.com"}]},
        "research_feeds": [{"name": "SemiAnalysis",
                            "url": "https://semianalysis.com/feed/"}]}}

    selected, status, batch = _semianalysis_materials(
        source, policy, now=NOW, store=object())
    assert selected == [] and status["status"] == "succeeded" and batch.complete
    assert observed == {"imap": policy["transport"]["imap"],
                        "rss": policy["transport"]["research_feeds"]}


def test_full_imap_body_wins_same_url_rss_preview():
    from ats.schemas.research import Article

    article_kwargs = {"source": "newsletter:SemiAnalysis", "title": "HBM research",
                      "url": "https://semianalysis.com/p/hbm?utm=mail",
                      "published_at": NOW}
    full = Article(id="imap:42", body="full analysis " * 200,
                   completeness="full", **article_kwargs)
    preview = Article(id="substack:42", body="preview " * 100,
                      completeness="partial", **{**article_kwargs,
                      "url": "https://semianalysis.com/p/hbm"})
    selected, duplicates = research.deduplicate_articles([preview, full])
    assert duplicates == 1
    assert selected == [full]
