"""Next earnings date per ticker.

Primary: Finnhub earnings calendar (aggregated from company IR announcements —
the same kind of feed broker software licenses), which also returns the session
(amc/bmo) and EPS/revenue estimates. Fallback: yfinance. Fully dynamic — no
static config or manual updates. Degrades to None.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ..config import get_config
from .base import safe_fetch

name = "earnings_calendar"
_HORIZON_DAYS = 150


def next_earnings(symbol: str) -> dict | None:
    """Richest available next-earnings record:
    {date, hour(amc/bmo/dmh), quarter, year, eps_estimate, rev_estimate, confirmed}.
    """
    ev = safe_fetch(lambda: _finnhub_next(symbol), source=f"finnhub-cal:{symbol}")
    if ev:
        return ev
    d = safe_fetch(lambda: _yf_next(symbol), source=f"yf-cal:{symbol}")
    return {"date": d, "hour": "", "quarter": None, "year": None,
            "eps_estimate": None, "rev_estimate": None, "confirmed": False} if d else None


def next_earnings_date(symbol: str) -> date | None:
    """Just the date (used by options expiry selection + the scheduler)."""
    ev = next_earnings(symbol)
    return ev["date"] if ev else None


# --------------------------------------------------------------------------- #
# Finnhub earnings calendar
# --------------------------------------------------------------------------- #
def _finnhub_next(symbol: str) -> dict | None:
    import httpx

    key = get_config().secrets.finnhub_api_key
    if not key:
        raise ValueError("no FINNHUB_API_KEY")
    today = datetime.now(timezone.utc).date()
    r = httpx.get("https://finnhub.io/api/v1/calendar/earnings", timeout=20, params={
        "symbol": symbol, "from": today.isoformat(),
        "to": (today + timedelta(days=_HORIZON_DAYS)).isoformat(), "token": key})
    r.raise_for_status()
    cal = r.json().get("earningsCalendar", []) or []
    future = sorted((c for c in cal if c.get("date", "") >= today.isoformat()),
                    key=lambda c: c["date"])
    if not future:
        raise ValueError(f"no upcoming earnings for {symbol}")
    c = future[0]
    return {"date": date.fromisoformat(c["date"]), "hour": c.get("hour", ""),
            "quarter": c.get("quarter"), "year": c.get("year"),
            "eps_estimate": c.get("epsEstimate"), "rev_estimate": c.get("revenueEstimate"),
            "confirmed": True}


# --------------------------------------------------------------------------- #
# yfinance fallback (date only)
# --------------------------------------------------------------------------- #
def _yf_next(symbol: str) -> date | None:
    import yfinance as yf
    from .base import yf_symbol

    t = yf.Ticker(yf_symbol(symbol))
    today = datetime.now(timezone.utc).date()
    df = t.get_earnings_dates(limit=12)
    if df is not None and not df.empty:
        future = [idx.date() for idx in df.index if idx.date() >= today]
        if future:
            return min(future)
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
