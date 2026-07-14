"""Batched price snapshot for a whole sector universe — ONE yf.download call.

Momentum / distance-to-high for ~25 names comes from a single HTTP request,
the key rate-limit mitigation for the weekly sector review. Never raises.
"""

from __future__ import annotations

import logging

from .base import safe_fetch

log = logging.getLogger("ats.data.sector_snapshot")

name = "sector_snapshot"


def fetch_prices(symbols: list[str], period: str = "1y") -> dict[str, list[float]]:
    """Daily closes per symbol via one batched download. Missing names -> absent key.
    Input symbols may be raw IBKR broker tickers; they are normalized to yfinance
    conventions before the download, and results are keyed back to IBKR symbols."""
    if not symbols:
        return {}
    from .base import yf_symbol

    # Build IBKR→yf and yf→IBKR maps (one-to-one; collisions keep last)
    ibkr_to_yf = {s: yf_symbol(s) for s in symbols}
    yf_to_ibkr = {v: k for k, v in ibkr_to_yf.items()}
    yf_syms = list(ibkr_to_yf.values())

    raw = safe_fetch(lambda: _download(yf_syms, period), source="sector-prices")
    if not raw:
        return {}
    # Remap yfinance keys → original IBKR keys
    return {yf_to_ibkr.get(k, k): v for k, v in raw.items()}


def _download(symbols: list[str], period: str) -> dict[str, list[float]]:
    import yfinance as yf

    df = yf.download(symbols, period=period, progress=False, auto_adjust=True,
                     group_by="column")["Close"]
    out: dict[str, list[float]] = {}
    if hasattr(df, "columns"):           # multi-symbol frame
        for sym in df.columns:
            closes = [float(v) for v in df[sym].dropna().tolist()]
            if closes:
                out[str(sym)] = closes
    else:                                # single symbol -> Series
        closes = [float(v) for v in df.dropna().tolist()]
        if closes:
            out[symbols[0]] = closes
    return out


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
