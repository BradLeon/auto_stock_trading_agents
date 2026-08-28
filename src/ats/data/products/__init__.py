"""Consumer-facing data products.

The first migration step keeps the existing implementation behind this stable
namespace. Domain-specific products can be split into sibling modules as their
ownership moves out of legacy packages.
"""

from ats.data_platform.products import DataProducts, get_data_products

from .structured import StructuredDataProducts, get_structured_products
from .unstructured import UnstructuredDataProducts, get_unstructured_products
from .routing import UnstructuredReadRouter, get_unstructured_read_router
from .regional import RegionalPoint, RegionalProducts, RegionalSnapshot
from .earnings import confirm_reported
from .workflows import WorkflowDataBoundary, workflow_data_boundary


def get_platform_data_products() -> DataProducts:
    """Open products backed only by migrated persistent data repositories."""
    from ..runtime import get_platform_structured_repository
    from ..stores.unstructured import get_platform_unstructured_repository

    return DataProducts(
        structured_repository=get_platform_structured_repository(),
        unstructured_repository=get_platform_unstructured_repository())

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
    "WorkflowDataBoundary",
    "workflow_data_boundary",
]
