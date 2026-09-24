"""News data layer — parsing, dedup, keyword filter, degradation (no network)."""

from datetime import datetime, timedelta, timezone

from ats.data import news
from ats.schemas.news import NewsItem

NOW = datetime.now(timezone.utc)
SINCE = NOW - timedelta(days=7)


def _item(id_, when=NOW):
    return NewsItem(id=id_, source="finnhub", headline="h", published_at=when)


def _forbid_legacy_providers(monkeypatch):
    """fetch_news 是平台只读消费路径：legacy provider 必须不可达。"""

    def _legacy(*_a):
        raise AssertionError("consumer must not touch legacy providers")

    monkeypatch.setattr(news, "_finnhub", _legacy)
    monkeypatch.setattr(news, "_rss", _legacy)
    monkeypatch.setattr(news, "_x", _legacy)


def _seed_platform_news(key: str, published, *, entity="AMD", tickers=("AMD",)):
    from ats.data import document_assets

    return document_assets.ingest(
        entity=entity, key=key, doc_type="news_item",
        text=f"{key} body for {entity}",
        source="ibkr_news", source_url=f"https://news.example.test/{key}",
        external_id=f"https://news.example.test/{key}", title=key,
        published_at=published.isoformat(), min_chars=1,
        related_entities=tuple(tickers))


def test_fetch_news_reads_released_platform_items_newest_first(monkeypatch):
    """Provider 聚合/去重已上收到 ingestion（yahoo/ibkr adapters）；消费路径的
    契约是：只读已发布平台资产、按最新在前排序、窗口过滤。"""
    _forbid_legacy_providers(monkeypatch)

    fresh = _seed_platform_news("story-new", NOW - timedelta(days=1))
    old = _seed_platform_news("story-old", NOW - timedelta(days=3))
    _seed_platform_news("story-outside", SINCE - timedelta(days=3))  # 窗口外

    out = news.fetch_news("AMD", SINCE)
    assert [i.id for i in out] == [fresh.document_id, old.document_id]
    assert all(i.source.startswith("platform:") for i in out)


def test_platform_miss_is_a_gap_not_a_provider_fallback(monkeypatch):
    """平台无发布即缺口——消费方不得回落到临时 provider 请求（那会绕过
    实体准入、去重与 ingestion 记录的 lineage）。"""
    _forbid_legacy_providers(monkeypatch)
    assert news.fetch_news("COHR", SINCE) == []


def test_platform_news_does_not_call_legacy_providers(monkeypatch):
    item = _item("platform")
    monkeypatch.setenv("ATS_STRUCTURED_PEAD_MONITOR_MODE", "platform")
    monkeypatch.setattr("ats.data.products.unstructured.platform_news_items",
                        lambda **_kwargs: [item])
    monkeypatch.setattr(news, "_finnhub", lambda *_args: (_ for _ in ()).throw(AssertionError("legacy")))

    assert news.fetch_news("NVDA", SINCE, consumer="pead_monitor") == [item]


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


def test_every_discovered_news_item_enters_shared_catalog():
    """Ingestion 把每个发现的条目按 canonical URL 收进共享目录，且关联全部提及实体。"""
    from ats.memory import get_store

    item = NewsItem(
        id="provider-42", source="finnhub", headline="AMD expands AI capacity",
        summary="The program also names TSM as a manufacturing partner.",
        url="https://news.example.test/story?utm_source=feed", published_at=NOW,
        tickers=["AMD", "TSM"],
    )
    news._catalog([item], store=get_store())

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
