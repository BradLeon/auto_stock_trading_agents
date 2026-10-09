"""Phase F 1.1 — the process-level broker write prohibition.

The shadow/isolated guarantee is only as strong as the weakest call site, so
these tests pin the property at the layer that cannot be bypassed: the broker's
own submit path. A test that only checked "shadow mode sets dry_run" would pass
while a new call site that forgot the flag wrote real orders.
"""

import pytest

from ats.broker.ibkr import IBKRBroker
from ats.execution import broker_write_guard as guard
from ats.schemas.decision import TradeDecision


@pytest.fixture(autouse=True)
def _clean_guard():
    """The prohibition is process state; without this a test would inherit it."""
    guard.reset_for_tests()
    yield
    guard.reset_for_tests()


def _decision(symbol: str = "AMD") -> TradeDecision:
    return TradeDecision(symbol=symbol, action="buy", qty=10,
                         rationale="phase F guard test")


def test_uninstalled_guard_refuses_writes():
    """An uninitialised process has no authority to submit."""
    assert guard.guard_installed() is False
    assert guard.is_prohibited() is False
    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        guard.check_broker_write(operation="place_orders", caller="test")
    assert exc.value.reason_code == guard.REASON_NO_GRANT


def test_installed_prohibition_refuses_every_submit():
    guard.prohibit_broker_writes()

    assert guard.is_prohibited() is True

    with pytest.raises(guard.BrokerWriteProhibited) as first:
        guard.check_broker_write(operation="place_orders", caller="test", symbol="AMD")
    with pytest.raises(guard.BrokerWriteProhibited):
        guard.check_broker_write(operation="place_orders", caller="test", symbol="NVDA")

    assert first.value.reason_code == guard.REASON_SHADOW_RUN


def test_refusal_is_an_auditable_record_not_a_silent_no_op():
    """A shadow run that placed nothing must be distinguishable from one that
    never tried. Every refusal carries an id, a reason and the caller."""
    guard.prohibit_broker_writes()

    with pytest.raises(guard.BrokerWriteProhibited) as excinfo:
        guard.check_broker_write(operation="place_orders", caller="ats.trader.execute",
                                 symbol="AMD", quantity=10.0, detail="cycle-1")

    rows = guard.refusal_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["refusal_id"] == excinfo.value.refusal_id
    assert row["reason_code"] == guard.REASON_SHADOW_RUN
    assert row["operation"] == "place_orders"
    assert row["caller"] == "ats.trader.execute"
    assert row["symbol"] == "AMD" and row["quantity"] == 10.0
    assert "cycle-1" in row["detail"]
    assert row["at"]


def test_broker_submit_layer_is_guarded_so_a_new_call_site_cannot_bypass_it():
    """The check lives in `place_orders`, not in a caller.

    This is the whole point of the design: a call site added next year, or one
    that forgets `dry_run`, still cannot write during a shadow run.
    """
    guard.prohibit_broker_writes()
    broker = IBKRBroker.__new__(IBKRBroker)  # no TWS connection is attempted

    with pytest.raises(guard.BrokerWriteProhibited):
        broker.place_orders([(_decision(), 10.0)], cycle_id="cycle-1")

    assert guard.refusal_rows()[0]["caller"] == "IBKRBroker.place_orders"


def test_empty_batch_is_not_a_refusal():
    """No intent, no refusal — otherwise a no-op cycle would pollute the
    evidence with attempts that were never made."""
    guard.prohibit_broker_writes()
    broker = IBKRBroker.__new__(IBKRBroker)

    assert broker.place_orders([], cycle_id="cycle-1") == []
    assert guard.refusals() == []


def test_a_refused_order_does_not_fail_the_shadow_run():
    """The prohibition must be *observable*, not fatal to the run that tests it.

    If a blocked submit raised through the shadow runner and aborted the cycle,
    the guarantee would hold only by disabling the very comparison it exists to
    enable.
    """
    guard.prohibit_broker_writes()

    refusals = []
    for symbol in ("AMD", "NVDA"):
        try:
            guard.check_broker_write(operation="place_orders", caller="shadow",
                                     symbol=symbol)
        except guard.BrokerWriteProhibited as exc:
            refusals.append(exc.refusal_id)

    assert len(refusals) == 2 and guard.refusals()  # the run continued


def test_runner_refuses_to_start_when_the_capability_is_missing():
    """`is_prohibited()` is False before installation — treating that as
    permission is how "the capability was never checked" becomes a green run."""
    with pytest.raises(guard.BrokerWriteProhibited) as excinfo:
        guard.assert_broker_writes_prohibited(operation="shadow_run", caller="test")

    assert excinfo.value.reason_code == "guard_missing"


def test_runner_refuses_to_start_when_the_guard_does_not_prohibit():
    """Installed-but-permitting is a distinct misconfiguration from absent, and
    must not read as ready."""
    guard.install_prohibition(guard.REASON_SHADOW_RUN)
    # Simulate a guard that exists but is not prohibiting.
    guard._STATE.mode = "permitted"

    with pytest.raises(guard.BrokerWriteProhibited) as excinfo:
        guard.assert_broker_writes_prohibited(operation="shadow_run", caller="test")

    assert excinfo.value.reason_code == "guard_not_prohibiting"


def test_runner_starts_once_the_capability_is_present():
    guard.prohibit_broker_writes()
    guard.assert_broker_writes_prohibited(operation="shadow_run", caller="test")


def test_first_prohibition_reason_wins_so_history_is_not_rewritten():
    """Re-prohibiting with a different reason must not restate why an earlier
    attempt was refused."""
    guard.prohibit_broker_writes(reason_code=guard.REASON_SHADOW_RUN)
    guard.prohibit_broker_writes(reason_code=guard.REASON_ISOLATED)

    with pytest.raises(guard.BrokerWriteProhibited) as excinfo:
        guard.check_broker_write(operation="place_orders", caller="test")

    assert excinfo.value.reason_code == guard.REASON_SHADOW_RUN


def test_unknown_reason_code_is_rejected_at_installation():
    with pytest.raises(ValueError, match="unknown broker write prohibition"):
        guard.prohibit_broker_writes(reason_code="because_i_said_so")


def test_environment_can_install_the_prohibition_for_a_resident_process(monkeypatch):
    """The scheduler is long-lived; arming it must not require a code change."""
    monkeypatch.setenv("ATS_BROKER_WRITE_PROHIBITION", "shadow")

    assert guard.install_from_environment() is True
    assert guard.is_prohibited() is True
    with pytest.raises(guard.BrokerWriteProhibited):
        guard.check_broker_write(operation="place_orders", caller="scheduler")


def test_environment_never_lifts_a_prohibition(monkeypatch):
    """There is deliberately no env var that enables writes. If one appeared it
    would be the easiest way to defeat the guarantee."""
    guard.prohibit_broker_writes()
    monkeypatch.setenv("ATS_BROKER_WRITE_PROHIBITION", "")
    monkeypatch.setenv("ATS_BROKER_WRITE_ALLOWED", "1")

    assert guard.install_from_environment() is True
    assert guard.is_prohibited() is True


def test_environment_reflects_the_isolated_reason(monkeypatch):
    monkeypatch.setenv("ATS_BROKER_WRITE_PROHIBITION", "1")
    monkeypatch.setenv("ATS_RUN_MODE", "isolated")

    guard.install_from_environment()
    with pytest.raises(guard.BrokerWriteProhibited) as excinfo:
        guard.check_broker_write(operation="place_orders", caller="test")

    assert excinfo.value.reason_code == guard.REASON_ISOLATED
