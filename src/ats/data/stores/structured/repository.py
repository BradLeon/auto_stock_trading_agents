"""Structured repository surface under the unified data namespace."""

from ats.structured.repository import (
    SQLiteStructuredRepository,
    StructuredRepository,
    default_db_path,
    get_repository,
    reset_repository_cache,
)

__all__ = [
    "SQLiteStructuredRepository",
    "StructuredRepository",
    "default_db_path",
    "get_repository",
    "reset_repository_cache",
]
