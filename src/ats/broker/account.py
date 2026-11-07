"""The broker handle an account-state read is made through.

A handle, not a read: it is not connected until the governed read API (`PORTFOLIO_DATA`) uses it. Kept in
the broker package so a decision role can hand it to the API without importing the account-snapshot
helper it must not read through (switch-trade r5 task 6.4).
"""

from __future__ import annotations

from .ibkr import IBKRBroker


def account_broker() -> IBKRBroker:
    from ..config import get_config

    return IBKRBroker(sector_by_symbol={t.symbol: t.sector for t in get_config().app.tickers})
