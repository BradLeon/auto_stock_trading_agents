"""overnight-session-scheduling: session check before the scheduled Chief, approval deadline, session-end cancel,
overnight book-depth cap. Synthetic clocks, quotes and broker transports; no TWS."""
from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from test_phase_f_trader_a import (  # noqa: F401  (fixtures)
    execution,
    price_clock,
    quote,
    ready_state,
)
from test_trade_schedules import _tick, world  # noqa: F401  (fixtures)

from ats.data import execution_prices as prices
from ats.execution import broker_write_guard as guard
from ats.graph import chief
from ats.graph.chief_state import ChiefDecisionState
from ats.schemas.decision import BossApproval, TradeDecision
from ats.workflow import overnight_session as osn
from ats.workflow import schedule_runtime as rt
from ats.workflow import trade_schedules as ts

ET = ZoneInfo("America/New_York")
TPE = ZoneInfo("Asia/Taipei")
ENTRY = {"scope": ts.ProjectionScope(kind="portfolio", id="portfolio")}


def tpe(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=TPE)


def _history(action):
    with closing(rt._connect()) as conn:
        return [row[0] for row in conn.execute(
            "SELECT key FROM schedule_runtime_history WHERE action = ?", (action,)).fetchall()]


# --- 1.1 the session must be open before the scheduled Chief runs ------------------------------------------------ #
@pytest.mark.parametrize("when", [
    tpe(11, 28, 10, 30),          # Taipei Saturday = Friday night in New York: no overnight session
    tpe(11, 26, 10, 30),          # Taipei Thursday = the night before Thanksgiving: the session of a holiday
])
def test_a_closed_session_skips_the_chief_and_records_why(world, monkeypatch, when):
    calls = []
    monkeypatch.setitem(ts._RUNNERS, "chief-daily", lambda entry: calls.append(1) or {})
    monkeypatch.setattr(osn, "_ibkr_overnight_segment", lambda point: pytest.fail("calendar-closed: IBKR not asked"))
    out = ts.run_trade_workflow("chief-daily", ENTRY, _tick("chief-daily", when))
    assert out == {"status": "skipped", "reason": "skipped_session_closed"} and calls == []
    assert _history("skipped_session_closed") == ["chief-daily"]


def test_a_taipei_weekday_morning_runs_the_chief_in_the_overnight_session(world, monkeypatch):
    from test_trade_schedules import _resume

    _resume("chief-daily")
    asked = []

    def segment(point):
        asked.append(point)
        return datetime(2026, 11, 24, 20, tzinfo=ET), datetime(2026, 11, 25, 3, 50, tzinfo=ET)

    monkeypatch.setattr(osn, "_ibkr_overnight_segment", segment)
    monkeypatch.setitem(ts._RUNNERS, "chief-daily", lambda entry: {"cycle_id": "c", "outcome": "pending_approval"})
    out = ts.run_trade_workflow("chief-daily", ENTRY, _tick("chief-daily", tpe(11, 25, 10, 30)))
    assert out["outcome"] == "pending_approval" and asked


def test_the_overnight_window_follows_the_exchange_calendar():
    window = osn.overnight_window(tpe(11, 25, 10, 30))                  # Wednesday: Tue 20:00 -> Wed 03:50 New York
    assert window.opens_at == datetime(2026, 11, 24, 20, tzinfo=ET)
    assert window.closes_at == datetime(2026, 11, 25, 3, 50, tzinfo=ET)
    assert window.approval_deadline.astimezone(TPE) == tpe(11, 25, 12, 50)     # 4 hours before the close, winter
    monday = osn.overnight_window(tpe(11, 30, 9, 30))                   # Monday morning: Sunday 18:00 open
    assert monday.opens_at == datetime(2026, 11, 29, 18, tzinfo=ET)
    assert osn.overnight_window(tpe(11, 25, 17, 0)) is None             # after the 16:50 Taipei close
    assert osn.overnight_window(tpe(11, 27, 10, 30)) is not None        # Thanksgiving night into the Friday session
    summer = osn.overnight_window(datetime(2026, 7, 15, 10, 30, tzinfo=TPE))
    assert summer.approval_deadline.astimezone(TPE).hour == 11          # 11:50 Taipei in US summer time


def test_ibkr_decides_when_reachable_and_the_calendar_stands_when_not(monkeypatch):
    point = tpe(11, 25, 10, 30)
    monkeypatch.setattr(osn, "_ibkr_overnight_segment", lambda p: None)
    assert osn.chief_session(point) is None                              # the contract's hours say closed
    early = (datetime(2026, 11, 24, 20, tzinfo=ET), datetime(2026, 11, 25, 2, 0, tzinfo=ET))
    monkeypatch.setattr(osn, "_ibkr_overnight_segment", lambda p: early)
    assert osn.chief_session(point).closes_at == early[1] and osn.chief_session(point).source == "ibkr"

    def down(p):
        raise ConnectionRefusedError("TWS down")

    monkeypatch.setattr(osn, "_ibkr_overnight_segment", down)
    assert osn.chief_session(point).source == "calendar"


def test_the_regular_session_still_runs_the_chief_without_asking_for_overnight_hours(monkeypatch):
    monkeypatch.setattr(osn, "_ibkr_overnight_segment", lambda point: pytest.fail("regular session: no probe"))
    assert osn.chief_session(datetime(2026, 11, 25, 9, 45, tzinfo=ET)).venue == "regular"


# --- 1.2 the approval deadline ------------------------------------------------------------------------------- #
def test_deadline_rejection_allows_up_to_the_deadline_only():
    deadline = datetime(2026, 11, 25, 4, 50, tzinfo=UTC)
    assert osn.deadline_rejection(deadline, deadline) is None
    assert osn.deadline_rejection(None, deadline + timedelta(hours=9)) is None      # regular venue: no deadline
    late = osn.deadline_rejection(deadline, deadline + timedelta(seconds=1))
    assert late.startswith("approval_deadline_passed") and "重新审批" in late


@pytest.mark.parametrize("late,placed", [(False, True), (True, False)])
def test_an_approval_after_the_deadline_is_refused_before_the_broker(execution, late, placed):
    state = ready_state(execution)
    approved_at = state.approval.reviewed_at
    deadline = approved_at + (timedelta(seconds=-1) if late else timedelta(minutes=5))
    out = chief.trader(state.model_copy(update={"approval_deadline": deadline}))
    if placed:
        assert out["gate_outcome"] == "placed" and execution.broker.placed
    else:
        assert out["gate_outcome"] == "refused" and not execution.broker.placed
        assert out["order_results"][0].error.startswith("approval_deadline_passed")


def _night(monkeypatch, **book):
    monkeypatch.setattr(prices, "session_context", lambda now: (False, datetime.now(UTC) - timedelta(hours=6)))
    monkeypatch.setattr("ats.data.runtime.execution_prices.overnight_session_candidate", lambda point: True)
    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price",
                        lambda symbol, **kw: quote(symbol=symbol, session="overnight", **book))
    now = datetime.now(UTC)
    window = osn.SessionWindow("overnight", now - timedelta(hours=2), now + timedelta(hours=6), "ibkr")
    monkeypatch.setattr(osn, "confirmed_overnight_window", lambda point: window)
    return window


def _overnight_state(notional=1000, action="buy"):
    return ChiefDecisionState(cycle_id="night", as_of=datetime.now(UTC), source="scheduled", decide=False,
                              use_llm=False, dry_run=False,
                              decisions=[TradeDecision(symbol="AAPL", action=action, notional_usd=notional,
                                                       order_type="market")])


def test_an_overnight_proposal_carries_its_deadline_to_every_approval_surface(execution, monkeypatch):
    from ats.channel.pending_channel import reminder_text
    from ats.schemas.channel import ApprovalRequest

    window = _night(monkeypatch, bid_size=500, ask_size=500)
    state = _overnight_state()
    reviewed = state.model_copy(update=chief.risk_gate(state))
    assert reviewed.risk_review.verdict == "approved", reviewed.risk_notes
    assert reviewed.approval_deadline == window.approval_deadline
    assert reviewed.approval_summary.startswith("⏰ 审批截止")               # terminal and card body
    request = ApprovalRequest(cycle_id="night", as_of=state.as_of, approval_deadline=reviewed.approval_deadline)
    assert ApprovalRequest.model_validate(request.model_dump(mode="json")).approval_deadline == window.approval_deadline
    assert "审批截止" in reminder_text("night", 1, "buy AAPL", request.approval_deadline)[1]   # the notification
    assert "审批截止" not in reminder_text("night", 1, "buy AAPL")[1]


def test_a_regular_session_proposal_has_no_deadline(execution):
    state = ChiefDecisionState(cycle_id="day", as_of=datetime.now(UTC), source="scheduled", decide=False,
                               use_llm=False, dry_run=False,
                               decisions=[TradeDecision(symbol="AAPL", action="buy", notional_usd=1000)])
    reviewed = state.model_copy(update=chief.risk_gate(state))
    assert reviewed.approval_deadline is None and "审批截止" not in reviewed.approval_summary


# --- 2.2 the overnight book bounds a single order ------------------------------------------------------------ #
def test_a_thin_overnight_book_shrinks_the_order_through_the_existing_boundary(execution, monkeypatch):
    _night(monkeypatch, bid_size=2, ask_size=2)                         # 2 shares at 100.1: cap 2 x 100.1 x 5
    state = _overnight_state(notional=5000)
    reviewed = state.model_copy(update=chief.risk_gate(state))
    verdict = reviewed.risk_review.order_verdicts[0]
    assert reviewed.risk_review.verdict == "rejected" and verdict.max_allowed_notional == pytest.approx(1001)
    assert [v.rule_id for v in reviewed.risk_review.violations] == ["overnight_book_depth"]
    revised = chief.chief_revise(reviewed)                               # the mechanical boundary adoption
    assert revised["decisions"][0].notional_usd == pytest.approx(1001)


def test_a_deep_overnight_book_leaves_the_regular_cap_in_charge(execution, monkeypatch):
    _night(monkeypatch, bid_size=10_000, ask_size=10_000)
    state = _overnight_state(notional=5000)
    reviewed = state.model_copy(update=chief.risk_gate(state))
    assert reviewed.risk_review.verdict == "approved", reviewed.risk_notes


def test_an_overnight_quote_without_a_book_size_passes_nothing():
    from ats.risk.checks import _overnight_book_boundary

    order = TradeDecision(symbol="AAPL", action="buy", notional_usd=10,
                          execution_basis={"venue": "overnight", "quote": {"ask": 100.1, "ask_size": None}})
    assert _overnight_book_boundary(order) == 0.0
    regular = order.model_copy(update={"execution_basis": {"venue": "regular", "quote": {}}})
    assert _overnight_book_boundary(regular) is None


def test_quote_hash_of_a_quote_stored_before_book_sizes_is_unchanged():
    stored = quote().model_dump(mode="json")
    for name in ("bid_size", "ask_size"):
        stored.pop(name)
    import hashlib
    import json

    legacy = "execution-quote:" + hashlib.sha256(json.dumps(stored, sort_keys=True).encode()).hexdigest()
    assert prices.quote_ref(prices.ExecutionPrice.model_validate(stored)) == legacy


# --- 2.1 cancel at the overnight close ------------------------------------------------------------------------ #
def _trade(order_id, exchange, ref, status="Submitted"):
    return SimpleNamespace(contract=SimpleNamespace(symbol="AAPL", exchange=exchange),
                           order=SimpleNamespace(orderId=order_id, permId=order_id * 10, orderRef=ref, account="DU1"),
                           orderStatus=SimpleNamespace(status=status, filled=0))


def test_the_broker_cancels_only_system_orders_on_the_overnight_venue(execution, monkeypatch):
    from phase_f_broker_harness import FakeIB, broker_for

    night = _trade(1, "OVERNIGHT", "ats:c:r1:0:AAPL")
    day = _trade(2, "SMART", "ats:c:r1:1:AAPL")
    manual = _trade(3, "OVERNIGHT", "")

    class OpenOrdersIB(FakeIB):
        def reqAllOpenOrders(self):
            pass

        def openTrades(self):
            return [night, day, manual]

        def cancelOrder(self, order):
            next(t for t in (night, day, manual) if t.order is order).orderStatus.status = "Cancelled"

    guard.reset_for_tests()
    with monkeypatch.context() as local:
        local.delenv("ATS_BROKER_WRITE_PROHIBITION", raising=False)
        local.delenv("ATS_RUN_MODE", raising=False)
        guard.grant_write("simulation", 1, environment="paper", account="DU1")
        rows = broker_for(OpenOrdersIB()).cancel_session_orders("overnight")
    assert [r["order_ref"] for r in rows] == ["ats:c:r1:0:AAPL"] and rows[0]["status"] == "cancelled"
    assert day.orderStatus.status == "Submitted" and manual.orderStatus.status == "Submitted"


def test_session_end_records_cancelled_session_end_and_leaves_regular_orders(execution):
    from ats.trader.session_end import cancel_overnight_orders

    store = execution.store
    for ref in ("ats:n:r1:0:AAPL", "ats:d:r1:0:MSFT"):
        store.conn.execute("INSERT INTO trades(order_id, cycle_id, symbol, status, order_ref) VALUES (?,?,?,?,?)",
                           (ref, ref.split(":")[1], ref.rsplit(":", 1)[1], "submitted", ref))
    store.conn.commit()

    class Broker:                                   # the simulated broker: one overnight order working
        cancelled = False

        def open_session_orders(self, venue):
            assert venue == "overnight"
            return [{"order_ref": "ats:n:r1:0:AAPL", "status": "submitted"}]

        def cancel_session_orders(self, venue):
            self.cancelled = True
            return [{"order_ref": "ats:n:r1:0:AAPL", "symbol": "AAPL", "status": "cancelled", "filled": 0}]

    broker = Broker()
    result = cancel_overnight_orders(broker=broker, store=store)
    rows = {r["order_ref"]: r for r in store.conn.execute("SELECT order_ref, status, error FROM trades")}
    assert broker.cancelled and result["status"] == "complete" and len(result["cancelled"]) == 1
    assert rows["ats:n:r1:0:AAPL"]["status"] == "cancelled"
    assert rows["ats:n:r1:0:AAPL"]["error"].startswith("cancelled_session_end")
    assert rows["ats:d:r1:0:MSFT"]["status"] == "submitted" and not rows["ats:d:r1:0:MSFT"]["error"]


def test_session_end_with_nothing_working_writes_nothing(execution):
    from ats.trader.session_end import cancel_overnight_orders

    class Broker:
        def open_session_orders(self, venue):
            return []

        def cancel_session_orders(self, venue):
            pytest.fail("nothing working: no broker write")

    assert cancel_overnight_orders(broker=Broker(), store=execution.store)["cancelled"] == []


def test_an_order_still_working_after_the_cancel_is_alerted(monkeypatch):
    from ats.trader import session_end

    alerts = []
    monkeypatch.setattr(session_end, "cancel_overnight_orders", lambda: {
        "status": "incomplete", "cancelled": [], "still_working": [{"symbol": "AAPL", "order_ref": "ats:x",
                                                                    "status": "submitted"}]})
    monkeypatch.setattr("ats.workflow.schedule_runtime.observe", lambda *a: None)
    monkeypatch.setattr("ats.runtime.alerts._push", lambda title, body: alerts.append((title, body)))
    session_end.cancel_job()
    assert alerts and "ats:x" in alerts[0][1]


def test_the_cancel_runs_ten_minutes_before_the_overnight_close():
    from ats.trader.session_end import CRON

    assert CRON == {"day_of_week": "mon-fri", "hour": 3, "minute": 40, "timezone": "America/New_York"}


# --- 2.4 the extended-hours venue (D6): a pre-market Chief at 21:00 Taipei ------------------------------------- #
def test_the_premarket_window_and_its_deadline_follow_the_regular_close():
    window = osn.chief_session(tpe(11, 25, 21, 0))                      # Wednesday 08:00 New York
    assert window.venue == "extended" and window.opens_at == datetime(2026, 11, 25, 4, tzinfo=ET)
    assert window.approval_deadline.astimezone(TPE) == tpe(11, 26, 1, 0)       # 12:00 NY = 4h before the close
    summer = osn.premarket_window(datetime(2027, 7, 14, 21, 0, tzinfo=TPE))      # 09:00 EDT, still pre-market
    assert summer.approval_deadline.astimezone(TPE) == datetime(2027, 7, 15, 0, 0, tzinfo=TPE)
    early = osn.premarket_window(tpe(11, 27, 21, 0))                    # day after Thanksgiving: 13:00 close
    assert early.approval_deadline == datetime(2026, 11, 27, 9, 0, tzinfo=ET)


@pytest.mark.parametrize("when", [
    tpe(11, 28, 21, 0),                                                 # Saturday 08:00 New York
    datetime(2026, 11, 25, 17, 0, tzinfo=ET),                           # after-hours: not a runnable session
    datetime(2026, 11, 25, 3, 59, tzinfo=ET) + timedelta(minutes=0),    # between the overnight close and 04:00
])
def test_no_extended_session_outside_the_premarket(monkeypatch, when):
    monkeypatch.setattr(osn, "_ibkr_overnight_segment", lambda p: None)
    assert osn.chief_session(when) is None


def _premarket(monkeypatch, **book):
    monkeypatch.setattr(prices, "session_context", lambda now: (False, datetime.now(UTC) - timedelta(hours=16)))
    monkeypatch.setattr("ats.data.runtime.execution_prices.overnight_session_candidate", lambda point: False)
    now = datetime.now(UTC)
    window = osn.SessionWindow("extended", now - timedelta(hours=1), now + timedelta(hours=7), "calendar")
    monkeypatch.setattr(osn, "premarket_window", lambda point: window)
    calls = []

    def fetch(symbol, **kw):
        calls.append(kw.get("venue"))
        return quote(symbol=symbol, session="extended", **{"bid_size": 5000, "ask_size": 5000, **book})

    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price", fetch)
    return window, calls


def test_a_premarket_scheduled_chief_prices_a_limit_on_the_extended_venue(execution, monkeypatch):
    window, calls = _premarket(monkeypatch)
    state = _overnight_state()
    reviewed = state.model_copy(update=chief.risk_gate(state))
    assert reviewed.risk_review.verdict == "approved", reviewed.risk_notes
    order = reviewed.decisions[0]
    assert calls and set(calls) == {"extended"}
    assert order.order_type == "limit" and order.execution_basis["venue"] == "extended"
    assert reviewed.approval_deadline == window.approval_deadline and reviewed.session_venue == "extended"
    assert reviewed.approval_summary.startswith("⏰ 审批截止") and "常规时段" in reviewed.approval_summary


def test_a_manual_premarket_run_keeps_the_regular_rule(execution, monkeypatch):
    _premarket(monkeypatch)
    state = _overnight_state().model_copy(update={"source": "chief"})
    reviewed = state.model_copy(update=chief.risk_gate(state))
    assert reviewed.risk_review.verdict == "rejected" and reviewed.normalization_errors


def test_a_thin_premarket_book_bounds_the_order(execution, monkeypatch):
    _premarket(monkeypatch, bid_size=2, ask_size=2)
    reviewed = _overnight_state(notional=5000)
    reviewed = reviewed.model_copy(update=chief.risk_gate(reviewed))
    assert [v.rule_id for v in reviewed.risk_review.violations] == ["overnight_book_depth"]


def test_an_extended_quote_is_checked_against_whichever_session_is_open(monkeypatch):
    monkeypatch.setattr(osn, "premarket_window", lambda point: object())
    monkeypatch.setattr(prices, "session_context", lambda now: (False, now - timedelta(hours=16)))
    assert prices.validate_quote(quote(session="extended"), symbol="AAPL", venue="extended").session == "extended"
    with pytest.raises(prices.PriceUnavailable, match="outside_extended"):
        prices.validate_quote(quote(session="overnight"), symbol="AAPL", venue="extended")
    monkeypatch.setattr(osn, "premarket_window", lambda point: None)                 # after-hours / closed
    with pytest.raises(prices.PriceUnavailable, match="outside_extended"):
        prices.validate_quote(quote(session="extended"), symbol="AAPL", venue="extended")
    monkeypatch.setattr(prices, "session_context", lambda now: (True, now - timedelta(hours=16)))   # opened
    assert prices.validate_quote(quote(), symbol="AAPL", venue="extended").session == "regular"
    with pytest.raises(prices.PriceUnavailable, match="outside_regular"):
        prices.validate_quote(quote(session="extended"), symbol="AAPL", venue="extended")
    assert prices.validate_quote(quote(session="extended"), symbol="AAPL", venue="extended", as_reference=True)


def test_a_premarket_order_approved_after_the_open_passes_the_execution_check(execution, monkeypatch):
    _premarket(monkeypatch)
    state = _overnight_state()
    state = state.model_copy(update=chief.risk_gate(state))
    state = state.model_copy(update=chief.persist_decision(state))
    approval = BossApproval(status="approved", reviewer="test-human", channel="test", reviewed_at=datetime.now(UTC))
    chief._record_approval(state, approval)
    state = state.model_copy(update={"approval": approval})
    monkeypatch.setattr(prices, "session_context", lambda now: (True, now - timedelta(hours=16)))   # the open
    monkeypatch.setattr("ats.data.runtime.execution_prices.fetch_execution_price",
                        lambda symbol, **kw: quote(symbol=symbol, bid_size=5000, ask_size=5000))
    out = chief.trader(state)
    assert out["gate_outcome"] == "placed", out.get("gate_rejections")
    assert execution.broker.placed[0][0].execution_basis["venue"] == "extended"


def test_an_extended_order_goes_to_smart_outside_regular_hours(execution, monkeypatch):
    from phase_f_broker_harness import FakeIB, broker_for
    from test_phase_f_trader_a import ready_state as ready

    decision = ready(execution).decisions[0]
    guard.reset_for_tests()
    sent = []

    class RecordingIB(FakeIB):
        def placeOrder(self, contract, order):
            sent.append((contract.exchange, order.outsideRth, order.orderType))
            return super().placeOrder(contract, order)

    with monkeypatch.context() as local:
        local.delenv("ATS_BROKER_WRITE_PROHIBITION", raising=False)
        local.delenv("ATS_RUN_MODE", raising=False)
        guard.grant_write("simulation", 1, environment="paper", account="DU1")
        for update in ({"order_type": "limit", "limit_price": 100.6}, {"order_type": "market", "limit_price": None}):
            d = decision.model_copy(update={**update, "execution_basis": {**decision.execution_basis,
                                                                          "venue": "extended"}})
            rows = broker_for(RecordingIB()).place_orders([(d, d.qty)], "ext", execution_check=lambda d, q: {})
    assert sent == [("SMART", True, "LMT")]                            # the market order never reached the broker
    assert "extended_order_requires_limit" in rows[0].error


def test_a_tick_replayed_after_its_approval_deadline_is_not_run(world, monkeypatch):
    """A restart replays last night's 21:00 Taipei tick the next morning: it must not start a cycle now."""
    from test_trade_schedules import _resume

    _resume("chief-daily")
    calls = []
    monkeypatch.setitem(ts._RUNNERS, "chief-daily", lambda entry: calls.append(1) or {"outcome": "pending_approval"})
    tick = _tick("chief-daily", tpe(11, 24, 21, 0))                    # pre-market, deadline 11-25 01:00 Taipei
    out = ts.run_trade_workflow("chief-daily", ENTRY, tick, current=tpe(11, 25, 11, 40))
    assert out["reason"] == "skipped_session_closed" and out["detail"] == "tick_session_expired" and not calls
    on_time = ts.run_trade_workflow("chief-daily", ENTRY, tick, current=tpe(11, 24, 21, 1))
    assert on_time["outcome"] == "pending_approval" and calls == [1]
