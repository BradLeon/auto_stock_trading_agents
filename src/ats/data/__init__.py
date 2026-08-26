"""Unified data-layer entrypoint.

Legacy provider modules remain available under this package during migration;
new shared contracts and consumer products are exposed from the subpackages
below.
"""

from . import market_data
from .base import DataSource, safe_fetch

__all__ = ["DataSource", "safe_fetch", "market_data"]


def get_data_products():
    """Return the stable consumer facade without eagerly importing its adapters."""

    from .products import get_data_products as _get_data_products

    return _get_data_products()
