"""Rehearsals for migration of legacy numeric measurements into structured storage."""

from __future__ import annotations

import sqlite3

import pytest

from ats.data.migration import StructuredLegacyMigrationRunner, load_migration_inventory


def _domain():
    return next(item for item in load_migration_inventory().domains
                if item.id == "structured-legacy-measurements")


def _legacy_measurements(path, *, mapped: bool = True):
    series = "regional.tw_ic_exports.value" if mapped else "unknown.metric"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE data_sources (
            source_id TEXT PRIMARY KEY, kind TEXT, label TEXT, adapter TEXT,
            cadence TEXT, entity TEXT, updated_at TEXT
        );
        CREATE TABLE ingestion_runs (
            run_id TEXT PRIMARY KEY, source_id TEXT, kind TEXT, started_at TEXT,
            completed_at TEXT, status TEXT, discovered INTEGER, accepted INTEGER,
            quarantined INTEGER, reason_codes TEXT, snapshot_updated_at TEXT,
            snapshot_lag_hours REAL, note TEXT
        );
        CREATE TABLE measurement_series (
            series_id TEXT PRIMARY KEY, source_id TEXT, series TEXT, label TEXT,
            entity TEXT, unit TEXT, cadence TEXT, updated_at TEXT
        );
        CREATE TABLE measurement_points (
            point_id TEXT PRIMARY KEY, series_id TEXT, period TEXT, value REAL,
            unit TEXT, published_at TEXT, fetched_at TEXT, content_hash TEXT,
            raw_payload TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO data_sources VALUES (?,?,?,?,?,?,?)",
        ("tw_mof_exports", "economic", "Taiwan exports", "tw_mof", "monthly",
         "TW_IC_EXPORT", "2026-08-01T00:00:00+00:00"))
    conn.execute(
        "INSERT INTO ingestion_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("run-1", "tw_mof_exports", "structured", "2026-08-01T00:00:00+00:00",
         "2026-08-01T00:01:00+00:00", "succeeded", 1, 1, 0, "{}", "", 0.0, ""))
    conn.execute(
        "INSERT INTO measurement_series VALUES (?,?,?,?,?,?,?,?)",
        ("tw:exports", "tw_mof_exports", series, "Taiwan IC exports", "TW_IC_EXPORT",
         "USD M", "monthly", "2026-08-01T00:00:00+00:00"))
    conn.execute(
        "INSERT INTO measurement_points VALUES (?,?,?,?,?,?,?,?,?)",
        ("point-1", "tw:exports", "2026-06", 100.0, "USD M",
         "2026-07-20T00:00:00+00:00", "2026-08-01T00:00:00+00:00", "legacy-hash",
         '{"period":"2026-06","value":100.0}'))
    conn.commit()
    conn.close()


def test_structured_migration_is_backed_up_reconciled_and_idempotent(tmp_path):
    source, target, backup = tmp_path / "legacy.sqlite", tmp_path / "data.sqlite", tmp_path / "backups"
    _legacy_measurements(source)
    runner = StructuredLegacyMigrationRunner(source, target, artifact_root=tmp_path / "artifacts")

    dry = runner.run(_domain(), dry_run=True)
    assert dry.reconciled is True
    assert dry.source_count == 3  # source registration + point vintage + ingestion record
    assert not target.exists()

    first = runner.run(_domain(), backup_root=backup)
    second = runner.run(_domain(), backup_root=backup)
    assert first.reconciled is True and second.reconciled is True
    assert first.backup_path and first.source_sha256 == first.backup_sha256

    conn = sqlite3.connect(target)
    assert conn.execute("SELECT count(*) FROM structured_observations").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM structured_artifacts").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM structured_ingestion_runs").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM data_structured_legacy_sources").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM data_structured_legacy_manifests").fetchone()[0] == 2
    conn.close()


def test_structured_dry_run_blocks_unmapped_metric_without_writing(tmp_path):
    source, target = tmp_path / "legacy.sqlite", tmp_path / "data.sqlite"
    _legacy_measurements(source, mapped=False)

    result = StructuredLegacyMigrationRunner(
        source, target, artifact_root=tmp_path / "artifacts").run(_domain(), dry_run=True)

    assert result.reconciled is False
    assert result.status == "incomplete"
    assert result.skipped_count == 1
    assert result.rows[0]["reason"] == "metric_or_dataset_unmapped"
    assert not target.exists()


def test_structured_migration_requires_backup_and_cli_defaults_to_dry_run(tmp_path, capsys):
    source, target = tmp_path / "legacy.sqlite", tmp_path / "data.sqlite"
    _legacy_measurements(source)
    runner = StructuredLegacyMigrationRunner(source, target, artifact_root=tmp_path / "artifacts")
    with pytest.raises(ValueError, match="backup_root"):
        runner.run(_domain())

    from ats.runtime.cli import main

    assert main([
        "data", "migrate", "--migration-domain", "structured-legacy-measurements",
        "--source-db", str(source), "--target-db", str(target),
    ]) == 0
    assert '"dry_run"' not in capsys.readouterr().out  # structured manifests have no dry-run field
    assert not target.exists()
