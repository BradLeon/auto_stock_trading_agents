"""Resumable SQLite copy-and-reconcile runner for legacy data assets.

The runner is deliberately source-read-only.  A target receives prefixed data
tables and a durable manifest; the original SQLite file is copied and hash
verified before any non-dry-run target write happens.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Iterable
from uuid import uuid4

from .inventory import MigrationDomain


_DOMAIN_TABLES = {
    "structured-governed-history": {
        "structured_artifact_blobs": "structured_artifact_blobs",
        "structured_artifacts": "structured_artifacts",
        "structured_sources": "structured_sources",
        "structured_datasets": "structured_datasets",
        "structured_metrics": "structured_metrics",
        "structured_provider_mappings": "structured_provider_mappings",
        "structured_pending_mappings": "structured_pending_mappings",
        "structured_series": "structured_series",
        "structured_observations": "structured_observations",
        "structured_derivations": "structured_derivations",
        "structured_evidence_links": "structured_evidence_links",
        "structured_entities": "structured_entities",
        "structured_events": "structured_events",
        "structured_evidence_candidates": "structured_evidence_candidates",
        "structured_evidence_reviews": "structured_evidence_reviews",
        "structured_snapshots": "structured_snapshots",
        "structured_snapshot_items": "structured_snapshot_items",
        "structured_ingestion_runs": "structured_ingestion_runs",
        "structured_candidates": "structured_candidates",
        "structured_conflicts": "structured_conflicts",
        "structured_legacy_audits": "structured_legacy_audits",
    },
    "unstructured-documents": {
        "source_documents": "data_documents",
        "document_candidates": "data_document_candidates",
        "document_versions": "data_document_versions",
        "document_entities": "data_document_entities",
        "document_source_aliases": "data_document_aliases",
        "document_chunks": "data_document_chunks",
        "document_processing_runs": "data_document_processing_runs",
    },
    "unstructured-evidence": {
        "evidence_observations": "data_evidence_observations",
        "evidence_failures": "data_evidence_failures",
        "evidence_facts": "data_evidence_facts",
        "evidence_fact_projections": "data_evidence_projections",
    },
}


_MANIFEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_migration_manifests (
    migration_id TEXT PRIMARY KEY, domain_id TEXT NOT NULL,
    source_path TEXT NOT NULL, target_path TEXT NOT NULL,
    created_at TEXT NOT NULL, dry_run INTEGER NOT NULL,
    backup_path TEXT NOT NULL, source_sha256 TEXT NOT NULL,
    backup_sha256 TEXT NOT NULL, status TEXT NOT NULL,
    manifest_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS data_migration_table_results (
    migration_id TEXT NOT NULL, source_table TEXT NOT NULL, target_table TEXT NOT NULL,
    source_count INTEGER NOT NULL, target_count INTEGER NOT NULL,
    source_digest TEXT NOT NULL, target_digest TEXT NOT NULL,
    copied INTEGER NOT NULL, status TEXT NOT NULL,
    PRIMARY KEY (migration_id, source_table)
);
"""


@dataclass(frozen=True)
class TableMigrationResult:
    source_table: str
    target_table: str
    source_count: int
    target_count: int
    source_digest: str
    target_digest: str
    copied: int
    status: str


@dataclass(frozen=True)
class MigrationManifest:
    migration_id: str
    domain_id: str
    source_path: str
    target_path: str
    created_at: str
    dry_run: bool
    backup_path: str
    source_sha256: str
    backup_sha256: str
    status: str
    tables: tuple[TableMigrationResult, ...]

    @property
    def reconciled(self) -> bool:
        return self.status == "reconciled" and all(
            row.status == "reconciled" for row in self.tables)

    def model_dump(self) -> dict:
        return {**asdict(self), "tables": [asdict(row) for row in self.tables],
                "reconciled": self.reconciled}


def _quote(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"unsafe SQLite identifier: {value}")
    return f'"{value}"'


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _table_digest(conn: sqlite3.Connection, table: str) -> tuple[int, str]:
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({_quote(table)})")]
    if not columns:
        return 0, hashlib.sha256(b"").hexdigest()
    order = ", ".join(_quote(column) for column in columns)
    digest = hashlib.sha256()
    count = 0
    for row in conn.execute(f"SELECT * FROM {_quote(table)} ORDER BY {order}"):
        digest.update(json.dumps(dict(row), ensure_ascii=False, sort_keys=True,
                                 default=str, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
        count += 1
    return count, digest.hexdigest()


def _target_ddl(source: sqlite3.Connection, source_table: str, target_table: str) -> str:
    row = source.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (source_table,)).fetchone()
    if row is None or not row[0]:
        raise ValueError(f"source table not found: {source_table}")
    ddl = row[0]
    pattern = r"^(CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)(?:\"?" + \
        re.escape(source_table) + r"\"?)"
    replaced, count = re.subn(pattern, r"\1" + _quote(target_table), ddl,
                              count=1, flags=re.IGNORECASE)
    if count != 1:
        raise ValueError(f"cannot derive target DDL for {source_table}")
    return re.sub(r"^CREATE\s+TABLE\s+", "CREATE TABLE IF NOT EXISTS ", replaced,
                  count=1, flags=re.IGNORECASE)


def _insert_rows(source: sqlite3.Connection, target: sqlite3.Connection,
                 source_table: str, target_table: str) -> int:
    columns = [row[1] for row in source.execute(f"PRAGMA table_info({_quote(source_table)})")]
    if not columns:
        return 0
    names = ", ".join(_quote(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    rows = source.execute(f"SELECT {names} FROM {_quote(source_table)}")
    before = target.total_changes
    target.executemany(
        f"INSERT OR IGNORE INTO {_quote(target_table)} ({names}) VALUES ({placeholders})",
        (tuple(row[column] for column in columns) for row in rows),
    )
    return target.total_changes - before


class SQLiteMigrationRunner:
    """Migrate declared unstructured domains to an independent data SQLite file."""

    def __init__(self, source_path: str | Path, target_path: str | Path):
        self.source_path = Path(source_path).expanduser().resolve()
        self.target_path = Path(target_path).expanduser().resolve()

    def _source(self) -> sqlite3.Connection:
        if not self.source_path.is_file():
            raise FileNotFoundError(f"legacy source database not found: {self.source_path}")
        conn = sqlite3.connect(f"file:{self.source_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _target(self) -> sqlite3.Connection:
        self.target_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.target_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_MANIFEST_SCHEMA)
        return conn

    def _backup(self, backup_root: str | Path | None, *, dry_run: bool) -> tuple[str, str, str]:
        source_digest = _sha256(self.source_path)
        if dry_run:
            return "", source_digest, ""
        if backup_root is None:
            raise ValueError("backup_root is required for a non-dry-run migration")
        root = Path(backup_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        backup = root / f"{self.source_path.stem}.{source_digest[:16]}.sqlite"
        if not backup.exists():
            shutil.copy2(self.source_path, backup)
        backup_digest = _sha256(backup)
        if backup_digest != source_digest:
            raise RuntimeError("backup hash mismatch; target was not migrated")
        return str(backup), source_digest, backup_digest

    def run(self, domain: MigrationDomain, *, backup_root: str | Path | None = None,
            dry_run: bool = False) -> MigrationManifest:
        mapping = _DOMAIN_TABLES.get(domain.id)
        if mapping is None:
            raise ValueError(f"migration runner does not support domain: {domain.id}")
        expected = set(domain.source_tables)
        if set(mapping) != expected:
            raise ValueError(f"configured tables do not match runner mapping for {domain.id}")
        backup_path, source_sha256, backup_sha256 = self._backup(backup_root, dry_run=dry_run)
        migration_id = uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        results: list[TableMigrationResult] = []
        source = self._source()
        target = None if dry_run else self._target()
        try:
            for source_table, target_table in mapping.items():
                if not _table_exists(source, source_table):
                    raise RuntimeError(f"missing source table for {domain.id}: {source_table}")
                source_count, source_digest = _table_digest(source, source_table)
                copied = 0
                if dry_run:
                    target_count, target_digest = source_count, source_digest
                else:
                    assert target is not None
                    target.execute(_target_ddl(source, source_table, target_table))
                    copied = _insert_rows(source, target, source_table, target_table)
                    target_count, target_digest = _table_digest(target, target_table)
                status = "reconciled" if (source_count, source_digest) == (
                    target_count, target_digest) else "mismatch"
                results.append(TableMigrationResult(
                    source_table=source_table, target_table=target_table,
                    source_count=source_count, target_count=target_count,
                    source_digest=source_digest, target_digest=target_digest,
                    copied=copied, status=status))
            status = "reconciled" if all(row.status == "reconciled" for row in results) else "mismatch"
            manifest = MigrationManifest(
                migration_id=migration_id, domain_id=domain.id,
                source_path=str(self.source_path), target_path=str(self.target_path),
                created_at=created_at, dry_run=dry_run, backup_path=backup_path,
                source_sha256=source_sha256, backup_sha256=backup_sha256,
                status=status, tables=tuple(results))
            if not dry_run:
                assert target is not None
                payload = json.dumps(manifest.model_dump(), ensure_ascii=False, sort_keys=True)
                with target:
                    target.execute(
                        "INSERT INTO data_migration_manifests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (manifest.migration_id, manifest.domain_id, manifest.source_path,
                         manifest.target_path, manifest.created_at, int(manifest.dry_run),
                         manifest.backup_path, manifest.source_sha256, manifest.backup_sha256,
                         manifest.status, payload))
                    target.executemany(
                        "INSERT INTO data_migration_table_results VALUES (?,?,?,?,?,?,?,?,?)",
                        [(manifest.migration_id, row.source_table, row.target_table,
                          row.source_count, row.target_count, row.source_digest,
                          row.target_digest, row.copied, row.status) for row in manifest.tables])
            return manifest
        finally:
            source.close()
            if target is not None:
                target.close()


def default_data_db_path() -> Path:
    from ...config import REPO_ROOT

    return Path(os.environ.get("ATS_DATA_DB_PATH", REPO_ROOT / "var" / "data.sqlite"))


__all__ = ["MigrationManifest", "SQLiteMigrationRunner", "TableMigrationResult", "default_data_db_path"]
