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
