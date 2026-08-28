"""Persistent, per-consumer comparison records for data-platform cutover."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_consumer_cutover_records (
    record_id TEXT PRIMARY KEY, consumer TEXT NOT NULL, entity TEXT NOT NULL,
    checked_at TEXT NOT NULL, status TEXT NOT NULL, details_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_data_cutover_consumer
    ON data_consumer_cutover_records(consumer, checked_at DESC);
"""


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _document_scope(table: str, *, document_table: str, entity_table: str) -> str:
    """Return an entity predicate which preserves both primary and linked entities."""
    if table == document_table:
        return (f"(entity=? OR EXISTS (SELECT 1 FROM {entity_table} de "
                f"WHERE de.document_id={table}.document_id AND de.entity=?))")
    return (f"document_id IN (SELECT d.document_id FROM {document_table} d WHERE "
            f"d.entity=? OR EXISTS (SELECT 1 FROM {entity_table} de "
            f"WHERE de.document_id=d.document_id AND de.entity=?))")


def _entity_predicate(conn: sqlite3.Connection, table: str, columns: list[str],
                      entity: str) -> tuple[str, list[str], str]:
    """Scope every migrated table by real lineage, not accidental global counts."""
    if not entity:
        return "", [], "all_entities"
    entity = entity.upper()
    if "entity" in columns:
        if table in {"source_documents", "data_documents"}:
            entity_table = "data_document_entities" if table.startswith("data_") else "document_entities"
            if _table_exists(conn, entity_table):
                return (f" WHERE {_document_scope(table, document_table=table, entity_table=entity_table)}",
                        [entity, entity], "entity_and_links")
        return " WHERE entity=?", [entity], "entity"
    if "entity_id" in columns:
        return " WHERE entity_id=?", [entity], "entity_id"

    document_table = "data_documents" if table.startswith("data_") else "source_documents"
    entity_table = "data_document_entities" if table.startswith("data_") else "document_entities"
    if "document_id" in columns and _table_exists(conn, document_table) and _table_exists(conn, entity_table):
        return (f" WHERE {_document_scope(table, document_table=document_table, entity_table=entity_table)}",
                [entity, entity], "document_lineage")
    if table in {"document_chunks", "data_document_chunks", "document_processing_runs", "data_document_processing_runs"}:
        version_table = "data_document_versions" if table.startswith("data_") else "document_versions"
        if _table_exists(conn, version_table) and _table_exists(conn, document_table) and _table_exists(conn, entity_table):
            return (
                f" WHERE version_id IN (SELECT v.version_id FROM {version_table} v "
                f"WHERE v.document_id IN (SELECT d.document_id FROM {document_table} d WHERE "
                f"d.entity=? OR EXISTS (SELECT 1 FROM {entity_table} de "
                f"WHERE de.document_id=d.document_id AND de.entity=?)))",
                [entity, entity], "document_version_lineage")
    if table in {"evidence_fact_projections", "data_evidence_projections"}:
        fact_table = "data_evidence_facts" if table.startswith("data_") else "evidence_facts"
        if _table_exists(conn, fact_table):
            return f" WHERE fact_id IN (SELECT fact_id FROM {fact_table} WHERE entity=?)", [entity], "fact_lineage"
    if "target_id" in columns:
        return " WHERE target_id=?", [entity], "target_entity"
    if table == "structured_observations" and _table_exists(conn, "structured_series"):
        return (" WHERE series_id IN (SELECT series_id FROM structured_series WHERE entity_id=?)",
                [entity], "structured_series_lineage")
    return "", [], "consumer_global"


def _signature(conn: sqlite3.Connection, table: str, *, entity: str = "") -> dict:
    if not _table_exists(conn, table):
        return {"count": 0, "digest": hashlib.sha256(b"").hexdigest(), "available": False}
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    if not columns:
        return {"count": 0, "digest": hashlib.sha256(b"").hexdigest(), "available": True}
    where, args, scope = _entity_predicate(conn, table, columns, entity)
    digest, count = hashlib.sha256(), 0
    for row in conn.execute(f"SELECT * FROM {table}{where} ORDER BY {','.join(columns)}", args):
        digest.update(json.dumps(dict(row), ensure_ascii=False, sort_keys=True,
                                 default=str, separators=(",", ":")).encode())
        digest.update(b"\n")
        count += 1
    return {"count": count, "digest": digest.hexdigest(), "available": True, "scope": scope}


_TABLE_PAIRS = {
    "documents": ("source_documents", "data_documents"),
    "document_candidates": ("document_candidates", "data_document_candidates"),
    "versions": ("document_versions", "data_document_versions"),
    "document_entities": ("document_entities", "data_document_entities"),
    "document_aliases": ("document_source_aliases", "data_document_aliases"),
    "document_chunks": ("document_chunks", "data_document_chunks"),
    "document_processing": ("document_processing_runs", "data_document_processing_runs"),
    "facts": ("evidence_facts", "data_evidence_facts"),
    "fact_projections": ("evidence_fact_projections", "data_evidence_projections"),
    "evidence_observations": ("evidence_observations", "data_evidence_observations"),
    "evidence_failures": ("evidence_failures", "data_evidence_failures"),
    "structured_observations": ("structured_observations", "structured_observations"),
}


def _consumer_id(value: str) -> str:
    """Release overlays use snake_case; accept historical hyphenated inventory IDs."""
    return value.strip().lower().replace("-", "_")


def consumer_cutover_status(*, consumer: str, data_db: str | Path,
                            minimum_distinct_reconciled_days: int = 1,
                            maximum_mismatches: int = 0) -> dict:
    """Decide whether a consumer has completed its real-time observation period."""
    consumer_id = _consumer_id(consumer)
    path = Path(data_db)
    if not path.exists():
        return {"consumer": consumer_id, "eligible": False, "reason": "data_database_missing",
                "minimum_distinct_reconciled_days": minimum_distinct_reconciled_days,
                "reconciled_days": 0, "mismatches": 0}
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        if not _table_exists(conn, "data_consumer_cutover_records"):
            rows = []
        else:
            rows = conn.execute(
                "SELECT checked_at,status FROM data_consumer_cutover_records "
                "WHERE consumer IN (?, ?) ORDER BY checked_at",
                (consumer_id, consumer_id.replace("_", "-"))).fetchall()
    finally:
        conn.close()
    # Preserve the full audit trail, but do not make a fixed historical mismatch
    # permanently block a corrected consumer.  A fresh clean observation window
    # starts after the most recent real mismatch and is what a release evaluates.
    latest_mismatch = max(
        (str(row[0]) for row in rows if row[1] != "reconciled"), default=None)
    active_rows = rows if latest_mismatch is None else [
        row for row in rows if str(row[0]) > latest_mismatch]
    reconciled_days = sorted({str(row[0])[:10] for row in active_rows
                              if row[1] == "reconciled"})
    mismatches = sum(1 for row in active_rows if row[1] != "reconciled")
    eligible = len(reconciled_days) >= minimum_distinct_reconciled_days and mismatches <= maximum_mismatches
    return {
        "consumer": consumer_id, "eligible": eligible,
        "minimum_distinct_reconciled_days": minimum_distinct_reconciled_days,
        "maximum_mismatches": maximum_mismatches,
        "reconciled_days": len(reconciled_days), "observed_on": reconciled_days,
        "mismatches": mismatches, "records": len(rows),
        "active_window_records": len(active_rows),
        "latest_historical_mismatch_at": latest_mismatch,
        "reason": "stable" if eligible else "observation_period_incomplete"
        if mismatches <= maximum_mismatches else "reconciliation_mismatch",
    }


def record_consumer_comparison(*, consumer: str, entity: str, data_db: str | Path,
                               status: str, details: dict) -> dict:
    """Persist one semantic shadow-read result for a consumer release gate.

    Table/hash migration reconciliation and DTO/output reconciliation are distinct
    checks.  This helper stores the latter without pretending that a byte-identical
    table copy proves equivalent PEAD inputs.
    """
    normalized_status = "reconciled" if status == "reconciled" else "mismatch"
    checked_at = datetime.now(timezone.utc).isoformat()
    path = Path(data_db)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        result = {"consumer": _consumer_id(consumer), "entity": entity.upper(),
                  "checked_at": checked_at, "status": normalized_status,
                  "comparison_type": "consumer_output", "details": details}
        conn.execute("INSERT INTO data_consumer_cutover_records VALUES (?,?,?,?,?,?)", (
            uuid4().hex, result["consumer"], result["entity"], checked_at,
            normalized_status, json.dumps(result, ensure_ascii=False, sort_keys=True)))
        conn.commit()
        return result
    finally:
        conn.close()


def compare_consumer_data(*, consumer: str, entity: str = "",
                          legacy_db: str | Path, data_db: str | Path,
                          record: bool = True) -> dict:
    """Compare one consumer's migrated data and persist an auditable result."""
    consumer_id = _consumer_id(consumer)
    legacy_path, data_path = Path(legacy_db), Path(data_db)
    old = sqlite3.connect(f"file:{legacy_path.resolve()}?mode=ro", uri=True)
    new = sqlite3.connect(data_path)
    old.row_factory = new.row_factory = sqlite3.Row
    try:
        comparisons = {}
        for name, (legacy_table, data_table) in _TABLE_PAIRS.items():
            left = _signature(old, legacy_table, entity=entity)
            right = _signature(new, data_table, entity=entity)
            comparisons[name] = {
                "legacy": left, "platform": right,
                "matched": left["available"] == right["available"]
                and left["count"] == right["count"] and left["digest"] == right["digest"],
            }
        result = {"consumer": consumer_id, "entity": entity.upper(),
                  "checked_at": datetime.now(timezone.utc).isoformat(),
                  "status": "reconciled" if all(row["matched"] for row in comparisons.values()) else "mismatch",
                  "comparisons": comparisons}
        if record:
            new.executescript(_SCHEMA)
            new.execute("INSERT INTO data_consumer_cutover_records VALUES (?,?,?,?,?,?)", (
                uuid4().hex, consumer_id, entity.upper(), result["checked_at"], result["status"],
                json.dumps(result, ensure_ascii=False, sort_keys=True)))
            new.commit()
        return result
    finally:
        old.close()
        new.close()


__all__ = ["compare_consumer_data", "consumer_cutover_status", "record_consumer_comparison"]
