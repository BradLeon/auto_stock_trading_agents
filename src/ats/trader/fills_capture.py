"""Read the day's executions from TWS before TWS's own midnight, so the 07:00 Clerk never has to.

`reqExecutions` returns only the account's executions since midnight (TWS local time = Taipei on the Boss's
machine), and the Clerk reads at 07:00 Taipei: every fill made from the US open (22:30) until midnight is gone by
then (blockers B11, tasks 11.12; measured 2026-11-05: after a TWS restart the API still returned fills after
Taipei midnight and none before it). This job reads once at 23:58 Taipei and records them with the same
attribution as a Clerk read. It runs inside the dispatcher (`phase_e:fills-capture`).

It records fills; it does not open or close a Clerk window: `last_reconcile_at` stays where the Clerk left it. The
two minutes between 23:58 and midnight, and any read TWS could not answer, are covered by the Flex statement
(`flex_statement.backfill`, run by every Clerk reconcile), a business day later.
"""

from __future__ import annotations

import logging

log = logging.getLogger("ats.fills_capture")

CRON = {"hour": 23, "minute": 58, "timezone": "Asia/Taipei"}


def capture(*, broker=None, store=None) -> dict:
    from . import reconcile

    summary = reconcile.reconcile(broker=broker, store=store, advance_window=False)
    if summary["errors"]:
        log.warning("fills capture: %s", "; ".join(summary["errors"]))
    else:
        log.info("fills capture: %s fill(s) seen, %s new", summary["fills_seen"], summary["fills_new"])
    return summary


def capture_job() -> None:
    """The scheduler entry: an unreachable TWS is a warning, not a crash of the dispatcher thread."""
    try:
        capture()
    except Exception:  # noqa: BLE001
        log.exception("fills capture failed")
