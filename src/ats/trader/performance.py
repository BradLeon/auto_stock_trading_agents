"""Performance tracking: record snapshots from live IBKR, and report analytics.

Deterministic, read/store only (no confirmation). record_snapshot() is what the
daily scheduler calls so a continuous NetLiq/P&L curve exists even if only PEAD runs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..broker import IBKRBroker, IBKRUnavailable
from ..memory import get_store
from ..memory import performance as perf_compute
from ..schemas.memory import PerformanceRecord
from . import analytics

log = logging.getLogger("ats.trader.performance")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record_snapshot(cycle_id: str = "") -> PerformanceRecord | None:
    """Read live portfolio + P&L + fills, compute the record (with drawdown/win-rate),
    persist. Returns None if TWS is unreachable."""
    from ..config import get_config

    store = get_store()
    try:
        broker = IBKRBroker(sector_by_symbol={t.symbol: t.sector for t in get_config().app.tickers})
        portfolio = broker.get_portfolio()
        fills = broker.get_fills()
    except IBKRUnavailable as exc:
        log.warning("performance snapshot skipped: %s", exc)
        return None

    store.upsert_fills(fills)
    record = perf_compute.compute(
        cycle_id=cycle_id or f"snap-{_now():%Y%m%d}", as_of=_now(), portfolio=portfolio,
        previous=store.last_performance(), order_results=[],
        fallback_net_liq=get_config().app.account.net_liquidation_usd)

    # Backfill the analytic fields from the full history + accumulated fills.
    hist = store.performance_history(limit=250) + [record]
    stats = analytics.trade_stats(store.recent_fills(limit=2000))
    record.win_rate = stats["win_rate"]
    record.profit_factor = stats["profit_factor"]
    record.max_drawdown = analytics.max_drawdown_pct(hist)
    store.save_performance(record)
    return record


def report(days: int = 30) -> dict:
    """History + full analytics (returns/drawdown/win-rate/profit-factor/benchmark)."""
    store = get_store()
    hist = store.performance_history(limit=max(days, 2))
    fills = store.recent_fills(limit=2000)
    benchmark = _benchmark_closes(len(hist))
    return {"history": hist, "analytics": analytics.summarize(hist, fills, benchmark)}


def _benchmark_closes(n: int) -> dict[str, list[float]]:
    """SPY/QQQ close series over roughly the same number of sessions."""
    out: dict[str, list[float]] = {}
    try:
        from ..data import market_data
        from ..schemas.market import Ticker

        for sym in ("SPY", "QQQ"):
            snap = market_data.fetch_snapshot(Ticker(symbol=sym))
            closes = [b.close for b in snap.history][-max(n, 2):]
            if len(closes) >= 2:
                out[sym] = closes
    except Exception as exc:  # noqa: BLE001 - benchmark is best-effort
        log.info("benchmark fetch skipped: %s", exc)
    return out
