"""Temporary bridge to the pre-unification structured runtime registry."""

from ats.structured.runtime_registry import (
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
