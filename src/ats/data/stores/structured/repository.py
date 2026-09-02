"""SQLite repository for governed structured research observations.

The repository owns additive structured tables only. Existing workflow and
measurement tables are read through compatibility methods and are never rewritten.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Protocol

from .artifacts import ArtifactStore, default_artifact_root
from ...catalog.structured import StructuredCatalog
from ...core.structured_models import (
    ArtifactDescriptor, EvidenceLink, MetricDefinition, ObservationInput,
    ObservationVintage, ProviderMapping, RawArtifact, SeriesIdentity,
    StructuredDataset, StructuredSource,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS structured_migrations (
    key TEXT PRIMARY KEY, applied_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS structured_sources (
    source_id TEXT PRIMARY KEY, provider TEXT NOT NULL, adapter TEXT NOT NULL,
    persistence TEXT NOT NULL, catalog_status TEXT NOT NULL, cadence TEXT NOT NULL,
    retention TEXT NOT NULL, upstream TEXT NOT NULL, datasets_json TEXT NOT NULL,
    constraints_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS structured_datasets (
    dataset_id TEXT PRIMARY KEY, catalog_status TEXT NOT NULL,
    expected_cadence TEXT NOT NULL, primary_sources_json TEXT NOT NULL,
    fallback_sources_json TEXT NOT NULL, core_metrics_json TEXT NOT NULL,
    quality_json TEXT NOT NULL, entities_json TEXT NOT NULL,
    acceptance_samples_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS structured_metrics (
    metric_id TEXT PRIMARY KEY, value_type TEXT NOT NULL, unit_family TEXT NOT NULL,
    cadence TEXT NOT NULL, period_basis_json TEXT NOT NULL, adjustment TEXT NOT NULL,
    derived INTEGER NOT NULL DEFAULT 0, description TEXT NOT NULL,
    definition_version TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS structured_provider_mappings (
    provider TEXT NOT NULL, provider_field TEXT NOT NULL, mapping_version TEXT NOT NULL,
    metric_id TEXT NOT NULL, dimensions_json TEXT NOT NULL,
    PRIMARY KEY (provider, provider_field, mapping_version)
);
CREATE INDEX IF NOT EXISTS idx_structured_mapping_metric
    ON structured_provider_mappings(metric_id, provider);
CREATE TABLE IF NOT EXISTS structured_pending_mappings (
    pending_id TEXT PRIMARY KEY, provider TEXT NOT NULL, dataset_id TEXT NOT NULL,
    provider_field TEXT NOT NULL, sample_payload TEXT NOT NULL, status TEXT NOT NULL,
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, seen_count INTEGER NOT NULL,
    UNIQUE(provider, dataset_id, provider_field)
);
CREATE TABLE IF NOT EXISTS structured_artifact_blobs (
    blob_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL UNIQUE,
    relative_path TEXT NOT NULL, bytes INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS structured_artifacts (
    artifact_id TEXT PRIMARY KEY, blob_id TEXT NOT NULL, source_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL, fetched_at TEXT NOT NULL, query_scope_json TEXT NOT NULL,
    query_hash TEXT NOT NULL, source_url TEXT NOT NULL, source_version TEXT NOT NULL,
    media_type TEXT NOT NULL, retention TEXT NOT NULL, storage_mode TEXT NOT NULL,
    pointer TEXT NOT NULL, metadata_json TEXT NOT NULL,
    UNIQUE(source_id, dataset_id, query_hash, blob_id)
);
CREATE INDEX IF NOT EXISTS idx_structured_artifact_source
    ON structured_artifacts(source_id, dataset_id, fetched_at);
CREATE TABLE IF NOT EXISTS structured_series (
    series_id TEXT PRIMARY KEY, identity_hash TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL, dataset_id TEXT NOT NULL, entity_id TEXT NOT NULL,
    metric_id TEXT NOT NULL, unit TEXT NOT NULL, currency TEXT NOT NULL,
    period_basis TEXT NOT NULL, adjustment TEXT NOT NULL,
    dimensions_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_structured_series_lookup
    ON structured_series(dataset_id, entity_id, metric_id, source_id);
CREATE TABLE IF NOT EXISTS structured_observations (
    observation_id TEXT PRIMARY KEY, series_id TEXT NOT NULL, period TEXT NOT NULL,
    period_start TEXT NOT NULL, period_end TEXT NOT NULL, event_time TEXT NOT NULL,
    value REAL NOT NULL, published_at TEXT NOT NULL, known_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL, artifact_id TEXT NOT NULL, content_hash TEXT NOT NULL,
    quality_status TEXT NOT NULL, quality_json TEXT NOT NULL, raw_payload TEXT NOT NULL,
    UNIQUE(series_id, period, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_structured_observation_lookup
    ON structured_observations(series_id, period, known_at);
CREATE TABLE IF NOT EXISTS structured_derivations (
    derivation_id TEXT NOT NULL, definition_version TEXT NOT NULL,
    operation TEXT NOT NULL, inputs_json TEXT NOT NULL, parameters_json TEXT NOT NULL,
    output_metric_id TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (derivation_id, definition_version)
);
CREATE TABLE IF NOT EXISTS structured_evidence_links (
    link_id TEXT PRIMARY KEY, observation_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
    document_id TEXT NOT NULL, version_id TEXT NOT NULL, char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL, extraction_method TEXT NOT NULL, source_tier TEXT NOT NULL,
    verification_status TEXT NOT NULL, reviewer TEXT NOT NULL, reviewed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_structured_evidence_target
    ON structured_evidence_links(candidate_id, observation_id, verification_status);
CREATE TABLE IF NOT EXISTS structured_entities (
    entity_id TEXT PRIMARY KEY, kind TEXT NOT NULL, canonical_name TEXT NOT NULL,
    aliases_json TEXT NOT NULL, securities_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS structured_events (
    event_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL, event_date TEXT NOT NULL, event_label TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(dataset_id, entity_id, event_type, event_date, event_label)
);
CREATE TABLE IF NOT EXISTS structured_evidence_candidates (
    candidate_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, source_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL, entity_id TEXT NOT NULL, metric_id TEXT NOT NULL,
    period TEXT NOT NULL, event_time TEXT NOT NULL, published_at TEXT NOT NULL,
    value REAL NOT NULL, unit TEXT NOT NULL, currency TEXT NOT NULL,
    document_id TEXT NOT NULL, version_id TEXT NOT NULL,
    char_start INTEGER NOT NULL, char_end INTEGER NOT NULL,
    extraction_method TEXT NOT NULL, source_tier TEXT NOT NULL,
    confidence REAL, status TEXT NOT NULL, reason_codes_json TEXT NOT NULL,
    observation_id TEXT NOT NULL, raw_payload TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_structured_evidence_candidate_status
    ON structured_evidence_candidates(dataset_id, entity_id, status, updated_at);
CREATE TABLE IF NOT EXISTS structured_evidence_reviews (
    review_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL,
    from_status TEXT NOT NULL, to_status TEXT NOT NULL,
    reviewer TEXT NOT NULL, note TEXT NOT NULL, reviewed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_structured_evidence_review_candidate
    ON structured_evidence_reviews(candidate_id, reviewed_at);
CREATE TABLE IF NOT EXISTS structured_snapshots (
    snapshot_id TEXT PRIMARY KEY, consumer TEXT NOT NULL, purpose TEXT NOT NULL,
    as_of TEXT NOT NULL, created_at TEXT NOT NULL, metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS structured_snapshot_items (
    snapshot_id TEXT NOT NULL, ordinal INTEGER NOT NULL, observation_id TEXT NOT NULL,
    selected_source TEXT NOT NULL, selection_reason TEXT NOT NULL,
    derivation_id TEXT NOT NULL, derivation_version TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, ordinal)
);
CREATE TABLE IF NOT EXISTS structured_legacy_audits (
    audit_id TEXT PRIMARY KEY, audited_at TEXT NOT NULL, series_count INTEGER NOT NULL,
    point_count INTEGER NOT NULL, missing_published_at INTEGER NOT NULL,
    note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS structured_ingestion_runs (
    run_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, dataset_id TEXT NOT NULL,
    query_scope_json TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
    status TEXT NOT NULL, discovered INTEGER NOT NULL DEFAULT 0,
    accepted INTEGER NOT NULL DEFAULT 0, quarantined INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0, reason_codes_json TEXT NOT NULL,
    note TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_structured_run_source
    ON structured_ingestion_runs(source_id, dataset_id, started_at);
CREATE TABLE IF NOT EXISTS structured_candidates (
    candidate_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL, entity_id TEXT NOT NULL, provider_field TEXT NOT NULL,
    metric_id TEXT NOT NULL, period TEXT NOT NULL, value_json TEXT NOT NULL,
    unit TEXT NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL, artifact_id TEXT NOT NULL,
    raw_payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_structured_candidate_status
    ON structured_candidates(dataset_id, status, created_at);
CREATE TABLE IF NOT EXISTS structured_conflicts (
    conflict_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, entity_id TEXT NOT NULL,
    metric_id TEXT NOT NULL, period TEXT NOT NULL, left_observation_id TEXT NOT NULL,
    right_source_id TEXT NOT NULL, right_value REAL NOT NULL,
    absolute_difference REAL NOT NULL, relative_difference REAL,
    status TEXT NOT NULL, detected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_structured_conflict_lookup
    ON structured_conflicts(dataset_id, entity_id, metric_id, period, status);
CREATE VIEW IF NOT EXISTS structured_observations_accepted AS
SELECT
    o.observation_id, o.series_id, s.dataset_id, s.entity_id, s.metric_id,
    o.period, o.period_start, o.period_end, o.event_time, o.value,
    s.unit, s.currency, s.period_basis, s.adjustment, s.dimensions_json,
    s.source_id, o.published_at, o.known_at, o.fetched_at, o.artifact_id,
    o.content_hash, o.quality_status, o.quality_json
FROM structured_observations o
JOIN structured_series s ON s.series_id=o.series_id
WHERE o.quality_status IN ('accepted','warning','conflict');
CREATE VIEW IF NOT EXISTS structured_observations_selected AS
WITH latest AS (
    SELECT a.*
    FROM structured_observations_accepted a
    WHERE NOT EXISTS (
        SELECT 1 FROM structured_observations_accepted newer
        WHERE newer.series_id=a.series_id AND newer.period=a.period
          AND (newer.known_at>a.known_at OR
               (newer.known_at=a.known_at AND newer.fetched_at>a.fetched_at))
    )
), ranked AS (
    SELECT latest.*,
        CASE
          WHEN instr(d.primary_sources_json, '"' || latest.source_id || '"')>0 THEN 0
          WHEN instr(d.fallback_sources_json, '"' || latest.source_id || '"')>0 THEN 1
          ELSE 2
        END AS source_tier,
        row_number() OVER (
          PARTITION BY latest.dataset_id,latest.entity_id,latest.metric_id,latest.period
          ORDER BY
            CASE
              WHEN instr(d.primary_sources_json, '"' || latest.source_id || '"')>0 THEN 0
              WHEN instr(d.fallback_sources_json, '"' || latest.source_id || '"')>0 THEN 1
              ELSE 2
            END,
            instr(d.primary_sources_json || d.fallback_sources_json, latest.source_id),
            latest.source_id
        ) AS source_rank
    FROM latest JOIN structured_datasets d ON d.dataset_id=latest.dataset_id
)
SELECT * FROM ranked WHERE source_rank=1;
"""


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stamp(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="microseconds")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


class StructuredRepository(Protocol):
    def register_source(self, source: StructuredSource) -> None: ...
    def register_dataset(self, dataset: StructuredDataset) -> None: ...
    def register_metric(self, metric: MetricDefinition) -> None: ...
    def save_observation(self, value: ObservationInput) -> ObservationVintage: ...
    def observations(self, **filters) -> list[dict]: ...


class SQLiteStructuredRepository:
    def __init__(self, path: str | Path, *, artifact_root: str | Path | None = None):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self._lock = RLock()
        self.artifacts = ArtifactStore(artifact_root or default_artifact_root())
        self._record_migration()

    def _record_migration(self) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO structured_migrations(key,applied_at,note) "
                "VALUES ('structured_foundation_v1',?,'additive governed structured tables')",
                (_stamp(),))

    def close(self) -> None:
        self.conn.close()

    def register_source(self, source: StructuredSource) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO structured_sources VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source_id) DO UPDATE SET provider=excluded.provider,"
                "adapter=excluded.adapter,persistence=excluded.persistence,"
                "catalog_status=excluded.catalog_status,cadence=excluded.cadence,"
                "retention=excluded.retention,upstream=excluded.upstream,"
                "datasets_json=excluded.datasets_json,constraints_json=excluded.constraints_json,"
                "updated_at=excluded.updated_at",
                (source.id, source.provider, source.adapter, source.persistence.value,
                 source.catalog_status.value, source.cadence, source.retention, source.upstream,
                 _json(source.datasets), _json(source.constraints), _stamp()))

    def register_dataset(self, dataset: StructuredDataset) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO structured_datasets VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(dataset_id) DO UPDATE SET catalog_status=excluded.catalog_status,"
                "expected_cadence=excluded.expected_cadence,"
                "primary_sources_json=excluded.primary_sources_json,"
                "fallback_sources_json=excluded.fallback_sources_json,"
                "core_metrics_json=excluded.core_metrics_json,quality_json=excluded.quality_json,"
                "entities_json=excluded.entities_json,"
                "acceptance_samples_json=excluded.acceptance_samples_json,"
                "updated_at=excluded.updated_at",
                (dataset.id, dataset.catalog_status.value, dataset.expected_cadence,
                 _json(dataset.primary_sources), _json(dataset.fallback_sources),
                 _json(dataset.core_metrics), _json(dataset.quality), _json(dataset.entities),
                 _json(dataset.acceptance_samples), _stamp()))

    def register_metric(self, metric: MetricDefinition) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO structured_metrics VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(metric_id) DO UPDATE SET value_type=excluded.value_type,"
                "unit_family=excluded.unit_family,cadence=excluded.cadence,"
                "period_basis_json=excluded.period_basis_json,adjustment=excluded.adjustment,"
                "derived=excluded.derived,description=excluded.description,"
                "definition_version=excluded.definition_version,updated_at=excluded.updated_at",
                (metric.id, metric.value_type, metric.unit_family, metric.cadence,
                 _json(metric.period_basis), metric.adjustment, int(metric.derived),
                 metric.description, metric.version, _stamp()))

    def register_mapping(self, mapping: ProviderMapping) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO structured_provider_mappings VALUES (?,?,?,?,?) "
                "ON CONFLICT(provider,provider_field,mapping_version) DO UPDATE SET "
                "metric_id=excluded.metric_id,dimensions_json=excluded.dimensions_json",
                (mapping.provider, mapping.provider_field, mapping.version,
                 mapping.metric_id, _json(mapping.dimensions)))

    def bootstrap_catalog(self, catalog: StructuredCatalog | None = None) -> None:
        catalog = catalog or StructuredCatalog.load()
        for entity_id, body in (catalog.raw.get("entities", {}) or {}).items():
            self.register_entity(
                entity_id=entity_id, kind=str((body or {}).get("kind", "economic_entity")),
                canonical_name=str((body or {}).get("canonical_name", entity_id)),
                aliases=list((body or {}).get("aliases", [])),
                securities=list((body or {}).get("securities", [])))
        for source in catalog.sources():
            self.register_source(source)
        for dataset in catalog.datasets():
            self.register_dataset(dataset)
        for metric in catalog.metrics():
            self.register_metric(metric)
        for provider, field, metric_id in catalog.provider_mappings():
            self.register_mapping(ProviderMapping(
                provider=provider, provider_field=field, metric_id=metric_id))

    def register_entity(self, *, entity_id: str, kind: str, canonical_name: str,
                        aliases: list[str] | None = None,
                        securities: list[dict] | None = None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO structured_entities VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(entity_id) DO UPDATE SET kind=excluded.kind,"
                "canonical_name=excluded.canonical_name,aliases_json=excluded.aliases_json,"
                "securities_json=excluded.securities_json,updated_at=excluded.updated_at",
                (entity_id.upper(), kind, canonical_name, _json(aliases or []),
                 _json(securities or []), _stamp()))

    def entities(self) -> list[dict]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM structured_entities ORDER BY entity_id").fetchall()]

    def resolve_entity(self, value: str) -> str | None:
        target = value.strip().casefold()
        for row in self.entities():
            aliases = json.loads(row["aliases_json"] or "[]")
            if target in {row["entity_id"].casefold(), row["canonical_name"].casefold(),
                          *(str(alias).casefold() for alias in aliases)}:
                return row["entity_id"]
        return None

    def resolve_metric(self, provider: str, provider_field: str,
                       *, version: str = "v1") -> str | None:
        row = self.conn.execute(
            "SELECT metric_id FROM structured_provider_mappings WHERE provider=? "
            "AND provider_field=? AND mapping_version=?",
            (provider, provider_field, version)).fetchone()
        return row["metric_id"] if row else None

    def record_pending_mapping(self, *, provider: str, dataset_id: str,
                               provider_field: str, sample: dict,
                               at: datetime | None = None) -> str:
        now = _stamp(at)
        pending_id = hashlib.sha1(
            f"{provider}|{dataset_id}|{provider_field}".encode()).hexdigest()[:24]
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO structured_pending_mappings VALUES (?,?,?,?,?,'pending',?,?,1) "
                "ON CONFLICT(provider,dataset_id,provider_field) DO UPDATE SET "
                "sample_payload=excluded.sample_payload,last_seen_at=excluded.last_seen_at,"
                "seen_count=structured_pending_mappings.seen_count+1",
                (pending_id, provider, dataset_id, provider_field, _json(sample), now, now))
        return pending_id

    def source(self, source_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM structured_sources WHERE source_id=?", (source_id,)).fetchone()
        return dict(row) if row else None

    def dataset(self, dataset_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM structured_datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
        return dict(row) if row else None

    def metric(self, metric_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM structured_metrics WHERE metric_id=?", (metric_id,)).fetchone()
        return dict(row) if row else None

    def sources(self) -> list[dict]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM structured_sources ORDER BY source_id").fetchall()]

    def datasets(self) -> list[dict]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM structured_datasets ORDER BY dataset_id").fetchall()]

    def metrics(self) -> list[dict]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM structured_metrics ORDER BY metric_id").fetchall()]

    def pending_mappings(self, *, status: str | None = None,
                         limit: int = 1000) -> list[dict]:
        sql = "SELECT * FROM structured_pending_mappings WHERE 1=1"
        args: list = []
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY last_seen_at DESC LIMIT ?"
        args.append(limit)
        return [dict(row) for row in self.conn.execute(sql, args).fetchall()]

    def conflicts(self, *, dataset_id: str | None = None,
                  status: str | None = "open", limit: int = 1000) -> list[dict]:
        sql = "SELECT * FROM structured_conflicts WHERE 1=1"
        args: list = []
        if dataset_id:
            sql += " AND dataset_id=?"
            args.append(dataset_id)
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY detected_at DESC LIMIT ?"
        args.append(limit)
        return [dict(row) for row in self.conn.execute(sql, args).fetchall()]

    def artifact_usage(self, *, source_id: str | None = None) -> dict:
        """Report logical references, physical blobs and content-hash deduplication."""
        where = "WHERE a.source_id=?" if source_id else ""
        args = [source_id] if source_id else []
        summary = self.conn.execute(
            f"""
            SELECT COUNT(*) AS artifacts,COUNT(DISTINCT a.blob_id) AS unique_blobs,
                   COALESCE(SUM(b.bytes),0) AS referenced_bytes
            FROM structured_artifacts a
            JOIN structured_artifact_blobs b ON b.blob_id=a.blob_id
            {where}
            """, args).fetchone()
        physical = self.conn.execute(
            f"""
            SELECT COALESCE(SUM(b.bytes),0) AS physical_bytes
            FROM structured_artifact_blobs b
            WHERE b.blob_id IN (
                SELECT DISTINCT a.blob_id FROM structured_artifacts a {where})
            """, args).fetchone()
        by_source_sql = """
            SELECT a.source_id,COUNT(*) AS artifacts,
                   COUNT(DISTINCT a.blob_id) AS unique_blobs,
                   COALESCE(SUM(b.bytes),0) AS referenced_bytes
            FROM structured_artifacts a
            JOIN structured_artifact_blobs b ON b.blob_id=a.blob_id
        """
        by_source_args: list = []
        if source_id:
            by_source_sql += " WHERE a.source_id=?"
            by_source_args.append(source_id)
        by_source_sql += " GROUP BY a.source_id ORDER BY a.source_id"
        rows = [dict(row) for row in self.conn.execute(
            by_source_sql, by_source_args).fetchall()]
        for row in rows:
            row["deduplicated_references"] = row["artifacts"] - row["unique_blobs"]
            row["deduplication_rate"] = (
                row["deduplicated_references"] / row["artifacts"]
                if row["artifacts"] else 0.0)
            source = self.source(row["source_id"])
            row["retention"] = source["retention"] if source else "unknown"
        artifacts = int(summary["artifacts"])
        unique_blobs = int(summary["unique_blobs"])
        return {
            "artifacts": artifacts,
            "unique_blobs": unique_blobs,
            "deduplicated_references": artifacts - unique_blobs,
            "deduplication_rate": ((artifacts - unique_blobs) / artifacts
                                   if artifacts else 0.0),
            "referenced_bytes": int(summary["referenced_bytes"]),
            "physical_bytes": int(physical["physical_bytes"]),
            "filesystem": self.artifacts.usage(),
            "by_source": rows,
        }

    def source_health(self) -> list[dict]:
        """Return one explicit health row for every registered source."""
        sql = """
        SELECT s.source_id,s.provider,s.persistence,s.catalog_status,s.cadence,
               r.status AS last_status,r.started_at AS last_started_at,
               r.completed_at AS last_completed_at,r.accepted,r.quarantined,r.unchanged,
               r.reason_codes_json
        FROM structured_sources s
        LEFT JOIN structured_ingestion_runs r ON r.run_id=(
            SELECT r2.run_id FROM structured_ingestion_runs r2
            WHERE r2.source_id=s.source_id ORDER BY r2.started_at DESC LIMIT 1)
        ORDER BY s.source_id
        """
        return [dict(row) for row in self.conn.execute(sql).fetchall()]

    def begin_ingestion(self, *, source_id: str, dataset_id: str,
                        query_scope: dict, at: datetime | None = None) -> str:
        started = _stamp(at)
        run_id = hashlib.sha1(
            f"{source_id}|{dataset_id}|{started}|{os.urandom(8).hex()}".encode()
        ).hexdigest()[:24]
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO structured_ingestion_runs VALUES "
                "(?,?,?,?,?,'','running',0,0,0,0,'{}','')",
                (run_id, source_id, dataset_id, _json(query_scope), started))
        return run_id

    def finish_ingestion(self, run_id: str, *, status: str, discovered: int = 0,
                         accepted: int = 0, quarantined: int = 0, unchanged: int = 0,
                         reason_codes: dict | list | None = None, note: str = "",
                         at: datetime | None = None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE structured_ingestion_runs SET completed_at=?,status=?,discovered=?,"
                "accepted=?,quarantined=?,unchanged=?,reason_codes_json=?,note=? "
                "WHERE run_id=?",
                (_stamp(at), status, discovered, accepted, quarantined, unchanged,
                 _json(reason_codes or {}), note, run_id))

    def ingestion_history(self, *, source_id: str | None = None,
                          dataset_id: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM structured_ingestion_runs WHERE 1=1"
        args: list = []
        if source_id:
            sql += " AND source_id=?"
            args.append(source_id)
        if dataset_id:
            sql += " AND dataset_id=?"
            args.append(dataset_id)
        sql += " ORDER BY started_at DESC LIMIT ?"
        args.append(limit)
        return [dict(row) for row in self.conn.execute(sql, args).fetchall()]

    def save_candidate(self, *, candidate_id: str, run_id: str, source_id: str,
                       dataset_id: str, entity_id: str, provider_field: str,
                       metric_id: str, period: str, value, unit: str, currency: str,
                       status: str, reason_codes: list[str], artifact_id: str,
                       raw: dict, at: datetime | None = None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO structured_candidates VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (candidate_id, run_id, source_id, dataset_id, entity_id, provider_field,
                 metric_id, period, _json(value), unit, currency, status,
                 _json(reason_codes), artifact_id, _json(raw), _stamp(at)))

    def candidates(self, *, status: str | None = None,
                   dataset_id: str | None = None, limit: int = 1000) -> list[dict]:
        sql = "SELECT * FROM structured_candidates WHERE 1=1"
        args: list = []
        if status:
            sql += " AND status=?"
            args.append(status)
        if dataset_id:
            sql += " AND dataset_id=?"
            args.append(dataset_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        return [dict(row) for row in self.conn.execute(sql, args).fetchall()]

    def ensure_event(self, *, dataset_id: str, entity_id: str, event_type: str,
                     event_date: str, event_label: str = "",
                     status: str = "active") -> str:
        normalized = (dataset_id, entity_id.upper(), event_type.strip().lower(),
                      event_date[:10], event_label.strip().casefold())
        event_id = hashlib.sha256("|".join(normalized).encode()).hexdigest()[:24]
        now = _stamp()
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO structured_events VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(dataset_id,entity_id,event_type,event_date,event_label) "
                "DO UPDATE SET status=CASE WHEN structured_events.status='active' "
                "THEN 'active' ELSE excluded.status END,updated_at=excluded.updated_at",
                (event_id, *normalized, status, now, now))
        return event_id

    def events(self, *, entity_id: str | None = None,
               event_type: str | None = None) -> list[dict]:
        sql = "SELECT * FROM structured_events WHERE 1=1"
        args: list = []
        if entity_id:
            sql += " AND entity_id=?"
            args.append(entity_id.upper())
        if event_type:
            sql += " AND event_type=?"
            args.append(event_type.lower())
        sql += " ORDER BY event_date,event_id"
        return [dict(row) for row in self.conn.execute(sql, args).fetchall()]

    def event(self, event_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM structured_events WHERE event_id=?", (event_id,)).fetchone()
        return dict(row) if row else None

    def save_evidence_candidate(self, payload: dict) -> str:
        candidate_id = payload["candidate_id"]
        now = _stamp(payload.get("at"))
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO structured_evidence_candidates VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(candidate_id) DO UPDATE SET "
                "reason_codes_json=excluded.reason_codes_json,raw_payload=excluded.raw_payload,"
                "updated_at=excluded.updated_at",
                (candidate_id, payload["event_id"], payload["source_id"],
                 payload["dataset_id"], payload["entity_id"], payload["metric_id"],
                 payload["period"], payload.get("event_time", ""),
                 payload.get("published_at", ""), float(payload["value"]),
                 payload["unit"], payload.get("currency", ""), payload["document_id"],
                 payload["version_id"], int(payload["char_start"]),
                 int(payload["char_end"]), payload["extraction_method"],
                 payload["source_tier"], payload.get("confidence"), payload["status"],
                 _json(payload.get("reason_codes", [])), payload.get("observation_id", ""),
                 _json(payload.get("raw", {})), now, now))
        return candidate_id

    def evidence_candidate(self, candidate_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM structured_evidence_candidates WHERE candidate_id=?",
            (candidate_id,)).fetchone()
        return dict(row) if row else None

    def evidence_candidates(self, *, status: str | None = None,
                            entity_id: str | None = None,
                            limit: int = 1000) -> list[dict]:
        sql = "SELECT * FROM structured_evidence_candidates WHERE 1=1"
        args: list = []
        if status:
            sql += " AND status=?"
            args.append(status)
        if entity_id:
            sql += " AND entity_id=?"
            args.append(entity_id.upper())
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(limit)
        return [dict(row) for row in self.conn.execute(sql, args).fetchall()]

    def save_evidence_link(self, link: EvidenceLink) -> str:
        body = link.model_dump(mode="json")
        link_id = hashlib.sha256(_json(body).encode()).hexdigest()[:24]
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO structured_evidence_links VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (link_id, link.observation_id, link.candidate_id, link.document_id,
                 link.version_id, link.char_start, link.char_end, link.extraction_method,
                 link.source_tier, link.verification_status.value, link.reviewer,
                 _stamp(link.reviewed_at) if link.reviewed_at else "", _stamp()))
        return link_id

    def evidence_links(self, *, candidate_id: str | None = None,
                       observation_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM structured_evidence_links WHERE 1=1"
        args: list = []
        if candidate_id:
            sql += " AND candidate_id=?"
            args.append(candidate_id)
        if observation_id:
            sql += " AND observation_id=?"
            args.append(observation_id)
        sql += " ORDER BY created_at,link_id"
        return [dict(row) for row in self.conn.execute(sql, args).fetchall()]

    def transition_evidence_candidate(self, *, candidate_id: str, to_status: str,
                                      reviewer: str, note: str,
                                      observation_id: str = "",
                                      at: datetime | None = None) -> dict:
        current = self.evidence_candidate(candidate_id)
        if current is None:
            raise KeyError(candidate_id)
        reviewed_at = _stamp(at)
        review_id = hashlib.sha256(
            f"{candidate_id}|{current['status']}|{to_status}|{reviewer}|{reviewed_at}".encode()
        ).hexdigest()[:24]
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE structured_evidence_candidates SET status=?,observation_id=?,"
                "updated_at=? WHERE candidate_id=?",
                (to_status, observation_id or current["observation_id"],
                 reviewed_at, candidate_id))
            self.conn.execute(
                "INSERT INTO structured_evidence_reviews VALUES (?,?,?,?,?,?,?)",
                (review_id, candidate_id, current["status"], to_status,
                 reviewer, note, reviewed_at))
            self.conn.execute(
                "UPDATE structured_evidence_links SET verification_status=?,"
                "reviewer=?,reviewed_at=? WHERE candidate_id=?",
                (to_status, reviewer, reviewed_at, candidate_id))
        return self.evidence_candidate(candidate_id) or {}

    def evidence_reviews(self, candidate_id: str) -> list[dict]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM structured_evidence_reviews WHERE candidate_id=? "
            "ORDER BY rowid", (candidate_id,)).fetchall()]

    def comparable_observations(self, *, dataset_id: str, entity_id: str,
                                metric_id: str, period: str) -> list[dict]:
        return self.observations(dataset_id=dataset_id, entity_id=entity_id,
                                 metric_id=metric_id, latest_only=True,
                                 accepted_only=True, limit=1000)

    def record_conflict(self, *, dataset_id: str, entity_id: str, metric_id: str,
                        period: str, left_observation_id: str, right_source_id: str,
                        right_value: float, absolute_difference: float,
                        relative_difference: float | None,
                        at: datetime | None = None) -> str:
        conflict_id = hashlib.sha1(
            f"{dataset_id}|{entity_id}|{metric_id}|{period}|{left_observation_id}|"
            f"{right_source_id}|{right_value}".encode()).hexdigest()[:24]
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO structured_conflicts VALUES "
                "(?,?,?,?,?,?,?,?,?,?,'open',?)",
                (conflict_id, dataset_id, entity_id, metric_id, period,
                 left_observation_id, right_source_id, right_value,
                 absolute_difference, relative_difference, _stamp(at)))
        return conflict_id

    def put_artifact(self, payload: bytes | str | dict | list | None,
                     descriptor: ArtifactDescriptor) -> RawArtifact:
        source = self.source(descriptor.source_id)
        if source and source["catalog_status"] == "runtime_excluded":
            raise ValueError(
                f"runtime/excluded source {descriptor.source_id} cannot persist artifacts")
        query = _json(descriptor.query_scope)
        query_hash = hashlib.sha256(query.encode()).hexdigest()
        if payload is None:
            pointer_payload = _json({
                "pointer": descriptor.pointer,
                "source_version": descriptor.source_version,
                "query_scope": descriptor.query_scope,
            })
            blob = self.artifacts.put(pointer_payload, suffix=".json")
        else:
            suffix = ".json" if descriptor.media_type.endswith("json") else ".bin"
            blob = self.artifacts.put(payload, suffix=suffix)
        artifact_id = hashlib.sha1(
            f"{descriptor.source_id}|{descriptor.dataset_id}|{query_hash}|{blob.blob_id}".encode()
        ).hexdigest()[:24]
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO structured_artifact_blobs VALUES (?,?,?,?,?)",
                (blob.blob_id, blob.content_hash, blob.relative_path, blob.bytes, _stamp()))
            self.conn.execute(
                "INSERT OR IGNORE INTO structured_artifacts VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (artifact_id, blob.blob_id, descriptor.source_id, descriptor.dataset_id,
                 _stamp(descriptor.fetched_at), query, query_hash, descriptor.source_url,
                 descriptor.source_version, descriptor.media_type, descriptor.retention,
                 descriptor.storage_mode, descriptor.pointer, _json(descriptor.metadata)))
        return RawArtifact(id=artifact_id, blob_id=blob.blob_id,
                           content_hash=blob.content_hash, relative_path=blob.relative_path,
                           bytes=blob.bytes, descriptor=descriptor)

    @staticmethod
    def _series_payload(series: SeriesIdentity) -> dict:
        return series.model_dump(mode="json")

    def ensure_series(self, series: SeriesIdentity) -> str:
        source = self.source(series.source_id)
        if source and source["catalog_status"] == "runtime_excluded":
            raise ValueError(
                f"runtime/excluded source {series.source_id} cannot persist series")
        payload = self._series_payload(series)
        identity_hash = hashlib.sha256(_json(payload).encode()).hexdigest()
        series_id = identity_hash[:24]
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO structured_series VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (series_id, identity_hash, series.source_id, series.dataset_id,
                 series.entity_id, series.metric_id, series.unit, series.currency,
                 series.period_basis, series.adjustment, _json(series.dimensions), _stamp()))
        return series_id

    def save_observation(self, value: ObservationInput) -> ObservationVintage:
        series_id = self.ensure_series(value.series)
        normalized = {
            "period": value.period,
            "period_start": value.period_start,
            "period_end": value.period_end,
            "event_time": _stamp(value.event_time) if value.event_time else "",
            "value": value.value,
            "published_at": _stamp(value.published_at) if value.published_at else "",
            "quality_status": value.quality_status.value,
            "quality": value.quality,
            "raw": value.raw,
        }
        content_hash = hashlib.sha256(_json(normalized).encode()).hexdigest()
        observation_id = hashlib.sha1(
            f"{series_id}|{value.period}|{content_hash}".encode()).hexdigest()[:24]
        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO structured_observations VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (observation_id, series_id, value.period, value.period_start, value.period_end,
                 normalized["event_time"], value.value, normalized["published_at"],
                 _stamp(value.known_at), _stamp(value.fetched_at), value.artifact_id,
                 content_hash, value.quality_status.value, _json(value.quality),
                 _json(value.raw),))
        return ObservationVintage(id=observation_id, series_id=series_id,
                                  content_hash=content_hash, created=cur.rowcount > 0,
                                  observation=value)

    def observations(self, *, dataset_id: str | None = None,
                     metric_id: str | None = None, entity_id: str | None = None,
                     source_id: str | None = None, since: str | None = None,
                     as_of: datetime | None = None, latest_only: bool = True,
                     accepted_only: bool = True, limit: int = 5000) -> list[dict]:
        sql = ("SELECT o.*,s.source_id,s.dataset_id,s.entity_id,s.metric_id,s.unit,"
               "s.currency,s.period_basis,s.adjustment,s.dimensions_json "
               "FROM structured_observations o JOIN structured_series s "
               "ON s.series_id=o.series_id WHERE 1=1")
        args: list = []
        for column, value in (("s.dataset_id", dataset_id), ("s.metric_id", metric_id),
                              ("s.entity_id", entity_id.upper() if entity_id else None),
                              ("s.source_id", source_id)):
            if value:
                sql += f" AND {column}=?"
                args.append(value)
        if since:
            sql += " AND o.period>=?"
            args.append(since)
        if accepted_only:
            sql += " AND o.quality_status IN ('accepted','warning','conflict')"
            sql += (" AND (s.source_id!='accepted_document_evidence' OR EXISTS ("
                    "SELECT 1 FROM structured_evidence_links el "
                    "WHERE el.observation_id=o.observation_id "
                    "AND el.verification_status='accepted'))")
        cutoff = _stamp(as_of) if as_of else ""
        if cutoff:
            sql += " AND o.known_at<=? AND (o.published_at='' OR o.published_at<=?)"
            args.extend([cutoff, cutoff])
        if latest_only:
            sql += (" AND NOT EXISTS (SELECT 1 FROM structured_observations newer "
                    "WHERE newer.series_id=o.series_id AND newer.period=o.period "
                    "AND (newer.known_at>o.known_at OR "
                    "(newer.known_at=o.known_at AND newer.fetched_at>o.fetched_at))")
            if cutoff:
                sql += " AND newer.known_at<=? AND (newer.published_at='' OR newer.published_at<=?)"
                args.extend([cutoff, cutoff])
            sql += ")"
        sql += " ORDER BY o.period,o.known_at,o.fetched_at LIMIT ?"
        args.append(limit)
        return [dict(row) for row in self.conn.execute(sql, args).fetchall()]

    def observation(self, observation_id: str) -> dict | None:
        rows = self.conn.execute(
            "SELECT o.*,s.source_id,s.dataset_id,s.entity_id,s.metric_id,s.unit,"
            "s.currency,s.period_basis,s.adjustment,s.dimensions_json "
            "FROM structured_observations o JOIN structured_series s "
            "ON s.series_id=o.series_id WHERE o.observation_id=?",
            (observation_id,)).fetchone()
        return dict(rows) if rows else None

    def lineage(self, observation_id: str) -> dict | None:
        observation = self.observation(observation_id)
        if observation is None:
            return None
        artifact = self.conn.execute(
            "SELECT a.*,b.content_hash AS artifact_content_hash,b.relative_path,b.bytes "
            "FROM structured_artifacts a JOIN structured_artifact_blobs b "
            "ON b.blob_id=a.blob_id WHERE a.artifact_id=?",
            (observation["artifact_id"],)).fetchone()
        evidence = self.conn.execute(
            "SELECT * FROM structured_evidence_links WHERE observation_id=? "
            "ORDER BY created_at", (observation_id,)).fetchall()
        metric = self.metric(observation["metric_id"])
        source = self.source(observation["source_id"])
        return {
            "observation": observation,
            "metric": metric,
            "source": source,
            "artifact": dict(artifact) if artifact else None,
            "evidence": [dict(row) for row in evidence],
        }

    def register_derivation(self, definition) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO structured_derivations VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(derivation_id,definition_version) DO UPDATE SET "
                "operation=excluded.operation,inputs_json=excluded.inputs_json,"
                "parameters_json=excluded.parameters_json,"
                "output_metric_id=excluded.output_metric_id,updated_at=excluded.updated_at",
                (definition.id, definition.version, definition.operation,
                 _json(definition.inputs), _json(definition.parameters),
                 definition.output_metric_id, _stamp()))

    def derivation(self, derivation_id: str, version: str | None = None) -> dict | None:
        if version:
            row = self.conn.execute(
                "SELECT * FROM structured_derivations WHERE derivation_id=? "
                "AND definition_version=?", (derivation_id, version)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM structured_derivations WHERE derivation_id=? "
                "ORDER BY definition_version DESC LIMIT 1", (derivation_id,)).fetchone()
        return dict(row) if row else None

    def derivations(self) -> list[dict]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM structured_derivations "
            "ORDER BY derivation_id,definition_version").fetchall()]

    def create_snapshot(self, *, consumer: str, purpose: str, as_of: datetime,
                        items: list, metadata: dict | None = None) -> dict:
        """Persist immutable references to accepted observations, never runtime inputs."""
        persistent_items = []
        seen: set[tuple] = set()
        for item in items:
            if isinstance(item, dict):
                payload = item
            else:
                payload = item.model_dump(mode="json")
            if payload.get("input_mode", "persistent") == "runtime":
                continue
            observation_id = payload.get("observation_id", "")
            if not observation_id or self.observation(observation_id) is None:
                continue
            normalized = {
                "observation_id": observation_id,
                "selected_source": payload.get("selected_source") or
                                   payload.get("source_id", ""),
                "selection_reason": payload.get("selection_reason", "explicit_input"),
                "derivation_id": payload.get("derivation_id", ""),
                "derivation_version": payload.get("derivation_version", ""),
            }
            key = tuple(normalized.values())
            if key not in seen:
                seen.add(key)
                persistent_items.append(normalized)
        persistent_items.sort(key=lambda row: (
            row["observation_id"], row["derivation_id"], row["derivation_version"]))
        body = {
            "consumer": consumer, "purpose": purpose, "as_of": _stamp(as_of),
            "items": persistent_items, "metadata": metadata or {},
        }
        snapshot_id = hashlib.sha256(_json(body).encode()).hexdigest()[:24]
        created_at = _stamp()
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO structured_snapshots VALUES (?,?,?,?,?,?)",
                (snapshot_id, consumer, purpose, _stamp(as_of), created_at,
                 _json(metadata or {})))
            for ordinal, item in enumerate(persistent_items):
                self.conn.execute(
                    "INSERT OR IGNORE INTO structured_snapshot_items VALUES (?,?,?,?,?,?,?)",
                    (snapshot_id, ordinal, item["observation_id"],
                     item["selected_source"], item["selection_reason"],
                     item["derivation_id"], item["derivation_version"]))
        return self.snapshot(snapshot_id) or {}

    def snapshot(self, snapshot_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM structured_snapshots WHERE snapshot_id=?",
            (snapshot_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        result["items"] = [dict(item) for item in self.conn.execute(
            "SELECT * FROM structured_snapshot_items WHERE snapshot_id=? "
            "ORDER BY ordinal", (snapshot_id,)).fetchall()]
        return result

    def replay_snapshot(self, snapshot_id: str) -> dict | None:
        manifest = self.snapshot(snapshot_id)
        if manifest is None:
            return None
        rows = []
        derivations: dict[tuple[str, str], dict] = {}
        for item in manifest["items"]:
            observation = self.observation(item["observation_id"])
            if observation is not None:
                observation.update({
                    "selected_source": item["selected_source"],
                    "selection_reason": item["selection_reason"],
                    "derivation_id": item["derivation_id"],
                    "derivation_version": item["derivation_version"],
                })
                rows.append(observation)
            if item["derivation_id"]:
                key = (item["derivation_id"], item["derivation_version"])
                definition = self.derivation(*key)
                if definition:
                    derivations[key] = definition
        return {**manifest, "rows": rows, "derivations": list(derivations.values())}

    def open_read_only(self) -> sqlite3.Connection:
        if self.path == ":memory:":
            raise RuntimeError("read-only SQL requires a file-backed structured database")
        conn = sqlite3.connect(f"file:{Path(self.path).resolve()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    def legacy_measurements(self, *, source_id: str | None = None,
                            entity: str | None = None, as_of: datetime | None = None,
                            latest_only: bool = True, limit: int = 5000) -> list[dict]:
        if not (_table_exists(self.conn, "measurement_series") and
                _table_exists(self.conn, "measurement_points")):
            return []
        sql = ("SELECT p.*,s.source_id,s.series,s.label,s.entity,s.cadence "
               "FROM measurement_points p JOIN measurement_series s "
               "ON s.series_id=p.series_id WHERE 1=1")
        args: list = []
        if source_id:
            sql += " AND s.source_id=?"
            args.append(source_id)
        if entity:
            sql += " AND s.entity=?"
            args.append(entity.upper())
        cutoff = _stamp(as_of) if as_of else ""
        if cutoff:
            sql += " AND p.fetched_at<=? AND (p.published_at='' OR p.published_at<=?)"
            args.extend([cutoff, cutoff])
        if latest_only:
            sql += (" AND NOT EXISTS (SELECT 1 FROM measurement_points newer "
                    "WHERE newer.series_id=p.series_id AND newer.period=p.period "
                    "AND newer.fetched_at>p.fetched_at")
            if cutoff:
                sql += " AND newer.fetched_at<=?"
                args.append(cutoff)
            sql += ")"
        sql += " ORDER BY p.period,p.fetched_at LIMIT ?"
        args.append(limit)
        return [dict(row) for row in self.conn.execute(sql, args).fetchall()]

    def audit_legacy_measurements(self, *, at: datetime | None = None) -> dict:
        if not (_table_exists(self.conn, "measurement_series") and
                _table_exists(self.conn, "measurement_points")):
            result = {"series_count": 0, "point_count": 0, "missing_published_at": 0}
        else:
            result = {
                "series_count": self.conn.execute(
                    "SELECT count(*) FROM measurement_series").fetchone()[0],
                "point_count": self.conn.execute(
                    "SELECT count(*) FROM measurement_points").fetchone()[0],
                "missing_published_at": self.conn.execute(
                    "SELECT count(*) FROM measurement_points "
                    "WHERE published_at IS NULL OR published_at='' ").fetchone()[0],
            }
        audited_at = _stamp(at)
        audit_id = hashlib.sha1(
            f"{audited_at}|{_json(result)}".encode()).hexdigest()[:24]
        note = ("read-only audit; missing publication timestamps remain unknown and "
                "legacy values were not rewritten")
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO structured_legacy_audits VALUES (?,?,?,?,?,?)",
                (audit_id, audited_at, result["series_count"], result["point_count"],
                 result["missing_published_at"], note))
        return {"audit_id": audit_id, **result, "note": note}


def default_db_path() -> str:
    from ....config import REPO_ROOT

    return os.environ.get(
        "ATS_STRUCTURED_DB_PATH",
        os.environ.get("ATS_DB_PATH", str(REPO_ROOT / "var" / "ats.sqlite")),
    )


@lru_cache(maxsize=None)
def _repository(path: str, artifact_root: str) -> SQLiteStructuredRepository:
    return SQLiteStructuredRepository(path, artifact_root=artifact_root)


def get_repository() -> SQLiteStructuredRepository:
    return _repository(default_db_path(), str(default_artifact_root()))


def reset_repository_cache() -> None:
    _repository.cache_clear()
