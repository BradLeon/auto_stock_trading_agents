"""Market data source (yfinance) — daily OHLCV + derived indicators.

Free, no API key. Returns a MarketSnapshot per ticker; on failure returns a
bare snapshot (no history) so the cycle degrades gracefully.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from ...schemas.market import OHLCV, MarketSnapshot, Ticker
from ..base import safe_fetch
from ..indicators import compute_indicators

name = "yfinance"


@dataclass(frozen=True)
class RuntimeCloseHistory:
    """Ephemeral close series plus its source-time and read status.

    This is deliberately a runtime result, not a persisted data product. Consumers
    that need to assess freshness can inspect ``bar_as_of`` separately from the
    time the query was made.
    """

    symbol: str
    closes: tuple[float, ...]
    bar_as_of: date | None
    queried_at: datetime
    status: str
    source: str = "yfinance"
    reason: str = ""


def _download(symbol: str, period: str, interval: str, adjust: bool = True):
    import yfinance as yf
    from ..base import yf_symbol

    df = yf.Ticker(yf_symbol(symbol)).history(period=period, interval=interval,
                                              auto_adjust=adjust)
    if df is None or df.empty:
        raise ValueError(f"no data returned for {symbol}")
    return df


def fetch_snapshot(ticker: Ticker, *, period: str = "1y", interval: str = "1d",
                   adjust: bool = True) -> MarketSnapshot:
    """Daily OHLCV history.

    `adjust=True` (the default, and what indicators want) back-adjusts for splits and
    dividends. Pass `adjust=False` when comparing against a RAW price captured at a
    point in time — an unadjusted fill price or reference price measured against an
    adjusted series silently drifts across any corporate action.
    """
    as_of = datetime.now(timezone.utc)
    df = safe_fetch(lambda: _download(ticker.symbol, period, interval, adjust),
                    source=f"{name}:{ticker.symbol}")
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


def _download_close_frame(symbols: list[str], period: str):
    """One yfinance request; exposed separately to make the boundary testable."""
    import yfinance as yf
    from ..base import yf_symbol

    normalized = [yf_symbol(symbol) for symbol in symbols]
    frame = yf.download(normalized, period=period, progress=False,
                        auto_adjust=True, group_by="column")["Close"]
    return frame, normalized


def fetch_close_history_many(
    symbols: list[str], *, period: str = "1y"
) -> dict[str, RuntimeCloseHistory]:
    """Fetch close history for a symbol basket in one ephemeral provider query.

    Missing or unavailable data is represented per symbol instead of being
    silently omitted. The returned ``queried_at`` is not mistaken for the last
    market bar: ``bar_as_of`` carries that timestamp independently.
    """
    requested = list(dict.fromkeys(str(symbol) for symbol in symbols if str(symbol)))
    if not requested:
        return {}
    queried_at = datetime.now(timezone.utc)
    downloaded = safe_fetch(
        lambda: _download_close_frame(requested, period),
        source="runtime-market-batch")
    if downloaded is None:
        return {
            symbol: RuntimeCloseHistory(symbol, (), None, queried_at,
                                        "unavailable", reason="provider_unavailable")
            for symbol in requested
        }

    frame, _normalized = downloaded
    if frame is None or getattr(frame, "empty", False):
        return {
            symbol: RuntimeCloseHistory(symbol, (), None, queried_at,
                                        "no_data", reason="empty_response")
            for symbol in requested
        }
    from ..base import yf_symbol

    reverse = {yf_symbol(original): original for original in requested}
    rows: dict[str, object] = {}
    if hasattr(frame, "columns"):
        for column in frame.columns:
            rows[reverse.get(str(column), str(column))] = frame[column]
    else:  # yfinance returns a Series for a one-symbol response
        rows[requested[0]] = frame

    results: dict[str, RuntimeCloseHistory] = {}
    for symbol in requested:
        series = rows.get(symbol)
        if series is None:
            results[symbol] = RuntimeCloseHistory(
                symbol, (), None, queried_at, "no_data", reason="symbol_missing")
            continue
        try:
            clean = series.dropna()
            closes = tuple(float(value) for value in clean.tolist())
            idx = clean.index[-1] if closes else None
            bar_as_of = idx.date() if hasattr(idx, "date") else None
            if isinstance(idx, date) and not isinstance(idx, datetime):
                bar_as_of = idx
        except Exception as exc:  # noqa: BLE001 - malformed provider row is isolated
            results[symbol] = RuntimeCloseHistory(
                symbol, (), None, queried_at, "invalid_data",
                reason=f"invalid_close_series:{type(exc).__name__}")
            continue
        if closes and bar_as_of is None:
            results[symbol] = RuntimeCloseHistory(
                symbol, (), None, queried_at, "invalid_data",
                reason="missing_bar_timestamp")
            continue
        results[symbol] = RuntimeCloseHistory(
            symbol, closes, bar_as_of, queried_at,
            "succeeded" if closes else "no_data",
            reason="" if closes else "empty_symbol_series")
    return results
