"""Durable consumer-level reconciliation for the physical data cutover."""

from __future__ import annotations

import sqlite3

from ats.data.migration import SQLiteMigrationRunner, load_migration_inventory
from ats.data.cutover import (
    compare_consumer_data,
    consumer_cutover_status,
    record_consumer_comparison,
)


def _domain(name: str):
    return next(item for item in load_migration_inventory().domains if item.id == name)


def _legacy_documents(path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE source_documents (
            document_id TEXT PRIMARY KEY, entity TEXT, title TEXT, content_hash TEXT
        );
        CREATE TABLE document_candidates (candidate_id TEXT PRIMARY KEY, document_id TEXT);
        CREATE TABLE document_versions (version_id TEXT PRIMARY KEY, document_id TEXT, content_hash TEXT);
        CREATE TABLE document_entities (document_id TEXT, entity TEXT);
        CREATE TABLE document_source_aliases (alias_id TEXT PRIMARY KEY, document_id TEXT);
        CREATE TABLE document_chunks (chunk_id TEXT PRIMARY KEY, version_id TEXT, text TEXT);
        CREATE TABLE document_processing_runs (version_id TEXT, consumer TEXT);
    """)
    conn.execute("INSERT INTO source_documents VALUES ('doc-1','NVDA','release','hash-1')")
    conn.execute("INSERT INTO document_candidates VALUES ('candidate-1','doc-1')")
    conn.execute("INSERT INTO document_versions VALUES ('version-1','doc-1','hash-1')")
    conn.execute("INSERT INTO document_entities VALUES ('doc-1','NVDA')")
    conn.execute("INSERT INTO document_source_aliases VALUES ('alias-1','doc-1')")
    conn.execute("INSERT INTO document_chunks VALUES ('chunk-1','version-1','body')")
    conn.execute("INSERT INTO document_processing_runs VALUES ('version-1','chain')")
    conn.commit()
    conn.close()


def test_consumer_cutover_records_reconciled_and_mismatch_results(tmp_path) -> None:
    source, target, backups = tmp_path / "ats.sqlite", tmp_path / "data.sqlite", tmp_path / "backups"
    _legacy_documents(source)
    assert SQLiteMigrationRunner(source, target).run(
        _domain("unstructured-documents"), backup_root=backups).reconciled

    reconciled = compare_consumer_data(
        consumer="evidence-chain", entity="NVDA", legacy_db=source, data_db=target)
    assert reconciled["status"] == "reconciled"
    assert reconciled["comparisons"]["documents"]["matched"]
    assert reconciled["comparisons"]["document_chunks"]["matched"]

    conn = sqlite3.connect(target)
    conn.execute("UPDATE data_documents SET title='wrong' WHERE document_id='doc-1'")
    conn.commit()
    conn.close()
    mismatch = compare_consumer_data(
        consumer="evidence-chain", entity="NVDA", legacy_db=source, data_db=target)
    assert mismatch["status"] == "mismatch"
    assert not mismatch["comparisons"]["documents"]["matched"]

    conn = sqlite3.connect(target)
    records = conn.execute(
        "SELECT status FROM data_consumer_cutover_records WHERE consumer='evidence_chain' ORDER BY checked_at"
    ).fetchall()
    conn.close()
    assert [row[0] for row in records] == ["reconciled", "mismatch"]


def test_consumer_status_requires_distinct_successful_days_and_no_mismatch(tmp_path) -> None:
    target = tmp_path / "data.sqlite"
    conn = sqlite3.connect(target)
    conn.executescript("""
        CREATE TABLE data_consumer_cutover_records (
            record_id TEXT PRIMARY KEY, consumer TEXT, entity TEXT, checked_at TEXT,
            status TEXT, details_json TEXT
        );
    """)
    conn.executemany("INSERT INTO data_consumer_cutover_records VALUES (?,?,?,?,?,?)", [
        ("1", "pead-graph", "NVDA", "2026-08-20T00:00:00+00:00", "reconciled", "{}"),
        ("2", "pead-graph", "NVDA", "2026-08-21T00:00:00+00:00", "reconciled", "{}"),
    ])
    conn.commit()
    conn.close()
    status = consumer_cutover_status(
        consumer="pead-graph", data_db=target, minimum_distinct_reconciled_days=2)
    assert status["eligible"] is True

    conn = sqlite3.connect(target)
    conn.execute("INSERT INTO data_consumer_cutover_records VALUES (?,?,?,?,?,?)", (
        "3", "pead-graph", "NVDA", "2026-08-22T00:00:00+00:00", "mismatch", "{}"))
    conn.commit()
    conn.close()
    assert consumer_cutover_status(
        consumer="pead-graph", data_db=target, minimum_distinct_reconciled_days=2)["eligible"] is False


def test_consumer_status_defaults_to_one_clean_reconciliation_day(tmp_path) -> None:
    target = tmp_path / "data.sqlite"
    conn = sqlite3.connect(target)
    conn.executescript("""
        CREATE TABLE data_consumer_cutover_records (
            record_id TEXT PRIMARY KEY, consumer TEXT, entity TEXT, checked_at TEXT,
            status TEXT, details_json TEXT
        );
    """)
    conn.execute("INSERT INTO data_consumer_cutover_records VALUES (?,?,?,?,?,?)", (
        "1", "sector_agent", "TW_IC_EXPORT", "2026-08-26T00:00:00+00:00", "reconciled", "{}"))
    conn.commit()
    conn.close()

    status = consumer_cutover_status(consumer="sector-agent", data_db=target)

    assert status["eligible"] is True
    assert status["minimum_distinct_reconciled_days"] == 1
    assert status["mismatches"] == 0


def test_consumer_status_starts_a_new_window_after_a_fixed_mismatch(tmp_path) -> None:
    target = tmp_path / "data.sqlite"
    conn = sqlite3.connect(target)
    conn.executescript("""
        CREATE TABLE data_consumer_cutover_records (
            record_id TEXT PRIMARY KEY, consumer TEXT, entity TEXT, checked_at TEXT,
            status TEXT, details_json TEXT
        );
    """)
    conn.executemany("INSERT INTO data_consumer_cutover_records VALUES (?,?,?,?,?,?)", [
        ("1", "pead_fundamentals", "TSM", "2026-08-26T00:00:00+00:00", "mismatch", "{}"),
        ("2", "pead_fundamentals", "TSM", "2026-08-28T00:00:00+00:00", "reconciled", "{}"),
    ])
    conn.commit()
    conn.close()

    status = consumer_cutover_status(consumer="pead_fundamentals", data_db=target)

    assert status["eligible"] is True
    assert status["mismatches"] == 0
    assert status["records"] == 2
    assert status["active_window_records"] == 1
    assert status["latest_historical_mismatch_at"] == "2026-08-26T00:00:00+00:00"


def test_semantic_consumer_comparison_is_a_release_gate_record(tmp_path) -> None:
    target = tmp_path / "data.sqlite"
    recorded = record_consumer_comparison(
        consumer="pead_fundamentals", entity="TSM", data_db=target, status="mismatch",
        details={"legacy": ["ADR EPS"], "platform": ["ordinary-share EPS"]})

    assert recorded["status"] == "mismatch"
    status = consumer_cutover_status(consumer="pead_fundamentals", data_db=target)
    # A recorded mismatch remains auditable but starts a fresh clean observation
    # window; it must not be eligible until a new reconciliation arrives.
    assert status["eligible"] is False and status["mismatches"] == 0
    assert status["latest_historical_mismatch_at"]
    assert status["reason"] == "observation_period_incomplete"
