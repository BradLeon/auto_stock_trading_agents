"""Live portfolio + P&L read (deterministic, read-only — no confirmation)."""

from __future__ import annotations

import logging

from ..broker import IBKRBroker, IBKRUnavailable
from ..schemas.portfolio import PortfolioSnapshot

log = logging.getLogger("ats.trader.portfolio")


def _sector_map() -> dict[str, str]:
    from ..config import get_config

    return {t.symbol: t.sector for t in get_config().app.tickers}


def snapshot() -> PortfolioSnapshot | None:
    """Live IBKR portfolio + account P&L. None (logged) if TWS is unreachable."""
    try:
        return IBKRBroker(sector_by_symbol=_sector_map()).get_portfolio()
    except IBKRUnavailable as exc:
        log.warning("portfolio read skipped: %s", exc)
        return None
