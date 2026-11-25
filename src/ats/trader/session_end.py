"""Cancel the system's overnight orders still working shortly before the overnight session closes (D3).

An overnight limit was priced from the overnight book; carried into the regular session it would fill on a stale
basis. So ten minutes before the overnight close (03:40 New York, every weekday morning) the Trader cancels every
`ats:`-tagged order still working on the OVERNIGHT venue and records `cancelled_session_end` on its trade row.
Nothing is resubmitted: the next Chief cycle re-decides from the book as it then is. Regular-session orders and
orders placed by hand in TWS are never touched.

An order the broker still shows as working after the cancel is alerted to the Boss, not retried.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from ..workflow.overnight_session import CANCEL_LEAD, CANCELLED_SESSION_END, OVERNIGHT_CLOSE

log = logging.getLogger("ats.trader.session_end")

#: 10 minutes before the 03:50 New York close, every weekday morning (a session ends Monday to Friday).
CRON = {"day_of_week": "mon-fri", "hour": OVERNIGHT_CLOSE.hour,
        "minute": OVERNIGHT_CLOSE.minute - int(CANCEL_LEAD.total_seconds() // 60), "timezone": "America/New_York"}
OBSERVE_KEY = "overnight-session-end"
_WORKING = ("submitted", "pending", "partial")
_NOTE = f"{CANCELLED_SESSION_END}: 隔夜时段收盘前撤销未成交部分，不带入常规时段；下一个首席周期重新评估"


def cancel_overnight_orders(*, broker=None, store=None, now: datetime | None = None) -> dict:
    """Cancel and record. Returns {"status", "cancelled": [...], "still_working": [...]}."""
    if broker is None:
        from ..broker import IBKRBroker

        broker = IBKRBroker()
    now = now or datetime.now(UTC)
    working = broker.open_session_orders("overnight")              # read-only: most mornings there is nothing
    if not working:
        return {"status": "complete", "cancelled": [], "still_working": [], "at": now.isoformat()}
    rows = broker.cancel_session_orders("overnight")
    cancelled = [r for r in rows if r["status"] not in _WORKING]
    still = [r for r in rows if r["status"] in _WORKING]
    if store is None:
        from ..memory import get_store

        store = get_store()
    for row in cancelled:
        store.conn.execute(
            "UPDATE trades SET status = ?, terminal_basis = 'broker', error = ? "
            "WHERE order_ref = ? AND status IN (?, ?, ?)",
            (row["status"], _NOTE, row["order_ref"], *_WORKING))
    store.conn.commit()
    return {"status": "incomplete" if still else "complete", "cancelled": cancelled, "still_working": still,
            "at": now.isoformat()}


def cancel_job() -> None:
    """The scheduler entry: never raises into the dispatcher thread; a refusal or leftover order is alerted."""
    from ..runtime import alerts

    try:
        result = cancel_overnight_orders()
    except Exception as exc:
        log.exception("overnight session-end cancel failed")
        alerts.overnight_cancel_failed(exc=exc)
        return
    if result["cancelled"] or result["still_working"]:
        from ..workflow.schedule_runtime import observe

        observe(CANCELLED_SESSION_END, OBSERVE_KEY, result)
        log.info("overnight session end: cancelled %d, still working %d",
                 len(result["cancelled"]), len(result["still_working"]))
    if result["still_working"]:
        alerts.overnight_cancel_failed(result=result)
