"""Consumer-facing data products.

The first migration step keeps the existing implementation behind this stable
namespace. Domain-specific products can be split into sibling modules as their
ownership moves out of legacy packages.
"""

from ats.data_platform.products import DataProducts, get_data_products

from .structured import StructuredDataProducts, get_structured_products
from .unstructured import UnstructuredDataProducts, get_unstructured_products

__all__ = [
    "DataProducts",
    "StructuredDataProducts",
    "UnstructuredDataProducts",
    "get_data_products",
    "get_structured_products",
    "get_unstructured_products",
]
