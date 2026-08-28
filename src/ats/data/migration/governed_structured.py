"""Copy already-governed structured tables and their content-addressed artifacts.

Unlike the legacy measurement converter, this migrator preserves structured IDs
verbatim.  It deliberately reconciles each table against the target so a target
with stale or extra records is not silently accepted as a successful cutover.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import sqlite3

from .inventory import MigrationDomain
from .runner import (
    MigrationManifest,
    SQLiteMigrationRunner,
    TableMigrationResult,
    _MANIFEST_SCHEMA,
    _quote,
    _sha256,
    _table_digest,
    _table_exists,
)


class GovernedStructuredMigrationRunner(SQLiteMigrationRunner):
    """Migrate structured repository state plus raw artifact blobs, idempotently."""

    def __init__(self, source_path: str | Path, target_path: str | Path, *,
                 source_artifact_root: str | Path, target_artifact_root: str | Path):
        super().__init__(source_path, target_path)
        self.source_artifact_root = Path(source_artifact_root).expanduser().resolve()
        self.target_artifact_root = Path(target_artifact_root).expanduser().resolve()

    @staticmethod
    def _artifact_digest(conn: sqlite3.Connection, root: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        count = 0
        for row in conn.execute(
                "SELECT blob_id,content_hash,relative_path,bytes FROM structured_artifact_blobs "
                "ORDER BY blob_id"):
            relative = Path(row["relative_path"])
            path = (root / relative).resolve()
            if root not in path.parents or not path.is_file():
                raise RuntimeError(f"structured artifact missing: {relative}")
            content_hash = _sha256(path)
            if content_hash != row["content_hash"] or path.stat().st_size != row["bytes"]:
                raise RuntimeError(f"structured artifact checksum mismatch: {relative}")
            digest.update(f"{row['blob_id']}|{content_hash}|{row['bytes']}\n".encode())
            count += 1
        return count, digest.hexdigest()

    def _copy_artifacts(self, source: sqlite3.Connection) -> int:
        copied = 0
        self.target_artifact_root.mkdir(parents=True, exist_ok=True)
        for row in source.execute(
                "SELECT content_hash,relative_path,bytes FROM structured_artifact_blobs "
                "ORDER BY blob_id"):
            relative = Path(row["relative_path"])
            source_path = (self.source_artifact_root / relative).resolve()
            target_path = (self.target_artifact_root / relative).resolve()
            if self.source_artifact_root not in source_path.parents or \
                    self.target_artifact_root not in target_path.parents:
                raise RuntimeError(f"unsafe structured artifact path: {relative}")
            if not source_path.is_file() or _sha256(source_path) != row["content_hash"]:
                raise RuntimeError(f"structured artifact source verification failed: {relative}")
            if target_path.exists():
                if _sha256(target_path) != row["content_hash"] or target_path.stat().st_size != row["bytes"]:
                    raise RuntimeError(f"structured artifact target conflict: {relative}")
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            if _sha256(target_path) != row["content_hash"] or target_path.stat().st_size != row["bytes"]:
                raise RuntimeError(f"structured artifact target verification failed: {relative}")
            copied += 1
        return copied

    @staticmethod
    def _sync_table(source: sqlite3.Connection, target: sqlite3.Connection,
                    table: str) -> tuple[int, int, str, int, str]:
        """Synchronize a same-schema table, without counting an identical retry."""
        source_count, source_digest = _table_digest(source, table)
        target_count, target_digest = _table_digest(target, table)
        if (source_count, source_digest) == (target_count, target_digest):
            return source_count, target_count, source_digest, 0, target_digest
        columns = [row[1] for row in source.execute(f"PRAGMA table_info({_quote(table)})")]
        names = ", ".join(_quote(column) for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        before = target.total_changes
        target.executemany(
            f"INSERT OR REPLACE INTO {_quote(table)} ({names}) VALUES ({placeholders})",
            (tuple(row[column] for column in columns)
             for row in source.execute(f"SELECT {names} FROM {_quote(table)}")),
        )
        target_count, target_digest = _table_digest(target, table)
        return source_count, target_count, source_digest, target.total_changes - before, target_digest

    def run(self, domain: MigrationDomain, *, backup_root: str | Path | None = None,
            dry_run: bool = False) -> MigrationManifest:
        if domain.id != "structured-governed-history":
            raise ValueError(f"unsupported governed structured domain: {domain.id}")
        expected = tuple(domain.source_tables)
        if tuple(domain.target_tables) != expected:
            raise ValueError("governed structured migration requires same-name target tables")
        backup_path, source_sha256, backup_sha256 = self._backup(backup_root, dry_run=dry_run)
        source = self._source()
        target = None
        try:
            missing = [table for table in expected if not _table_exists(source, table)]
            if missing:
                raise RuntimeError(f"missing governed structured tables: {missing}")
            if dry_run:
                artifact_count, artifact_digest = self._artifact_digest(source, self.source_artifact_root)
                rows = [TableMigrationResult(
                    source_table=table, target_table=table,
                    source_count=(count := _table_digest(source, table)[0]), target_count=count,
                    source_digest=(digest := _table_digest(source, table)[1]), target_digest=digest,
                    copied=0, status="reconciled") for table in expected]
                rows.append(TableMigrationResult(
                    source_table="structured_artifact_files", target_table="structured_artifact_files",
                    source_count=artifact_count, target_count=artifact_count,
                    source_digest=artifact_digest, target_digest=artifact_digest,
                    copied=0, status="reconciled"))
                return self._manifest(domain, source_sha256, backup_sha256, backup_path,
                                      dry_run=True, rows=tuple(rows))

            from ..stores.structured.repository import SQLiteStructuredRepository

            repo = SQLiteStructuredRepository(self.target_path, artifact_root=self.target_artifact_root)
            try:
                target = repo.conn
                target.executescript(_MANIFEST_SCHEMA)
                results: list[TableMigrationResult] = []
                for table in expected:
                    source_count, target_count, source_digest, copied, target_digest = \
                        self._sync_table(source, target, table)
                    results.append(TableMigrationResult(
                        source_table=table, target_table=table, source_count=source_count,
                        target_count=target_count, source_digest=source_digest,
                        target_digest=target_digest, copied=copied,
                        status="reconciled" if (source_count, source_digest) ==
                        (target_count, target_digest) else "mismatch"))
                copied_files = self._copy_artifacts(source)
                source_files, source_file_digest = self._artifact_digest(source, self.source_artifact_root)
                target_files, target_file_digest = self._artifact_digest(target, self.target_artifact_root)
                results.append(TableMigrationResult(
                    source_table="structured_artifact_files", target_table="structured_artifact_files",
                    source_count=source_files, target_count=target_files,
                    source_digest=source_file_digest, target_digest=target_file_digest,
                    copied=copied_files, status="reconciled" if (source_files, source_file_digest) ==
                    (target_files, target_file_digest) else "mismatch"))
                manifest = self._manifest(domain, source_sha256, backup_sha256, backup_path,
                                          dry_run=False, rows=tuple(results))
                self._write_manifest(target, manifest)
                return manifest
            finally:
                repo.close()
        finally:
            source.close()

    def _manifest(self, domain: MigrationDomain, source_sha256: str, backup_sha256: str,
                  backup_path: str, *, dry_run: bool,
                  rows: tuple[TableMigrationResult, ...]) -> MigrationManifest:
        from datetime import datetime, timezone
        from uuid import uuid4

        status = "reconciled" if all(row.status == "reconciled" for row in rows) else "mismatch"
        return MigrationManifest(
            migration_id=uuid4().hex, domain_id=domain.id, source_path=str(self.source_path),
            target_path=str(self.target_path), created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            dry_run=dry_run, backup_path=backup_path, source_sha256=source_sha256,
            backup_sha256=backup_sha256, status=status, tables=rows)

    @staticmethod
    def _write_manifest(target: sqlite3.Connection, manifest: MigrationManifest) -> None:
        import json

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


__all__ = ["GovernedStructuredMigrationRunner"]
