"""Which trading session a scheduled Chief run would use, when it closes, and the approval deadline it implies.

overnight-session-scheduling D2/D3. The Chief's daily run moves to Taipei morning, inside IBKR's overnight session
(exchange OVERNIGHT, IBEOS). Before it runs, the session it would trade in must be open; when it is, the approval
request carries a deadline a fixed window before that session's close, and orders still working near the close are
cancelled rather than carried into the next session.

Two sources decide whether the overnight session is open:

- the exchange calendar (XNYS): an overnight session belongs to the trading day it ends on. It ends 03:50 ET on
  that day and starts the evening before (20:00 ET, Sunday 18:00 ET). No trading day, no session: Friday night
  (ends on a Saturday) and the night before a US holiday are closed;
- the OVERNIGHT contract's own trading hours, read from IBKR, which also know early closes and unscheduled
  closures. When IBKR can be asked and says closed, the session is closed. When it cannot be asked (TWS down), the
  calendar answer stands: the run then fails closed later on its own quote read, which asks the contract again.

D6 (2026-11-25): a run in the pre-market (04:00 New York to the open of a trading day, e.g. 21:00 Taipei) uses the
`extended` venue: SMART limit DAY orders with `outsideRth`, working through the pre-market, regular and after-hours
sessions until IBKR expires them at 20:00. Its approval deadline counts back from that day's regular close.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("ats.workflow.overnight_session")

ET = ZoneInfo("America/New_York")
TAIPEI = ZoneInfo("Asia/Taipei")

OVERNIGHT_OPEN = time(20, 0)
OVERNIGHT_SUNDAY_OPEN = time(18, 0)
OVERNIGHT_CLOSE = time(3, 50)
PREMARKET_OPEN = time(4, 0)

#: D2: the approval deadline is this long before the session's close.
APPROVAL_WINDOW = timedelta(hours=4)
#: D3: overnight orders still working this long before the close are cancelled.
CANCEL_LEAD = timedelta(minutes=10)
#: The instrument whose OVERNIGHT contract is asked for the session's hours (accepted an overnight order 2026-11-05).
PROBE_SYMBOL = "NVDA"

SKIPPED_SESSION_CLOSED = "skipped_session_closed"
CANCELLED_SESSION_END = "cancelled_session_end"
APPROVAL_DEADLINE_PASSED = "approval_deadline_passed"


@dataclass(frozen=True)
class SessionWindow:
    venue: str                  # "regular" | "overnight" | "extended"
    opens_at: datetime
    closes_at: datetime         # the instant the approval window counts back from (extended: the regular close)
    source: str                 # "calendar" | "ibkr"

    @property
    def approval_deadline(self) -> datetime:
        return self.closes_at - APPROVAL_WINDOW


def _is_trading_day(day: date) -> bool:
    from .trade_schedules import is_trading_day

    return is_trading_day(day)


def regular_window(point: datetime) -> SessionWindow | None:
    """The NYSE regular session containing `point`, from the exchange calendar (holidays and early closes)."""
    import pandas_market_calendars as mcal

    day = point.astimezone(ET).date().isoformat()
    for row in mcal.get_calendar("XNYS").schedule(start_date=day, end_date=day).itertuples():
        opens, closes = row.market_open.to_pydatetime(), row.market_close.to_pydatetime()
        if opens <= point < closes:
            return SessionWindow("regular", opens, closes, "calendar")
    return None


def premarket_window(point: datetime) -> SessionWindow | None:
    """D6: the pre-market of a NYSE trading day (04:00 New York to the open) containing `point`, or None.

    `closes_at` is that day's regular close (early closes included): the extended-venue order keeps working
    through the regular session, and the approval deadline counts back from its close."""
    import pandas_market_calendars as mcal

    et = point.astimezone(ET)
    day = et.date().isoformat()
    for row in mcal.get_calendar("XNYS").schedule(start_date=day, end_date=day).itertuples():
        opens = datetime.combine(et.date(), PREMARKET_OPEN, tzinfo=ET)
        if opens <= et < row.market_open.to_pydatetime():
            return SessionWindow("extended", opens, row.market_close.to_pydatetime(), "calendar")
    return None


def overnight_window(point: datetime) -> SessionWindow | None:
    """The overnight session containing `point` by the exchange calendar alone, or None."""
    et = point.astimezone(ET)
    end_day = et.date() if et.time() < OVERNIGHT_CLOSE else et.date() + timedelta(days=1)
    if end_day.weekday() > 4 or not _is_trading_day(end_day):
        return None
    start_day = end_day - timedelta(days=1)
    opens = datetime.combine(start_day, OVERNIGHT_SUNDAY_OPEN if start_day.weekday() == 6 else OVERNIGHT_OPEN,
                             tzinfo=ET)
    closes = datetime.combine(end_day, OVERNIGHT_CLOSE, tzinfo=ET)
    return SessionWindow("overnight", opens, closes, "calendar") if opens <= et < closes else None


def _ibkr_overnight_segment(point: datetime) -> tuple[datetime, datetime] | None:
    """The OVERNIGHT contract's own trading-hours segment containing `point` (raises when IBKR cannot be asked)."""
    from ..broker import IBKRBroker

    return IBKRBroker().overnight_session_hours(PROBE_SYMBOL, point=point)


def confirmed_overnight_window(point: datetime) -> SessionWindow | None:
    """The calendar's overnight session, confirmed (and its close taken) from the contract's hours when reachable."""
    window = overnight_window(point)
    if window is None:
        return None
    try:
        segment = _ibkr_overnight_segment(point)
    except Exception as exc:  # noqa: BLE001 - TWS unreachable: the calendar answer stands, the quote read re-asks
        log.warning("overnight session hours unavailable from IBKR (%s); using the exchange calendar", exc)
        return window
    if segment is None:
        return None
    return SessionWindow("overnight", segment[0], segment[1], "ibkr")


def chief_session(point: datetime) -> SessionWindow | None:
    """The session a scheduled Chief run at `point` would trade in, or None when none is open.

    The regular session when it is open (the venue rule in `graph.chief.risk_gate` picks it first), else the
    overnight session, else the pre-market (D6). None is the `skipped_session_closed` case: the after-hours are
    left out on purpose, a proposal made there has under 4 hours before its DAY order expires.
    """
    return regular_window(point) or confirmed_overnight_window(point) or premarket_window(point)


def offhours_window(point: datetime) -> SessionWindow | None:
    """Outside the regular session: the overnight session (confirmed) else the pre-market, or None."""
    return confirmed_overnight_window(point) or premarket_window(point)


def deadline_rejection(deadline: datetime | None, approved_at: datetime | None) -> str | None:
    """Why an approval given at `approved_at` may not be executed against `deadline`, or None when it may."""
    if deadline is None or approved_at is None or approved_at <= deadline:
        return None
    return (f"{APPROVAL_DEADLINE_PASSED}: 批准于 {fmt_taipei(approved_at)}，晚于审批截止 {fmt_taipei(deadline)}；"
            "需重新审批（下一个首席周期按最新持仓重新评估）")


def fmt_taipei(point: datetime) -> str:
    return point.astimezone(TAIPEI).strftime("%m-%d %H:%M") + " 台北"


def deadline_line(deadline: datetime, closes_at: datetime | None = None, venue: str = "overnight") -> str:
    """The line every approval surface shows (terminal, reminder, card)."""
    name = "常规时段" if venue == "extended" else "隔夜时段"
    close = f"（{name} {fmt_taipei(closes_at)} 收盘前 {int(APPROVAL_WINDOW.total_seconds() // 3600)} 小时）" \
        if closes_at else ""
    return f"⏰ 审批截止：{fmt_taipei(deadline)}{close}；超时批准不会执行，需重新审批"
