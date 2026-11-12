"""Market data source (yfinance) — daily OHLCV + derived indicators.

Free, no API key. Returns a MarketSnapshot per ticker; on failure returns a
bare snapshot (no history) so the cycle degrades gracefully.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import unquote, urlsplit

from ...schemas.market import OHLCV, MarketSnapshot, Ticker
from ..base import safe_fetch
from ..indicators import compute_indicators

name = "yfinance"
log = logging.getLogger(__name__)


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


# Close-history downloads go out one at a time over one long-lived session.
#
# Since 2026-11-10 roughly 10-30% of *new* TLS handshakes to Yahoo through the
# local proxy are reset (curl 35, "SSL_connect: Connection closed abruptly"), and
# far more when several start at once. yfinance's default batch download opens a
# fresh session and one thread per symbol, so each basket paid many handshakes and
# lost about half its symbols; yfinance reports those as "possibly delisted".
# Serial requests over a reused session keep one connection alive instead, and the
# lock stops the dispatcher's parallel task workers from interleaving downloads.
_DOWNLOAD_LOCK = threading.Lock()
_SESSION = None
_RETRY_DELAYS = (1.0, 3.0)   # seconds before the 2nd and 3rd attempt of failed symbols


def _tracking_session():
    """The shared curl_cffi session, recording which chart requests died in transport."""
    global _SESSION
    if _SESSION is None:
        from curl_cffi import requests as curl_requests

        class _TrackingSession(curl_requests.Session):
            transport_failed: set[str]

            def request(self, method, url, *args, **kwargs):
                try:
                    return super().request(method, url, *args, **kwargs)
                except Exception:
                    path = urlsplit(str(url)).path
                    if "/v8/finance/chart/" in path:
                        self.transport_failed.add(unquote(path.rsplit("/", 1)[-1]).upper())
                    raise

        _SESSION = _TrackingSession(impersonate="chrome")
        _SESSION.transport_failed = set()
    return _SESSION


def _download_close_frame(symbols: list[str], period: str):
    """One serial yfinance request; exposed separately to make the boundary testable.

    Returns ``(frame, normalized, transport_failed)`` where ``transport_failed``
    holds the original symbols whose request died before any HTTP response.
    """
    import yfinance as yf
    from ..base import yf_symbol

    normalized = [yf_symbol(symbol) for symbol in symbols]
    with _DOWNLOAD_LOCK:
        session = _tracking_session()
        session.transport_failed = set()
        frame = yf.download(normalized, period=period, progress=False, threads=False,
                            auto_adjust=True, group_by="column", session=session)["Close"]
        failed = set(session.transport_failed)
    by_yahoo = {yf_symbol(symbol).upper(): symbol for symbol in symbols}
    return frame, normalized, {by_yahoo[item] for item in failed if item in by_yahoo}


def _frame_columns(frame, requested: list[str]) -> dict[str, object]:
    """{original symbol: close series} for one downloaded frame."""
    from ..base import yf_symbol

    if frame is None or getattr(frame, "empty", False):
        return {}
    reverse = {yf_symbol(original): original for original in requested}
    if hasattr(frame, "columns"):
        return {reverse.get(str(column), str(column)): frame[column] for column in frame.columns}
    return {requested[0]: frame}  # yfinance returns a Series for a one-symbol response


def _has_closes(series) -> bool:
    try:
        return bool(series.dropna().size)
    except Exception:  # noqa: BLE001 - judged again, and isolated, when the row is built
        return True


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
    rows: dict[str, object] = {}
    transport_failed: set[str] = set()
    any_response = False
    pending = list(requested)
    # Only symbols whose request died in transport are retried: a symbol Yahoo
    # answered with no data would answer the same way again.
    for delay in (0.0, *_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        downloaded = safe_fetch(
            lambda: _download_close_frame(pending, period),
            source="runtime-market-batch")
        if downloaded is None:
            break
        any_response = True
        frame, _normalized, *extra = downloaded
        failed = set(extra[0]) if extra else set()
        for symbol, series in _frame_columns(frame, pending).items():
            if symbol not in rows or _has_closes(series):
                rows[symbol] = series
        transport_failed = {symbol for symbol in failed
                            if symbol not in rows or not _has_closes(rows[symbol])}
        if len(transport_failed) < len(failed):
            log.info("runtime-market-batch: %d symbol(s) recovered on retry",
                     len(failed) - len(transport_failed))
        pending = [symbol for symbol in pending if symbol in transport_failed]
        if not pending:
            break
    if transport_failed:
        log.warning("runtime-market-batch: transport failed after %d attempt(s) for %s",
                    1 + len(_RETRY_DELAYS), ", ".join(sorted(transport_failed)))

    if not any_response:
        return {
            symbol: RuntimeCloseHistory(symbol, (), None, queried_at,
                                        "unavailable", reason="provider_unavailable")
            for symbol in requested
        }
    if not rows and not transport_failed:
        return {
            symbol: RuntimeCloseHistory(symbol, (), None, queried_at,
                                        "no_data", reason="empty_response")
            for symbol in requested
        }

    results: dict[str, RuntimeCloseHistory] = {}
    for symbol in requested:
        series = rows.get(symbol)
        if symbol in transport_failed:
            results[symbol] = RuntimeCloseHistory(
                symbol, (), None, queried_at, "unavailable", reason="transport_error")
            continue
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
