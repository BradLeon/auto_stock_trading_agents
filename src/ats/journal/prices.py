"""Shared daily-bar cache for the journal's price-dependent calculations.

Used by both `predictions.py` (T+N outcome scoring) and `marks.py` (MAE/MFE). Always
UNADJUSTED (`adjust=False`): both consumers compare against a RAW price captured at a
point in time — a reference close or a cost basis — and an adjusted series silently
drifts against that across any split or dividend.
"""

from __future__ import annotations

from datetime import date

_CACHE: dict[str, list] = {}


def bars(symbol: str) -> list:
    """-> list[OHLCV], oldest first. Cached per process; empty list on failure."""
    if symbol not in _CACHE:
        from ..data.market_data import fetch_snapshot
        from ..schemas.market import Ticker

        snap = fetch_snapshot(Ticker(symbol=symbol), period="2y", adjust=False)
        _CACHE[symbol] = snap.history or []
    return _CACHE[symbol]


def close_on_or_after(symbol: str, d: date) -> tuple[date, float] | None:
    for b in bars(symbol):
        if b.date >= d:
            return (b.date, b.close)
    return None


def window(symbol: str, start: date, end: date) -> list:
    """-> list[OHLCV] with start <= date <= end, oldest first."""
    return [b for b in bars(symbol) if start <= b.date <= end]
