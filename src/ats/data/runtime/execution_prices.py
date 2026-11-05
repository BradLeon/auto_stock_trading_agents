"""Read-only execution quotes. Provider timestamps never come from receipt time."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel


class ExecutionPrice(BaseModel):
    schema_version: str = "runtime-execution-price-v1"
    symbol: str
    currency: str
    source: str
    source_as_of: datetime
    queried_at: datetime
    price_kind: str
    bid: float | None = None
    ask: float | None = None
    close: float | None = None
    session: str
    market_data_mode: str
    adjusted: bool
    min_size: float
    size_increment: float
    min_tick: float
    source_precision: str = "tick"
    # session: "regular" (SMART, 09:30-16:00 ET) or "overnight" (IBKR's OVERNIGHT venue, IBEOS) for a live
    # two-sided quote; "previous_completed_regular" for the dated close that only a deferred order is priced from.


def session_context(point: datetime) -> tuple[bool, datetime]:
    """NYSE regular session and most recent COMPLETED session close, including holidays."""
    import pandas_market_calendars as mcal

    point = point.astimezone(UTC)
    day = point.astimezone(ZoneInfo("America/New_York")).date()
    schedule = mcal.get_calendar("XNYS").schedule(
        start_date=day - timedelta(days=10), end_date=day)
    regular = any(row.market_open.to_pydatetime() <= point < row.market_close.to_pydatetime()
                  for row in schedule.itertuples())
    completed = [row.market_close.to_pydatetime() for row in schedule.itertuples()
                 if row.market_close.to_pydatetime() <= point]
    if not completed:
        raise ValueError("calendar_unavailable")
    return regular, completed[-1]


def overnight_session_candidate(point: datetime) -> bool:
    """Could IBKR's overnight session (exchange OVERNIGHT) be open at `point`? A rule for choosing the venue only.

    The session runs 20:00-03:50 ET on Sunday (from 18:00) to Thursday nights; Friday night is closed (measured from
    the contract's own trading hours, 2026-11-05). Whether it IS open (holidays, early closes) is IBKR's to say: the
    quote read asks the contract (`IBKRBroker.get_execution_quote`) and refuses outside the hours it returns.
    """
    et = point.astimezone(ZoneInfo("America/New_York"))
    minutes, weekday = et.hour * 60 + et.minute, et.weekday()        # Monday = 0
    evening = (weekday in (0, 1, 2, 3) and minutes >= 20 * 60) or (weekday == 6 and minutes >= 18 * 60)
    morning = weekday in (0, 1, 2, 3, 4) and minutes < 3 * 60 + 50
    return evening or morning


def fetch_execution_price(symbol: str, *, currency: str, overnight: bool = False,
                          venue: str = "regular") -> ExecutionPrice:
    """The runtime gateway alone owns provider reads; no persistence or orders.

    `overnight` is the deferred-order basis (the previous close, for a market that is closed). `venue` is where a
    live quote is read from and the order will be sent: "regular" (SMART) or "overnight" (the OVERNIGHT venue).
    """
    from ...broker import IBKRBroker

    broker = IBKRBroker()
    metadata = broker.get_stock_execution_metadata(symbol, currency=currency)
    if not overnight:
        return ExecutionPrice.model_validate(broker.get_execution_quote(
            symbol, currency=currency, metadata=metadata, **({"venue": venue} if venue != "regular" else {})))

    # This is a dated, raw session bar, explicitly not a tick or executable quote.
    from .market_data import _download

    queried_at = datetime.now(UTC)
    regular, previous_close = session_context(queried_at)
    if regular:
        raise ValueError("overnight_close_not_valid_during_regular_session")
    frame = _download(symbol, "10d", "1d", adjust=False)
    rows = [(idx, row) for idx, row in frame.iterrows()
            if idx.date() == previous_close.astimezone(ZoneInfo("America/New_York")).date()]
    if not rows:
        raise ValueError("previous_session_close_missing")
    return ExecutionPrice(
        symbol=symbol, currency=metadata["currency"], source="yfinance:raw_daily",
        source_as_of=previous_close, queried_at=queried_at,
        price_kind="previous_session_close", close=float(rows[-1][1]["Close"]),
        session="previous_completed_regular", market_data_mode="historical",
        adjusted=False, source_precision="session_date",
        min_size=metadata["min_size"], size_increment=metadata["size_increment"],
        min_tick=metadata["min_tick"])
