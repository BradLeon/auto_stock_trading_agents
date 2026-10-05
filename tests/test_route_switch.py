"""The atomic route-switch protocol (Phase F 2.6).

These tests are about ORDER, not about the individual steps working. Each step is
individually trivial; the value is in the guarantees that only hold because of the
sequence, and the two ways the sequence can be wrong (drain-then-freeze,
open-then-bump) both fail silently rather than loudly.
"""

import importlib.util
import os
import sqlite3
from pathlib import Path

import pytest

from ats.execution import broker_write_guard as guard
from ats.execution import route_registry as rr
from ats.execution import route_switch as rs
from ats.execution.authorization import AuthorizationError, bind_to_active_route
from ats.execution.authorization_lifecycle import AuthorizationLifecycle


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "routes.sqlite"
    monkeypatch.setenv("ATS_ROUTE_REGISTRY_PATH", str(path))
    rr.install_route("A", generation=1, environment="paper", account="DU1",
                     actor="test", reason="fixture", path=str(path))
    guard.reset_for_tests()
    yield path
    guard.reset_for_tests()


def _settled() -> AuthorizationLifecycle:
    return AuthorizationLifecycle(
        cycle_status="executed",
        order_rows=[{"order_id": "o1", "status": "filled"}])


def _grant(account: str = "DU1") -> None:
    guard.grant_write("A", 1, account=account)


def _submit() -> None:
    guard.check_grant(operation="place_orders", caller="test",
                      state_reader=rr.read_state, freeze_reader=rr.read_freeze,
                      account="DU1")


# --------------------------------------------------------------------------- #
# the happy path, and the step order it asserts
# --------------------------------------------------------------------------- #

def test_the_four_steps_run_in_order_and_the_generation_advances(registry):
    """Not just "the switch worked" — the order is the deliverable.

    The report is the evidence an operator reads afterwards, so it records the
    steps rather than only the outcome: a switch that reached `bump` but silently
    skipped `drain` would look identical from the generation alone.
    """
    rr.record_issuance("auth-1", path=str(registry))
    report = rs.perform_switch("B", actor="op", reason="routine",
                               lifecycle=_settled(), environment="paper",
                               account="DU1", path=str(registry))

    assert report.steps_completed == ["freeze", "drain", "bump", "open"]
    assert report.succeeded
    assert (report.from_generation, report.to_generation) == (1, 2)
    assert rr.read_freeze(str(registry)).frozen is False
    assert rr.read_state(str(registry)).route_id == "B"


def test_submissions_work_again_after_a_completed_switch(registry):
    """A switch that never reopened would leave the installation unable to trade."""
    rs.perform_switch("B", actor="op", lifecycle=_settled(), environment="paper",
                      account="DU1", path=str(registry))

    # The pre-switch grant is dead — the generation moved, which is exactly what
    # makes A→B→A distinguishable from A→A.
    _grant()
    with pytest.raises(guard.BrokerWriteProhibited) as excinfo:
        _submit()
    assert excinfo.value.reason_code == guard.REASON_GENERATION_STALE

    guard.grant_write("B", 2, account="DU1")
    _submit()


# --------------------------------------------------------------------------- #
# step 1 must actually close the submission path
# --------------------------------------------------------------------------- #

def test_a_freeze_stops_submissions_before_the_generation_moves(registry):
    """The reason freeze is checked before the generation comparison.

    During step 2 the generation is still the old one, so a grant that is valid by
    generation would sail through a cutover in progress. Freezing first is what
    makes "drain" an interval rather than an instant.
    """
    _grant()
    _submit()  # the baseline: this grant works while nothing is frozen

    freeze = rr.freeze_submissions(actor="op", reason="switch", path=str(registry))
    with pytest.raises(guard.BrokerWriteProhibited) as excinfo:
        _submit()

    assert excinfo.value.reason_code == guard.REASON_FROZEN
    # Not a stale generation — nothing has moved yet. Conflating the two would
    # make a cutover in progress indistinguishable from a revoked capability.
    assert rr.read_state(str(registry)).generation == freeze.from_generation
    rr.abort_freeze(freeze.switch_token, path=str(registry))
    _submit()


def test_binding_refuses_while_frozen(registry):
    """Binding is issuance, so a freeze that only stopped submits would be porous.

    An authorization signed during the drain would be executable against a route the
    switch is about to retire, which is the exact race the freeze exists to remove.
    """
    from ats.execution.authorization import ExecutionAuthorization

    auth = ExecutionAuthorization(
        cycle_id="c1", revision_no=1, decision_hash="dh", review_id="r1",
        review_at="2026-10-05T00:00:00+00:00", approval_id="a1",
        approval_at="2026-10-05T00:00:01+00:00", ruleset_version="v1",
        portfolio_snapshot_id="pf1", market_as_of="2026-10-05T00:00:00+00:00")

    freeze = rr.freeze_submissions(actor="op", reason="switch", path=str(registry))
    with pytest.raises(AuthorizationError, match="frozen"):
        bind_to_active_route(auth)

    rr.abort_freeze(freeze.switch_token, path=str(registry))
    bound = bind_to_active_route(auth)
    assert (bound.route_id, bound.route_generation) == ("A", 1)


# --------------------------------------------------------------------------- #
# the two orderings that fail silently
# --------------------------------------------------------------------------- #

def test_a_concurrent_issuance_after_the_freeze_voids_the_switch(registry):
    """Task 2.6's first named scenario.

    A drain performed BEFORE the freeze can be true when taken and false when acted
    on. The counter comparison is what closes that window: any issuance after the
    freeze means the drain inspected a state that no longer holds.
    """
    rr.record_issuance("auth-1", path=str(registry))

    # Take the freeze the way the protocol does, then let a rogue signer through.
    freeze = rr.freeze_submissions(actor="op", reason="switch", path=str(registry))
    # Simulate a signer that did not consult the freeze (another process, older
    # code, a manual approval written directly to the audit trail).
    conn = sqlite3.connect(registry)
    conn.execute("UPDATE trade_route_issuance SET counter=counter+1, "
                 "last_authorization_id='rogue' WHERE singleton=1")
    conn.commit()
    conn.close()

    reasons = rs._drain_reasons(freeze, _settled(), path=str(registry))
    assert any("signed after the freeze" in reason for reason in reasons)


def test_an_unfinished_order_blocks_the_switch_and_reports_which(registry):
    """The retryable half of the drain: real outstanding work, with identifiers.

    Reported per order because "there is something unfinished" is not actionable;
    the operator needs the order id to go and settle it.
    """
    live = AuthorizationLifecycle(
        cycle_status="executed",
        order_rows=[{"order_id": "o9", "status": "submitted"}])

    report = rs.perform_switch("B", actor="op", lifecycle=live, path=str(registry))

    assert not report.succeeded
    assert any("o9" in reason for reason in report.reasons)
    assert "drain" not in report.steps_completed
    # Nothing was applied, so the route is untouched.
    assert rr.read_state(str(registry)).route_id == "A"
    assert rr.read_state(str(registry)).generation == 1


def test_a_live_cycle_alone_blocks_the_switch(registry):
    """No unfinished orders is not sufficient — the cycle itself may yet submit."""
    live = AuthorizationLifecycle(cycle_status="approved", order_rows=[])
    report = rs.perform_switch("B", actor="op", lifecycle=live, path=str(registry))

    assert not report.succeeded
    assert any("still live" in reason for reason in report.reasons)


def test_an_unknown_order_status_blocks_rather_than_passes(registry):
    """Local state that cannot say is not local state that says "finished"."""
    unknown = AuthorizationLifecycle(
        cycle_status="executed",
        order_rows=[{"order_id": "o1", "status": ""}])
    report = rs.perform_switch("B", actor="op", lifecycle=unknown, path=str(registry))

    assert not report.succeeded
    assert any("unknown" in reason for reason in report.reasons)


def test_a_switch_without_a_lifecycle_refuses(registry):
    """Absent evidence is not evidence of an empty queue."""
    report = rs.perform_switch("B", actor="op", path=str(registry))
    assert not report.succeeded
    assert any("no lifecycle evidence" in reason for reason in report.reasons)
    assert rr.read_state(str(registry)).route_id == "A"
    assert rr.read_freeze(str(registry)).frozen is False


# --------------------------------------------------------------------------- #
# abort releases the freeze; nothing is applied
# --------------------------------------------------------------------------- #

def test_an_aborted_switch_releases_the_freeze_without_bumping(registry):
    """Abort is not rollback: nothing changed, so nothing needs re-approving.

    If the abort bumped the generation it would retire every authorization signed
    before the attempt — turning a failed attempt into a trading outage.
    """
    live = AuthorizationLifecycle(cycle_status="approved", order_rows=[])
    report = rs.perform_switch("B", actor="op", lifecycle=live, path=str(registry))

    assert not report.succeeded
    assert rr.read_freeze(str(registry)).frozen is False
    assert rr.read_state(str(registry)).generation == 1

    # The pre-existing grant still works: the world was left as it was found.
    _grant()
    _submit()


def test_a_failed_switch_leaves_no_frozen_registry(registry):
    """A wedge here would be a silent outage — nobody would know why trades stopped."""
    for lifecycle in (AuthorizationLifecycle(cycle_status="approved", order_rows=[]),
                      None,
                      AuthorizationLifecycle(cycle_status="executed",
                                              order_rows=[{"order_id": "x",
                                                           "status": "partial"}])):
        rs.perform_switch("B", actor="op", lifecycle=lifecycle, path=str(registry))
        assert rr.read_freeze(str(registry)).frozen is False


# --------------------------------------------------------------------------- #
# crash between bump and open (task 2.6's second named scenario)
# --------------------------------------------------------------------------- #

def test_a_crash_after_the_bump_leaves_no_submittable_route(registry):
    """Task 2.6's second named scenario, stated as the outcome it must produce.

    Simulated by freezing, bumping, and abandoning — exactly the state a process
    death between step 3 and step 4 leaves behind. Nothing may submit, because the
    switch's outcome is unknown and half-applied.
    """
    freeze = rr.freeze_submissions(actor="op", reason="switch", path=str(registry))
    rr.switch_route(freeze.from_generation, "B", environment="paper",
                    account="DU1", actor="op", reason="switch", path=str(registry))
    # No open_submissions: the crash.

    _grant()
    with pytest.raises(guard.BrokerWriteProhibited) as excinfo:
        _submit()
    assert excinfo.value.reason_code == guard.REASON_FROZEN

    recovery = rs.recover_interrupted_switch(registry)
    assert recovery["interrupted"] and recovery["can_submit"] is False
    assert recovery["generation_moved"] is True
    assert recovery["stage"] == "bump_completed_open_pending"


def test_recovery_reports_the_other_interruption_stage(registry):
    """A crash between freeze and bump looks different and must not be confused."""
    rr.freeze_submissions(actor="op", reason="switch", path=str(registry))
    recovery = rs.recover_interrupted_switch(registry)

    assert recovery["generation_moved"] is False
    assert recovery["stage"] == "freeze_completed_drain_pending"
    assert recovery["remedies"]


def test_recovery_does_not_decide_on_its_which_side_to_take(registry):
    """Automatic completion would turn a crash into a cutover nobody asked for."""
    freeze = rr.freeze_submissions(actor="op", reason="switch", path=str(registry))
    rr.switch_route(freeze.from_generation, "B", environment="paper",
                    account="DU1", path=str(registry))

    # Reading recovery repeatedly must not mutate anything.
    before = rr.read_state(str(registry))
    rs.recover_interrupted_switch(registry)
    rs.recover_interrupted_switch(registry)
    assert rr.read_state(str(registry)).same_as(before)
    assert rr.read_freeze(str(registry)).frozen is True


def test_opening_after_recovery_does_not_bump_again(registry):
    """The generation already moved; bumping twice would kill live authorizations.

    This is the trap in finishing an interrupted switch: the natural-looking
    "re-run the switch" would retire everything signed against the route the
    operator is trying to keep.
    """
    freeze = rr.freeze_submissions(actor="op", reason="switch", path=str(registry))
    rr.switch_route(freeze.from_generation, "B", environment="paper",
                    account="DU1", path=str(registry))

    state = rs.open_after_recovery(freeze.switch_token, path=str(registry))
    assert state.generation == freeze.from_generation + 1
    assert rr.read_freeze(str(registry)).frozen is False
    guard.grant_write("B", state.generation, account="DU1")
    _submit()


def test_aborting_after_recovery_reopens_on_the_current_generation(registry):
    """The other remedy, and it must be genuinely available."""
    freeze = rr.freeze_submissions(actor="op", reason="switch", path=str(registry))
    rr.switch_route(freeze.from_generation, "B", environment="paper",
                    account="DU1", path=str(registry))

    state = rs.abort_after_recovery(freeze.switch_token, actor="op",
                                    reason="changed my mind", path=str(registry))
    assert rr.read_freeze(str(registry)).frozen is False
    assert state.generation == 2
    guard.grant_write(state.route_id, state.generation, account="DU1")
    _submit()


# --------------------------------------------------------------------------- #
# ownership of the attempt
# --------------------------------------------------------------------------- #

def test_only_the_attempt_that_froze_may_open(registry):
    """Two operators must not be able to reopen each other's cutover."""
    first = rr.freeze_submissions(actor="op-1", reason="switch", path=str(registry))
    rr.abort_freeze(first.switch_token, path=str(registry))
    second = rr.freeze_submissions(actor="op-2", reason="switch", path=str(registry))

    with pytest.raises(rr.RouteRegistryError, match="token mismatch"):
        rr.open_submissions(first.switch_token, path=str(registry))

    assert rr.read_freeze(str(registry)).frozen is True
    rr.abort_freeze(second.switch_token, path=str(registry))


def test_a_second_freeze_while_frozen_is_refused(registry):
    """Otherwise the first operator's drain would inspect a freeze that moved."""
    rr.freeze_submissions(actor="op-1", reason="switch", path=str(registry))
    with pytest.raises(rr.RouteRegistryError, match="already frozen"):
        rr.freeze_submissions(actor="op-2", reason="switch", path=str(registry))


def test_rebinding_the_same_authorization_does_not_inflate_the_counter(registry):
    """A resumed loop re-validates; counting that as issuance would void switches."""
    rr.record_issuance("auth-1", path=str(registry))
    first = rr.record_issuance("auth-1", path=str(registry))
    assert first == 1

    assert rr.record_issuance("auth-2", path=str(registry)) == 2


def test_the_history_records_the_switch_for_later_audit(registry):
    """A cutover that leaves no history is indistinguishable from a config edit."""
    rs.perform_switch("B", actor="op", reason="planned cutover",
                      lifecycle=_settled(), environment="paper", account="DU1",
                      path=str(registry))
    rows = rr.history(str(registry))
    assert any(row["to_route"] == "B" and row["actor"] == "op" for row in rows)


def test_the_freeze_survives_a_process_restart(registry):
    """It lives in the registry precisely because process memory is per-process.

    Without this, the freeze would only bind the process that froze — and a
    resident scheduler would keep trading through the whole cutover.
    """
    rr.freeze_submissions(actor="op", reason="switch", path=str(registry))
    # Simulate a restart: the in-process state is gone, the file is not.
    guard.reset_for_tests()
    guard.grant_write("A", 1, account="DU1")
    with pytest.raises(guard.BrokerWriteProhibited) as excinfo:
        _submit()
    assert excinfo.value.reason_code == guard.REASON_FROZEN


def test_the_route_registry_is_not_on_the_fingerprint_surface(registry):
    """Recording generations must not invalidate anyone's qualification evidence.

    Asserted because it is easy to break by accident: `execution/` holds modules
    that ARE fingerprint paths (`authorization.py`), and a future move could put
    this one on the surface, at which point every switch would retire evidence.
    """
    from ats.workflow.assurance_surface import load_surface

    assert "src/ats/execution/route_registry.py" not in load_surface().all_paths()
    assert "src/ats/execution/route_switch.py" not in load_surface().all_paths()
