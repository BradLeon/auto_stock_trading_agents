"""Guardrail review (Phase B, task 4.9): boundaries reported, never rewritten.

The legacy `apply_guardrails` silently dropped/clipped/injected/scaled — that
behavior contradicts §5.2 (risk must not modify a proposal) and is scheduled
for retirement. The default flow (PEAD recommendation path) now consumes the
read-only `review_guardrails`: the same six checks, expressed as BOUNDARY
notes on untouched decisions.
"""

from datetime import datetime, timezone

from ats.agents.risk_validator import review_guardrails
from ats.schemas.decision import TradeDecision
from ats.schemas.risk import RiskGuardrails

NOW = datetime.now(timezone.utc)
SECTORS = {"NVDA": "ai_hardware", "AMD": "ai_hardware", "GOOGL": "internet_software"}


def _gr(**kw):
    base = dict(as_of=NOW, max_position_pct=0.2, max_sector_pct=0.4, max_gross_leverage=1.0,
                max_single_order_usd=25000, cash_floor_pct=0.05)
    base.update(kw)
    return RiskGuardrails(**base)


def test_review_reports_no_add_without_dropping():
    d = [TradeDecision(symbol="NVDA", action="buy", notional_usd=5000)]
    out, notes = review_guardrails(d, _gr(no_add_list=["NVDA"]), sector_by_symbol=SECTORS,
                                   net_liquidation=100000)
    assert out == d and out[0].notional_usd == 5000     # proposal untouched
    assert any("do-not-add" in n for n in notes)
    assert all("BOUNDARY" in n for n in notes)          # every note is a boundary


def test_review_reports_order_cap_without_clipping():
    d = [TradeDecision(symbol="NVDA", action="buy", notional_usd=40000)]
    out, notes = review_guardrails(d, _gr(), sector_by_symbol=SECTORS, net_liquidation=100000)
    assert out[0].notional_usd == 40000
    assert any("25,000" in n for n in notes)


def test_review_recommends_forced_trim_without_injecting():
    d = [TradeDecision(symbol="NVDA", action="buy", notional_usd=5000)]
    out, notes = review_guardrails(d, _gr(forced_trim=["AAPL"]), sector_by_symbol=SECTORS,
                                   net_liquidation=100000)
    assert [x.symbol for x in out] == ["NVDA"]          # no synthetic trim injected
    assert any("AAPL" in n and "trim" in n for n in notes)


def test_review_reports_sector_over_cap_without_scaling():
    d = [TradeDecision(symbol="NVDA", action="buy", notional_usd=25000),
         TradeDecision(symbol="AMD", action="buy", notional_usd=25000)]
    out, notes = review_guardrails(d, _gr(), sector_by_symbol=SECTORS, net_liquidation=100000)
    assert round(sum(x.notional_usd for x in out)) == 50000   # unscaled
    assert any("sector" in n for n in notes)


def test_review_reports_budget_boundary_without_scaling():
    d = [TradeDecision(symbol="GOOGL", action="buy", notional_usd=25000),
         TradeDecision(symbol="NVDA", action="buy", notional_usd=25000)]
    out, notes = review_guardrails(d, _gr(cash_floor_pct=0.6), sector_by_symbol=SECTORS,
                                   net_liquidation=100000)
    assert round(sum(x.notional_usd for x in out)) == 50000   # unscaled
    assert any("cash floor/leverage" in n for n in notes)
