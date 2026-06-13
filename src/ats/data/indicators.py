"""Hand-rolled technical indicators (pandas only).

We compute a small, well-understood set directly rather than depending on
pandas-ta, which is fragile against modern numpy/pandas. Inputs are a daily
close series (and high/low for ATR).
"""

from __future__ import annotations

import pandas as pd


def sma(close: pd.Series, window: int) -> float | None:
    if len(close) < window:
        return None
    return float(close.tail(window).mean())


def rsi(close: pd.Series, window: int = 14) -> float | None:
    if len(close) <= window:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / window, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    val = 100 - (100 / (1 + rs.iloc[-1])) if pd.notna(rs.iloc[-1]) else 100.0
    return float(val)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float]:
    if len(close) < slow + signal:
        return {}
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return {
        "macd": float(macd_line.iloc[-1]),
        "macd_signal": float(signal_line.iloc[-1]),
        "macd_hist": float(macd_line.iloc[-1] - signal_line.iloc[-1]),
    }


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> float | None:
    if len(close) <= window:
        return None
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1 / window, adjust=False).mean().iloc[-1])


def compute_indicators(df: pd.DataFrame) -> dict[str, float]:
    """df must have columns: open, high, low, close, volume (daily, ascending)."""
    close = df["close"].astype(float)
    out: dict[str, float] = {}
    for w in (20, 50, 200):
        v = sma(close, w)
        if v is not None:
            out[f"sma_{w}"] = v
    r = rsi(close, 14)
    if r is not None:
        out["rsi_14"] = r
    out.update(macd(close))
    a = atr(df["high"].astype(float), df["low"].astype(float), close, 14)
    if a is not None:
        out["atr_14"] = a
    if len(close) >= 2:
        out["pct_change_1d"] = float((close.iloc[-1] / close.iloc[-2] - 1) * 100)
    return out
