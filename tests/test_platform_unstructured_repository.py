"""Read contracts for migrated document and evidence history."""

from __future__ import annotations

import sqlite3

from ats.data.stores.unstructured import PlatformUnstructuredRepository


def _target(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE data_documents (document_id TEXT PRIMARY KEY, entity TEXT, period TEXT,
            doc_type TEXT, source TEXT, source_url TEXT, local_path TEXT, sha256 TEXT,
            chars INTEGER, ok INTEGER, note TEXT, fetched_at TEXT, external_id TEXT,
            title TEXT, published_at TEXT, completeness TEXT, truncation_reason TEXT,
            carrier_format TEXT, mime_source TEXT);
        CREATE TABLE data_document_entities (document_id TEXT, entity TEXT, relation TEXT);
        CREATE TABLE data_document_versions (version_id TEXT PRIMARY KEY, document_id TEXT,
            content_hash TEXT, local_path TEXT, chars INTEGER, source_url TEXT,
            fetched_at TEXT, created_at TEXT);
        CREATE TABLE data_document_chunks (chunk_id TEXT PRIMARY KEY, version_id TEXT,
            ordinal INTEGER, char_start INTEGER, char_end INTEGER, text TEXT, content_hash TEXT);
        CREATE TABLE data_evidence_facts (fact_id TEXT PRIMARY KEY, document_id TEXT,
            document_version_id TEXT, source_url TEXT, entity TEXT, source_entity TEXT,
            metric TEXT, period TEXT, observation_type TEXT, value REAL, unit TEXT,
            evidence_span TEXT, observed_at TEXT, extraction_confidence REAL,
            discovery_evidence INTEGER, superseded_at TEXT);
        CREATE TABLE data_evidence_projections (projection_id TEXT PRIMARY KEY, fact_id TEXT,
            legacy_observation_id TEXT, profile TEXT, profile_version TEXT, concept TEXT,
            stance TEXT, direction TEXT, payload TEXT, created_at TEXT, superseded_at TEXT);
        CREATE TABLE data_evidence_observations (id TEXT PRIMARY KEY, document_id TEXT,
            source_url TEXT, entity TEXT, metric TEXT, period TEXT, observation_type TEXT,
            stance TEXT, direction TEXT, value REAL, unit TEXT, evidence_span TEXT,
            observed_at TEXT, discovery_evidence INTEGER, extraction_confidence REAL,
            concept TEXT, source_entity TEXT, superseded_at TEXT);
        CREATE TABLE data_task_projections (projection_id TEXT PRIMARY KEY, profile TEXT,
            profile_version TEXT, input_kind TEXT, input_ref TEXT, target_type TEXT,
            target_id TEXT, payload TEXT, created_at TEXT, expires_at TEXT);
        CREATE TABLE data_document_processing_runs (version_id TEXT, consumer TEXT,
            processor_version TEXT, status TEXT, started_at TEXT, completed_at TEXT,
            outputs INTEGER, note TEXT);
    """)
    conn.execute("INSERT INTO data_documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("doc-1", "NVDA", "2026Q2", "earnings_release", "issuer", "url", "path",
                  "hash", 10, 1, "", "2026-08-01", "", "Earnings", "2026-07-30", "full", "", "html", "text/html"))
    conn.execute("INSERT INTO data_document_entities VALUES ('doc-1','AMD','mentioned')")
    conn.execute("INSERT INTO data_document_versions VALUES ('v1','doc-1','hash','path',10,'url','2026-08-01','2026-08-01')")
    conn.execute("INSERT INTO data_document_chunks VALUES ('c1','v1',0,0,10,'GPU demand accelerates','hash')")
    conn.execute("INSERT INTO data_evidence_facts VALUES ('f1','doc-1','v1','url','NVDA','NVDA','demand','2026Q2','guidance',1,'x','span','2026-08-01',1,0,NULL)")
    conn.execute("INSERT INTO data_evidence_projections VALUES ('p1','f1','o1','chain','v1','demand','management','up','{}','2026-08-01',NULL)")
    conn.execute("INSERT INTO data_evidence_observations VALUES ('o1','doc-1','url','NVDA','demand','2026Q2','guidance','management','up',1,'x','span','2026-08-01',0,1,'demand','NVDA',NULL)")
    conn.commit(); conn.close()


def test_migrated_document_and_evidence_queries_preserve_identity(tmp_path):
    path = tmp_path / "data.sqlite"
    _target(path)
    repository = PlatformUnstructuredRepository(path)
    try:
        assert repository.documents(entity="AMD")[0]["document_id"] == "doc-1"
        assert repository.latest_document_version("doc-1")["version_id"] == "v1"
        assert repository.search_document_chunks("demand", entity="NVDA")[0]["chunk_id"] == "c1"
        assert repository.facts(entity="NVDA")[0]["fact_id"] == "f1"
        assert repository.fact_projections(concept="demand")[0]["projection_id"] == "p1"
        assert repository.observations(entity="NVDA")[0]["id"] == "o1"
    finally:
        repository.close()
