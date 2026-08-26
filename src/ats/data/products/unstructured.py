"""Unstructured research products under the unified data namespace."""

from ats.data_platform.products import DataProducts

UnstructuredDataProducts = DataProducts


def get_unstructured_products() -> UnstructuredDataProducts:
    return UnstructuredDataProducts()


__all__ = ["UnstructuredDataProducts", "get_unstructured_products"]
