"""Earnings dates per ticker — both upcoming and ALREADY REPORTED.

Primary: Finnhub earnings calendar (aggregated from company IR announcements —
the same kind of feed broker software licenses), which also returns the session
(amc/bmo) and EPS/revenue estimates. Fallback/complement: yfinance. Fully dynamic
— no static config or manual updates. Degrades to None.

Two views, for two different questions:

  next_earnings()  — "when is the NEXT print?"  drives prep + option expiry choice.
  last_print()     — "has a print ALREADY happened that we haven't handled?"

The second exists because prediction is not good enough to trigger scoring off.
Measured on this universe (2026-07):
  * Finnhub's `hour` (amc/bmo) is EMPTY for 5 of 13 targets — always for
    SKHY/CRDO/MRVL, intermittently for COHR/LRCX/VRT/KLAC.
  * Finnhub revised GOOG's next date 2026-10-28 → 2026-10-27 within a day.
  * Finnhub and yfinance disagree by 6 days on SKHY.
  * yfinance intermittently fails outright ("KLAC may be delisted").
  * For foreign issuers (TSM/ASML) amc/bmo doesn't map onto ET at all.

So `EarningsPrint` treats the presence of an ACTUAL EPS as the authoritative
"已公布" signal (an observation, not a forecast), derives the session from
yfinance's real clock time in preference to Finnhub's enum, and leaves the session
`"unknown"` rather than guessing when neither source can say.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..config import get_config
from .base import safe_fetch

log = logging.getLogger("ats.data")

name = "earnings_calendar"
_HORIZON_DAYS = 150
ET = ZoneInfo("America/New_York")

# Session classification from a real clock time (ET). Deliberately narrow: a
# timestamp outside these bands (midnight placeholders, overnight foreign filings)
# yields "unknown", which the scheduler handles by trying both windows. Guessing
# "amc" by default is how a before-open print gets scored a session late.
_BMO_FROM, _BMO_TO = time(4, 0), time(9, 30)
_AMC_FROM, _AMC_TO = time(16, 0), time(20, 0)


@dataclass(frozen=True)
class EarningsPrint:
    """One concrete earnings print, ET-dated."""

    symbol: str
    date: date                                  # ET calendar date of the print
    session: str = "unknown"                    # bmo | amc | dmh | unknown
    session_source: str = "none"                # yf-clock | finnhub-hour | none
    at: datetime | None = None                  # tz-aware ET timestamp, when known
    quarter: int | None = None
    year: int | None = None
    eps_actual: float | None = None
    eps_estimate: float | None = None
    rev_actual: float | None = None
    rev_estimate: float | None = None
    sources: tuple[str, ...] = field(default_factory=tuple)

    @property
    def reported(self) -> bool:
        """True once an ACTUAL figure exists — the authoritative '已公布' signal."""
        return self.eps_actual is not None or self.rev_actual is not None


# --------------------------------------------------------------------------- #
# Print detection: recent + upcoming, from BOTH sources
# --------------------------------------------------------------------------- #
def _session_from_clock(ts: datetime | None) -> str:
    """Classify a print time into a market session. 'unknown' when out of band."""
    if ts is None:
        return "unknown"
    t = ts.astimezone(ET).time()
    if _BMO_FROM <= t < _BMO_TO:
        return "bmo"
    if _AMC_FROM <= t <= _AMC_TO:
        return "amc"
    if _BMO_TO <= t < _AMC_FROM:
        return "dmh"
    return "unknown"


def _finnhub_window(symbol: str, start: date, end: date) -> list[dict]:
    """Raw Finnhub calendar rows in [start, end] — includes PAST prints, which carry
    epsActual/revenueActual (that is how we know a print has happened)."""
    import httpx

    key = get_config().secrets.finnhub_api_key
    if not key:
        raise ValueError("no FINNHUB_API_KEY")
    r = httpx.get("https://finnhub.io/api/v1/calendar/earnings", timeout=20, params={
        "symbol": symbol, "from": start.isoformat(), "to": end.isoformat(), "token": key})
    r.raise_for_status()
    rows = []
    for c in r.json().get("earningsCalendar", []) or []:
        if not c.get("date"):
            continue
        rows.append({"date": date.fromisoformat(c["date"]), "hour": (c.get("hour") or "").lower(),
                     "quarter": c.get("quarter"), "year": c.get("year"),
                     "eps_actual": c.get("epsActual"), "eps_estimate": c.get("epsEstimate"),
                     "rev_actual": c.get("revenueActual"),
                     "rev_estimate": c.get("revenueEstimate")})
    return rows


def _yf_prints(symbol: str, start: date, end: date) -> list[dict]:
    """yfinance earnings rows in [start, end].

    Worth having despite Finnhub: the index is a tz-aware TIMESTAMP, so the session
    can be derived from the actual clock even when Finnhub's `hour` is blank.
    """
    import yfinance as yf

    from .base import yf_symbol

    df = yf.Ticker(yf_symbol(symbol)).get_earnings_dates(limit=25)
    if df is None or df.empty:
        raise ValueError(f"no yfinance earnings rows for {symbol}")
    rows = []
    for idx, row in df.iterrows():
        ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ET)
        d = ts.astimezone(ET).date()
        if not (start <= d <= end):
            continue

        def _num(val):
            return None if val is None or val != val else float(val)   # NaN-safe

        rows.append({"date": d, "at": ts.astimezone(ET),
                     "eps_actual": _num(row.get("Reported EPS")),
                     "eps_estimate": _num(row.get("EPS Estimate"))})
    return rows


def _merge(symbol: str, fh_rows: list[dict], yf_rows: list[dict]) -> list[EarningsPrint]:
    """Union both feeds into one print per event.

    Pairing is by date within ±1 day (the feeds routinely differ by a day on
    timezone/announcement conventions). yfinance wins the timestamp/session,
    Finnhub wins quarter/year/estimates, an actual from either counts. A gap larger
    than a day (SKHY: 6 days) is NOT collapsed — both survive as separate candidates
    and we warn, because silently picking one would be a guess.
    """
    prints: list[EarningsPrint] = []
    unpaired_yf = list(yf_rows)

    for fh in fh_rows:
        match = next((y for y in unpaired_yf if abs((y["date"] - fh["date"]).days) <= 1), None)
        if match:
            unpaired_yf.remove(match)
        at = match.get("at") if match else None
        session = _session_from_clock(at)
        session_source = "yf-clock" if session != "unknown" else "none"
        if session == "unknown" and fh["hour"] in ("amc", "bmo", "dmh"):
            session, session_source = fh["hour"], "finnhub-hour"
        srcs = ("finnhub",) + (("yfinance",) if match else ())
        # Prefer the clock-derived date: an ET date computed from a real timestamp
        # beats a bare date string whose timezone convention is undocumented.
        event_date = match["date"] if match else fh["date"]
        prints.append(EarningsPrint(
            symbol=symbol.upper(), date=event_date,
            session=session, session_source=session_source, at=at,
            quarter=fh["quarter"], year=fh["year"],
            eps_actual=fh["eps_actual"] if fh["eps_actual"] is not None
            else (match or {}).get("eps_actual"),
            eps_estimate=fh["eps_estimate"] if fh["eps_estimate"] is not None
            else (match or {}).get("eps_estimate"),
            rev_actual=fh["rev_actual"], rev_estimate=fh["rev_estimate"], sources=srcs))

    # yfinance-only events (Finnhub blind spot, e.g. SKHY's differing date).
    for y in unpaired_yf:
        session = _session_from_clock(y.get("at"))
        prints.append(EarningsPrint(
            symbol=symbol.upper(), date=y["date"], session=session,
            session_source="yf-clock" if session != "unknown" else "none",
            at=y.get("at"), eps_actual=y.get("eps_actual"),
            eps_estimate=y.get("eps_estimate"), sources=("yfinance",)))

    prints.sort(key=lambda p: p.date)
    for a, b in zip(prints, prints[1:]):
        if 1 < (b.date - a.date).days <= 10:
            log.warning("earnings sources disagree for %s: %s (%s) vs %s (%s) — keeping both",
                        symbol, a.date, ",".join(a.sources), b.date, ",".join(b.sources))
    return prints


def recent_and_next_prints(symbol: str, *, back_days: int = 10, fwd_days: int = _HORIZON_DAYS,
                           as_of: date | None = None) -> list[EarningsPrint]:
    """Prints in [as_of - back_days, as_of + fwd_days], oldest first. Never raises."""
    today = as_of or datetime.now(ET).date()
    start, end = today - timedelta(days=back_days), today + timedelta(days=fwd_days)
    fh = safe_fetch(lambda: _finnhub_window(symbol, start, end),
                    source=f"finnhub-cal:{symbol}") or []
    yfr = safe_fetch(lambda: _yf_prints(symbol, start, end), source=f"yf-cal:{symbol}") or []
    if not fh and not yfr:
        return []
    return _merge(symbol, fh, yfr)


def last_print(symbol: str, *, as_of: date | None = None,
               back_days: int = 10) -> EarningsPrint | None:
    """The most recent print on/before `as_of`, reported or not (caller decides).

    This is the trigger source for scoring: it answers "did a print already happen"
    from observation, unlike next_earnings() which only ever looks forward.
    """
    today = as_of or datetime.now(ET).date()
    past = [p for p in recent_and_next_prints(symbol, back_days=back_days, fwd_days=0,
                                              as_of=today) if p.date <= today]
    return past[-1] if past else None


def next_print(symbol: str, *, as_of: date | None = None) -> EarningsPrint | None:
    """The next print strictly after `as_of` (prep scheduling)."""
    today = as_of or datetime.now(ET).date()
    future = [p for p in recent_and_next_prints(symbol, back_days=0, as_of=today)
              if p.date > today]
    return future[0] if future else None


# --------------------------------------------------------------------------- #
# Next-earnings view (unchanged: prep windows + option expiry selection)
# --------------------------------------------------------------------------- #
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
