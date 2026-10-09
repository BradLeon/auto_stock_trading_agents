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


def fetch_execution_price(symbol: str, *, currency: str, overnight: bool = False) -> ExecutionPrice:
    """The runtime gateway alone owns provider reads; no persistence or orders."""
    from ...broker import IBKRBroker

    broker = IBKRBroker()
    metadata = broker.get_stock_execution_metadata(symbol, currency=currency)
    if not overnight:
        return ExecutionPrice.model_validate(broker.get_execution_quote(
            symbol, currency=currency, metadata=metadata))

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
