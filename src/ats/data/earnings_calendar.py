"""Next earnings date per ticker (yfinance, Finnhub fallback). Degrades to None."""

from __future__ import annotations

from datetime import date, datetime, timezone

from .base import safe_fetch

name = "earnings_calendar"


def _yf_next(symbol: str) -> date | None:
    import yfinance as yf

    t = yf.Ticker(symbol)
    today = datetime.now(timezone.utc).date()

    # Preferred: full earnings-dates table (future + past).
    df = t.get_earnings_dates(limit=12)
    if df is not None and not df.empty:
        future = [idx.date() for idx in df.index if idx.date() >= today]
        if future:
            return min(future)

    # Fallback: the calendar dict.
    cal = getattr(t, "calendar", None)
    if isinstance(cal, dict):
        ed = cal.get("Earnings Date")
        if isinstance(ed, (list, tuple)) and ed:
            ed = ed[0]
        if isinstance(ed, datetime):
            return ed.date()
        if isinstance(ed, date):
            return ed
    raise ValueError(f"no earnings date for {symbol}")


def next_earnings_date(symbol: str) -> date | None:
    return safe_fetch(lambda: _yf_next(symbol), source=f"{name}:{symbol}")
