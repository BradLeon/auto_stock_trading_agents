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
        from ..data.runtime.broker import portfolio_snapshot
        from ..workflow.consumer_reads import record_read
        from ..workflow.runtime_reads import current_read_context, gate_read

        context = current_read_context()
        if context:
            gate_read(context.identity)
        packet = portfolio_snapshot(IBKRBroker(sector_by_symbol=_sector_map()))
        record_read(context.identity.consumer_id if context else "risk",
                    "ats.data.runtime.broker.portfolio_snapshot",
                    refs=[f"broker-portfolio:{packet['source_as_of']}"] if packet["source_as_of"] else [],
                    status=packet["status"])
        return PortfolioSnapshot.model_validate(packet["payload"]) if packet["payload"] else None
    except IBKRUnavailable as exc:
        log.warning("portfolio read skipped: %s", exc)
        return None
