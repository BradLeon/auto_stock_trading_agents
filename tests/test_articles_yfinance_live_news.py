from datetime import datetime, timezone

from ats.data.articles import yfinance_live_news as live_news


NOW = datetime(2026, 8, 29, 14, 20, tzinfo=timezone.utc)


def _story(*, identifier, title, url, publisher="Reuters", published="2026-08-29T14:10:00Z"):
    return {"content": {
        "id": identifier, "title": title, "canonicalUrl": {"url": url},
        "provider": {"displayName": publisher}, "pubDate": published,
    }}


def test_discovery_requires_a_title_level_entity_match_and_retains_rejected_associations(monkeypatch):
    rows = {
        "NVDA": [_story(identifier="nvidia-1", title="NVIDIA announces new AI platform",
                         url="https://finance.yahoo.com/article/nvidia")],
        "MRVL": [_story(identifier="wrong-1", title="Take-Two shares rise on game trailer",
                         url="https://finance.yahoo.com/article/taketwo")],
    }
    monkeypatch.setattr(live_news, "_ticker_news", lambda symbol: rows[symbol])

    refs, status = live_news.discover_with_status(symbols=["NVDA", "MRVL"], now=NOW)

    assert status["status"] == "succeeded"
    assert status["queried_entities"] == ["NVDA", "MRVL"]
    assert len(refs) == 2
    records = {ref.title: live_news.provenance(ref) for ref in refs}
    assert records["NVIDIA announces new AI platform"]["entity_association"] == "title_verified"
    assert records["NVIDIA announces new AI platform"]["title_verified_entities"] == "NVDA"
    assert records["Take-Two shares rise on game trailer"]["entity_association"] == "association_rejected"
    assert records["Take-Two shares rise on game trailer"]["association_rejected_entities"] == "MRVL"


def test_discovery_deduplicates_one_yahoo_story_and_keeps_per_entity_association_audit(monkeypatch):
    shared = _story(identifier="shared", title="NVIDIA and Marvell expand networking partnership",
                    url="https://finance.yahoo.com/article/shared")
    monkeypatch.setattr(live_news, "_ticker_news", lambda _symbol: [shared])

    refs, _status = live_news.discover_with_status(symbols=["NVDA", "MRVL"], now=NOW)

    assert len(refs) == 1
    provenance = live_news.provenance(refs[0])
    assert provenance["queried_entities"] == "MRVL, NVDA"
    assert provenance["title_verified_entities"] == "MRVL, NVDA"


def test_discovery_deduplicates_different_yahoo_ids_that_share_a_canonical_url(monkeypatch):
    url = "https://finance.yahoo.com/article/shared-canonical"
    rows = {
        "NVDA": [_story(identifier="nvidia-view", title="NVIDIA expands AI platform", url=url)],
        "MRVL": [_story(identifier="marvell-view", title="NVIDIA expands AI platform", url=url)],
    }
    monkeypatch.setattr(live_news, "_ticker_news", lambda symbol: rows[symbol])

    refs, _status = live_news.discover_with_status(symbols=["NVDA", "MRVL"], now=NOW)

    assert len(refs) == 1
    provenance = live_news.provenance(refs[0])
    assert provenance["queried_entities"] == "MRVL, NVDA"
    assert provenance["title_verified_entities"] == "NVDA"
    assert provenance["association_rejected_entities"] == "MRVL"


def test_body_requires_article_container_and_exact_title_anchor(monkeypatch):
    title = "NVIDIA announces new AI platform"
    good = f"<html><article><h1>{title}</h1><p>{'body ' * 250}</p></article></html>"
    assert live_news.extract_body(good, title=title).startswith(title)
    assert live_news.extract_body("<main><p>Navigation only</p></main>", title=title) == ""

    monkeypatch.setattr(live_news, "_ticker_news", lambda _symbol: [
        _story(identifier="nvidia-2", title=title,
               url="https://finance.yahoo.com/article/nvidia-2")])
    refs, _ = live_news.discover_with_status(symbols=["NVDA"], now=NOW)
    monkeypatch.setattr(live_news, "_get", lambda _url: good)
    assert "body body" in live_news.fetch_body(refs[0].url)
