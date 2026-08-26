"""Controlled structured adapter registry exposed under the data namespace."""

from ats.data.compat.structured_registry import (
    RuntimeSourceSpec,
    build_ingestion,
    ingest_source,
    register_runtime,
    validate_source_registration,
)

__all__ = [
    "RuntimeSourceSpec",
    "build_ingestion",
    "ingest_source",
    "register_runtime",
    "validate_source_registration",
]
