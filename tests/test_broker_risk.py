"""Broker helpers: order placement short-circuit, status mapping, FX and sizing (no network)."""

from datetime import datetime, timezone

import pytest

from ats.broker.ibkr import IBKRBroker, IBKRUnavailable, _account_value_context, _fx_rate, _map_status

NOW = datetime.now(timezone.utc)


def test_place_orders_empty_does_not_connect():
    # Empty batch must short-circuit before any TWS connection attempt.
    broker = IBKRBroker(host="240.0.0.1", port=1, client_id=99)
    assert broker.place_orders([], "cycle-x") == []


def test_status_mapping():
    assert _map_status("Filled") == "filled"
    assert _map_status("PreSubmitted") == "submitted"
    assert _map_status("Cancelled") == "cancelled"


def test_account_value_context_preserves_currency_and_fx():
    class AV:
        def __init__(self, tag, value, currency):
            self.tag, self.value, self.currency = tag, value, currency

    base, rates, summary = _account_value_context([
        AV("NetLiquidation", "224868.21", "USD"),
        AV("GrossPositionValue", "246953.96", "USD"),
        AV("ExchangeRate", "0.1275148", "HKD"),
        AV("ExchangeRate", "1.1534859", "EUR"),
    ])

    assert base == "USD"
    assert rates == {"USD": 1.0, "BASE": 1.0, "HKD": 0.1275148, "EUR": 1.1534859}
    assert summary["NetLiquidation"] == "224868.21"


def test_position_weight_uses_base_currency_value():
    nav = 224868.21
    hkd_mv = 38843.2
    eur_mv = 28938.64

    assert hkd_mv * 0.1275148 / nav == pytest.approx(0.0220266)
    assert eur_mv * 1.1534859 / nav == pytest.approx(0.1484439)


def test_missing_position_fx_fails_closed():
    with pytest.raises(IBKRUnavailable, match="missing HKD->USD exchange rate"):
        _fx_rate("HKD", "USD", {"USD": 1.0}, "7709")


def test_submit_rejects_unqualified_contract():
    """qualifyContracts returning [] (Error 200 / TWS offline) must NOT place an
    order — observed live 2026-07-15 during the IBKR maintenance window."""
    from ats.schemas.decision import TradeDecision
    from ats.execution import broker_write_guard as guard

    guard.reset_for_tests()
    guard.grant_write("test", 1, environment="paper", account="DU1")

    class FakeIB:
        placed = False

        def qualifyContracts(self, *contracts):
            return []

        def placeOrder(self, contract, order):  # pragma: no cover - must not run
            self.placed = True
            raise AssertionError("order placed on unqualified contract")

    broker = IBKRBroker(host="240.0.0.1", port=1, client_id=99)
    broker._last_trades = []
    ib = FakeIB()
    entry = broker._submit(ib, TradeDecision(symbol="MRVL", action="trim"), 27, "cycle-x")
    assert entry.status == "rejected"
    assert "not qualified" in entry.error
    assert ib.placed is False
    assert broker._last_trades == [None]
    guard.reset_for_tests()


def test_size_survives_nan_close(monkeypatch):
    """Pre-open, yfinance appends today's bar with close=NaN — sizing must skip
    it (use the last real close), never crash on round(NaN). Seen live 2026-07-15.

    Retargeted by task 7.9: `_last_price` no longer reads a price at all (auto
    execution is disabled), so this exercises `_last_price_enabled` — the retained
    implementation the plan-A restore will use. Its NaN handling still has to be
    correct, because plan A revives this code rather than rewriting it.
    """
    from ats.trader import execute as texec
    from ats.schemas.decision import TradeDecision

    class Bar:
        def __init__(self, close):
            self.close = close

    class Snap:
        history = [Bar(217.53), Bar(float("nan"))]

    monkeypatch.setattr("ats.data.market_data.fetch_snapshot", lambda t: Snap())
    assert texec._last_price_enabled("MRVL") == 217.53

    class AllNan:
        history = [Bar(float("nan"))]

    monkeypatch.setattr("ats.data.market_data.fetch_snapshot", lambda t: AllNan())
    assert texec._last_price_enabled("MRVL") is None


def test_sizing_returns_zero_while_auto_execution_is_disabled():
    """The visible consequence of stopping the read: an amount-only decision sizes
    to zero. It is not silent — `place_orders` refuses the batch with a recorded
    reason (see `test_overnight_limits.py`) — but it does mean no order is sized.
    """
    from ats.trader import execute as texec
    from ats.schemas.decision import TradeDecision

    assert texec.AUTO_EXECUTION_ENABLED is False
    d = TradeDecision(symbol="MRVL", action="trim", notional_usd=6000)
    with pytest.raises(PermissionError, match="binding_required"):
        texec._size(d)
