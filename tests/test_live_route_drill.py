"""Live-trading gate and route drills (tasks 11.1–11.3).

Three permissions exist and confusing any two is the failure this work exists to
prevent: **deployment** authorisation (routes may change), **execution**
authorisation (a revision passed risk review), and **live** authorisation (a
human approved sending real orders). Only the third says money may move.

Most tests here assert refusals, because the dangerous states are the ones where
everything else is green:

- every gate green, no live authorisation — where an unauthorised change looks
  most reasonable, so it must be the default-deny
- a *deployment* authorisation offered for the live switch — the two name
  different actions
- a **shadow run's own output** offered as authority — the subtle one, since a
  self-issued grant is exactly how "simulated" and "somebody authorised this"
  stop meaning anything

One test pins the property that makes a drill honest rather than decorative: a
green drill must not open the live gate, whatever reference it is handed.
"""

from datetime import datetime, timedelta, timezone

import pytest

from ats.execution import live_authorisation as live
from ats.execution import route_drill as drill

FUTURE = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
PAST = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


@pytest.fixture
def live_db(tmp_path) -> str:
    return str(tmp_path / "live.sqlite")


def _auth(**overrides) -> live.LiveAuthorisation:
    fields = dict(reference="LIVE-2026-10-07", issuer=live.ISSUER_HUMAN,
                  authorised_by="account owner", issued_by="ops",
                  scope=("trader",), environment="live", account="U123",
                  valid_until=FUTURE, note="")
    fields.update(overrides)
    return live.LiveAuthorisation(**fields)


def _auth_missing(field: str) -> live.LiveAuthorisation:
    """An authorisation with one auditable field blank.

    Built from a blank slate rather than from `_auth` with an override, because
    a defaulted field would survive the "override" and the case would not
    actually be exercised — which is what happened on the first attempt.
    """
    fields = dict(reference="LIVE-incomplete", issuer=live.ISSUER_HUMAN,
                  authorised_by="owner", issued_by="ops", scope=("trader",),
                  environment="live", account="U123", valid_until=FUTURE, note="")
    fields[field] = ""
    return live.LiveAuthorisation(**fields)


# --- 11.1 the gate refuses ---------------------------------------------------

def test_no_live_authorisation_is_refused_even_though_everything_else_may_be_green(
        live_db):
    """The state in which an unauthorised change looks most reasonable."""
    decision = live.evaluate_live_switch(authorisation_ref="", path=live_db)
    assert decision.opened is False
    assert decision.reason == live.REASON_NO_LIVE_AUTHORISATION
    assert "deployment authorisation" in decision.detail, (
        "the refusal must say WHY this is a separate permission, or an operator "
        "who has a deployment approval in hand reads it as an oversight")


def test_a_deployment_authorisation_cannot_authorise_the_live_switch(live_db):
    """11.1's third case. The two name different actions."""
    decision = live.evaluate_live_switch(authorisation_ref="DEP-2026-10-06-A",
                                         path=live_db)
    assert decision.opened is False
    assert decision.reason == live.REASON_NOT_AUDITABLE
    assert "DEP-" in decision.detail


def test_an_unrecorded_live_reference_is_refused(live_db):
    decision = live.evaluate_live_switch(authorisation_ref="LIVE-never-issued",
                                         path=live_db)
    assert decision.opened is False
    assert decision.reason == live.REASON_NO_LIVE_AUTHORISATION


def test_an_expired_authorisation_is_refused(live_db):
    live.record_authorisation(_auth(valid_until=PAST), path=live_db)
    decision = live.evaluate_live_switch(authorisation_ref="LIVE-2026-10-07",
                                         path=live_db)
    assert decision.opened is False
    assert decision.reason == live.REASON_EXPIRED


@pytest.mark.parametrize("missing", ["authorised_by", "issued_by", "environment",
                                     "account", "valid_until", "issuer"])
def test_an_incomplete_authorisation_is_not_stored(live_db, missing):
    """Incomplete means unauditable, not weaker-but-valid."""
    with pytest.raises(live.LiveAuthorisationError) as excinfo:
        live.record_authorisation(_auth_missing(missing), path=live_db)
    assert missing in str(excinfo.value)
    assert live.read_authorisation("LIVE-incomplete", path=live_db) is None


def test_an_empty_scope_is_not_stored(live_db):
    with pytest.raises(live.LiveAuthorisationError):
        live.record_authorisation(_auth(scope=()), path=live_db)


def test_an_authorisation_must_be_referenced_as_live(live_db):
    """Reusing a deployment reference is the confusion this gate prevents."""
    with pytest.raises(live.LiveAuthorisationError) as excinfo:
        live.record_authorisation(_auth(reference="DEP-something"), path=live_db)
    assert live.LIVE_PREFIX in str(excinfo.value)


def test_recording_the_same_reference_again_is_refused(live_db):
    """Overwriting would silently widen the scope and hide that the grant changed."""
    live.record_authorisation(_auth(), path=live_db)
    with pytest.raises(live.LiveAuthorisationError) as excinfo:
        live.record_authorisation(_auth(authorised_by="someone else",
                                        scope=("trader", "clerk")), path=live_db)
    assert "already recorded" in str(excinfo.value)
    assert live.read_authorisation("LIVE-2026-10-07", path=live_db).scope == ("trader",)


# --- 11.2 provenance and scope ----------------------------------------------

@pytest.mark.parametrize("issuer", [live.ISSUER_SHADOW, live.ISSUER_PAPER])
def test_a_shadow_or_paper_run_may_not_grant_itself_authority(live_db, issuer):
    """The subtle case: a self-issued grant collapses "simulated" into "authorised"."""
    with pytest.raises(live.LiveAuthorisationError) as excinfo:
        live.record_authorisation(_auth(reference="LIVE-self", issuer=issuer,
                                        authorised_by=issuer, issued_by=issuer),
                                  path=live_db)
    assert live.REASON_SELF_ISSUED in str(excinfo.value)


def test_a_shadow_label_cannot_open_the_gate(live_db):
    """11.2: a real authorisation, seen by a shadow run, is a mirror not a source."""
    auth = _auth()
    live.record_authorisation(auth, path=live_db)
    label = live.as_shadow_reference(auth)
    assert label.startswith("shadow:")

    decision = live.evaluate_live_switch(
        authorisation_ref=label, consumer_id="trader", environment="live",
        account="U123", path=live_db)
    assert decision.opened is False
    assert decision.reason == live.REASON_NOT_AUDITABLE


def test_a_shadow_label_for_no_authorisation_is_explicit(live_db):
    assert live.as_shadow_reference(None) == "shadow:none"


def test_an_authorisation_for_paper_does_not_cover_live(live_db):
    live.record_authorisation(_auth(environment="paper", account="DU123"),
                              path=live_db)
    decision = live.evaluate_live_switch(
        authorisation_ref="LIVE-2026-10-07", consumer_id="trader",
        environment="live", account="DU123", path=live_db)
    assert decision.opened is False
    assert decision.reason == live.REASON_ENVIRONMENT_MISMATCH


def test_an_authorisation_for_one_account_does_not_cover_another(live_db):
    live.record_authorisation(_auth(account="U123"), path=live_db)
    decision = live.evaluate_live_switch(
        authorisation_ref="LIVE-2026-10-07", consumer_id="trader",
        environment="live", account="U999", path=live_db)
    assert decision.opened is False
    assert decision.reason == live.REASON_ACCOUNT_MISMATCH


def test_an_authorisation_omitting_a_consumer_holds_exactly_that_one(live_db):
    live.record_authorisation(_auth(scope=("trader",)), path=live_db)
    inside = live.evaluate_live_switch(
        authorisation_ref="LIVE-2026-10-07", consumer_id="trader",
        environment="live", account="U123", path=live_db)
    outside = live.evaluate_live_switch(
        authorisation_ref="LIVE-2026-10-07", consumer_id="clerk",
        environment="live", account="U123", path=live_db)
    assert inside.opened is True
    assert outside.opened is False
    assert outside.reason == live.REASON_OUT_OF_SCOPE


def test_a_valid_authorisation_opens_the_gate(live_db):
    """The positive case, so the refusals above are refusals rather than breakage."""
    live.record_authorisation(_auth(), path=live_db)
    decision = live.assert_live_authorised(
        authorisation_ref="LIVE-2026-10-07", consumer_id="trader",
        environment="live", account="U123", path=live_db)
    assert decision.opened is True
    assert "authorised by account owner" in decision.detail


def test_the_helper_raises_rather_than_returning_a_verdict(live_db):
    """The caller's next action is a real order; a boolean invites proceeding anyway."""
    with pytest.raises(live.LiveAuthorisationError):
        live.assert_live_authorised(authorisation_ref="", path=live_db)


def test_each_refusal_carries_a_distinct_reason(live_db):
    """A wall of identical text tells an operator nothing about which gate to fix."""
    live.record_authorisation(_auth(environment="paper"), path=live_db)
    reasons = {
        live.evaluate_live_switch(authorisation_ref="", path=live_db).reason,
        live.evaluate_live_switch(authorisation_ref="DEP-x", path=live_db).reason,
        live.evaluate_live_switch(authorisation_ref="LIVE-none", path=live_db).reason,
        live.evaluate_live_switch(authorisation_ref="LIVE-2026-10-07",
                                  environment="live", account="U123",
                                  path=live_db).reason,
    }
    assert len(reasons) == 3, reasons


# --- 11.3 the drill ----------------------------------------------------------

@pytest.fixture
def drill_base(tmp_path) -> str:
    return str(tmp_path / "registry.sqlite")


def test_a_drill_writes_to_its_own_store_and_creates_no_real_one(drill_base):
    """A drill recorded beside real switches is indistinguishable at exactly the
    point where someone asks "was this authorised?"."""
    import os

    result = drill.run_switch_drill(drill_id="d1", base_path=drill_base,
                                    environment="paper", account="DU1")
    assert result.outcome == "completed"
    assert os.path.exists(drill.drill_path(drill_base, "d1"))
    assert not os.path.exists(drill_base), (
        "the drill created the real registry — it must never touch it")


def test_the_drill_record_declares_itself_a_drill(drill_base):
    result = drill.run_switch_drill(drill_id="d1", base_path=drill_base,
                                    environment="paper", account="DU1")
    row = result.as_row()
    assert row["mode"] == "drill"
    assert "limits" in row and row["limits"], (
        "a drill that does not say what it did not prove reads as though it "
        "proved everything")


def test_the_four_atomic_steps_hold(drill_base):
    """11.3's core: freeze, drain, bump, open — in that order, all four."""
    result = drill.run_switch_drill(drill_id="d1", base_path=drill_base,
                                    environment="paper", account="DU1")
    held = [s.name for s in result.steps if s.held]
    assert held[:4] == list(drill.ATOMIC_STEPS)
    assert result.failures == []


def test_the_generation_advances_by_exactly_one(drill_base):
    """The property cross-process single-activation rests on."""
    result = drill.run_switch_drill(drill_id="d1", base_path=drill_base,
                                    environment="paper", account="DU1")
    switch = result.generations[-1]
    assert switch["to_generation"] == switch["from_generation"] + 1


def test_a_drill_never_opens_the_live_gate_even_with_a_valid_reference(drill_base):
    """The property that makes a drill honest rather than decorative."""
    import tempfile
    import pathlib

    live_db = str(pathlib.Path(drill_base).parent / "live.sqlite")
    live.record_authorisation(_auth(), path=live_db)

    result = drill.run_switch_drill(
        drill_id="d1", base_path=drill_base, environment="live", account="U123",
        live_authorisation_ref="LIVE-2026-10-07")
    gate = next(s for s in result.steps if s.name == "live_gate_consulted")
    assert gate.held is True, "a drill must not be able to open the live gate"
    assert "refused" in gate.detail or live.REASON_NO_LIVE_AUTHORISATION in gate.detail


def test_a_drill_with_an_unfinished_order_stops_at_the_drain(drill_base):
    """Group 2 refuses without lifecycle evidence; the drill must not paper over it."""
    from ats.execution.authorization_lifecycle import AuthorizationLifecycle

    live_cycle = AuthorizationLifecycle(cycle_status="live", order_rows=())
    result = drill.run_switch_drill(
        drill_id="d1", base_path=drill_base, environment="paper", account="DU1",
        lifecycle=live_cycle,
        expect_failures=[f"atomic step {name!r} did not hold"
                         for name in ("drain", "bump", "open")])
    drain = next(s for s in result.steps if s.name == "drain")
    assert drain.held is False


def test_a_rollback_needs_live_authorisation_too(drill_base):
    """A rollback changes who may send orders exactly as much as a switch does."""
    result = drill.run_rollback_drill(drill_id="d1", base_path=drill_base,
                                      environment="paper", account="DU1")
    assert result.rollback["performed"] is False
    assert result.rollback["reason"] == live.REASON_NO_LIVE_AUTHORISATION
    step = result.steps[0]
    assert step.name == "rollback_authorised"
    assert step.held is True, (
        "the refusal IS the passing outcome here — a rollback taken without live "
        "authority is the failure this is testing for")


def test_an_unavailable_fallback_means_holding_not_forcing(drill_base, tmp_path):
    """Switching to a fallback that cannot serve traffic turns one outage into a
    different one."""
    result = drill.run_rollback_drill(drill_id="d1", base_path=drill_base,
                                      environment="paper", account="DU1",
                                      fallback_available=False)
    assert "保持现状" in (result.note or "") or result.rollback["performed"] is False


def test_a_step_the_drill_does_not_know_is_a_failure(drill_base, monkeypatch):
    """If the protocol gains a phase, a drill must not report "all four held" while
    a fifth ran unexamined."""
    from ats.execution import route_switch as switch_mod

    original = switch_mod.perform_switch

    def five_steps(*args, **kwargs):
        report = original(*args, **kwargs)
        report.steps_completed = list(report.steps_completed) + ["a_new_phase"]
        return report

    monkeypatch.setattr(drill.switch, "perform_switch", five_steps)
    result = drill.run_switch_drill(drill_id="d1", base_path=drill_base,
                                    environment="paper", account="DU1")
    assert any("does not know" in f for f in result.failures)
