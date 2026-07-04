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

            # Account-level P&L in the same session (daily/realized).
            daily_pnl = realized_pnl = 0.0
            acct = get_config().secrets.ibkr_account or (
                ib.managedAccounts()[0] if ib.managedAccounts() else "")
            if acct:
                pnl = ib.reqPnL(acct)
                ib.sleep(2.0)
                dv, rv = getattr(pnl, "dailyPnL", None), getattr(pnl, "realizedPnL", None)
                daily_pnl = float(dv) if isinstance(dv, (int, float)) and dv == dv else 0.0
                realized_pnl = float(rv) if isinstance(rv, (int, float)) and rv == rv else 0.0

            return PortfolioSnapshot(
                as_of=_now(),
                account_id=acct or (items[0].account if items else ""),
                net_liquidation=net_liq, cash=cash, gross_exposure=gross,
                net_exposure=gross, leverage=(gross / net_liq) if net_liq else 0.0,
                daily_pnl=daily_pnl, realized_pnl=realized_pnl,
                positions=positions, exposure=exposure,
            )

    def get_pnl(self, account: str = "") -> dict:
        """Account-level P&L: {daily_pnl, unrealized_pnl, realized_pnl}. 0-filled on miss."""
        out = {"daily_pnl": 0.0, "unrealized_pnl": 0.0, "realized_pnl": 0.0}
        with self.session() as ib:
            acct = account or get_config().secrets.ibkr_account
            if not acct:
                accts = ib.managedAccounts()
                acct = accts[0] if accts else ""
            if not acct:
                return out
            pnl = ib.reqPnL(acct)
            ib.sleep(2.0)   # let the subscription deliver a snapshot
            for field, key in (("dailyPnL", "daily_pnl"), ("unrealizedPnL", "unrealized_pnl"),
                               ("realizedPnL", "realized_pnl")):
                v = getattr(pnl, field, None)
                if isinstance(v, (int, float)) and v == v:   # filter NaN
                    out[key] = float(v)
            return out

    def get_fills(self) -> list[dict]:
        """Executed fills with per-trade realized P&L (IBKR's authoritative source)."""
        out: list[dict] = []
        with self.session() as ib:
            ib.reqExecutions()
            ib.sleep(1.5)
            for f in ib.fills():
                ex, cr = f.execution, f.commissionReport
                rp = getattr(cr, "realizedPNL", None)
                out.append({
                    "exec_id": ex.execId, "symbol": f.contract.symbol,
                    "side": ex.side, "shares": float(ex.shares), "price": float(ex.price),
                    "time": ex.time.isoformat() if ex.time else "",
                    "realized_pnl": float(rp) if isinstance(rp, (int, float)) and rp == rp else None,
                    "commission": float(getattr(cr, "commission", 0) or 0),
                    "order_id": str(ex.orderId),
                })
        return out

    def open_orders(self) -> list[dict]:
        with self.session() as ib:
            ib.reqAllOpenOrders()
            ib.sleep(1.0)
            return [{"order_id": str(t.order.orderId), "symbol": t.contract.symbol,
                     "action": t.order.action, "qty": float(t.order.totalQuantity),
                     "type": t.order.orderType, "status": t.orderStatus.status}
                    for t in ib.openTrades()]

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

        try:
            side = "BUY" if decision.action in ("buy", "add") else "SELL"
            contract = Stock(decision.symbol, "SMART", "USD")
            ib.qualifyContracts(contract)
            order = (LimitOrder(side, qty, decision.limit_price)
                     if decision.order_type == "limit" and decision.limit_price
                     else MarketOrder(side, qty))
            trade = ib.placeOrder(contract, order)
            self._last_trades.append(trade)
        except Exception as exc:  # noqa: BLE001 - bad symbol / rejected contract must not escape
            log.warning("order submit failed for %s: %s", decision.symbol, exc)
            entry.status = "error"
            entry.error = str(exc)
            self._last_trades.append(None)
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
