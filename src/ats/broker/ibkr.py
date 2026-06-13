"""IBKR (paper) broker via ib_async.

Connects to a local TWS / IB Gateway paper account. Two read paths feed the risk
manager (portfolio) and one write path serves the Trader (place_order). Every
method degrades loudly: if TWS is down or the API is disabled, callers get an
IBKRUnavailable they can catch and fall back from — the cycle never hard-crashes.

TWS setup: File ▸ Global Config ▸ API ▸ Settings → enable "ActiveX and Socket
Clients", port 7497 (paper), and trust 127.0.0.1. TWS auto-logs-out daily, so a
probe (`ats ibkr`) before a live run is wise.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone

from ..config import get_config
from ..schemas.decision import TradeDecision
from ..schemas.memory import TradeLogEntry
from ..schemas.portfolio import ExposureBreakdown, PortfolioSnapshot, Position

log = logging.getLogger("ats.broker")


class IBKRUnavailable(RuntimeError):
    """Raised when TWS/Gateway cannot be reached or the API is disabled."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class IBKRBroker:
    def __init__(self, host: str | None = None, port: int | None = None,
                 client_id: int | None = None, sector_by_symbol: dict[str, str] | None = None):
        s = get_config().secrets
        self.host = host or s.ibkr_host
        self.port = port or s.ibkr_port
        self.client_id = client_id or s.ibkr_client_id
        self.sector_by_symbol = sector_by_symbol or {}
        self._ib = None

    # --- connection ------------------------------------------------------ #
    @contextmanager
    def session(self, timeout: float = 6.0):
        """Connect for the duration of the block, then disconnect."""
        try:
            from ib_async import IB
        except ImportError as exc:  # pragma: no cover
            raise IBKRUnavailable("ib_async not installed (pip install ib_async)") from exc

        ib = IB()
        try:
            ib.connect(self.host, self.port, clientId=self.client_id, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            raise IBKRUnavailable(
                f"cannot reach IBKR at {self.host}:{self.port} (is TWS running with API enabled?): {exc}"
            ) from exc
        self._ib = ib
        try:
            yield ib
        finally:
            ib.disconnect()
            self._ib = None

    # --- reads ----------------------------------------------------------- #
    def get_portfolio(self) -> PortfolioSnapshot:
        with self.session() as ib:
            summary = {av.tag: av.value for av in ib.accountSummary()}
            items = ib.portfolio()
            net_liq = float(summary.get("NetLiquidation", 0) or 0)
            cash = float(summary.get("TotalCashValue", 0) or 0)
            gross = float(summary.get("GrossPositionValue", 0) or 0)

            positions: list[Position] = []
            for it in items:
                sym = it.contract.symbol
                mv = float(it.marketValue)
                positions.append(Position(
                    symbol=sym,
                    sector=self.sector_by_symbol.get(sym, "unknown"),
                    qty=float(it.position),
                    avg_cost=float(it.averageCost),
                    market_price=float(it.marketPrice),
                    market_value=mv,
                    unrealized_pnl=float(it.unrealizedPNL),
                    weight=(mv / net_liq) if net_liq else 0.0,
                ))

            exposure = ExposureBreakdown()
            for p in positions:
                exposure.by_ticker[p.symbol] = p.weight
                exposure.by_sector[p.sector] = exposure.by_sector.get(p.sector, 0.0) + p.weight

            return PortfolioSnapshot(
                as_of=_now(),
                account_id=get_config().secrets.ibkr_account or (items[0].account if items else ""),
                net_liquidation=net_liq, cash=cash, gross_exposure=gross,
                net_exposure=gross, leverage=(gross / net_liq) if net_liq else 0.0,
                positions=positions, exposure=exposure,
            )

    # --- writes ---------------------------------------------------------- #
    def place_orders(self, items: list[tuple[TradeDecision, float]], cycle_id: str,
                     wait: float = 3.0) -> list[TradeLogEntry]:
        """Submit a batch of orders in a single session; one log entry each."""
        if not items:
            return []
        with self.session() as ib:
            self._last_trades = []
            entries = [self._submit(ib, d, qty, cycle_id) for d, qty in items]
            ib.sleep(wait)  # let the paper engine ack/fill
            for e, (_, _), trade in zip(entries, items, self._last_trades):
                if trade is None:
                    continue
                st = trade.orderStatus
                e.order_id = str(trade.order.orderId)
                e.status = _map_status(st.status)
                if st.filled and st.avgFillPrice:
                    e.avg_fill_price = float(st.avgFillPrice)
                    e.filled_at = _now()
            return entries

    def place_order(self, decision: TradeDecision, qty: float, cycle_id: str,
                    wait: float = 3.0) -> TradeLogEntry:
        return self.place_orders([(decision, qty)], cycle_id, wait)[0]

    def _submit(self, ib, decision: TradeDecision, qty: float, cycle_id: str) -> TradeLogEntry:
        from ib_async import LimitOrder, MarketOrder, Stock

        entry = TradeLogEntry(order_id="", cycle_id=cycle_id, symbol=decision.symbol,
                              action=decision.action, qty=qty, order_type=decision.order_type,
                              limit_price=decision.limit_price, status="submitted",
                              submitted_at=_now(), rationale=decision.rationale)
        if qty <= 0:
            entry.status = "rejected"
            entry.error = "non-positive quantity"
            self._last_trades.append(None)
            return entry

        side = "BUY" if decision.action in ("buy", "add") else "SELL"
        contract = Stock(decision.symbol, "SMART", "USD")
        ib.qualifyContracts(contract)
        order = (LimitOrder(side, qty, decision.limit_price)
                 if decision.order_type == "limit" and decision.limit_price
                 else MarketOrder(side, qty))
        trade = ib.placeOrder(contract, order)
        self._last_trades.append(trade)
        return entry


def _map_status(status: str) -> str:
    s = (status or "").lower()
    if s == "filled":
        return "filled"
    if s in ("submitted", "presubmitted", "pendingsubmit"):
        return "submitted"
    if "partial" in s:
        return "partial"
    if s in ("cancelled", "apicancelled", "inactive"):
        return "cancelled"
    return "submitted"
