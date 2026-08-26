"""Unstructured repository boundary over the existing SQLite implementation.

The wrapper is intentionally thin during the migration: it gives document and
evidence consumers a data-layer-owned dependency while preserving the current
SQLite schema and transaction behavior.
"""

from __future__ import annotations

from typing import Any


class UnstructuredRepository:
    def __init__(self, backend=None):
        if backend is None:
            from ats.memory import get_store

            backend = get_store()
        self.backend = backend

    def __getattr__(self, name: str) -> Any:
        return getattr(self.backend, name)


def get_unstructured_repository() -> UnstructuredRepository:
    """Return a fresh facade around the current context-memory store."""

    return UnstructuredRepository()


__all__ = ["UnstructuredRepository", "get_unstructured_repository"]
