"""Phase 5: Manager stub + deterministic guardrail validator (no network)."""

from datetime import datetime, timezone

from ats.agents import manager as mgr
from ats.agents.risk_validator import apply_guardrails
from ats.schemas.decision import TradeDecision
from ats.schemas.reports import FundamentalReport
from ats.schemas.risk import RiskGuardrails

NOW = datetime.now(timezone.utc)
SECTORS = {"NVDA": "ai_hardware", "AMD": "ai_hardware", "GOOGL": "internet_software"}


def _gr(**kw):
    base = dict(as_of=NOW, max_position_pct=0.2, max_sector_pct=0.4, max_gross_leverage=1.0,
               max_single_order_usd=25000, cash_floor_pct=0.05)
    base.update(kw)
    return RiskGuardrails(**base)


def test_manager_stub_buys_bullish_only():
    reports = [
        FundamentalReport(as_of=NOW, symbol="NVDA", signal="bullish"),
        FundamentalReport(as_of=NOW, symbol="GOOGL", signal="neutral"),
    ]
    decisions, _ = mgr.decide(as_of=NOW, macro=None, industry_reports=[],
                              fundamental_reports=reports, technical_reports=[],
                              guardrails=_gr(), market_data={}, net_liquidation=100000,
                              use_llm=False)
    assert [d.symbol for d in decisions] == ["NVDA"]


def test_validator_drops_no_add():
    d = [TradeDecision(symbol="NVDA", action="buy", notional_usd=5000)]
    out, notes = apply_guardrails(d, _gr(no_add_list=["NVDA"]), sector_by_symbol=SECTORS,
                                  net_liquidation=100000)
    assert out == []
    assert any("do-not-add" in n for n in notes)


def test_validator_clips_order_notional():
    d = [TradeDecision(symbol="NVDA", action="buy", notional_usd=40000)]
    out, notes = apply_guardrails(d, _gr(), sector_by_symbol=SECTORS, net_liquidation=100000)
    assert out[0].notional_usd == 25000
    assert any("CLIP" in n for n in notes)


def test_validator_injects_forced_trim():
    out, notes = apply_guardrails([], _gr(forced_trim=["AAPL"]), sector_by_symbol=SECTORS,
                                  net_liquidation=100000)
    assert [d.symbol for d in out] == ["AAPL"]
    assert out[0].action == "trim"


def test_validator_scales_sector_over_cap():
    # Two ai_hardware buys at 25k each = 50k = 50% of 100k book; sector cap 40% -> 40k.
    d = [TradeDecision(symbol="NVDA", action="buy", notional_usd=25000),
         TradeDecision(symbol="AMD", action="buy", notional_usd=25000)]
    out, notes = apply_guardrails(d, _gr(), sector_by_symbol=SECTORS, net_liquidation=100000)
    total = sum(x.notional_usd for x in out)
    assert round(total) == 40000
    assert any("sector" in n for n in notes)


def test_validator_scales_book_under_cash_floor():
    # Two buys (different sectors) = 50k. Cash floor 0.6 -> deployable 40k -> scale to 40k.
    d = [TradeDecision(symbol="GOOGL", action="buy", notional_usd=25000),
         TradeDecision(symbol="NVDA", action="buy", notional_usd=25000)]
    out, notes = apply_guardrails(d, _gr(cash_floor_pct=0.6), sector_by_symbol=SECTORS,
                                  net_liquidation=100000)
    assert round(sum(x.notional_usd for x in out)) == 40000
    assert any("cash floor" in n for n in notes)
