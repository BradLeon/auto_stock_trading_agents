"""Market data source (yfinance) — daily OHLCV + derived indicators.

Free, no API key. Returns a MarketSnapshot per ticker; on failure returns a
bare snapshot (no history) so the cycle degrades gracefully.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..schemas.market import OHLCV, MarketSnapshot, Ticker
from .base import safe_fetch
from .indicators import compute_indicators

name = "yfinance"


def _download(symbol: str, period: str, interval: str):
    import yfinance as yf

    df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
    if df is None or df.empty:
        raise ValueError(f"no data returned for {symbol}")
    return df


def fetch_snapshot(ticker: Ticker, *, period: str = "1y", interval: str = "1d") -> MarketSnapshot:
    as_of = datetime.now(timezone.utc)
    df = safe_fetch(lambda: _download(ticker.symbol, period, interval), source=f"{name}:{ticker.symbol}")
    if df is None:
        return MarketSnapshot(ticker=ticker, as_of=as_of)

    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    history = [
        OHLCV(date=idx.date(), open=float(row.open), high=float(row.high),
              low=float(row.low), close=float(row.close), volume=float(row.volume))
        for idx, row in df.iterrows()
    ]
    return MarketSnapshot(
        ticker=ticker,
        as_of=as_of,
        last_price=float(df["close"].iloc[-1]),
        history=history,
        indicators=compute_indicators(df),
    )


def fetch_many(tickers: list[Ticker], **kw) -> dict[str, MarketSnapshot]:
    return {t.symbol: fetch_snapshot(t, **kw) for t in tickers}
