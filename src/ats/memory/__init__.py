"""Context Memory: structured store + performance tracking."""

from __future__ import annotations

import os
from functools import lru_cache

from .performance import compute as compute_performance
from .store import TradingMemory

__all__ = ["TradingMemory", "get_store", "compute_performance", "reset_store_cache"]


def _db_path() -> str:
    # Env override (tests point this at a tmp file); default under ./var.
    from ..config import REPO_ROOT

    return os.environ.get("ATS_DB_PATH", str(REPO_ROOT / "var" / "ats.sqlite"))


@lru_cache(maxsize=None)
def _store(path: str) -> TradingMemory:
    return TradingMemory(path)


def get_store() -> TradingMemory:
    return _store(_db_path())


def reset_store_cache() -> None:
    _store.cache_clear()
