"""Migration of legacy measurement tables into governed structured observations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from .inventory import MigrationDomain
from .runner import _sha256, _table_digest
from ...structured import (
    ArtifactDescriptor,
    ObservationInput,
    QualityStatus,
    SeriesIdentity,
    StructuredCatalog,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_structured_legacy_manifests (
    migration_id TEXT PRIMARY KEY, source_path TEXT NOT NULL, target_path TEXT NOT NULL,
    created_at TEXT NOT NULL, backup_path TEXT NOT NULL, source_sha256 TEXT NOT NULL,
    backup_sha256 TEXT NOT NULL, source_count INTEGER NOT NULL, migrated_count INTEGER NOT NULL,
    skipped_count INTEGER NOT NULL, source_digest TEXT NOT NULL, status TEXT NOT NULL,
    manifest_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS data_structured_legacy_map (
    migration_id TEXT NOT NULL, legacy_point_id TEXT NOT NULL, observation_id TEXT NOT NULL,
    raw_hash TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
    PRIMARY KEY (migration_id, legacy_point_id)
);
CREATE TABLE IF NOT EXISTS data_structured_legacy_sources (
    migration_id TEXT NOT NULL, legacy_source_id TEXT NOT NULL,
    source_id TEXT NOT NULL, raw_payload TEXT NOT NULL,
    PRIMARY KEY (migration_id, legacy_source_id)
);
"""


@dataclass(frozen=True)
class StructuredMigrationManifest:
    migration_id: str
    source_path: str
    target_path: str
    created_at: str
    backup_path: str
    source_sha256: str
    backup_sha256: str
    source_count: int
    migrated_count: int
    skipped_count: int
    source_digest: str
    status: str
    rows: tuple[dict[str, str], ...]

    @property
    def reconciled(self) -> bool:
        return self.status == "reconciled" and self.source_count == self.migrated_count

    def model_dump(self) -> dict:
        return {**asdict(self), "rows": list(self.rows), "reconciled": self.reconciled}


def _aware(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


class StructuredLegacyMigrationRunner:
    """Convert legacy measurement rows with explicit metric/dataset mapping only."""

    def __init__(self, source_path: str | Path, target_path: str | Path,
                 *, artifact_root: str | Path):
        self.source_path = Path(source_path).expanduser().resolve()
        self.target_path = Path(target_path).expanduser().resolve()
        self.artifact_root = Path(artifact_root).expanduser().resolve()

    def _backup(self, backup_root: str | Path | None, *, dry_run: bool) -> tuple[str, str, str]:
        source_digest = _sha256(self.source_path)
        if dry_run:
            return "", source_digest, ""
        if backup_root is None:
            raise ValueError("backup_root is required for a non-dry-run migration")
        root = Path(backup_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{self.source_path.stem}.{source_digest[:16]}.sqlite"
        if not target.exists():
            import shutil

            shutil.copy2(self.source_path, target)
        backup_digest = _sha256(target)
        if backup_digest != source_digest:
            raise RuntimeError("backup hash mismatch; target was not migrated")
        return str(target), source_digest, backup_digest

    def run(self, domain: MigrationDomain, *, backup_root: str | Path | None = None,
            dry_run: bool = False) -> StructuredMigrationManifest:
        if domain.id != "structured-legacy-measurements":
            raise ValueError(f"unsupported structured migration domain: {domain.id}")
        if not self.source_path.is_file():
            raise FileNotFoundError(f"legacy source database not found: {self.source_path}")
        backup_path, source_sha256, backup_sha256 = self._backup(backup_root, dry_run=dry_run)
        source = sqlite3.connect(f"file:{self.source_path}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        try:
            required = set(domain.source_tables)
            actual = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = required - actual
            if missing:
                raise RuntimeError(f"missing structured legacy tables: {sorted(missing)}")
            point_count, point_digest = _table_digest(source, "measurement_points")
            run_count, run_digest = _table_digest(source, "ingestion_runs")
            legacy_source_count, legacy_source_digest = _table_digest(source, "data_sources")
            source_count = point_count + run_count + legacy_source_count
            source_digest = hashlib.sha256(
                f"measurement_points:{point_digest}\ningestion_runs:{run_digest}\n"
                f"data_sources:{legacy_source_digest}\n".encode()
            ).hexdigest()
            rows = source.execute(
                "SELECT p.*, s.source_id, s.series, s.entity, s.unit AS series_unit, s.cadence "
                "FROM measurement_points p JOIN measurement_series s ON s.series_id=p.series_id "
                "ORDER BY p.point_id"
            ).fetchall()
            created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            migration_id = uuid4().hex
            outcomes: list[dict[str, str]] = []
            from ..stores.structured.repository import SQLiteStructuredRepository

            catalog = StructuredCatalog.load()
            configured_sources = {item.id: item for item in catalog.sources()}
            metric_ids = {item.id: item for item in catalog.metrics()}
            mappings = {(provider, field): metric for provider, field, metric
                        in catalog.provider_mappings()}
            legacy_sources = source.execute("SELECT * FROM data_sources ORDER BY source_id").fetchall()
            unmapped: list[dict[str, str]] = []
            for row in legacy_sources:
                if str(row["source_id"] or "") not in configured_sources:
                    unmapped.append({"legacy_point_id": f"source:{row['source_id']}",
                                     "status": "skipped", "reason": "source_unmapped"})
            for row in rows:
                source_id, field = str(row["source_id"] or ""), str(row["series"] or "")
                source_def = configured_sources.get(source_id)
                metric_id = mappings.get((source_id, field), field if field in metric_ids else "")
                if not source_def or not source_def.datasets or not metric_id:
                    unmapped.append({"legacy_point_id": str(row["point_id"]),
                                     "status": "skipped", "reason": "metric_or_dataset_unmapped"})
            for row in source.execute("SELECT * FROM ingestion_runs ORDER BY run_id"):
                source_def = configured_sources.get(str(row["source_id"] or ""))
                if not source_def or not source_def.datasets:
                    unmapped.append({"legacy_point_id": f"run:{row['run_id']}",
                                     "status": "skipped", "reason": "run_source_unmapped"})
            if dry_run:
                skipped = len(unmapped)
                return StructuredMigrationManifest(
                    migration_id=migration_id, source_path=str(self.source_path),
                    target_path=str(self.target_path), created_at=created_at, backup_path="",
                    source_sha256=source_sha256, backup_sha256="", source_count=source_count,
                    migrated_count=source_count - skipped, skipped_count=skipped,
                    source_digest=source_digest,
                    status="reconciled" if not skipped else "incomplete", rows=tuple(unmapped))

            repo = SQLiteStructuredRepository(self.target_path, artifact_root=self.artifact_root)
            target = repo.conn
            try:
                repo.bootstrap_catalog()
                target.executescript(_SCHEMA)
                migrated = skipped = 0
                for row in legacy_sources:
                    source_id = str(row["source_id"] or "")
                    if source_id not in configured_sources:
                        skipped += 1
                        outcomes.append({"legacy_point_id": f"source:{source_id}",
                                         "status": "skipped", "reason": "source_unmapped"})
                        continue
                    target.execute(
                        "INSERT INTO data_structured_legacy_sources VALUES (?,?,?,?)",
                        (migration_id, source_id, source_id,
                         json.dumps(dict(row), ensure_ascii=False, sort_keys=True)))
                    migrated += 1
                    outcomes.append({"legacy_point_id": f"source:{source_id}",
                                     "observation_id": source_id,
                                     "status": "migrated", "reason": ""})
                for row in rows:
                    source_id = str(row["source_id"] or "")
                    field = str(row["series"] or "")
                    metric_id = mappings.get((source_id, field), field if field in metric_ids else "")
                    source_def = configured_sources.get(source_id)
                    dataset_id = source_def.datasets[0] if source_def and source_def.datasets else ""
                    if not metric_id or not dataset_id:
                        skipped += 1
                        outcomes.append({"legacy_point_id": str(row["point_id"]),
                                         "status": "skipped", "reason": "metric_or_dataset_unmapped"})
                        continue
                    fetched_at = _aware(row["fetched_at"], datetime.now(timezone.utc))
                    published_at = _aware(row["published_at"], fetched_at) if row["published_at"] else None
                    raw_value: Any
                    try:
                        raw_value = json.loads(row["raw_payload"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        raw_value = {"legacy_raw_payload": row["raw_payload"]}
                    raw = {"legacy_point_id": row["point_id"], "legacy_series_id": row["series_id"],
                           "legacy_content_hash": row["content_hash"], "payload": raw_value}
                    artifact = repo.put_artifact(raw, descriptor=ArtifactDescriptor(
                        source_id=source_id, dataset_id=dataset_id, fetched_at=fetched_at,
                        query_scope={"legacy_series_id": row["series_id"],
                                     "legacy_point_id": row["point_id"]},
                        source_url="legacy:measurement_points", source_version="legacy-v1",
                        media_type="application/json", retention="full_response",
                        metadata={"legacy_content_hash": row["content_hash"]}))
                    observation = repo.save_observation(ObservationInput(
                        series=SeriesIdentity(source_id=source_id, dataset_id=dataset_id,
                                              entity_id=str(row["entity"] or "GLOBAL"),
                                              metric_id=metric_id,
                                              unit=str(row["unit"] or row["series_unit"] or ""),
                                              period_basis=str(row["cadence"] or "")),
                        period=str(row["period"]), value=float(row["value"]),
                        known_at=fetched_at, fetched_at=fetched_at, published_at=published_at,
                        artifact_id=artifact.id, quality_status=QualityStatus.WARNING,
                        quality={"migration": "legacy_measurements", "published_at_unknown": published_at is None},
                        raw=raw,
                    ))
                    migrated += 1
                    outcomes.append({"legacy_point_id": str(row["point_id"]),
                                     "observation_id": observation.id,
                                     "status": "migrated", "reason": ""})
                run_rows = source.execute(
                    "SELECT * FROM ingestion_runs ORDER BY run_id").fetchall()
                for row in run_rows:
                    source_id = str(row["source_id"] or "")
                    source_def = configured_sources.get(source_id)
                    dataset_id = source_def.datasets[0] if source_def and source_def.datasets else ""
                    if not source_def or not dataset_id:
                        skipped += 1
                        outcomes.append({"legacy_point_id": f"run:{row['run_id']}",
                                         "status": "skipped", "reason": "run_source_unmapped"})
                        continue
                    run_id = hashlib.sha1(
                        f"legacy-ingestion|{row['run_id']}".encode()).hexdigest()[:24]
                    target.execute(
                        "INSERT OR IGNORE INTO structured_ingestion_runs VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, source_id, dataset_id, "{}", row["started_at"] or created_at,
                         row["completed_at"] or row["started_at"] or created_at,
                         row["status"] or "unknown", int(row["discovered"] or 0),
                         int(row["accepted"] or 0), int(row["quarantined"] or 0), 0,
                         row["reason_codes"] or "{}", row["note"] or ""))
                    migrated += 1
                    outcomes.append({"legacy_point_id": f"run:{row['run_id']}",
                                     "observation_id": run_id,
                                     "status": "migrated", "reason": ""})
                status = "reconciled" if migrated == source_count and skipped == 0 else "incomplete"
                manifest = StructuredMigrationManifest(
                    migration_id=migration_id, source_path=str(self.source_path),
                    target_path=str(self.target_path), created_at=created_at, backup_path=backup_path,
                    source_sha256=source_sha256, backup_sha256=backup_sha256,
                    source_count=source_count, migrated_count=migrated, skipped_count=skipped,
                    source_digest=source_digest, status=status, rows=tuple(outcomes))
                with target:
                    target.execute(
                        "INSERT INTO data_structured_legacy_manifests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (manifest.migration_id, manifest.source_path, manifest.target_path,
                         manifest.created_at, manifest.backup_path, manifest.source_sha256,
                         manifest.backup_sha256, manifest.source_count, manifest.migrated_count,
                         manifest.skipped_count, manifest.source_digest, manifest.status,
                         json.dumps(manifest.model_dump(), ensure_ascii=False, sort_keys=True)))
                    target.executemany(
                        "INSERT INTO data_structured_legacy_map VALUES (?,?,?,?,?,?)",
                        [(manifest.migration_id, item["legacy_point_id"], item.get("observation_id", ""),
                          hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest(),
                          item["status"], item["reason"]) for item in outcomes])
                return manifest
            finally:
                repo.close()
        finally:
            source.close()


__all__ = ["StructuredLegacyMigrationRunner", "StructuredMigrationManifest"]
