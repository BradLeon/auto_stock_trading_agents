"""Phase 6: risk-manager portfolio logic + broker helpers (no network)."""

from datetime import datetime, timezone

from ats.agents import risk_manager as rm
from ats.broker.ibkr import IBKRBroker, _map_status
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
