"""Consumer-facing data products.

The first migration step keeps the existing implementation behind this stable
namespace. Domain-specific products can be split into sibling modules as their
ownership moves out of legacy packages.
"""

from .base import DataProducts, get_data_products

from .structured import StructuredDataProducts, get_structured_products
from .unstructured import UnstructuredDataProducts, get_unstructured_products
from .routing import UnstructuredReadRouter, get_unstructured_read_router
from .regional import RegionalPoint, RegionalProducts, RegionalSnapshot
from .earnings import confirm_reported
from .earnings_insight import (
    EarningsInsightAnalysisPacket,
    EarningsInsightDiagnostic,
    EarningsInsightEvidence,
    EarningsInsightLineage,
    EarningsInsightObservation,
    EarningsInsightNarrativeEvidence,
    EarningsInsightPartitionStatus,
    EarningsInsightReport,
    EarningsInsightSnapshot,
    EarningsInsightStatus,
    load_analysis_packet,
    to_earnings_backdrop,
)
from .workflows import WorkflowDataBoundary, workflow_data_boundary
from .calendar import ScheduleCalendarProduct, schedule_calendar_snapshot


def get_platform_data_products(*, readonly: bool = False) -> DataProducts:
    """Open products backed only by migrated persistent data repositories."""
    from ..runtime import get_platform_structured_repository
    from ..stores.unstructured import get_platform_unstructured_repository

    if readonly:
        from ..runtime.repository import platform_artifact_root, platform_data_db_path
        from ..stores.structured.repository import SQLiteStructuredRepository
        structured = SQLiteStructuredRepository(platform_data_db_path(),
            artifact_root=platform_artifact_root(), readonly=True)
    else:
        structured = get_platform_structured_repository()
    try:
        unstructured = get_platform_unstructured_repository()
    except Exception:
        structured.close()
        raise
    return DataProducts(structured_repository=structured, unstructured_repository=unstructured)

__all__ = [
    "DataProducts",
    "StructuredDataProducts",
    "UnstructuredDataProducts",
    "UnstructuredReadRouter",
    "get_data_products",
    "get_platform_data_products",
    "get_structured_products",
    "get_unstructured_products",
    "get_unstructured_read_router",
    "RegionalPoint",
    "RegionalProducts",
    "RegionalSnapshot",
    "confirm_reported",
    "EarningsInsightAnalysisPacket",
    "EarningsInsightDiagnostic",
    "EarningsInsightEvidence",
    "EarningsInsightLineage",
    "EarningsInsightObservation",
    "EarningsInsightNarrativeEvidence",
    "EarningsInsightPartitionStatus",
    "EarningsInsightReport",
    "EarningsInsightSnapshot",
    "EarningsInsightStatus",
    "load_analysis_packet",
    "to_earnings_backdrop",
    "WorkflowDataBoundary",
    "workflow_data_boundary",
    "ScheduleCalendarProduct",
    "schedule_calendar_snapshot",
]
