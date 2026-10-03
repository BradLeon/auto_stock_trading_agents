"""Compatibility helpers backed by the Runtime Data Gateway.

Persistent sector products may still use ``fetch_prices``' legacy mapping shape;
new runtime consumers should use ``fetch_close_history`` to preserve per-symbol
status and market-bar timestamps.
"""

from __future__ import annotations

from .runtime.market_data import RuntimeCloseHistory, fetch_close_history_many

name = "sector_snapshot"


def fetch_prices(symbols: list[str], period: str = "1y") -> dict[str, list[float]]:
    """Compatibility projection of runtime close history to the legacy shape."""
    return {
        symbol: list(row.closes)
        for symbol, row in fetch_close_history(symbols, period=period).items()
        if row.status == "succeeded" and row.closes
    }


def fetch_close_history(
    symbols: list[str], *, period: str = "1y"
) -> dict[str, RuntimeCloseHistory]:
    """Runtime-only close histories with explicit status and last-bar date."""
    return fetch_close_history_many(symbols, period=period)


def momentum(closes: list[float], days: int) -> float | None:
    """Pct return over the last `days` sessions."""
    if len(closes) <= days or closes[-1 - days] == 0:
        return None
    return round((closes[-1] / closes[-1 - days] - 1) * 100, 2)


def dist_to_high(closes: list[float]) -> float | None:
    """Pct distance of the last close from the period high (negative = below)."""
    if not closes:
        return None
    hi = max(closes)
    return round((closes[-1] / hi - 1) * 100, 2) if hi else None
