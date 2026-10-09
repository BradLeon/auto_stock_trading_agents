"""Governed, order-bound execution prices shared by Risk and Trader."""
from __future__ import annotations

from ats.workflow.evaluation_clock import now as evaluation_now

import hashlib
import json
import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from .runtime.execution_prices import ExecutionPrice, session_context

POLICY_VERSION = "phase-f-execution-price-v1"
PURPOSES = {"preapproval_normalization", "approved_execution_check"}


class PriceUnavailable(ValueError):
    pass


def quote_ref(quote: ExecutionPrice) -> str:
    body = json.dumps(quote.model_dump(mode="json"), sort_keys=True, allow_nan=False)
    return "execution-quote:" + hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True)
class PriceRequest:
    consumer: str
    cycle_id: str
    purpose: str
    symbols: tuple[str, ...]
    currency: str
    identity: object


_REQUEST: ContextVar[PriceRequest | None] = ContextVar("execution_price_request", default=None)
_FIXED_QUOTES: ContextVar[dict | None] = ContextVar("review_execution_quotes", default=None)


@contextmanager
def price_request(consumer, orders, *, cycle_id, purpose, currency="USD", audit=None,
                  business_scope=None):
    from ..workflow.runtime_reads import bind_read, business_identity, current_read_context

    if consumer not in {"risk", "trader"} or purpose not in PURPOSES or not cycle_id:
        raise PermissionError("invalid_execution_price_purpose")
    symbols = tuple(sorted({d.symbol for d in orders if d.action != "hold"}))
    if not symbols:
        raise PermissionError("execution_price_empty_order_scope")
    if purpose == "approved_execution_check":
        from ..execution.authorization import build_authorization

        if audit is None:
            raise PermissionError("execution_price_approved_revision_required")
        auth = build_authorization(audit, cycle_id)
        revision = audit.latest_revision(cycle_id)
        approved = json.loads(revision["orders_json"])
        if [d.model_dump(mode="json") for d in orders] != approved:
            raise PermissionError("execution_price_order_revision_mismatch")
        if not auth.approval_id:
            raise PermissionError("execution_price_approval_required")
    parent = current_read_context()
    point = evaluation_now(UTC)
    scope = {"kind": "decision", "id": cycle_id, "entities": list(symbols),
             "time_range": {"start": (point - timedelta(minutes=5)).isoformat(),
                            "end": (point + timedelta(minutes=5)).isoformat()},
             "purpose": purpose, "currency": currency}
    if parent is not None:
        # Preserve the originating request's frozen entity/time identity. Each role
        # still gates its OWN domain/version/qualification, rather than borrowing Chief's.
        scope = {**parent.identity.scope, "purpose": purpose, "currency": currency}
        if not set(symbols) <= set(scope["entities"]):
            raise PermissionError("execution_price_order_escapes_business_scope")
    if business_scope is not None:
        supplied = business_scope.scope if hasattr(business_scope, "scope") else business_scope
        if supplied.get("purpose") != purpose or supplied.get("currency") != currency:
            raise PermissionError("execution_price_business_purpose_mismatch")
        scope = supplied
    identity = business_identity(consumer, kind=scope["kind"], scope_id=scope["id"],
                                 entities=scope["entities"], explicit=scope,
                                 event_id=scope.get("event_id", ""),
                                 event_version=scope.get("event_version", ""))
    with bind_read(identity):
        token = _REQUEST.set(PriceRequest(consumer, cycle_id, purpose, symbols, currency, identity))
        quotes = {d.execution_basis["quote_ref"]: d.execution_basis["quote"] for d in orders
                  if d.execution_basis.get("quote")}
        fixed_token = _FIXED_QUOTES.set(quotes if consumer == "risk" else None)
        try:
            yield
        finally:
            _FIXED_QUOTES.reset(fixed_token)
            _REQUEST.reset(token)


def assert_price_scope(consumer, scope):
    request = _REQUEST.get()
    if request is None or request.consumer != consumer:
        raise PermissionError("execution_price_order_binding_required")
    if (scope.get("purpose") != request.purpose or scope.get("cycle_id") != request.cycle_id
            or scope.get("currency") != request.currency
            or scope.get("entity") not in request.symbols
            or scope.get("kind") != "execution_price"):
        raise PermissionError("execution_price_scope_or_purpose_mismatch")
    return request


def runtime_quote(scope):
    """Risk consumes the SAME normalized quote, with its own read authority."""
    from .runtime.execution_prices import fetch_execution_price

    fixed = _FIXED_QUOTES.get()
    if fixed is not None:
        if scope.get("approval_quote_ref") not in fixed:
            raise PriceUnavailable("review_quote_missing")
        return fixed[scope["approval_quote_ref"]]
    return fetch_execution_price(scope["entity"], currency=scope["currency"],
                                 overnight=bool(scope.get("overnight")))


def validate_quote(quote, *, symbol, currency="USD", overnight=False, now=None):
    q = ExecutionPrice.model_validate(quote)
    point = now or evaluation_now(UTC)
    if q.schema_version != "runtime-execution-price-v1":
        raise PriceUnavailable("quote_schema_version_invalid")
    if any(t.tzinfo is None for t in (q.source_as_of, q.queried_at, point)):
        raise PriceUnavailable("quote_timestamp_timezone_missing")
    if q.symbol != symbol:
        raise PriceUnavailable("quote_symbol_mismatch")
    if q.currency != currency or currency != "USD":
        raise PriceUnavailable("quote_currency_mismatch")
    if not q.source or q.adjusted:
        raise PriceUnavailable("quote_source_or_adjustment_invalid")
    for value in (q.min_size, q.size_increment, q.min_tick):
        if not math.isfinite(value) or value <= 0:
            raise PriceUnavailable("instrument_precision_missing")
    if (q.source_as_of - q.queried_at).total_seconds() > 2 \
            or (q.source_as_of - point).total_seconds() > 2 \
            or (q.queried_at - point).total_seconds() > 2:
        raise PriceUnavailable("quote_timestamp_future")
    try:
        regular, last_close = session_context(point)
    except Exception as exc:
        raise PriceUnavailable("calendar_unavailable") from exc
    age = (point - q.source_as_of).total_seconds()
    if overnight:
        if (regular or q.price_kind != "previous_session_close"
                or q.session != "previous_completed_regular"
                or q.market_data_mode != "historical"
                or q.source_precision != "session_date"
                or q.source_as_of != last_close or age > 4 * 86400):
            raise PriceUnavailable("previous_session_close_invalid")
        values = [q.close]
    else:
        if not regular or q.session != "regular":
            raise PriceUnavailable("quote_outside_regular_session")
        if q.price_kind != "bid_ask" or q.market_data_mode != "live" or q.source_precision != "tick":
            raise PriceUnavailable("quote_not_current_bid_ask")
        if age > 30:
            raise PriceUnavailable("quote_stale")
        values = [q.bid, q.ask]
    if any(v is None or not math.isfinite(v) or v <= 0 for v in values):
        raise PriceUnavailable("quote_price_invalid")
    if not overnight and (q.ask < q.bid or (q.ask - q.bid) / ((q.ask + q.bid) / 2) > .01):
        raise PriceUnavailable("quote_spread_invalid")
    return q


def read_price(consumer, decision, *, overnight=False):
    from .consumer_api import read_input

    request = _REQUEST.get()
    if request is None:
        raise PermissionError("execution_price_order_binding_required")
    scope = {"kind": "execution_price", "entity": decision.symbol,
             "currency": request.currency, "cycle_id": request.cycle_id,
             "purpose": request.purpose, "overnight": overnight,
             "approval_quote_ref": decision.execution_basis.get("quote_ref")}
    packet = read_input(consumer, "MARKET_DATA", scope=scope)
    if packet.status != "complete":
        raise PriceUnavailable(";".join(packet.gaps))
    return validate_quote(packet.payload, symbol=decision.symbol, currency=request.currency,
                          overnight=overnight)


def directional_price(quote, action):
    from ..schemas.decision import action_direction

    return quote.close if quote.price_kind == "previous_session_close" else (
        quote.ask if action_direction(action) > 0 else quote.bid)


def normalize_orders(orders, *, cycle_id, currency="USD", overnight=False,
                     slippage_pct=.5, net_liquidation=0, business_scope=None):
    """Freeze executable fields BEFORE risk review. An unavailable order rejects the batch."""
    from ..schemas.decision import action_direction

    if not math.isfinite(slippage_pct) or slippage_pct < 0 or slippage_pct > 1:
        raise PriceUnavailable("limit_slippage_policy_invalid")
    normalized, notes = [], []
    with price_request("trader", orders, cycle_id=cycle_id,
                       purpose="preapproval_normalization", currency=currency,
                       business_scope=business_scope):
        for decision in orders:
            if decision.execution_basis:
                # A fresh re-review of a stale portfolio does not invent a new
                # order/quote revision. Reuse the same still-valid normalized input.
                basis = decision.execution_basis
                captured = ExecutionPrice.model_validate(basis["quote"])
                if quote_ref(captured) != basis.get("quote_ref") or basis.get("policy_version") != POLICY_VERSION:
                    raise PriceUnavailable("normalized_quote_binding_invalid")
                try:
                    validate_quote(captured, symbol=decision.symbol,
                                   currency=currency, overnight=overnight)
                except PriceUnavailable:
                    # Expired/session-changed input is refreshed BEFORE the next
                    # immutable revision and review, never inside execution.
                    from ..schemas.decision import TradeDecision

                    decision = TradeDecision.model_validate(basis["requested_order"])
                    if decision.execution_basis:
                        raise PriceUnavailable("recursive_normalization_input")
                else:
                    normalized.append(decision.model_copy(deep=True))
                    notes.append(f"{decision.symbol}: 复核同一规范订单；{basis['quote_ref']}")
                    continue
            quote = read_price("trader", decision, overnight=overnight)
            ref = directional_price(quote, decision.action)
            increasing = action_direction(decision.action) > 0
            limit = decision.limit_price
            if ((decision.order_type == "limit" and limit is None or overnight)
                    and (limit is None or decision.order_type == "market")):
                raw = Decimal(str(ref)) * (1 + Decimal(str(slippage_pct)) / 100
                                           * (1 if increasing else -1))
                tick = Decimal(str(quote.min_tick))
                limit = float((raw / tick).to_integral_value(
                    rounding=ROUND_DOWN if increasing else ROUND_UP) * tick)
            if limit is not None and (not math.isfinite(limit) or limit <= 0
                    or Decimal(str(limit)) % Decimal(str(quote.min_tick)) != 0):
                raise PriceUnavailable(f"{decision.symbol}:limit_precision_invalid")
            # Review and sizing use the worst permitted price, not a cheaper quote.
            cap_price = max(ref, limit or ref) if increasing else ref
            budget = decision.notional_usd
            if budget is None and decision.target_weight:
                budget = decision.target_weight * net_liquidation
            if budget is not None and (not math.isfinite(budget) or budget < 0):
                raise PriceUnavailable(f"{decision.symbol}:budget_invalid")
            step = Decimal(str(quote.size_increment))
            if decision.qty is not None:
                qty = abs(decision.qty)
                if not math.isfinite(qty) or Decimal(str(qty)) % step != 0:
                    raise PriceUnavailable(f"{decision.symbol}:quantity_precision_invalid")
            elif budget is not None:
                qty = float((Decimal(str(budget)) / Decimal(str(cap_price)) / step)
                            .to_integral_value(rounding=ROUND_DOWN) * step)
            else:
                raise PriceUnavailable(f"{decision.symbol}:quantity_or_budget_missing")
            if qty < quote.min_size:
                raise PriceUnavailable(f"{decision.symbol}:no_action_below_minimum_quantity")
            notional = qty * cap_price
            if budget is not None and notional > budget + 1e-8:
                raise PriceUnavailable(f"{decision.symbol}:quantity_exceeds_budget")
            basis = {"policy_version": POLICY_VERSION, "quote": quote.model_dump(mode="json"),
                     "requested_order": decision.model_dump(mode="json"),
                     "quote_ref": quote_ref(quote), "reference_price": ref,
                     "max_deviation": .01, "max_notional": notional,
                     "currency": currency, "defer_to_regular_session": overnight}
            normalized.append(decision.model_copy(update={
                "qty": qty, "notional_usd": notional, "limit_price": limit,
                "order_type": "limit" if overnight else decision.order_type,
                "execution_basis": basis}))
            notes.append(f"{decision.symbol}: {qty:g} 股；{quote.price_kind} 参考 {ref:g} "
                         f"{currency}，来源 {quote.source_as_of.isoformat()}；{basis['quote_ref']}")
    return normalized, notes


def check_execution(decision, qty, quote, *, now=None):
    """Recheck only; never resize or reprice an approved decision."""
    basis = decision.execution_basis
    if not basis or basis.get("policy_version") != POLICY_VERSION or decision.qty != qty:
        raise PriceUnavailable("approved_order_not_normalized")
    reference = ExecutionPrice.model_validate(basis["quote"])
    if basis.get("quote_ref") != quote_ref(reference):
        raise PriceUnavailable("approval_quote_hash_mismatch")
    quote = validate_quote(quote, symbol=decision.symbol, currency=basis["currency"], now=now)
    ref = directional_price(reference, decision.action)
    if ref is None or not math.isfinite(ref) or ref <= 0 or ref != basis.get("reference_price"):
        raise PriceUnavailable("approval_reference_mismatch")
    price = directional_price(quote, decision.action)
    threshold = min(.01, basis["max_deviation"])
    if not math.isfinite(threshold) or threshold < 0 or abs(price - ref) / ref > threshold + 1e-12:
        raise PriceUnavailable("execution_price_deviation_reapprove")
    from ..schemas.decision import action_direction

    if decision.order_type == "limit":
        if decision.limit_price is None:
            raise PriceUnavailable("approved_limit_missing")
        if (action_direction(decision.action) > 0 and price > decision.limit_price
                or action_direction(decision.action) < 0 and price < decision.limit_price):
            raise PriceUnavailable("execution_limit_not_marketable")
    if not math.isfinite(basis["max_notional"]) or basis["max_notional"] <= 0 \
            or qty * price > basis["max_notional"] + 1e-8:
        raise PriceUnavailable("execution_funds_exceeded_reapprove")
    if Decimal(str(qty)) % Decimal(str(quote.size_increment)) != 0 or qty < quote.min_size:
        raise PriceUnavailable("execution_instrument_precision_changed")
    if decision.limit_price is not None and Decimal(str(decision.limit_price)) % Decimal(str(quote.min_tick)) != 0:
        raise PriceUnavailable("execution_tick_precision_changed")
    return {"policy_version": POLICY_VERSION, "approval_quote_ref": basis["quote_ref"],
            "execution_quote_ref": quote_ref(quote), "quote": quote.model_dump(mode="json")}
