"""The Chief states a size the order path can use, and its report says what became of the proposals."""

from __future__ import annotations

from datetime import datetime, timezone

from ats.agents.chief import decide, report
from ats.agents.chief.decide import ChiefResult
from ats.agents.chief.outputs import DecisionView
from ats.schemas.decision import TradeDecision


def _view(**kw) -> DecisionView:
    base = dict(symbol="axti", action="sell", conviction=0.9, rationale="止损")
    base.update(kw)
    return DecisionView(**base)


def test_the_chiefs_share_count_reaches_the_trade_decision():
    assert decide._to_decision(_view(qty=100)).qty == 100
    assert decide._to_decision(_view(qty=0)).qty is None            # 0 means "none", not a breach
    assert decide._to_decision(_view(notional_usd=3746)).qty is None


def test_the_report_shows_size_and_price_and_flags_proposals_that_became_no_order():
    now = datetime.now(timezone.utc)
    sized = TradeDecision(symbol="AXTI", action="sell", qty=100, notional_usd=3746, limit_price=37.46,
                          order_type="limit", conviction=0.9, rationale="清仓")
    bare = TradeDecision(symbol="MRVL", action="sell", order_type="limit", conviction=0.9, rationale="清仓")
    text = report.render(ChiefResult(cycle_id="c", as_of=now, decisions=[sized, bare],
                                     outcome_note="订单规范化拒绝：MRVL:quantity_or_budget_missing"))
    assert "100 股 ≈ $3,746" in text and "37.46" in text
    assert "未给规模" in text and "系统按盘口定" in text
    assert "没有形成订单，不在待审批里" in text and "MRVL:quantity_or_budget_missing" in text
    assert "没有形成订单" not in report.render(ChiefResult(cycle_id="c", as_of=now, decisions=[sized]))


def test_the_report_names_proposals_left_out_of_a_sent_batch():
    now = datetime.now(timezone.utc)
    sized = TradeDecision(symbol="AXTI", action="sell", qty=100, notional_usd=3746, order_type="limit",
                          conviction=0.9, rationale="清仓")
    text = report.render(ChiefResult(cycle_id="c", as_of=now, decisions=[sized],
                                     skipped_note="FORM 隔夜盘口今晚不可用（quote_spread_invalid）"))
    assert "另有建议本轮没有下单" in text and "FORM" in text and "没有形成订单" not in text
