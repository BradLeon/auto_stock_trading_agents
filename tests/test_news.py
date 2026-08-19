"""News data layer — parsing, dedup, keyword filter, degradation (no network)."""

from datetime import datetime, timedelta, timezone

from ats.data import news
from ats.schemas.news import NewsItem

NOW = datetime.now(timezone.utc)
SINCE = NOW - timedelta(days=7)


def _item(id_, when=NOW):
    return NewsItem(id=id_, source="finnhub", headline="h", published_at=when)


def test_fetch_news_dedups_and_orders(monkeypatch):
    older = NOW - timedelta(days=1)
    monkeypatch.setattr(news, "_finnhub", lambda *a: [_item("a", NOW), _item("a", NOW)])  # dup id
    monkeypatch.setattr(news, "_rss", lambda *a: [_item("b", older)])
    monkeypatch.setattr(news, "_x", lambda *a: [])
    out = news.fetch_news("COHR", SINCE)
    assert [i.id for i in out] == ["a", "b"]      # deduped, newest first


def test_finnhub_failure_degrades_to_other_sources(monkeypatch):
    monkeypatch.setattr(news, "_finnhub", lambda *a: (_ for _ in ()).throw(RuntimeError("429")))
    monkeypatch.setattr(news, "_rss", lambda *a: [_item("r1")])
    monkeypatch.setattr(news, "_x", lambda *a: [])
    out = news.fetch_news("COHR", SINCE)
    assert [i.id for i in out] == ["r1"]          # finnhub died, rss survived


def test_rss_keyword_filter():
    feed = {"name": "Test", "url": "http://x"}
    entries = [{"title": "Coherent ships 1.6T optics", "summary": "", "link": "u1",
                "published_parsed": NOW.timetuple()},
               {"title": "Unrelated macro note", "summary": "", "link": "u2",
                "published_parsed": NOW.timetuple()}]

    class FakeParsed:
        pass

    fp = FakeParsed()
    fp.entries = entries
    import ats.data.news as n
    import sys
    import types
    fake = types.ModuleType("feedparser")
    fake.parse = lambda url: fp
    sys.modules["feedparser"] = fake
    try:
        out = n._parse_feed(feed, "COHR", SINCE, ["coherent", "1.6t"])
    finally:
        del sys.modules["feedparser"]
    assert [i.url for i in out] == ["u1"]          # only the keyword-matched item


def test_clean_strips_html():
    assert news._clean("<p>hello <b>world</b></p>") == "hello world"


def test_every_discovered_news_item_enters_shared_catalog(monkeypatch):
    from ats.memory import get_store

    item = NewsItem(
        id="provider-42", source="finnhub", headline="AMD expands AI capacity",
        summary="The program also names TSM as a manufacturing partner.",
        url="https://news.example.test/story?utm_source=feed", published_at=NOW,
        tickers=["AMD", "TSM"],
    )
    monkeypatch.setattr(news, "_finnhub", lambda *a: [item])
    monkeypatch.setattr(news, "_rss", lambda *a: [])
    monkeypatch.setattr(news, "_x", lambda *a: [])

    assert news.fetch_news("AMD", SINCE) == [item]
    store = get_store()
    assert len(store.documents(entity="AMD", doc_type="news")) == 1
    assert len(store.documents(entity="TSM", doc_type="news")) == 1
    row = store.documents(doc_type="news")[0]
    assert row["external_id"] == "https://news.example.test/story"
    assert row["source"] == "finnhub:metadata"


def test_full_news_body_upgrades_one_asset_and_is_reused_across_tickers(monkeypatch):
    from ats.memory import get_store

    amd = NewsItem(
        id="fh-1", source="finnhub", headline="Shared story", summary="Short summary",
        url="https://news.example.test/shared?utm_campaign=x", published_at=NOW,
        tickers=["AMD"],
    )
    tsm = amd.model_copy(update={
        "id": "rss-9", "source": "rss:wire",
        "url": "https://news.example.test/shared", "tickers": ["TSM"],
    })
    news._catalog([amd], store=get_store())
    calls = []
    full = ("Full article discusses AMD and TSM capacity expansion. " * 40).strip()

    def fetch(url, **_):
        calls.append(url)
        return full

    monkeypatch.setattr("ats.data.web.fetch_article_text", fetch)
    assert news.acquire_body(amd, store=get_store()) == full
    assert news.acquire_body(tsm, store=get_store()) == full
    assert len(calls) == 1

    rows = get_store().documents(doc_type="news")
    assert len(rows) == 1
    assert len(get_store().document_versions(rows[0]["document_id"])) == 2
    assert len(get_store().documents(entity="AMD", doc_type="news")) == 1
    assert len(get_store().documents(entity="TSM", doc_type="news")) == 1
