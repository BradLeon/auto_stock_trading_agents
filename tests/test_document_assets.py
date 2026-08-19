"""Unified document assets: immutable bodies plus versioned consumer processing."""

from datetime import datetime, timedelta, timezone

from ats.data import source_cache
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
