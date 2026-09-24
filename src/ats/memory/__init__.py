"""Context Memory: structured store + performance tracking."""

from __future__ import annotations

import os
from functools import lru_cache
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock
from typing import Iterator

from .performance import compute as compute_performance
from .store import TradingMemory

__all__ = ["TradingMemory", "get_store", "compute_performance", "reset_store_cache"]

_STORE_LOCK = RLock()
_TASK_STORE: ContextVar[TradingMemory | None] = ContextVar("ats_task_store", default=None)


def _db_path() -> str:
    # Env override (tests point this at a tmp file); default under ./var.
    from ..config import REPO_ROOT

    return os.environ.get("ATS_DB_PATH", str(REPO_ROOT / "var" / "ats.sqlite"))


@lru_cache(maxsize=None)
def _store(path: str) -> TradingMemory:
    return TradingMemory(path)


def get_store() -> TradingMemory:
    scoped = _TASK_STORE.get()
    if scoped is not None:
        return scoped
    # lru_cache protects its dictionary, but permits duplicate factory calls on a
    # concurrent cache miss. Serialize the first destructive legacy bootstrap.
    with _STORE_LOCK:
        return _store(_db_path())


@contextmanager
def task_store_scope(path: str | None = None) -> Iterator[TradingMemory]:
    """Bind an independent SQLite connection to one synchronous task.

    The cached store performs the historic migrations once before a worker opens a
    plain connection. The task-local connection is thread-confined and always closed
    at scope exit; worker code can keep using ``get_store()`` without sharing handles.
    """
    db_path = str(path or _db_path())
    if db_path == ":memory:":
        raise ValueError("task-local Workflow Memory requires a file-backed SQLite database")
    with _STORE_LOCK:
        _store(db_path)  # explicit one-time bootstrap/migration owner
    task_store = TradingMemory(db_path, initialize=False)
    token = _TASK_STORE.set(task_store)
    try:
        yield task_store
    finally:
        _TASK_STORE.reset(token)
        task_store.close()


def reset_store_cache() -> None:
    with _STORE_LOCK:
        _store.cache_clear()
