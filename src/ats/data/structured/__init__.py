"""Compatibility entrypoint for the governed structured data platform.

The implementation remains in :mod:`ats.structured` until the structured
pipeline/store migration phase. This module intentionally only re-exports the
legacy public surface so there is one implementation during the transition.
"""

from ats.structured import *  # noqa: F401,F403
from ats.structured import __all__ as _legacy_all
from ats.data.catalog import StructuredCatalog
from ats.data.adapters.structured.registry import (
    RuntimeSourceSpec,
    build_ingestion,
    ingest_source,
    register_runtime,
    validate_source_registration,
)
from ats.data.stores.structured.repository import (
    SQLiteStructuredRepository,
    StructuredRepository,
    default_db_path,
    get_repository,
    reset_repository_cache,
)

__all__ = list(dict.fromkeys([
    *_legacy_all,
    "StructuredCatalog",
    "RuntimeSourceSpec",
    "build_ingestion",
    "ingest_source",
    "register_runtime",
    "validate_source_registration",
    "SQLiteStructuredRepository",
    "StructuredRepository",
    "default_db_path",
    "get_repository",
    "reset_repository_cache",
]))
