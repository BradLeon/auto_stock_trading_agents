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
import os
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
        cfg = get_config()
        s = cfg.secrets
        br = cfg.app.broker          # settings.yaml [broker] overrides .env defaults
        self.host = host or s.ibkr_host
        self.port = port or br.port or s.ibkr_port
        # Distinct client_id per PROCESS: serve (approval execution), the scheduler,
        # and ad-hoc CLI all connect independently — sharing one id (12) makes a
        # second connection kick the first (IBKR error 326 "in use" + 1100
        # "connectivity lost") and drop orders mid-execution. An explicit client_id
        # (tests / pinned callers) still wins; otherwise offset the config base by
        # the pid so concurrent connections never collide. Same id within a process
        # (get_fills must see place_orders' fills).
        base = br.client_id or s.ibkr_client_id or 1
        self.client_id = client_id if client_id is not None else base + (os.getpid() % 80) + 1
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
            opt_contracts: list[tuple[Position, object]] = []   # (position, ib contract) for greeks
            for it in items:
                sym = it.contract.symbol
                mv = float(it.marketValue)
                sec_type = getattr(it.contract, "secType", "STK") or "STK"
                pos = Position(
                    symbol=sym,
                    sector=self.sector_by_symbol.get(sym, "unknown"),
                    sec_type=sec_type,
                    qty=float(it.position),
                    avg_cost=float(it.averageCost),
                    market_price=float(it.marketPrice),
                    market_value=mv,
                    unrealized_pnl=float(it.unrealizedPNL),
                    weight=(mv / net_liq) if net_liq else 0.0,
                )
                if sec_type == "OPT":
                    c = it.contract
                    pos.underlying = sym
                    pos.right = (getattr(c, "right", "") or "")[:1].upper() or None
                    try:
                        pos.strike = float(getattr(c, "strike", 0) or 0) or None
                    except (TypeError, ValueError):
                        pos.strike = None
                    pos.expiry = (getattr(c, "lastTradeDateOrContractMonth", "") or "") or None
                    try:
                        pos.multiplier = float(getattr(c, "multiplier", 0) or 0) or 100.0
                    except (TypeError, ValueError):
                        pos.multiplier = 100.0
                    opt_contracts.append((pos, c))
                positions.append(pos)

            # --- IBKR model greeks (authoritative; BSM fallback if unavailable) ------
            self._fetch_option_greeks(ib, opt_contracts)

            # --- IBKR authoritative margin (default accountSummary carries these tags) --
            init_m = _fnum(summary.get("InitMarginReq"))
            maint_m = _fnum(summary.get("MaintMarginReq"))
            excess_l = _fnum(summary.get("ExcessLiquidity"))
            bpower = _fnum(summary.get("BuyingPower"))
            avail = _fnum(summary.get("AvailableFunds"))
            margin_source = "ibkr" if init_m else None

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
                init_margin=init_m, maint_margin=maint_m, excess_liquidity=excess_l,
                buying_power=bpower, available_funds=avail, margin_source=margin_source,
            )

    def _fetch_option_greeks(self, ib, opt_contracts: list) -> None:
        """Stream IBKR model greeks (genericTick 106) for each OPT contract; write onto the
        Position (greeks_source='ibkr'). Best-effort: any failure leaves fields None so the
        risk layer's BSM fallback takes over. Never raises — must not break get_portfolio."""
        if not opt_contracts:
            return
        try:
            tickers = []
            for pos, c in opt_contracts:
                try:
                    t = ib.reqMktData(c, "106", False, False)
                    tickers.append((pos, t))
                except Exception as exc:  # noqa: BLE001
                    log.warning("reqMktData greeks failed for %s: %s", pos.symbol, exc)
            if not tickers:
                return
            ib.sleep(4.0)   # let streaming modelGreeks ticks land
            for pos, t in tickers:
                mg = getattr(t, "modelGreeks", None)
                if mg is None:
                    continue
                d = _fnum(getattr(mg, "delta", None))
                if d is None:
                    continue   # no usable computation → leave for BSM fallback
                pos.delta = d
                pos.gamma = _fnum(getattr(mg, "gamma", None))
                pos.vega = _fnum(getattr(mg, "vega", None))
                pos.theta = _fnum(getattr(mg, "theta", None))
                pos.iv = _fnum(getattr(mg, "impliedVol", None))
                pos.underlying_price = _fnum(getattr(mg, "undPrice", None))
                pos.greeks_source = "ibkr"
            for _, c in opt_contracts:
                try:
                    ib.cancelMktData(c)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            log.warning("option greeks fetch skipped: %s", exc)

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

    def cancel_all(self, symbol: str | None = None) -> list[str]:
        """Cancel open orders (optionally filtered by symbol). Returns cancelled ids."""
        with self.session() as ib:
            cancelled = []
            for t in ib.openTrades():
                if symbol and t.contract.symbol != symbol.upper():
                    continue
                ib.cancelOrder(t.order)
                cancelled.append(str(t.order.orderId))
            if cancelled:
                ib.sleep(1.5)
            return cancelled

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
            if not ib.qualifyContracts(contract):
                # Never send an order on an unverified contract. Error 200 on a
                # normal ticker usually means TWS lost its IB-server link
                # (nightly restart / maintenance window) — not a bad symbol.
                entry.status = "rejected"
                entry.error = ("contract not qualified — check symbol, or TWS "
                               "disconnected from IB servers (Error 1100/200)")
                self._last_trades.append(None)
                return entry
            order = (LimitOrder(side, qty, decision.limit_price)
                     if decision.order_type == "limit" and decision.limit_price
                     else MarketOrder(side, qty))
            order.tif = decision.time_in_force          # DAY/GTC (avoid preset TIF warning)
            trade = ib.placeOrder(contract, order)
            self._last_trades.append(trade)
        except Exception as exc:  # noqa: BLE001 - bad symbol / rejected contract must not escape
            log.warning("order submit failed for %s: %s", decision.symbol, exc)
            entry.status = "error"
            entry.error = str(exc)
            self._last_trades.append(None)
        return entry


def _fnum(v) -> float | None:
    """Parse an IBKR string/number tag to float; None on missing/blank/NaN."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None   # filter NaN


def _map_status(status: str) -> str:
    s = (status or "").lower()
    if s == "filled":
        return "filled"
    if s in ("submitted", "presubmitted", "pendingsubmit"):
        return "submitted"
    if "partial" in s:
        return "partial"
    if s in ("cancelled", "apicancelled"):
        return "cancelled"
    if s in ("inactive", "validationerror"):   # IBKR rejected (bad TIF, closed mkt, etc.)
        return "rejected"
    return "submitted"
