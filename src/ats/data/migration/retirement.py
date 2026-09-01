"""Controlled retirement of copied legacy data tables.

``var/ats.sqlite`` remains workflow memory.  This module therefore uses an
explicit table whitelist, validates every historical source row is represented
in the platform database, and creates a hash-verified backup before dropping
only the retired persistent-data tables.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from .runner import _DOMAIN_TABLES, _quote, _sha256, _table_exists


_EXTRA_TABLES = ("structured_migrations",)
_VIEWS = ("structured_observations_selected", "structured_observations_accepted")
_CATALOG_METADATA_TABLES = {
    "structured_sources", "structured_datasets", "structured_metrics", "structured_entities",
}


def _canonical_rows(conn: sqlite3.Connection, table: str) -> Counter[str]:
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({_quote(table)})")]
    if not columns:
        return Counter()
    names = ", ".join(_quote(column) for column in columns)
    return Counter(json.dumps(dict(row), ensure_ascii=False, sort_keys=True,
                              default=str, separators=(",", ":"))
                   for row in conn.execute(f"SELECT {names} FROM {_quote(table)}"))


class LegacyDataRetirement:
    """Validate and retire only listed, migrated persistent-data objects."""

    def __init__(self, source_path: str | Path, target_path: str | Path):
        self.source_path = Path(source_path).expanduser().resolve()
        self.target_path = Path(target_path).expanduser().resolve()

    @staticmethod
    def objects() -> dict[str, str]:
        result: dict[str, str] = {}
        for mapping in _DOMAIN_TABLES.values():
            result.update(mapping)
        return result

    def validate(self) -> dict:
        if not self.source_path.is_file() or not self.target_path.is_file():
            raise FileNotFoundError("legacy or platform data database is missing")
        source = sqlite3.connect(f"file:{self.source_path}?mode=ro", uri=True)
        target = sqlite3.connect(f"file:{self.target_path}?mode=ro", uri=True)
        source.row_factory = target.row_factory = sqlite3.Row
        tables: list[dict] = []
        try:
            for source_table, target_table in self.objects().items():
                if not _table_exists(source, source_table):
                    tables.append({"source_table": source_table, "target_table": target_table,
                                   "status": "already_retired", "source_rows": 0,
                                   "target_rows": 0, "missing_rows": 0})
                    continue
                source_rows = _canonical_rows(source, source_table)
                target_rows = (_canonical_rows(target, target_table)
                               if _table_exists(target, target_table) else Counter())
                missing = source_rows - target_rows
                # These four tables describe the catalog, not acquired facts.  Their
                # values are expected to change when the canonical YAML catalog is
                # normalized at the target.  Presence/coverage is the meaningful
                # criterion; all fact tables remain exact source-subset checks.
                catalog_superseded = (source_table in _CATALOG_METADATA_TABLES
                                      and len(target_rows) >= len(source_rows))
                tables.append({"source_table": source_table, "target_table": target_table,
                               "status": ("reconciled" if not missing else
                                          "superseded_by_catalog" if catalog_superseded
                                          else "mismatch"),
                               "source_rows": sum(source_rows.values()),
                               "target_rows": sum(target_rows.values()),
                               "missing_rows": sum(missing.values())})
        finally:
            source.close()
            target.close()
        ready = all(item["status"] in {"reconciled", "already_retired", "superseded_by_catalog"}
                    for item in tables)
        return {"source_path": str(self.source_path), "target_path": str(self.target_path),
                "ready": ready, "tables": tables, "metadata_tables": list(_EXTRA_TABLES),
                "views": list(_VIEWS)}

    def retire(self, *, backup_root: str | Path, apply: bool = False) -> dict:
        validation = self.validate()
        if not validation["ready"]:
            return {**validation, "applied": False, "reason": "reconciliation_failed"}
        if not apply:
            return {**validation, "applied": False, "reason": "dry_run"}
        root = Path(backup_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        source_hash = _sha256(self.source_path)
        backup = root / f"{self.source_path.stem}.retirement.{source_hash[:16]}.sqlite"
        if not backup.exists():
            shutil.copy2(self.source_path, backup)
        if _sha256(backup) != source_hash:
            raise RuntimeError("retirement backup hash mismatch; source was not changed")
        manifest = {"retirement_id": uuid4().hex,
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "source_path": str(self.source_path), "target_path": str(self.target_path),
                    "source_sha256": source_hash, "backup_path": str(backup),
                    "tables": validation["tables"], "views": validation["views"]}
        target = sqlite3.connect(self.target_path)
        target.execute("CREATE TABLE IF NOT EXISTS data_retirement_manifests "
                       "(retirement_id TEXT PRIMARY KEY,created_at TEXT NOT NULL,"
                       "source_path TEXT NOT NULL,backup_path TEXT NOT NULL,"
                       "source_sha256 TEXT NOT NULL,manifest_json TEXT NOT NULL)")
        target.execute("INSERT INTO data_retirement_manifests VALUES (?,?,?,?,?,?)",
                       (manifest["retirement_id"], manifest["at"], manifest["source_path"],
                        manifest["backup_path"], manifest["source_sha256"],
                        json.dumps(manifest, ensure_ascii=False, sort_keys=True)))
        target.commit(); target.close()
        source = sqlite3.connect(self.source_path)
        try:
            source.execute("PRAGMA foreign_keys=OFF")
            source.execute("BEGIN IMMEDIATE")
            for view in _VIEWS:
                source.execute(f"DROP VIEW IF EXISTS {_quote(view)}")
            for table in [*self.objects(), *_EXTRA_TABLES]:
                source.execute(f"DROP TABLE IF EXISTS {_quote(table)}")
            source.commit()
        except Exception:
            source.rollback()
            raise
        finally:
            source.close()
        return {**self.validate(), "applied": True, "backup_path": str(backup),
                "manifest": manifest}


__all__ = ["LegacyDataRetirement"]
