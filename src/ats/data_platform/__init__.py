"""Deprecated compatibility facade for agents and workflows.

New code should import the same implementation from ``ats.data.products``.
This module remains available during the staged data-layer migration.
"""

__deprecated__ = True

from .products import DataProducts, get_data_products

__all__ = ["DataProducts", "get_data_products"]
