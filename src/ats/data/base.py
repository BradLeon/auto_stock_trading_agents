"""DataSource base: common protocol, retry, and graceful degradation.

Every adapter returns a typed Pydantic model (or None on failure). Sources must
never raise into the graph — a dead source degrades the cycle, it does not abort
it. `safe_fetch` wraps a callable with retry + swallow-to-None semantics.
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol, TypeVar

from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger("ats.data")

T = TypeVar("T")


class DataSource(Protocol[T]):
    name: str

    def fetch(self, *args, **kwargs) -> T | None: ...


def safe_fetch(fn: Callable[[], T], *, source: str, attempts: int = 3) -> T | None:
    """Run `fn` with bounded retries; log and return None on final failure."""

    @retry(stop=stop_after_attempt(attempts),
           wait=wait_exponential(multiplier=0.5, max=8), reraise=True)
    def _call() -> T:
        return fn()

    try:
        return _call()
    except Exception as exc:  # noqa: BLE001 - sources must degrade, not crash
        log.warning("data source %s failed after %d attempts: %s", source, attempts, exc)
        return None
