"""Phase 6: risk-manager portfolio logic + broker helpers (no network)."""

from datetime import datetime, timezone

import pytest

from ats.agents import risk_manager as rm
from ats.broker.ibkr import IBKRBroker, IBKRUnavailable, _account_value_context, _fx_rate, _map_status
from ats.config import RiskConfig
from ats.schemas.portfolio import ExposureBreakdown, PortfolioSnapshot, Position

NOW = datetime.now(timezone.utc)
SECTORS = {"NVDA": "ai_hardware", "AMD": "ai_hardware", "GOOGL": "internet_software"}


def _cfg():
    return RiskConfig(max_position_pct=0.2, max_sector_pct=0.4, max_gross_leverage=1.0,
                      max_single_order_usd=25000, cash_floor_pct=0.05)


def test_assess_without_portfolio_uses_config():
    gr = rm.assess(as_of=NOW, risk_cfg=_cfg(), portfolio=None, sector_by_symbol=SECTORS)
    assert gr.max_position_pct == 0.2
    assert gr.no_add_list == [] and gr.forced_trim == []


def test_assess_flags_overweight_and_hot_sector():
    pf = PortfolioSnapshot(
        as_of=NOW, net_liquidation=100000, leverage=0.45,
        positions=[Position(symbol="NVDA", sector="ai_hardware", qty=100, avg_cost=200,
                            market_price=300, market_value=30000, weight=0.30)],
        exposure=ExposureBreakdown(by_sector={"ai_hardware": 0.45}, by_ticker={"NVDA": 0.30}),
    )
    gr = rm.assess(as_of=NOW, risk_cfg=_cfg(), portfolio=pf, sector_by_symbol=SECTORS)
    # NVDA is over the 20% position cap -> forced trim.
    assert "NVDA" in gr.forced_trim
    # ai_hardware sector is at 45% (>40%) -> no adds to ai_hardware names.
    assert set(gr.no_add_list) >= {"NVDA", "AMD"}
    assert "GOOGL" not in gr.no_add_list


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


def test_size_survives_nan_close(monkeypatch):
    """Pre-open, yfinance appends today's bar with close=NaN — sizing must skip
    it (use the last real close), never crash on round(NaN). Seen live 2026-07-15."""
    from ats.trader import execute as texec
    from ats.schemas.decision import TradeDecision

    class Bar:
        def __init__(self, close):
            self.close = close

    class Snap:
        history = [Bar(217.53), Bar(float("nan"))]

    monkeypatch.setattr("ats.data.market_data.fetch_snapshot", lambda t: Snap())
    d = TradeDecision(symbol="MRVL", action="trim", notional_usd=6000)
    assert texec._size(d) == 28.0

    class AllNan:
        history = [Bar(float("nan"))]

    monkeypatch.setattr("ats.data.market_data.fetch_snapshot", lambda t: AllNan())
    assert texec._size(d) == 0.0
