from datetime import datetime, timezone

from ats.data.pipelines.unstructured.article_ingest import (
    _article_sources, _materials_from_adapter, ingest_article_source,
)
from ats.data.pipelines.unstructured.source_acceptance import load_policy
from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository


def test_ibkr_connection_refusal_is_explicit_unreachable(monkeypatch):
    from ats.data.articles import ibkr_news

    def fail(**_kwargs):
        raise ConnectionRefusedError("TWS is not listening")

    monkeypatch.setattr(ibkr_news, "discover_with_status", fail)
    source = _article_sources()["ibkr_news"]
    materials, status, has_refs = _materials_from_adapter(
        source, load_policy(source.id), now=datetime(2026, 9, 25, tzinfo=timezone.utc))
    assert materials == [] and not has_refs
    assert status["status"] == "unreachable"
    assert status["reason"] == "ConnectionRefusedError"


def test_full_ibkr_outage_fallback_stays_in_registered_primary_scope(tmp_path, monkeypatch):
    from ats.data.pipelines.unstructured import article_ingest

    captured = []

    def materials(source, policy, *, now, adapter_params=None):
        captured.append((source.id, adapter_params))
        if source.id == "ibkr_news":
            return [], {"status": "unreachable", "failed_slices": []}, False
        return [], {"status": "succeeded", "failed_slices": []}, False

    monkeypatch.setattr(article_ingest, "_materials_from_adapter", materials)
    store = PlatformUnstructuredRepository(tmp_path / "article.sqlite", writable=True)
    try:
        ingest_article_source("ibkr_news", store=store,
                              now=datetime(2026, 9, 25, tzinfo=timezone.utc))
    finally:
        store.close()
    assert captured == [
        ("ibkr_news", None),
        ("yfinance_live_news", {"symbols": load_policy("ibkr_news")["policy"]["symbols"]}),
    ]
