"""Orders on IBKR's overnight venue (exchange OVERNIGHT): quote rules, normalization, handoff. Synthetic quotes, no broker."""
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from test_phase_f_trader_a import execution, price_clock, quote, ready_state  # noqa: F401  (fixtures)

from ats.broker.ibkr import IBKRBroker, IBKRUnavailable, trading_hours_open
from ats.data import execution_prices as prices
from ats.data.runtime.execution_prices import overnight_session_candidate
from ats.execution import broker_write_guard as guard
from ats.schemas.decision import TradeDecision

ET = ZoneInfo("America/New_York")
HOURS = ("20261104:2000-20261105:0350;20261105:2000-20261106:0350;20261107:CLOSED;20261108:1800-20261109:0350;"
         "20261109:2000-20261110:0350")


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=ET)


@pytest.mark.parametrize("when,expected", [
    (at(5, 19, 59), False), (at(5, 20, 0), True), (at(6, 3, 49), True), (at(6, 3, 50), False),    # Thu night into Fri morning
    (at(6, 20, 0), False), (at(7, 2, 0), False),                                                      # Friday night: closed
    (at(8, 17, 59), False), (at(8, 18, 0), True), (at(9, 3, 0), True),                                # Sunday 18:00 opens the week
    (at(9, 9, 45), False),                                                                            # Monday regular
])
def test_overnight_session_candidate_follows_the_weekly_pattern(when, expected):
    assert overnight_session_candidate(when.astimezone(UTC)) is expected


def test_trading_hours_come_from_the_contract():
    assert trading_hours_open(HOURS, "US/Eastern", at(5, 22).astimezone(UTC))
    assert not trading_hours_open(HOURS, "US/Eastern", at(5, 9, 45).astimezone(UTC))
    assert not trading_hours_open(HOURS, "US/Eastern", at(7, 22).astimezone(UTC))     # the CLOSED day
    assert not trading_hours_open("", "US/Eastern", at(5, 22).astimezone(UTC))


def _overnight_quote(**changes):
    return quote(session="overnight", **changes)


def test_overnight_quote_is_validated_like_a_live_quote_only_while_the_regular_session_is_closed(monkeypatch):
    close = datetime.now(UTC) - timedelta(hours=6)
    monkeypatch.setattr(prices, "session_context", lambda now: (False, close))
    assert prices.validate_quote(_overnight_quote(), symbol="AAPL", venue="overnight").session == "overnight"
    with pytest.raises(prices.PriceUnavailable, match="outside_overnight"):
        prices.validate_quote(quote(), symbol="AAPL", venue="overnight")                  # a regular-session quote
    with pytest.raises(prices.PriceUnavailable, match="outside_regular"):
        prices.validate_quote(_overnight_quote(), symbol="AAPL")                          # an overnight quote, regular venue
    with pytest.raises(prices.PriceUnavailable, match="spread_invalid"):
        prices.validate_quote(_overnight_quote(bid=100, ask=102), symbol="AAPL", venue="overnight")
    with pytest.raises(prices.PriceUnavailable, match="stale"):
        prices.validate_quote(_overnight_quote(source_as_of=datetime.now(UTC) - timedelta(seconds=31)),
                              symbol="AAPL", venue="overnight")
    monkeypatch.setattr(prices, "session_context", lambda now: (True, close))             # regular session open: not overnight
    with pytest.raises(prices.PriceUnavailable, match="outside_overnight"):
        prices.validate_quote(_overnight_quote(), symbol="AAPL", venue="overnight")


def test_overnight_normalization_prices_a_limit_from_the_live_quote_and_binds_the_venue(execution, monkeypatch):
    monkeypatch.setattr(prices, "session_context", lambda now: (False, datetime.now(UTC) - timedelta(hours=6)))
    calls = []

    def provider(symbol, **kwargs):
        calls.append(kwargs)
        return _overnight_quote(symbol=symbol)

    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price", provider)
    orders, notes = prices.normalize_orders(
        [TradeDecision(symbol="AAPL", action="buy", notional_usd=1000, order_type="market")],
        cycle_id="night", venue="overnight")
    order = orders[0]
    assert calls[0]["venue"] == "overnight" and not calls[0].get("overnight")
    assert order.order_type == "limit" and order.limit_price == 100.6          # ask 100.1 + 0.5%, rounded down to the tick
    assert order.execution_basis["venue"] == "overnight" and not order.execution_basis["defer_to_regular_session"]
    # the approval-time check: same venue passes, a regular-session quote or a closed overnight session does not
    assert prices.check_execution(order, order.qty, _overnight_quote())["approval_quote_ref"]
    with pytest.raises(prices.PriceUnavailable, match="outside_overnight"):
        prices.check_execution(order, order.qty, quote())
    monkeypatch.setattr(prices, "session_context", lambda now: (True, datetime.now(UTC) - timedelta(hours=6)))
    with pytest.raises(prices.PriceUnavailable, match="outside_overnight"):
        prices.check_execution(order, order.qty, _overnight_quote())


class _IB:
    def __init__(self, hours):
        self.hours, self.contracts = hours, []

    def qualifyContracts(self, c):
        self.contracts.append(c)
        return [c]

    def reqContractDetails(self, c):
        return [SimpleNamespace(tradingHours=self.hours, timeZoneId="US/Eastern")]

    def reqTickByTickData(self, c, *a):
        return SimpleNamespace(marketDataType=1, tickByTicks=[SimpleNamespace(
            time=datetime.now(UTC) - timedelta(seconds=1), bidPrice=100, askPrice=100.1)])

    def cancelTickByTickData(self, *a):
        pass

    def sleep(self, delay):
        pass


def _broker(monkeypatch, ib, now):
    @contextmanager
    def session(*a, **kw):
        yield ib

    broker = IBKRBroker()
    monkeypatch.setattr(broker, "session", session)
    monkeypatch.setattr("ats.broker.ibkr._now", lambda: now)
    return broker


def test_overnight_quote_read_uses_the_overnight_contract_and_the_contracts_own_hours(monkeypatch):
    ib = _IB(HOURS)
    row = _broker(monkeypatch, ib, at(5, 22).astimezone(UTC)).get_execution_quote(
        "AAPL", currency="USD", metadata={"symbol": "AAPL", "currency": "USD"}, venue="overnight")
    assert ib.contracts[0].exchange == "OVERNIGHT" and row["session"] == "overnight"
    closed = _broker(monkeypatch, _IB(HOURS), at(5, 9, 45).astimezone(UTC))
    with pytest.raises(IBKRUnavailable, match="overnight_session_closed"):
        closed.get_execution_quote("AAPL", currency="USD", metadata={}, venue="overnight")


def _place(execution, monkeypatch, decision_update):
    from phase_f_broker_harness import FakeIB, broker_for

    state = ready_state(execution)
    decision = state.decisions[0]
    decision = decision.model_copy(update={**decision_update, "execution_basis": {
        **decision.execution_basis, **decision_update.get("execution_basis", {})}})
    guard.reset_for_tests()
    with monkeypatch.context() as local:
        local.delenv("ATS_BROKER_WRITE_PROHIBITION")
        local.delenv("ATS_RUN_MODE")
        guard.grant_write("simulation", 1, environment="paper", account="DU1")
        exchanges = []

        class RecordingIB(FakeIB):
            def placeOrder(self, contract, order):
                exchanges.append(contract.exchange)
                return super().placeOrder(contract, order)

        rows = broker_for(RecordingIB()).place_orders([(decision, decision.qty)], state.cycle_id,
                                                      execution_check=lambda d, q: {})
    return rows, exchanges


def test_an_overnight_order_goes_to_the_overnight_venue_as_a_limit(execution, monkeypatch):
    rows, exchanges = _place(execution, monkeypatch, {"order_type": "limit", "limit_price": 100.6,
                                                      "execution_basis": {"venue": "overnight"}})
    assert exchanges == ["OVERNIGHT"] and rows[0].status != "error"


def test_an_overnight_order_without_a_limit_is_refused_before_the_broker(execution, monkeypatch):
    rows, exchanges = _place(execution, monkeypatch, {"order_type": "market", "limit_price": None,
                                                      "execution_basis": {"venue": "overnight"}})
    assert exchanges == [] and rows[0].status == "error" and "requires_limit" in rows[0].error


def test_a_regular_order_still_goes_to_smart(execution, monkeypatch):
    rows, exchanges = _place(execution, monkeypatch, {})
    assert exchanges == ["SMART"]


def test_the_scheduled_chief_trades_the_overnight_session_on_the_overnight_venue(execution, monkeypatch):
    from ats.graph import chief
    from ats.graph.chief_state import ChiefDecisionState

    monkeypatch.setattr(prices, "session_context", lambda now: (False, datetime.now(UTC) - timedelta(hours=6)))
    monkeypatch.setattr("ats.data.runtime.execution_prices.overnight_session_candidate", lambda point: True)
    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price",
                        lambda symbol, **kw: _overnight_quote(symbol=symbol))
    state = ChiefDecisionState(cycle_id="overnight-venue", as_of=datetime.now(UTC), source="pead-chief", decide=False,
                               dry_run=False, decisions=[TradeDecision(symbol="AAPL", action="buy", notional_usd=1000,
                                                                       order_type="market")])
    reviewed = state.model_copy(update=chief.risk_gate(state))
    assert reviewed.risk_review.verdict == "approved", reviewed.risk_notes
    order = reviewed.decisions[0]
    assert order.order_type == "limit" and order.execution_basis["venue"] == "overnight"
    assert order.execution_basis["quote"]["session"] == "overnight"
