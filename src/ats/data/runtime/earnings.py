"""Volatile earnings-calendar inputs for scheduled workflows.

Earnings dates and reported/estimated values change intraday and therefore remain
runtime inputs.  This facade keeps schedulers out of the historical flat module
while preserving the existing calendar and fiscal-label contracts during the
compatibility period.
"""

from __future__ import annotations

from ats.data.earnings_calendar import EarningsPrint


def next_earnings(*args, **kwargs):
    """Forward at call time so compatibility patches retain their old contract."""
    from ats.data import earnings_calendar

    return earnings_calendar.next_earnings(*args, **kwargs)


def last_print(*args, **kwargs):
    """Forward at call time so compatibility patches retain their old contract."""
    from ats.data import earnings_calendar

    return earnings_calendar.last_print(*args, **kwargs)


def resolve_fiscal_label(*args, **kwargs):
    from ats.data import period

    return period.resolve_fiscal_label(*args, **kwargs)


def resolve_and_cache(*args, **kwargs):
    from ats.data import period

    return period.resolve_and_cache(*args, **kwargs)

__all__ = [
    "EarningsPrint",
    "last_print",
    "next_earnings",
    "resolve_and_cache",
    "resolve_fiscal_label",
]
