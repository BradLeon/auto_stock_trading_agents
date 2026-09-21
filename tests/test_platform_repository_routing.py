"""Released consumer paths must not silently fall back to the legacy SQLite file."""

from __future__ import annotations


def test_platform_repository_uses_data_database_and_artifact_root(monkeypatch, tmp_path):
    from ats.data.runtime import (
        get_platform_structured_repository,
        platform_artifact_root,
        platform_data_db_path,
    )

    target = tmp_path / "data.sqlite"
    artifacts = tmp_path / "data-artifacts"
    monkeypatch.setenv("ATS_DATA_DB_PATH", str(target))
    monkeypatch.setenv("ATS_DATA_ARTIFACT_ROOT", str(artifacts))

    assert platform_data_db_path() == target
    assert platform_artifact_root() == artifacts
    repository = get_platform_structured_repository()
    try:
        assert repository.path == str(target)
        assert repository.artifacts.root == artifacts
    finally:
        repository.close()


def test_compatibility_repository_resolves_to_same_platform_store(monkeypatch, tmp_path):
    from ats.data.stores.structured.artifacts import default_artifact_root
    from ats.data.stores.structured.repository import default_db_path

    database = tmp_path / "one-data.sqlite"
    artifacts = tmp_path / "one-artifact-root"
    monkeypatch.setenv("ATS_DATA_DB_PATH", str(database))
    monkeypatch.setenv("ATS_DATA_ARTIFACT_ROOT", str(artifacts))
    monkeypatch.setenv("ATS_STRUCTURED_DB_PATH", str(tmp_path / "must-not-win.sqlite"))
    monkeypatch.setenv("ATS_STRUCTURED_ARTIFACT_ROOT", str(tmp_path / "must-not-win-artifacts"))

    assert default_db_path() == str(database)
    assert default_artifact_root() == artifacts
