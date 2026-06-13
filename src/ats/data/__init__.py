"""Data source adapters. Each returns typed Pydantic models and degrades to None."""

from . import market_data
from .base import DataSource, safe_fetch

__all__ = ["DataSource", "safe_fetch", "market_data"]
