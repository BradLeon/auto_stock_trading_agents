"""Structured data products under the unified data namespace.

This is an alias during the migration so there is one query implementation and
one compatibility contract. Domain-specific methods can later be extracted from
the combined facade without changing callers.
"""

from .base import DataProducts

StructuredDataProducts = DataProducts


def get_structured_products() -> StructuredDataProducts:
    return StructuredDataProducts()


__all__ = ["StructuredDataProducts", "get_structured_products"]
