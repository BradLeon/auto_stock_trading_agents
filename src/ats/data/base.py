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

# yfinance logs its own ERROR lines for delisted / invalid tickers (e.g. HK 7709 that the
# user does not hold long-term). Those are handled gracefully by safe_fetch → None, so
# silence yfinance's own noise; our own warnings still fire.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# IBKR sends broker-native tickers; yfinance uses different conventions.
# Map IBKR symbol → yfinance ticker here; applied in all data/*.py calls.
_YF_SYMBOL_MAP: dict[str, str] = {
    "BRK B": "BRK-B",    # Berkshire B: IBKR uses space, yfinance uses dash
    "BRK A": "BRK-A",
    "HY9H": "SKHY",       # SK Hynix Frankfurt ADR → US ADR (same company, USD-priced)
}


def yf_symbol(symbol: str) -> str:
    """Normalize an IBKR broker symbol to its yfinance-compatible ticker."""
    return _YF_SYMBOL_MAP.get(symbol, symbol)

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
