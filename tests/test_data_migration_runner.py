"""Rehearsals for resumable, backed-up legacy data migration."""

from __future__ import annotations

import sqlite3

import pytest

from ats.data.migration import SQLiteMigrationRunner, load_migration_inventory


def _domain(name: str):
    return next(item for item in load_migration_inventory().domains if item.id == name)


def _document_source(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE source_documents (document_id TEXT PRIMARY KEY, content_hash TEXT, title TEXT);
        CREATE TABLE document_candidates (candidate_id TEXT PRIMARY KEY, document_id TEXT, status TEXT);
        CREATE TABLE document_versions (version_id TEXT PRIMARY KEY, document_id TEXT, content_hash TEXT);
        CREATE TABLE document_entities (document_id TEXT, entity TEXT, PRIMARY KEY(document_id, entity));
        CREATE TABLE document_source_aliases (alias_id TEXT PRIMARY KEY, document_id TEXT, source TEXT);
        CREATE TABLE document_chunks (chunk_id TEXT PRIMARY KEY, version_id TEXT, text TEXT);
        CREATE TABLE document_processing_runs (version_id TEXT, consumer TEXT, PRIMARY KEY(version_id, consumer));
        """
    )
    conn.execute("INSERT INTO source_documents VALUES ('doc-1','hash-1','MSFT release')")
    conn.execute("INSERT INTO document_candidates VALUES ('candidate-1','doc-1','accepted')")
    conn.execute("INSERT INTO document_versions VALUES ('version-1','doc-1','hash-1')")
    conn.execute("INSERT INTO document_entities VALUES ('doc-1','MSFT')")
    conn.execute("INSERT INTO document_source_aliases VALUES ('alias-1','doc-1','sec')")
    conn.execute("INSERT INTO document_chunks VALUES ('chunk-1','version-1','body')")
    conn.execute("INSERT INTO document_processing_runs VALUES ('version-1','chain')")
    conn.commit()
    conn.close()


def test_document_migration_is_backed_up_reconciled_and_idempotent(tmp_path):
    source = tmp_path / "legacy.sqlite"
    target = tmp_path / "data.sqlite"
    backup = tmp_path / "backups"
    _document_source(source)
    runner = SQLiteMigrationRunner(source, target)

    dry = runner.run(_domain("unstructured-documents"), dry_run=True)
    assert dry.reconciled is True
    assert dry.backup_path == ""
    assert not target.exists()

    first = runner.run(_domain("unstructured-documents"), backup_root=backup)
    assert first.reconciled is True
    assert first.backup_path
    assert all(item.copied == 1 for item in first.tables)

    second = runner.run(_domain("unstructured-documents"), backup_root=backup)
    assert second.reconciled is True
    assert all(item.copied == 0 for item in second.tables)

    target_conn = sqlite3.connect(target)
    assert target_conn.execute("SELECT count(*) FROM data_documents").fetchone()[0] == 1
    assert target_conn.execute("SELECT count(*) FROM data_migration_manifests").fetchone()[0] == 2
    target_conn.close()


def test_non_dry_migration_requires_a_verified_backup_root(tmp_path):
    source = tmp_path / "legacy.sqlite"
    _document_source(source)

    with pytest.raises(ValueError, match="backup_root"):
        SQLiteMigrationRunner(source, tmp_path / "data.sqlite").run(
            _domain("unstructured-documents"))


def test_cli_migration_defaults_to_dry_run_until_apply(tmp_path, capsys):
    source = tmp_path / "legacy.sqlite"
    _document_source(source)
    target = tmp_path / "data.sqlite"
    from ats.runtime.cli import main

    assert main([
        "data", "migrate", "--migration-domain", "unstructured-documents",
        "--source-db", str(source), "--target-db", str(target),
    ]) == 0
    assert '"dry_run": true' in capsys.readouterr().out
    assert not target.exists()
