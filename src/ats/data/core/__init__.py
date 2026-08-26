"""Shared, provider-neutral contracts for the ATS data layer.

The contracts in this package deliberately contain metadata that is common to
structured observations and unstructured document assets.  Domain-specific
payloads remain in their respective structured/unstructured packages.
"""

from .models import (
    EntityKind,
    EntityRef,
    IngestionRun,
    IngestionRunStatus,
    LineageRef,
    QualityLevel,
    QualitySnapshot,
    SourceKind,
    SourceRef,
    SourceStatus,
)

__all__ = [
    "EntityKind",
    "EntityRef",
    "IngestionRun",
    "IngestionRunStatus",
    "LineageRef",
    "QualityLevel",
    "QualitySnapshot",
    "SourceKind",
    "SourceRef",
    "SourceStatus",
]
