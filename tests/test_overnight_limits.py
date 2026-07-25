"""Overnight orders are repriced as limits before the Boss ever sees the card.

The after-close score window raises orders at 20:00 ET and the Boss approves around
08:00 Asia — hours before the open. A market order submitted then just queues to the
open, and on a post-earnings gap that is the worst available fill.
"""

from __future__ import annotations

from ats.schemas.decision import TradeDecision
from ats.trader import execute as texec


def _d(action="buy", **kw):
    return TradeDecision(symbol="GOOG", action=action, notional_usd=3000.0,
                         order_type="market", rationale="pead", **kw)


def test_buy_limit_is_above_the_reference(monkeypatch):
    monkeypatch.setattr(texec, "_last_price", lambda s: 200.0)
    out, notes = texec.as_overnight_limits([_d("buy")], slippage_pct=0.5)
    assert out[0].order_type == "limit"
    assert out[0].limit_price == 201.0
    assert "改限价" in notes[0]


def test_sell_limit_is_below_the_reference(monkeypatch):
    monkeypatch.setattr(texec, "_last_price", lambda s: 200.0)
    out, _ = texec.as_overnight_limits([_d("sell")], slippage_pct=0.5)
    assert out[0].limit_price == 199.0


def test_existing_limit_is_left_alone(monkeypatch):
    monkeypatch.setattr(texec, "_last_price", lambda s: 999.0)
    d = TradeDecision(symbol="GOOG", action="buy", notional_usd=3000.0,
                      order_type="limit", limit_price=180.0, rationale="pead")
    out, notes = texec.as_overnight_limits([d])
    assert out[0].limit_price == 180.0
    assert notes == []


def test_missing_price_keeps_a_visible_market_order(monkeypatch):
    """Better a market order the Boss can see on the card than an invented limit."""
    monkeypatch.setattr(texec, "_last_price", lambda s: None)
    out, notes = texec.as_overnight_limits([_d("buy")])
    assert out[0].order_type == "market"
    assert out[0].limit_price is None
    assert "取不到参考价" in notes[0]


def test_approval_card_shows_the_limit_price(monkeypatch):
    """The card must show the price that will actually be submitted."""
    monkeypatch.setattr(texec, "_last_price", lambda s: 200.0)
    out, _ = texec.as_overnight_limits([_d("buy")], slippage_pct=0.5)
    summary = texec.build_approval_summary([(out[0], 15.0)], [], "pead-chief")
    assert "@ 201.0" in summary
    assert "(mkt)" not in summary
