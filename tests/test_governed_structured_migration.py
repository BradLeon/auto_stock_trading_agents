"""End-to-end checks for copying existing governed structured repository state."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from ats.data.migration import GovernedStructuredMigrationRunner, load_migration_inventory
from ats.data.structured import ArtifactDescriptor, ObservationInput, SeriesIdentity, SQLiteStructuredRepository


def _domain():
    return next(item for item in load_migration_inventory().domains
                if item.id == "structured-governed-history")


def _source(path, artifact_root):
    repo = SQLiteStructuredRepository(path, artifact_root=artifact_root)
    repo.bootstrap_catalog()
    artifact = repo.put_artifact({"entity": "NVDA", "value": 2.0}, ArtifactDescriptor(
        source_id="yfinance_consensus", dataset_id="market_consensus",
        fetched_at="2026-08-01T00:00:00+00:00", query_scope={"entity": "NVDA"},
        source_version="fixture", media_type="application/json"))
    repo.save_observation(ObservationInput(
        series=SeriesIdentity(source_id="yfinance_consensus", dataset_id="market_consensus",
                              entity_id="NVDA", metric_id="consensus.eps.mean",
                              unit="USD/share", currency="USD", period_basis="target_quarter"),
        period="FY2026Q4", value=2.0, known_at="2026-08-01T00:00:00+00:00",
        fetched_at="2026-08-01T00:00:00+00:00", artifact_id=artifact.id,
        raw={"legacy": False}))
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    run_id = repo.begin_ingestion(source_id="yfinance_consensus", dataset_id="market_consensus",
                                 query_scope={"entity": "NVDA"}, at=started)
    repo.finish_ingestion(run_id, status="succeeded", discovered=1, accepted=1,
                         at=started.replace(minute=1))
    repo.close()


def test_governed_structured_history_is_reconciled_with_artifacts_and_idempotent(tmp_path):
    source, target = tmp_path / "legacy.sqlite", tmp_path / "data.sqlite"
    source_artifacts, target_artifacts = tmp_path / "legacy-artifacts", tmp_path / "data-artifacts"
    _source(source, source_artifacts)
    runner = GovernedStructuredMigrationRunner(
        source, target, source_artifact_root=source_artifacts,
        target_artifact_root=target_artifacts)

    dry = runner.run(_domain(), dry_run=True)
    assert dry.reconciled is True
    assert not target.exists()

    first = runner.run(_domain(), backup_root=tmp_path / "backups")
    second = runner.run(_domain(), backup_root=tmp_path / "backups")
    assert first.reconciled is True and second.reconciled is True
    assert next(row for row in second.tables if row.source_table == "structured_observations").copied == 0
    assert next(row for row in first.tables if row.source_table == "structured_artifact_files").copied == 1

    conn = sqlite3.connect(target)
    assert conn.execute("SELECT count(*) FROM structured_observations").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM structured_ingestion_runs").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM data_migration_manifests").fetchone()[0] == 2
    conn.close()
    assert sorted(path.relative_to(target_artifacts) for path in target_artifacts.rglob("*.json")) == \
        sorted(path.relative_to(source_artifacts) for path in source_artifacts.rglob("*.json"))


def test_governed_structured_history_dry_run_rejects_missing_artifact(tmp_path):
    source, target = tmp_path / "legacy.sqlite", tmp_path / "data.sqlite"
    source_artifacts = tmp_path / "legacy-artifacts"
    _source(source, source_artifacts)
    for path in source_artifacts.rglob("*.json"):
        path.unlink()

    import pytest

    with pytest.raises(RuntimeError, match="artifact missing"):
        GovernedStructuredMigrationRunner(
            source, target, source_artifact_root=source_artifacts,
            target_artifact_root=tmp_path / "data-artifacts").run(_domain(), dry_run=True)
