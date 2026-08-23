from datetime import datetime, timezone
import json

import duckdb

from ats.data import news, yahoo_news
from ats.data import document_assets
from ats.data.defeatbeta import DefeatBetaConfig
from ats.memory import get_store
from ats.schemas.news import NewsItem


NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)


def _fixture(tmp_path):
    parquet = tmp_path / "stock_news.parquet"
    spec = tmp_path / "spec.json"
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE stories (uuid VARCHAR,symbol VARCHAR,title VARCHAR,publisher VARCHAR,"
        "report_date VARCHAR,type VARCHAR,link VARCHAR,"
        "news STRUCT(paragraph_number INTEGER,highlight VARCHAR,paragraph VARCHAR)[])"
    )
    con.execute(
        "INSERT INTO stories VALUES "
        "('uuid-1','AMD','AMD expands AI capacity','Reuters','2026-08-22','STORY',"
        "'https://www.example.test/story/?utm_source=yahoo',["
        "{'paragraph_number':1,'highlight':'Capacity','paragraph':'AMD added capacity.'},"
        "{'paragraph_number':2,'highlight':'','paragraph':'TSM is the manufacturing partner.'}]),"
        "('uuid-old','AMD','Old story','Wire','2026-07-01','STORY','https://x/old',["
        "{'paragraph_number':1,'highlight':'','paragraph':'Outside the window.'}])"
    )
    con.execute(f"COPY stories TO '{parquet}' (FORMAT PARQUET)")
    spec.write_text(json.dumps({"files": {
        "stock_news.parquet": "2026-08-22T05:45:58Z"}}), encoding="utf-8")
    return DefeatBetaConfig(news_uri=str(parquet), spec_uri=str(spec))


def test_local_yahoo_news_keeps_uuid_publisher_date_paragraphs_and_snapshot(tmp_path):
    batch = yahoo_news.fetch(
        "AMD", datetime(2026, 8, 20, tzinfo=timezone.utc), NOW,
        config=_fixture(tmp_path), now=NOW,
    )

    assert batch.status == "succeeded"
    assert len(batch.items) == 1
    item = batch.items[0]
    assert item.id == "yahoo:uuid-1"
    assert item.publisher == "Reuters" and item.report_date == "2026-08-22"
    assert [p.paragraph_number for p in item.paragraphs] == [1, 2]
    assert item.snapshot_updated_at == "2026-08-22T05:45:58+00:00"
    assert round(item.snapshot_lag_hours, 2) == 18.23


def test_structured_body_and_raw_paragraphs_share_one_catalog_asset(tmp_path):
    item = yahoo_news.fetch(
        "AMD", datetime(2026, 8, 20, tzinfo=timezone.utc), NOW,
        config=_fixture(tmp_path), now=NOW,
    ).items[0]

    news._catalog([item], store=get_store())

    rows = get_store().documents(entity="AMD", doc_type="news")
    assert len(rows) == 1 and rows[0]["external_id"] == "https://example.test/story"
    assert rows[0]["carrier_format"] == "structured_dataset"
    body = news.acquire_body(item, store=get_store())
    assert "## Capacity" in body and "TSM is the manufacturing partner" in body
    version = get_store().latest_document_version(rows[0]["document_id"])
    sidecar = version["local_path"].replace(".md", ".json").replace(
        "/.versions/", "/.versions/.structured/")
    payload = json.loads(open(sidecar, encoding="utf-8").read())
    assert payload["uuid"] == "uuid-1" and len(payload["paragraphs"]) == 2


def test_fetch_news_deduplicates_yahoo_and_finnhub_by_canonical_url(monkeypatch):
    yahoo = NewsItem(
        id="yahoo:uuid-1", source="yahoo:defeatbeta", headline="Same story",
        url="https://www.example.test/story/?utm_source=yahoo", published_at=NOW,
        tickers=["AMD"],
    )
    finnhub = yahoo.model_copy(update={
        "id": "finnhub:9", "source": "finnhub",
        "url": "https://example.test/story", "published_at": NOW,
    })
    monkeypatch.setattr(news, "load_news_sources", lambda: {
        "yahoo_news": {"enabled": True}, "rss": []})
    monkeypatch.setattr(yahoo_news, "stored", lambda *a, **k: [yahoo])
    monkeypatch.setattr(news, "_finnhub", lambda *a: [finnhub])
    monkeypatch.setattr(news, "_rss", lambda *a: [])
    monkeypatch.setattr(news, "_x", lambda *a: [])

    out = news.fetch_news("AMD", datetime(2026, 8, 20, tzinfo=timezone.utc))

    assert len(out) == 1 and out[0].id == "yahoo:uuid-1"


def test_yahoo_backfill_aliases_an_existing_ibkr_story_instead_of_copying(tmp_path):
    existing = document_assets.ingest(
        entity="DOWJONES", key="dj-42", doc_type="news_item",
        text="The same complete wire story. " * 80, source="ibkr_news",
        source_url="ibkr-news://DJ-N/42", external_id="ibkr-news://DJ-N/42",
        title="AMD expands AI capacity", published_at="2026-08-22",
        min_chars=1, store=get_store())
    assert existing is not None

    result = yahoo_news.backfill(
        ["AMD"], datetime(2026, 8, 20, tzinfo=timezone.utc), NOW,
        config=_fixture(tmp_path), now=NOW, store=get_store())

    assert result.status == "succeeded" and len(result.items) == 1
    assert len(get_store().documents(doc_type="news")) == 1
    aliases = get_store().document_aliases(existing.document_id)
    assert {row["source"] for row in aliases} == {"yahoo:defeatbeta"}
    assert len(get_store().documents(entity="AMD", doc_type="news")) == 1
    health = get_store().data_source_health()[0]
    assert health["status"] == "succeeded"
    assert (health["discovered"], health["accepted"], health["quarantined"]) == (1, 1, 0)
    assert health["snapshot_updated_at"] == "2026-08-22T05:45:58+00:00"


def test_zero_matches_stale_and_unauthorized_are_distinct_states(tmp_path, monkeypatch):
    config = _fixture(tmp_path)
    zero = yahoo_news.fetch(
        "NVDA", datetime(2026, 8, 20, tzinfo=timezone.utc), NOW,
        config=config, now=NOW)
    stale = yahoo_news.fetch(
        "AMD", datetime(2026, 8, 20, tzinfo=timezone.utc), NOW,
        config=config, now=datetime(2026, 9, 1, tzinfo=timezone.utc),
        stale_after_hours=24)

    class BrokenConnection:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("HTTP 403 Forbidden")

    monkeypatch.setattr(yahoo_news, "_connect", lambda *_: BrokenConnection())
    denied = yahoo_news.fetch(
        "AMD", datetime(2026, 8, 20, tzinfo=timezone.utc), NOW, config=config)

    assert zero.status == "zero_matches"
    assert stale.status == "stale"
    assert denied.status == "unauthorized"


def test_backfill_merges_symbol_associations_for_one_shared_story(monkeypatch):
    amd = NewsItem(
        id="yahoo:one", source="yahoo:defeatbeta", headline="Shared supply story",
        url="https://example.test/shared", published_at=NOW, tickers=["AMD"])
    tsm = amd.model_copy(update={"tickers": ["TSM"]})
    batches = {
        "AMD": yahoo_news.YahooNewsBatch((amd,), "succeeded", discovered=1),
        "TSM": yahoo_news.YahooNewsBatch((tsm,), "succeeded", discovered=1),
    }
    monkeypatch.setattr(yahoo_news, "fetch_many", lambda *a, **k: batches)

    result = yahoo_news.backfill(
        ["AMD", "TSM"], datetime(2026, 8, 20, tzinfo=timezone.utc), NOW,
        store=get_store())

    assert len(result.items) == 1
    assert result.items[0].tickers == ["AMD", "TSM"]


def test_consumers_read_backfilled_assets_without_querying_parquet(tmp_path, monkeypatch):
    yahoo_news.backfill(
        ["AMD"], datetime(2026, 8, 20, tzinfo=timezone.utc), NOW,
        config=_fixture(tmp_path), now=NOW, store=get_store())
    monkeypatch.setattr(yahoo_news, "fetch", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("consumer must not query remote parquet")))

    rows = yahoo_news.stored(
        "AMD", datetime(2026, 8, 20, tzinfo=timezone.utc), store=get_store())

    assert len(rows) == 1
    assert rows[0].headline == "AMD expands AI capacity"
    assert "AMD added capacity" in rows[0].summary
