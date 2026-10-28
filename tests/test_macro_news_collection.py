"""Macro theme news is collected by the data platform; the review only reads admitted documents."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
BODY = "Federal Reserve officials signalled a slower pace of easing. " * 8


def _cfg():
    theme = lambda key, queries: SimpleNamespace(key=key, queries=queries)       # noqa: E731
    return SimpleNamespace(search={"recency_days": 21, "max_results_per_query": 4},
                           themes=[theme("fed_policy", ["q1", "q2"]), theme("employment", []),
                                   theme("geopolitics", ["q3"])])


class _Store:
    def __init__(self, seen=()):
        self.seen = set(seen)

    def document_by_external_id(self, external_id):
        return {"document_id": "x"} if external_id in self.seen else None


@pytest.fixture
def ingested(monkeypatch):
    from ats.data import document_assets

    rows = []

    def fake_ingest(**kw):
        rows.append(kw)
        return SimpleNamespace(**kw)

    monkeypatch.setattr(document_assets, "ingest", fake_ingest)
    return rows


def _hit(url, *, body=BODY, published="Wed, 14 Oct 2026 10:00:00 GMT", title="Fed signals"):
    return {"title": title, "url": url, "content": body, "published": published}


def test_results_become_partial_documents_under_theme_entities(ingested):
    from ats.data.collection import macro_news

    packet = macro_news.collect(search=lambda q, **k: [_hit(f"https://x/{q}")], config=_cfg(),
                                now=NOW, store=_Store())
    assert packet["status"] == "succeeded" and packet["new"] == 3 and packet["requests"] == 3
    assert {r["entity"] for r in ingested} == {"MACRO_FED_POLICY", "MACRO_GEOPOLITICS"}
    row = ingested[0]
    assert row["source"] == macro_news.SOURCE_ID and row["completeness"] == "partial"
    assert row["external_id"] == row["source_url"] and row["published_at"].startswith("2026-10-14")


def test_a_theme_without_queries_costs_no_request(ingested):
    from ats.data.collection import macro_news

    calls = []
    macro_news.collect(search=lambda q, **k: calls.append(q) or [], config=_cfg(), now=NOW, store=_Store())
    assert calls == ["q1", "q2", "q3"]        # employment has none


def test_a_url_already_admitted_is_not_stored_twice(ingested):
    from ats.data.collection import macro_news

    packet = macro_news.collect(search=lambda q, **k: [_hit("https://x/same")], config=_cfg(), now=NOW,
                                store=_Store(seen={"https://x/same"}))
    assert packet["status"] == "no_change" and packet["reason"] == "nothing_new" and not ingested


def test_stubs_and_stale_results_are_dropped_not_stored(ingested):
    from ats.data.collection import macro_news

    def search(q, **k):
        return [_hit("https://x/stub", body="Subscribe to read"),
                _hit("https://x/old", published="Thu, 17 Sep 2026 00:00:00 GMT"),
                _hit("")]

    packet = macro_news.collect(search=search, config=_cfg(), now=NOW, store=_Store())
    assert not ingested and packet["themes"]["fed_policy"]["dropped"] == 6


def test_a_missing_publish_date_is_stamped_with_collection_time_and_noted(ingested):
    from ats.data.collection import macro_news

    macro_news.collect(search=lambda q, **k: [_hit("https://x/nodate", published="")], config=_cfg(),
                       now=NOW, store=_Store(), theme_keys={"geopolitics"})
    assert ingested[0]["published_at"].startswith("2026-10-16") and "missing" in ingested[0]["note"]


def test_every_query_empty_is_a_failure_not_no_news(ingested):
    from ats.data.collection import macro_news

    packet = macro_news.collect(search=lambda q, **k: [], config=_cfg(), now=NOW, store=_Store())
    assert (packet["status"], packet["reason"]) == ("failed", "no_search_results")


def test_dry_run_writes_nothing(ingested):
    from ats.data.collection import macro_news

    packet = macro_news.collect(search=lambda q, **k: [_hit("https://x/a")], config=_cfg(), now=NOW,
                                store=_Store(), dry_run=True)
    assert packet["new"] == 3 and not ingested


# --------------------------------------------------------------------------- #
# Reader and boundary
# --------------------------------------------------------------------------- #
def test_the_reader_asks_for_the_theme_entity_and_news_types_only(monkeypatch):
    from ats.data.products import macro_inputs, unstructured

    seen = {}

    def fake(**kw):
        seen.update(kw)
        return [SimpleNamespace(source="macro_theme_news", n=1), SimpleNamespace(source="ibkr_news", n=2)]

    monkeypatch.setattr(unstructured, "admitted_documents", fake)
    docs = macro_inputs.admitted_news("fed_policy", days=21, now=NOW)
    assert seen["entities"] == ["MACRO_FED_POLICY"] and set(seen["document_types"]) == {"news_item", "news"}
    assert seen["published_since"].startswith("2026-09-25")
    assert [d.n for d in docs] == [1]          # another source's document never leaks in


def test_no_data_product_module_reaches_the_search_provider():
    """The launder path (agents -> macro_inputs -> websearch) is closed: products must not import it."""
    root = Path(__file__).resolve().parents[1] / "src/ats/data/products"
    offenders = [p.name for p in root.glob("*.py") if "websearch" in p.read_text(encoding="utf-8")
                 and p.name != "macro_inputs.py"]
    assert offenders == []
    assert "import websearch" not in (root / "macro_inputs.py").read_text(encoding="utf-8").split('"""')[2]


def test_the_shipped_schedule_and_catalog_accept_the_news_source():
    from ats.data.catalog import DataCatalog
    from ats.data.refresh import load_plan

    assert "macro_theme_news" in {s.id for s in DataCatalog.load().target_unstructured_sources()}
    jobs = [j for j in load_plan().jobs if j.id == "macro_theme_news_daily"]
    assert jobs and jobs[0].domain == "derived" and jobs[0].request_budget >= 13
