"""Repository selection for a released data-platform consumer.

The retired compatibility package used ``ATS_DB_PATH``.  New consumers use
``ATS_DATA_DB_PATH`` through ``ats.data``.
consumer explicitly released to platform mode uses the independently migrated
data database instead.
"""

from __future__ import annotations

import os
from pathlib import Path


def platform_data_db_path() -> Path:
    from ...config import REPO_ROOT

    return Path(os.environ.get("ATS_DATA_DB_PATH", REPO_ROOT / "var" / "data.sqlite"))


def platform_artifact_root() -> Path:
    from ...config import REPO_ROOT

    return Path(os.environ.get("ATS_DATA_ARTIFACT_ROOT", REPO_ROOT / "var" / "data_artifacts"))


def get_platform_structured_repository():
    """Open the released data repository; callers own and close the handle."""
    from ..stores.structured.repository import SQLiteStructuredRepository

    repository = SQLiteStructuredRepository(
        platform_data_db_path(), artifact_root=platform_artifact_root())
    repository.bootstrap_catalog()
    return repository


__all__ = [
    "get_platform_structured_repository",
    "platform_artifact_root",
    "platform_data_db_path",
]
