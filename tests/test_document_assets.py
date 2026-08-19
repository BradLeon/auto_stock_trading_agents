"""Unified document assets: immutable bodies plus versioned consumer processing."""

from datetime import datetime, timedelta, timezone

from ats.data import source_cache
from ats.data import document_assets
from ats.memory import get_store


def _body(marker: str) -> str:
    return (f"{marker}: publisher supplied research body. " * 40).strip()


def test_document_content_versions_are_immutable_and_latest_stays_compatible():
    store = get_store()
    t0 = datetime(2026, 8, 19, tzinfo=timezone.utc)

    first = source_cache.store("SEMIANALYSIS", "article-1", "article", _body("v1"),
                               source="imap", source_url="https://example.test/a", now=t0)
    assert first is not None
    store.save_document(first)

    second = source_cache.store(
        "SEMIANALYSIS", "article-1", "article", _body("v2"), source="imap",
        source_url="https://example.test/a", now=t0 + timedelta(days=1))
    assert second is not None
    store.save_document(second)

    versions = store.document_versions(first.document_id)
    assert len(versions) == 2
    assert {v["content_hash"] for v in versions} == {first.sha256, second.sha256}
    assert all(v["local_path"] for v in versions)
    assert first.version_path and first.version_path.is_file()
    assert second.version_path and second.version_path.is_file()
    assert first.version_path != second.version_path

    latest = source_cache.load("SEMIANALYSIS", "article-1", "article")
    assert latest is not None and latest.sha256 == second.sha256
    assert latest.text == _body("v2")


def test_saving_the_same_body_twice_is_idempotent():
    store = get_store()
    body = _body("same")
    first = source_cache.store("TRENDFORCE", "story", "article", body, source="web")
    second = source_cache.store("TRENDFORCE", "story", "article", body, source="web")
    assert first is not None and second is not None
    store.save_document(first)
    store.save_document(second)

    assert len(store.document_versions(first.document_id)) == 1
    assert first.version_path == second.version_path


def test_processing_is_idempotent_per_content_consumer_and_processor_version():
    store = get_store()
    doc = source_cache.store("SEMIANALYSIS", "article-2", "article", _body("body"),
                             source="imap")
    assert doc is not None
    store.save_document(doc)

    version_id = store.begin_document_processing(doc.document_id, "pead", "prompt-v1")
    assert version_id
    assert store.begin_document_processing(doc.document_id, "pead", "prompt-v1") is None
    store.finish_document_processing(version_id, "pead", "prompt-v1",
                                     ok=False, note="temporary model failure")
    assert store.begin_document_processing(doc.document_id, "pead", "prompt-v1") is None

    # A deliberate processor upgrade is a new, auditable computation, not a retry.
    upgraded = store.begin_document_processing(doc.document_id, "pead", "prompt-v2")
    evidence = store.begin_document_processing(doc.document_id, "chain", "observer-v1")
    assert upgraded == version_id and evidence == version_id
    store.finish_document_processing(upgraded, "pead", "prompt-v2", ok=True, outputs=3)
    store.finish_document_processing(evidence, "chain", "observer-v1", ok=True, outputs=5)

    rows = store.document_processing(doc.document_id)
    assert {(r["consumer"], r["processor_version"], r["status"], r["outputs"])
            for r in rows} == {
                ("pead", "prompt-v1", "failed", 0),
                ("pead", "prompt-v2", "succeeded", 3),
                ("chain", "observer-v1", "succeeded", 5),
            }


def test_new_content_version_is_eligible_for_the_same_processor():
    store = get_store()
    first = source_cache.store("SEMIANALYSIS", "article-3", "article", _body("old"),
                               source="imap")
    assert first is not None
    store.save_document(first)
    old_version = store.begin_document_processing(first.document_id, "pead", "v1")
    assert old_version
    store.finish_document_processing(old_version, "pead", "v1", ok=True, outputs=1)

    second = source_cache.store("SEMIANALYSIS", "article-3", "article", _body("corrected"),
                                source="imap")
    assert second is not None
    store.save_document(second)
    new_version = store.begin_document_processing(second.document_id, "pead", "v1")

    assert new_version and new_version != old_version


def test_common_ingestion_preserves_metadata_and_multi_entity_associations():
    store = get_store()
    body = _body("shared-news")
    doc = document_assets.ingest(
        entity="NEWS", key=document_assets.stable_key("provider:42"), doc_type="news",
        text=body, source="finnhub", source_url="https://example.test/news/42",
        external_id="provider:42", title="AMD and TSM expand AI capacity",
        published_at="2026-08-19T00:00:00+00:00", related_entities=("AMD", "TSM"),
        store=store,
    )
    assert doc is not None

    # One publisher-owned body, discoverable from both company scopes.
    assert len(store.documents(entity="AMD", doc_type="news")) == 1
    assert len(store.documents(entity="TSM", doc_type="news")) == 1
    assert len(store.documents(doc_type="news")) == 1
    row, loaded = document_assets.read_external("provider:42", store=store)
    assert row and row["title"] == "AMD and TSM expand AI capacity"
    assert loaded == body

    cached = source_cache.load("NEWS", doc.period, "news")
    assert cached is not None
    assert cached.external_id == "provider:42"
    assert cached.related_entities == ("AMD", "TSM")


def test_entity_association_backfill_covers_pre_platform_documents(tmp_path):
    from ats.memory.store import TradingMemory

    path = tmp_path / "backfill.sqlite"
    first = TradingMemory(path)
    doc = source_cache.store("AMD", "2026Q2", "release", _body("legacy"), source="sec")
    assert doc is not None
    first.save_document(doc)
    first.conn.execute("DELETE FROM document_entities")
    first.conn.commit()
    first.conn.close()

    reopened = TradingMemory(path)
    assert len(reopened.documents(entity="AMD", doc_type="release")) == 1


def test_evidence_compatibility_id_resolves_to_exact_shared_asset():
    from ats.agents.evidence import observer

    store = get_store()
    body = _body("AMD reported_actual revenue growth")
    doc = document_assets.ingest(
        entity="AMD", key="2026Q2", doc_type="release", text=body, source="sec",
        source_url="https://example.test/release", store=store,
    )
    assert doc is not None
    # Keep extraction deterministic; this test is about the id handed to it.
    from ats.schemas.chain import Observation

    observed = Observation(
        id="resolved", document_id="legacy-id", source_url=doc.source_url,
        entity="AMD", source_entity="AMD", metric="revenue", concept="ai_adoption",
        period="2026Q2", observation_type="reported_actual", stance="supplier",
        direction="up", value=None, unit="", evidence_span="revenue growth",
        observed_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
    original = observer.extract
    try:
        observer.extract = lambda *a, **k: ([observed.model_copy(
            update={"document_id": a[1]})], "")
        result = observer.observe_document("AMD", "AMD:20260819", body, store=store)
    finally:
        observer.extract = original
    assert result["saved"] == 1
    fact = store.facts(entity="AMD")[0]
    assert fact["document_id"] == doc.document_id
    assert fact["document_version_id"] == store.latest_document_version(doc.document_id)["version_id"]
