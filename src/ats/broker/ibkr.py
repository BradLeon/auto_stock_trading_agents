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

import hashlib
import json
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from ..config import get_config
from ..execution import broker_write_guard as guard
from ..execution.broker_write_guard import check_broker_write, check_grant
from ..schemas.decision import TradeDecision, broker_side
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
        self._expected_account = str(s.ibkr_account or "").strip()

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
    def get_stock_execution_metadata(self, symbol: str, *, currency: str) -> dict:
        """Confirmed instrument identity and lot/tick precision, without defaults."""
        from ib_async import Stock

        with self.session() as ib:
            contracts = ib.qualifyContracts(Stock(symbol, "SMART", currency))
            if len(contracts) != 1:
                raise IBKRUnavailable("execution_instrument_ambiguous")
            contract = contracts[0]
            details = ib.reqContractDetails(contract)
            if len(details) != 1 or contract.symbol != symbol or contract.secType != "STK":
                raise IBKRUnavailable("execution_instrument_mismatch")
            detail = details[0]
            return {"symbol": contract.symbol, "currency": contract.currency,
                    "min_size": float(detail.minSize),
                    "size_increment": float(detail.sizeIncrement),
                    "min_tick": float(detail.minTick)}

    def get_execution_quote(self, symbol: str, *, currency: str, metadata: dict) -> dict:
        """BidAsk ticks carry SERVER time; ticker.time is only local receipt time."""
        from ib_async import Stock

        with self.session() as ib:
            contracts = ib.qualifyContracts(Stock(symbol, "SMART", currency))
            if len(contracts) != 1 or contracts[0].symbol != symbol \
                    or contracts[0].currency != currency:
                raise IBKRUnavailable("execution_instrument_mismatch")
            contract = contracts[0]
            ticker = ib.reqTickByTickData(contract, "BidAsk", 0, False)
            tick = None
            try:
                for _ in range(20):
                    ib.sleep(0.1)
                    if ticker.tickByTicks:
                        tick = ticker.tickByTicks[-1]
                        break
                if tick is None or not hasattr(tick, "bidPrice"):
                    raise IBKRUnavailable("execution_quote_source_timestamp_missing")
                return {**metadata, "source": "ibkr:tick_by_tick_bid_ask",
                        "source_as_of": tick.time, "queried_at": _now(),
                        "price_kind": "bid_ask", "bid": tick.bidPrice, "ask": tick.askPrice,
                        "session": "regular", "market_data_mode": (
                            "live" if ticker.marketDataType == 1 else "delayed_or_unknown"),
                        "adjusted": False, "source_precision": "tick"}
            finally:
                ib.cancelTickByTickData(contract, "BidAsk")

    def get_portfolio(self) -> PortfolioSnapshot:
        with self.session() as ib:
            account_values = list(ib.accountSummary())
            base_currency, exchange_rates, summary = _account_value_context(account_values)
            items = ib.portfolio()
            net_liq = float(summary.get("NetLiquidation", 0) or 0)
            cash = float(summary.get("TotalCashValue", 0) or 0)
            gross = float(summary.get("GrossPositionValue", 0) or 0)

            positions: list[Position] = []
            opt_contracts: list[tuple[Position, object]] = []   # (position, ib contract) for greeks
            for it in items:
                sym = it.contract.symbol
                currency = (getattr(it.contract, "currency", "") or base_currency).upper()
                fx = _fx_rate(currency, base_currency, exchange_rates, sym)
                mv_local = float(it.marketValue)
                upnl_local = float(it.unrealizedPNL)
                mv = mv_local * fx
                sec_type = getattr(it.contract, "secType", "STK") or "STK"
                pos = Position(
                    symbol=sym,
                    sector=self.sector_by_symbol.get(sym, "unknown"),
                    sec_type=sec_type,
                    qty=float(it.position),
                    avg_cost=float(it.averageCost),
                    market_price=float(it.marketPrice),
                    market_value=mv,
                    unrealized_pnl=upnl_local * fx,
                    weight=(mv / net_liq) if net_liq else 0.0,
                    currency=currency, fx_rate_to_base=fx,
                    market_value_local=mv_local, unrealized_pnl_local=upnl_local,
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
                base_currency=base_currency, exchange_rates=exchange_rates,
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
                    # Durable identities: orderId is a per-client sequence that TWS
                    # resets, so it cannot be joined on across days. permId is global
                    # and permanent; orderRef is our own tag, echoed back on every
                    # execution, and is what separates our orders from manual ones.
                    "perm_id": str(getattr(ex, "permId", "") or ""),
                    "order_ref": str(getattr(ex, "orderRef", "") or ""),
                })
        return out

    def completed_orders(self) -> list[dict]:
        """Terminal state of today's orders, including ones that never filled.

        `place_orders` only polls for 3 seconds, so anything settling later is left
        as 'submitted' forever — including DAY orders the exchange cancels at the
        close. This is the read that resolves them.
        """
        out: list[dict] = []
        with self.session() as ib:
            ib.reqCompletedOrders(apiOnly=False)
            ib.sleep(1.5)
            for t in list(ib.trades()) + list(getattr(ib, "completedTrades", lambda: [])()):
                st, o = t.orderStatus, t.order
                out.append({
                    "order_id": str(o.orderId), "perm_id": str(getattr(o, "permId", "") or ""),
                    "order_ref": str(getattr(o, "orderRef", "") or ""),
                    "symbol": t.contract.symbol, "status": _map_status(st.status),
                    "filled": float(st.filled or 0),
                    "avg_fill_price": float(st.avgFillPrice) if st.avgFillPrice else None,
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
    def connected_account(self, ib=None) -> str:
        """Select an explicitly expected account from broker-reported accounts."""
        session = ib if ib is not None else self._ib
        try:
            accounts = [str(a).strip() for a in session.managedAccounts()]
        except Exception as exc:  # noqa: BLE001 - unconfirmed session identity must refuse
            guard.refuse(guard.REASON_ACCOUNT_MISMATCH, operation="placeOrder",
                         caller="IBKRBroker.connected_account",
                         detail=f"actual session accounts unavailable: {type(exc).__name__}")
        expected = getattr(self, "_expected_account", "")
        if not accounts or any(not a for a in accounts) or \
                (expected and expected not in accounts) or \
                (not expected and len(set(accounts)) != 1):
            guard.refuse(guard.REASON_ACCOUNT_MISMATCH, operation="placeOrder",
                         caller="IBKRBroker.connected_account",
                         detail="expected account absent or actual session account ambiguous")
        return expected or accounts[0]

    @contextmanager
    def _submission_gate(self, ib, *, operation: str, detail: str):
        """Hold route and local capability stable through the broker write."""
        from ..execution import route_registry as rr
        from ..execution.route_arbitration import authority_lock

        target = rr.default_registry_path()
        entered = False
        try:
            with authority_lock(target), guard._STATE.lock:
                check_broker_write(operation=operation, caller="IBKRBroker", detail=detail)
                # Do not recreate an absent/corrupt authority on the submit path.
                if not os.path.isfile(target):
                    raise rr.RouteRegistryError("route authority file is absent")
                state = rr.read_state(target)
                account = self.connected_account(ib)
                # Supported individual-account identities only; unknown formats
                # are never inferred from the connection port or configuration.
                environment = ("paper" if re.fullmatch(r"DU[0-9]+", account) else
                               "live" if re.fullmatch(r"U[0-9]+", account) else "")
                check_grant(operation=operation, caller="IBKRBroker._submit",
                            state_reader=lambda: state,
                            freeze_reader=lambda: rr.read_freeze(target),
                            account=account, environment=environment,
                            require_binding=True, detail=f"pid={os.getpid()} {detail}")
                entered = True
                yield state
        except (OSError, sqlite3.Error, rr.RouteRegistryError) as exc:
            if entered and isinstance(exc, OSError):
                # A network timeout after handoff is a broker uncertainty, not
                # an unreadable route. Its durable receipt must stay unknown.
                raise
            guard.refuse(guard.REASON_AUTHORITY_UNREADABLE, operation=operation,
                         caller="IBKRBroker._submit", detail=f"{type(exc).__name__}: {exc}")

    def place_orders(self, items: list[tuple[TradeDecision, float]], cycle_id: str,
                     wait: float = 3.0, revision_no: int = 0,
                     chain: dict | None = None, execution_check=None,
                     order_sequences=None) -> list[TradeLogEntry]:
        """Submit a batch of orders in a single session; one log entry each.

        `revision_no` participates in the per-order identity (task 7.7): orders
        are sequenced by their position within the revision's order list.
        `chain` (task 2.1) carries decision_hash/approval_id from the verified
        authorization onto every submitted entry.

        Two gates run before any order is built:

        - the process-level prohibition (task 1.1), so a shadow or isolated run
          cannot write even if the caller omitted `dry_run`;
        - the write grant (task 2.2/2.3), re-verified against the authoritative
          route registry on EVERY call, so a grant that predates a cutover stops
          working and a grant for one account cannot be used against another.

        Early batch checks avoid connecting without authority. Every individual
        write also rechecks under the shared mutex; a batch may stop partway if
        frozen, with earlier intents durably recorded for reconciliation.
        """
        if not items:
            return []
        check_broker_write(operation="place_orders", caller="IBKRBroker.place_orders",
                           detail=f"cycle_id={cycle_id} revision_no={revision_no} "
                                  f"orders={len(items)}")
        from ..execution.route_registry import read_freeze, read_state

        grant = guard.active_grant()
        check_grant(operation="place_orders", caller="IBKRBroker.place_orders",
                    state_reader=read_state, freeze_reader=read_freeze,
                    account=grant.account if grant else "",
                    detail=f"pid={os.getpid()} cycle_id={cycle_id}")
        chain = chain or {}
        with self.session() as ib:
            self._last_trades = []
            with self._submission_gate(ib, operation="place_orders", detail=cycle_id):
                pass
            entries = [self._submit(ib, d, qty, cycle_id, revision_no, seq, chain,
                                    execution_check=execution_check)
                       for seq, (d, qty) in zip(order_sequences or range(len(items)), items)]
            ib.sleep(wait)  # let the paper engine ack/fill
            for e, (_, _), trade in zip(entries, items, self._last_trades):
                if trade is None:
                    continue
                st = trade.orderStatus
                e.order_id = str(trade.order.orderId)
                # permId is assigned by IBKR at acknowledgement and is globally
                # permanent — the only id safe to join executions on later.
                e.perm_id = str(getattr(trade.order, "permId", "") or "")
                e.status = _map_status(st.status)
                if st.filled and st.avgFillPrice:
                    e.avg_fill_price = float(st.avgFillPrice)
                    e.filled_at = _now()
                from ..execution import route_registry as rr
                rr.record_submission_result(
                    self._intent_id(cycle_id, revision_no, e.order_seq), e.status, e.order_id)
            return entries

    def place_order(self, decision: TradeDecision, qty: float, cycle_id: str,
                    wait: float = 3.0) -> TradeLogEntry:
        return self.place_orders([(decision, qty)], cycle_id, wait)[0]

    def cancel_all(self, symbol: str | None = None) -> list[str]:
        """Cancel open orders (optionally filtered by symbol). Returns cancelled ids."""
        check_broker_write(operation="cancel_all", caller="IBKRBroker.cancel_all")
        with self.session() as ib:
            cancelled = []
            for t in ib.openTrades():
                if symbol and t.contract.symbol != symbol.upper():
                    continue
                with self._submission_gate(ib, operation="cancelOrder", detail=str(t.order.orderId)) as state:
                    if t.order.account != state.account:
                        guard.refuse(guard.REASON_ACCOUNT_MISMATCH, operation="cancelOrder",
                                     caller="IBKRBroker.cancel_all", detail="order account differs")
                    ib.cancelOrder(t.order)
                cancelled.append(str(t.order.orderId))
            if cancelled:
                ib.sleep(1.5)
            return cancelled

    def _submit(self, ib, decision: TradeDecision, qty: float, cycle_id: str,
                revision_no: int = 0, seq: int = 0,
                chain: dict | None = None, *, execution_check=None) -> TradeLogEntry:
        from ib_async import LimitOrder, MarketOrder, Stock

        check_broker_write(operation="placeOrder", caller="IBKRBroker._submit",
                           symbol=decision.symbol, quantity=qty)

        chain = chain or {}
        entry = TradeLogEntry(order_id="", cycle_id=cycle_id, symbol=decision.symbol,
                              action=decision.action, qty=qty, order_type=decision.order_type,
                              limit_price=decision.limit_price, status="submitted",
                              submitted_at=_now(), rationale=decision.rationale,
                              revision_no=revision_no, order_seq=seq,
                              decision_hash=chain.get("decision_hash", ""),
                              approval_id=chain.get("approval_id", ""))
        if qty <= 0:
            entry.status = "rejected"
            entry.error = "non-positive quantity"
            self._last_trades.append(None)
            return entry

        try:
            side = broker_side(decision.action, where=f"IBKRBroker.place({decision.symbol})")
            if side is None:      # `hold` places no order at all
                entry.status = "rejected"
                entry.error = f"action {decision.action!r} places no order"
                self._last_trades.append(None)
                return entry
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
            # Tag the order so its executions can be recognised as ours. reqExecutions
            # returns the whole ACCOUNT's fills, including ones placed by hand in TWS;
            # without a tag the only link is orderId, which TWS resets on restart and
            # therefore cannot be joined on across days. IBKR echoes orderRef back on
            # every execution. Capped at 60 chars — IBKR silently truncates long refs.
            order.orderRef = order_ref(cycle_id, revision_no, seq, decision.symbol)
            from ..execution import route_registry as rr

            with self._submission_gate(ib, operation="placeOrder",
                                       detail=f"cycle_id={cycle_id} revision={revision_no} seq={seq}") as state:
                if decision.execution_basis:
                    if execution_check is None:
                        raise ValueError("normalized_order_execution_check_required")
                    execution_check(decision, qty)
                order.account = state.account
                intent_id = self._intent_id(cycle_id, revision_no, seq)
                payload = {"decision": decision.model_dump(mode="json"), "qty": qty,
                           "account": order.account, "chain": chain}
                payload_hash = hashlib.sha256(json.dumps(
                    payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
                rr.reserve_submission(intent_id, payload_hash, state,
                                      order_ref=order.orderRef, cycle_id=cycle_id,
                                      revision_no=revision_no, sequence=seq)
                trade = ib.placeOrder(contract, order)
                # Even an immediate response may not be terminal. Leave unknown
                # if the call raises or the process dies before observation.
                if getattr(trade.order, "account", "") != state.account:
                    guard.refuse(guard.REASON_ACCOUNT_MISMATCH, operation="placeOrder",
                                 caller="IBKRBroker._submit", detail="broker echoed a different order account")
                rr.record_submission_result(intent_id, _map_status(trade.orderStatus.status),
                                             str(trade.order.orderId))
            entry.order_ref = order.orderRef
            self._last_trades.append(trade)
        except guard.BrokerWriteProhibited:
            raise
        except Exception as exc:  # noqa: BLE001 - bad symbol / rejected contract must not escape
            log.warning("order submit failed for %s: %s", decision.symbol, exc)
            entry.status = "error"
            entry.error = str(exc)
            self._last_trades.append(None)
        return entry

    @staticmethod
    def _intent_id(cycle_id: str, revision_no: int, seq: int) -> str:
        # Full identity, not the broker's truncated orderRef. Deliberately omit
        # route/generation/account/symbol so none permits a replay or mutation.
        if not cycle_id.strip():
            guard.refuse("order_identity_missing", operation="placeOrder",
                         caller="IBKRBroker._submit", detail="cycle_id is required")
        return hashlib.sha256(json.dumps(
            [cycle_id, revision_no, seq]).encode()).hexdigest()


def order_ref(cycle_id: str, revision_no: int, seq: int, symbol: str) -> str:
    """Our tag on an outgoing order: `ats:<cycle_id>:r<rev>:<seq>:<SYMBOL>`.

    The `ats:` prefix is what distinguishes a system order from a manual TWS trade in
    the account-wide execution feed. Task 7.7: the tag is derived from cycle +
    revision + sequence within the revision, so two revisions of one cycle with a
    same-symbol same-direction order get DIFFERENT identities. IBKR silently
    truncates long orderRefs — callers keep the result <= 60 chars.
    """
    return f"ats:{cycle_id}:r{revision_no}:{seq}:{symbol.upper()}"[:60]


def _fnum(v) -> float | None:
    """Parse an IBKR string/number tag to float; None on missing/blank/NaN."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None   # filter NaN


def _account_value_context(values) -> tuple[str, dict[str, float], dict[str, str]]:
    """Select base-currency account totals and retain FX rates for portfolio conversion.

    IBKR portfolio-item prices/values are denominated in the contract currency, while
    account-summary totals such as NetLiquidation are in the account base currency.
    Never collapse account values by tag before inspecting their currency.
    """
    base_currency = next(
        ((getattr(v, "currency", "") or "").upper() for v in values
         if getattr(v, "tag", "") == "NetLiquidation"
         and (getattr(v, "currency", "") or "").upper() not in ("", "BASE")),
        "USD",
    )
    rates: dict[str, float] = {base_currency: 1.0, "BASE": 1.0}
    for v in values:
        if getattr(v, "tag", "") != "ExchangeRate":
            continue
        currency = (getattr(v, "currency", "") or "").upper()
        rate = _fnum(getattr(v, "value", None))
        if currency and rate is not None:
            rates[currency] = rate

    summary: dict[str, str] = {}
    for v in values:
        currency = (getattr(v, "currency", "") or "").upper()
        if currency in (base_currency, "BASE", ""):
            summary[getattr(v, "tag", "")] = getattr(v, "value", "")
    return base_currency, rates, summary


def _fx_rate(currency: str, base_currency: str, rates: dict[str, float], symbol: str) -> float:
    rate = rates.get(currency)
    if rate is None:
        raise IBKRUnavailable(
            f"missing {currency}->{base_currency} exchange rate for {symbol}; "
            "refusing to mix local-currency market value with base-currency NAV")
    return rate


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
