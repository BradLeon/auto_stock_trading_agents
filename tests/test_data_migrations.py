"""Upgrade tests start from pre-data-platform SQLite shapes, not the current schema."""

import hashlib
import sqlite3

from ats.memory.store import TradingMemory


def _legacy_database(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE source_documents (
            document_id TEXT PRIMARY KEY, entity TEXT, period TEXT, doc_type TEXT,
            source TEXT, source_url TEXT, local_path TEXT, sha256 TEXT,
            chars INTEGER, ok INTEGER DEFAULT 1, note TEXT, fetched_at TEXT
        );
        CREATE TABLE evidence_observations (
            id TEXT PRIMARY KEY, document_id TEXT, source_url TEXT,
            entity TEXT, metric TEXT, period TEXT, observation_type TEXT,
            stance TEXT, direction TEXT, value REAL, unit TEXT, evidence_span TEXT,
            observed_at TEXT, discovery_evidence INTEGER DEFAULT 0,
            extraction_confidence REAL DEFAULT 1.0
        );
    """)
    body_hash = hashlib.sha256(b"legacy body").hexdigest()
    conn.execute(
        "INSERT INTO source_documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("AMD:2026Q2:transcript", "AMD", "2026Q2", "transcript", "legacy:sec",
         "https://example.test/legacy", "/tmp/legacy.txt", body_hash, 11, 1, "",
         "2026-08-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO evidence_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("legacy-observation", "AMD:2026Q2:transcript",
         "https://example.test/legacy", "AMD", "inference_demand", "2026Q2",
         "reported_actual", "supplier", "up", None, "",
         "inference demand increased", "2026-08-01T00:00:00+00:00", 0, 0.9),
    )
    conn.commit()
    conn.close()
    return body_hash


def test_legacy_documents_and_observations_upgrade_without_data_loss(tmp_path):
    """Legacy rows must survive the upgrade — in the data layer, where they now live.

    Workflow memory drops `evidence_observations` and `source_documents` as part of the
    boundary decision, so the legacy content is only preserved because the migration
    promotes it across the boundary into `data_evidence_*` / `data_document_*`. Reading
    the local tables here would assert against rows that are gone by design.
    """
    path = tmp_path / "legacy.sqlite"
    body_hash = _legacy_database(path)

    store = TradingMemory(path)

    version = store.latest_document_version("AMD:2026Q2:transcript")
    assert version is not None and version["content_hash"] == body_hash
    runs = store.document_processing("AMD:2026Q2:transcript")
    assert [(r["consumer"], r["status"]) for r in runs] == [("chain", "succeeded")]

    facts = store.facts(entity="AMD")
    projections = store.fact_projections(profile="legacy-evidence")
    assert len(facts) == len(projections) == 1
    assert facts[0]["evidence_span"] == "inference demand increased"
    assert facts[0]["document_version_id"] == version["version_id"]
    assert projections[0]["legacy_observation_id"] == "legacy-observation"
    assert {r["key"] for r in store.conn.execute("SELECT key FROM data_migrations")} >= {
        "document_processing_chain_v1", "shared_facts_v1",
    }
    store.conn.close()

    # Reopening is idempotent: migration history and projections do not duplicate.
    reopened = TradingMemory(path)
    assert len(reopened.document_versions("AMD:2026Q2:transcript")) == 1
    assert len(reopened.document_processing("AMD:2026Q2:transcript")) == 1
    assert len(reopened.facts(entity="AMD")) == 1
    assert len(reopened.fact_projections(profile="legacy-evidence")) == 1


def test_later_documents_are_not_mislabelled_as_legacy_chain_work(tmp_path):
    path = tmp_path / "legacy.sqlite"
    _legacy_database(path)
    store = TradingMemory(path)
    _add_document_after_the_backfill(store)
    store.conn.close()

    reopened = TradingMemory(path)
    assert reopened.latest_document_version("SEMIANALYSIS:new:article") is not None
    assert reopened.document_processing("SEMIANALYSIS:new:article") == []


def _add_document_after_the_backfill(store) -> None:
    """Acquire a document the way a later run would — through the data layer."""
    data = store.data_store()
    stamp = "2026-08-19T00:00:00+00:00"
    body_hash = hashlib.sha256(b"new").hexdigest()
    data.conn.execute(
        "INSERT OR REPLACE INTO data_documents "
        "(document_id,entity,period,doc_type,source,source_url,local_path,sha256,chars,"
        " ok,note,fetched_at,external_id,title,published_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("SEMIANALYSIS:new:article", "SEMIANALYSIS", "new", "article", "newsletter",
         "https://example.test/new", "/tmp/new.txt", body_hash, 3, 1, "", stamp,
         "new", "New", "2026-08-19"))
    data.conn.execute(
        "INSERT OR IGNORE INTO data_document_versions "
        "(version_id,document_id,content_hash,local_path,chars,source_url,fetched_at,"
        " created_at) VALUES (?,?,?,?,?,?,?,?)",
        (f"SEMIANALYSIS:new:article@{body_hash[:16]}", "SEMIANALYSIS:new:article",
         body_hash, "/tmp/new.txt", 3, "https://example.test/new", stamp, stamp))
    data.conn.commit()
