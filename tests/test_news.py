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
