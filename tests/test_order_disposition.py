"""Per-order disposition for a route switch (Phase F 2.7).

The requirement is not "block when unsettled" — 2.4 already does that. It is that
different unsettled states deserve different treatment, and that the one case which
looks settled (`expired`, inferred from silence) is the one that most needs a
second look.
"""

import pytest

from ats.execution import broker_write_guard as guard
from ats.execution import route_registry as rr
from ats.execution import route_switch as rs
from ats.execution.authorization_lifecycle import AuthorizationLifecycle
from ats.execution.order_disposition import (
    BLOCKS_SWITCH,
    CONFIRMED_COMPLETE,
    PENDING_RECONCILIATION,
    disposition_for_order,
    disposition_report,
    late_fill_disposition,
    register_late_fill,
)


def _lifecycle(*rows, cycle_status: str = "executed") -> AuthorizationLifecycle:
    return AuthorizationLifecycle(cycle_status=cycle_status, order_rows=list(rows))


# --------------------------------------------------------------------------- #
# what may be confirmed complete
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("status", ["filled", "cancelled", "rejected", "error"])
def test_a_definitively_resolved_order_is_confirmed_complete(status):
    """The broker acted or refused; nothing about it will change."""
    result = disposition_for_order({"order_id": "o1", "status": status})
    assert result.disposition == CONFIRMED_COMPLETE
    assert not result.blocks_switch


@pytest.mark.parametrize("status", ["partial", "submitted", "pending"])
def test_an_order_the_broker_may_still_act_on_blocks_the_switch(status):
    """`submitted` is the ordinary case, not an edge case.

    `place_orders` polls for three seconds and returns, so an order that is about
    to fill routinely still reads `submitted` when a drain looks at it.
    """
    result = disposition_for_order({"order_id": "o1", "status": status})
    assert result.disposition == BLOCKS_SWITCH
    assert "may still change" in result.reason


def test_an_absent_status_blocks_rather_than_passes():
    """Local state that cannot say is not local state that says "finished"."""
    result = disposition_for_order({"order_id": "o1", "status": ""})
    assert result.disposition == BLOCKS_SWITCH
    assert result.lifecycle == "unknown"


def test_an_unrecognised_status_blocks():
    """A new status from a newer broker client must not be read as terminal."""
    assert disposition_for_order(
        {"order_id": "o1", "status": "weird_new_state"}).blocks_switch


# --------------------------------------------------------------------------- #
# `expired`: terminal by inference, so it needs evidence
# --------------------------------------------------------------------------- #

def test_an_expired_order_with_broker_evidence_is_complete():
    """`terminal_basis='broker'` means the broker told us, so it counts."""
    result = disposition_for_order(
        {"order_id": "o1", "status": "expired", "terminal_basis": "broker"})
    assert result.disposition == CONFIRMED_COMPLETE


def test_an_expired_order_without_evidence_waits_for_reconciliation():
    """The gap this module exists for.

    `reconcile` infers `expired` for a DAY order nobody ever resolved. The
    inference is "we heard nothing", and a late fill is exactly the case where
    hearing nothing was wrong — so the status alone cannot close the order.
    """
    result = disposition_for_order({"order_id": "o1", "status": "expired"})
    assert result.disposition == PENDING_RECONCILIATION
    assert result.reconciliation_required is True
    assert not result.blocks_switch  # routed to reconciliation, not accepted
    assert "inferred from silence" in result.reason


def test_an_inferred_expired_order_blocks_the_switch_until_reconciled():
    """It blocks the switch even though it is not counted as blocking on its own.

    Two different things: `blocks_switch` on the disposition is about what the
    switch may conclude about the order; the switch as a whole must still wait for
    the reconciliation pass that could confirm it.
    """
    from ats.execution import route_registry as registry

    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / "r.sqlite"
    registry.install_route("A", generation=1, environment="paper", account="DU1",
                   path=str(path))
    report = rs.perform_switch(
        "B", actor="op",
        lifecycle=_lifecycle({"order_id": "o1", "status": "expired"}),
        path=str(path))

    assert not report.succeeded
    assert any("inference only" in reason for reason in report.reasons)
    assert registry.read_state(str(path)).route_id == "A"


# --------------------------------------------------------------------------- #
# the report
# --------------------------------------------------------------------------- #

def test_the_report_counts_each_disposition_separately():
    """"There is something outstanding" is not actionable; the breakdown is."""
    report = disposition_report(_lifecycle(
        {"order_id": "o1", "status": "filled"},
        {"order_id": "o2", "status": "partial"},
        {"order_id": "o3", "status": "expired"},
        {"order_id": "o4", "status": "cancelled"}))

    assert report["confirmed_complete"] == 2
    assert report["pending_reconciliation"] == 1
    assert report["blocking"] == 1
    assert report["blocks_switch"] is True
    assert any("o2" in reason for reason in report["reasons"])
    assert any("o3" in reason for reason in report["reasons"])


def test_a_live_cycle_is_reported_even_with_every_order_settled():
    """No unsettled orders is necessary but not sufficient."""
    report = disposition_report(_lifecycle(
        {"order_id": "o1", "status": "filled"}, cycle_status="approved"))
    assert report["confirmed_complete"] == 1
    assert report["blocks_switch"] is True
    assert any("still live" in reason for reason in report["reasons"])


# --------------------------------------------------------------------------- #
# late fills
# --------------------------------------------------------------------------- #

_ORDER = {"order_id": "o1", "status": "expired", "qty": 100,
          "first_submitted_at": "2026-10-01T14:00:00+00:00"}


def test_a_fill_inside_the_submit_day_is_ordinary():
    outcome = late_fill_disposition(_ORDER, {"time": "2026-10-01T18:00:00",
                                             "shares": 100, "price": 10.0})
    assert outcome.late is False
    assert outcome.stop_switch is False


def test_a_late_fill_is_absorbed_by_read_only_reconciliation():
    """The switch does not decide the order is finished; reconciliation does."""
    def _reconcile(order, fill):
        return {"status": "filled"}

    outcome = late_fill_disposition(
        _ORDER, {"time": "2026-10-03T15:00:00", "shares": 100, "price": 10.0},
        reconcile=_reconcile)

    assert outcome.late is True
    assert outcome.recorded_as == "reconciled_read_only"
    assert outcome.resulting_status == "filled"
    # Absorbing is always correct, so it does not stop a switch...
    assert outcome.stop_switch is False


def test_a_late_partial_fill_stops_the_switch():
    """The asymmetry: absorbing the fill is right, continuing past it is not.

    A late fill that only partially covers the order proves the broker was still
    working, so the switch must not proceed even though the order now looks handled.
    """
    outcome = late_fill_disposition(
        _ORDER, {"time": "2026-10-03T15:00:00", "shares": 40, "price": 10.0},
        reconcile=lambda order, fill: {"status": "partial"})

    assert outcome.late is True
    assert outcome.stop_switch is True
    assert "partial" in outcome.reason


def test_a_late_fill_with_no_read_model_stops_the_switch():
    """Unknown after evidence of movement is the case that must stop, not pass."""
    outcome = late_fill_disposition(
        _ORDER, {"time": "2026-10-03T15:00:00", "shares": 40, "price": 10.0})

    assert outcome.late is True
    assert outcome.resulting_status == "unknown"
    assert outcome.stop_switch is True
    assert outcome.recorded_as == "late_fill_unabsorbed"


def test_a_late_fill_is_recorded_so_the_stop_is_auditable(tmp_path):
    """Uses the existing ledger-exception channel rather than a new table."""
    from ats.memory import get_store

    outcome = late_fill_disposition(
        _ORDER, {"time": "2026-10-03T15:00:00", "shares": 40, "price": 10.0})
    store = get_store()
    exception_id = register_late_fill(store, outcome, actor="clerk",
                                      reason="late fill after switch attempt")

    rows = store.conn.execute(
        "SELECT kind, detail_json FROM ledger_exceptions WHERE exception_id=?",
        (exception_id,)).fetchone()
    assert rows is not None
    assert "o1" in rows["detail_json"]
    assert "stop_switch=True" in rows["detail_json"]


# --------------------------------------------------------------------------- #
# read-only reconciliation must not restore the ability to submit
# --------------------------------------------------------------------------- #

def test_reading_a_late_fill_does_not_restore_the_old_route(tmp_path, monkeypatch):
    """Task 2.7's last clause, stated as the outcome.

    Absorbing a late fill is a read-only bookkeeping act. If it handed back
    submission capability — by unfreezing, by re-granting, or by reopening the
    registry — then a reconciliation pass would be a way to undo a cutover without
    going through one.
    """
    path = tmp_path / "routes.sqlite"
    monkeypatch.setenv("ATS_ROUTE_REGISTRY_PATH", str(path))
    rr.install_route("A", generation=1, environment="paper", account="DU1",
                   path=str(path))
    rr.record_issuance("auth-1", path=str(path))
    rs.perform_switch("B", actor="op",
                      lifecycle=_lifecycle({"order_id": "o1", "status": "filled"}),
                      environment="paper", account="DU1", path=str(path))
    guard.reset_for_tests()
    guard.grant_write("A", 1, account="DU1")

    outcome = late_fill_disposition(
        {"order_id": "o9", "status": "submitted", "qty": 100,
         "first_submitted_at": "2026-10-01T14:00:00+00:00"},
        {"time": "2026-10-03T15:00:00", "shares": 40, "price": 10.0})

    # The reconciliation act itself.
    assert outcome.late is True

    # Nothing about it restored the pre-switch capability.
    assert rr.read_freeze(str(path)).frozen is False
    assert rr.read_state(str(path)).route_id == "B"
    with pytest.raises(guard.BrokerWriteProhibited):
        guard.check_grant(operation="place_orders", caller="t",
                          state_reader=rr.read_state, freeze_reader=rr.read_freeze,
                          account="DU1")
