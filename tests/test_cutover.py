"""Cutover control plane and qualification-gated routing (5.1–5.2, 5.4, 5.6–5.11).

The guarantee is "exactly one route serves traffic per boundary", and most of the
ways to lose it are not about routes at all — they are about two components
disagreeing about which route is active, or about a switch nothing reads.

So most of these tests are about things that must FAIL: an unwired boundary, a
half-migrated pair, an unreadable authority, a fallback into a retired route, a
downstream approval whose evidence moved.
"""

import sqlite3

import pytest

from ats.workflow import cutover as co
from ats.workflow import cutover_routing as cr


@pytest.fixture
def plane(tmp_path):
    path = str(tmp_path / "cutover.sqlite")
    co.bootstrap(actor="test", path=path)
    return path


def _wire_all(path: str) -> None:
    for boundary in co.SIX_BOUNDARIES:
        co.declare_wiring(boundary=boundary,
                          call_site=f"ats.example.{boundary}.enforce",
                          authority=f"ats.example.{boundary}",
                          semantics=f"{boundary} decides which representation is read",
                          path=path)


# --------------------------------------------------------------------------- #
# 5.1 — six independent boundaries
# --------------------------------------------------------------------------- #

def test_bootstrap_creates_all_six_boundaries(plane):
    assert set(co.all_boundaries(plane)) == set(co.SIX_BOUNDARIES)


def test_no_boundary_starts_on_the_target_route(plane):
    """A fresh installation that boots into the new route was never qualified."""
    for boundary, state in co.all_boundaries(plane).items():
        assert state.route != co.ROUTE_TARGET, boundary


def test_the_live_trader_starts_disabled(plane):
    """Not merely legacy: disabled, so no configuration error can enable it."""
    assert co.read_boundary(co.LIVE_TRADER, plane).route == co.ROUTE_DISABLED


def test_switching_one_boundary_leaves_the_other_five_alone(plane):
    """The requirement's case, and the reason boundaries exist at all.

    Coupling them would mean a read migration requires a schedule migration, which
    is the bundling the six-boundary model exists to prevent.
    """
    before = {b: s.route for b, s in co.all_boundaries(plane).items()}
    co.set_route(co.PROJECTION_READ, co.ROUTE_TARGET, actor="op",
                 reason="read cutover", path=plane)
    after = {b: s.route for b, s in co.all_boundaries(plane).items()}

    assert after[co.PROJECTION_READ] == co.ROUTE_TARGET
    for boundary in co.SIX_BOUNDARIES:
        if boundary != co.PROJECTION_READ:
            assert after[boundary] == before[boundary], boundary


def test_a_boundary_change_records_who_when_and_why(plane):
    co.set_route(co.PROJECTION_READ, co.ROUTE_TARGET, actor="alice",
                 reason="qualified for MU", path=plane)

    history = co.boundary_history(co.PROJECTION_READ, plane)
    latest = history[0]
    assert latest["to_route"] == co.ROUTE_TARGET
    assert latest["actor"] == "alice"
    assert "qualified" in latest["reason"]
    assert latest["changed_at"]


def test_a_change_without_a_reason_is_refused(plane):
    """A history that records moves without reasons is readable but not explainable."""
    with pytest.raises(co.CutoverError, match="must state a reason"):
        co.set_route(co.PROJECTION_READ, co.ROUTE_TARGET, actor="op", reason="  ",
                     path=plane)


def test_an_invalid_route_is_refused(plane):
    with pytest.raises(co.CutoverError, match="invalid route"):
        co.set_route(co.PROJECTION_READ, "maybe", actor="op", reason="x",
                     path=plane)


def test_boundary_history_is_append_only(plane):
    co.set_route(co.PROJECTION_READ, co.ROUTE_TARGET, actor="op", reason="x",
                 path=plane)
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(plane) as conn:
            conn.execute("UPDATE cutover_boundary_history SET actor='someone else'")


def test_an_absent_boundary_does_not_read_as_legacy(tmp_path):
    """The same rule as the route registry: absence is not a value."""
    with pytest.raises(co.CutoverError, match="no state"):
        co.read_boundary(co.PROJECTION_READ, str(tmp_path / "empty.sqlite"))


def test_an_unknown_boundary_is_refused(plane):
    with pytest.raises(co.CutoverError, match="unknown boundary"):
        co.read_boundary("some_boundary_added_later", plane)


# --------------------------------------------------------------------------- #
# 5.2 — declared wiring
# --------------------------------------------------------------------------- #

def test_a_boundary_with_no_wiring_is_reported_unwired(plane):
    """A boundary nothing reads still records a route, so pointing it at the target
    looks like a cutover and does nothing."""
    assert set(co.unwired_boundaries(plane)) == set(co.SIX_BOUNDARIES)
    assert co.read_boundary(co.PROJECTION_READ, plane).wired is False


def test_declaring_wiring_marks_the_boundary_wired(plane):
    co.declare_wiring(boundary=co.PROJECTION_READ,
                      call_site="ats.data.consumer_api.read_input",
                      authority="ats.data.consumer_api",
                      semantics="decides which projection serves the read",
                      path=plane)

    state = co.read_boundary(co.PROJECTION_READ, plane)
    assert state.wired is True
    assert co.unwired_boundaries(plane) == [
        b for b in co.SIX_BOUNDARIES if b != co.PROJECTION_READ]


def test_an_unwired_boundary_cannot_justify_a_cutover(plane):
    """The requirement's case: undeclared wiring is judged unwired and refused."""
    with pytest.raises(co.CutoverError, match="no declared wiring"):
        co.assert_wired(co.PROJECTION_READ, plane)


def test_wiring_must_declare_an_authority_and_semantics(plane):
    """A boundary with no authority is a setting nobody reads."""
    with pytest.raises(co.CutoverError, match="must declare a call_site"):
        co.declare_wiring(boundary=co.PROJECTION_READ, call_site="  ",
                          authority="x", semantics="y", path=plane)
    with pytest.raises(co.CutoverError, match="must declare a authority"):
        co.declare_wiring(boundary=co.PROJECTION_READ, call_site="x", authority=" ",
                          semantics="y", path=plane)


def test_a_boundary_can_have_several_declared_call_sites(plane):
    for site in ("ats.a.read", "ats.b.read"):
        co.declare_wiring(boundary=co.PROJECTION_READ, call_site=site,
                          authority=site, semantics="read dispatch", path=plane)
    assert len(co.wiring_of(co.PROJECTION_READ, plane)) == 2


# --------------------------------------------------------------------------- #
# 5.4 — cross-boundary compatibility
# --------------------------------------------------------------------------- #

def test_the_pre_cutover_state_is_compatible(plane):
    _wire_all(plane)
    assert co.check_compatibility(path=plane).compatible is True


def test_a_fully_migrated_pair_is_compatible(plane):
    _wire_all(plane)
    for boundary in (co.PROJECTION_READ, co.DISPATCHER_SCHEDULE):
        co.set_route(boundary, co.ROUTE_TARGET, actor="op", reason="migrated together",
                     path=plane)
    assert co.check_compatibility(path=plane).compatible is True


def test_a_half_migrated_read_schedule_pair_is_refused(plane):
    """The bundling a cutover would produce if the pairs were independent.

    The old schedule writes into stores the new reader no longer treats as
    authoritative — so this is a data-integrity conflict, not a tidiness one.
    """
    _wire_all(plane)
    co.set_route(co.PROJECTION_READ, co.ROUTE_TARGET, actor="op", reason="reads only",
                 path=plane)

    verdict = co.check_compatibility(path=plane)
    assert verdict.compatible is False
    conflict = verdict.conflicts[0]
    assert set(conflict["boundary_group"]) == {co.PROJECTION_READ,
                                               co.DISPATCHER_SCHEDULE}
    assert "no longer treats as authoritative" in conflict["reason"]


def test_a_conflict_refuses_rather_than_picking_one_side(plane):
    """The requirement: it must not select one of the two and carry on."""
    _wire_all(plane)
    co.set_route(co.PROJECTION_READ, co.ROUTE_TARGET, actor="op", reason="reads",
                 path=plane)

    with pytest.raises(co.CutoverError) as excinfo:
        co.assert_compatible(path=plane)
    message = str(excinfo.value)
    assert "refusing to pick one side" in message
    assert "dispatcher_schedule" in message


def test_an_analysis_output_switch_without_approvals_is_refused(plane):
    """A decision taken on one representation cannot be recorded on another."""
    _wire_all(plane)
    co.set_route(co.ANALYST_OUTPUT, co.ROUTE_TARGET, actor="op", reason="analysis",
                 path=plane)
    assert co.check_compatibility(path=plane).compatible is False


def test_a_disabled_live_trader_is_not_a_conflict(plane):
    """It is off, not half-migrated. Disabling must not read as in progress."""
    _wire_all(plane)
    assert co.check_compatibility(path=plane).compatible is True


# --------------------------------------------------------------------------- #
# 5.9 / 5.10 — pre-flight
# --------------------------------------------------------------------------- #

def test_preflight_passes_when_everything_is_wired_and_compatible(plane):
    _wire_all(plane)
    result = co.preflight(path=plane)

    assert result.ok is True, result.problems
    assert "cross_boundary_compatibility" in result.checked
    assert "wiring_declared" in result.checked


def test_preflight_reports_every_unwired_boundary_at_once(plane):
    result = co.preflight(path=plane)
    assert result.ok is False
    assert len([p for p in result.problems if "no declared wiring" in p]) == 6


def test_preflight_narrows_the_wiring_problem_to_the_requested_boundary(plane):
    """A pre-flight for one boundary should not fail on the other five."""
    co.declare_wiring(boundary=co.PROJECTION_READ, call_site="ats.x",
                      authority="ats.x", semantics="reads", path=plane)

    result = co.preflight(boundary=co.PROJECTION_READ, path=plane)
    assert result.ok is True, result.problems
    assert any("no declared wiring" in w for w in result.warnings)


def test_preflight_fails_closed_when_the_authority_is_unreadable(tmp_path):
    """5.10's third case. Treating unreadable as "no conflict" ships two routes."""
    corrupt = tmp_path / "cutover.sqlite"
    corrupt.write_text("this is not a database", encoding="utf-8")

    result = co.preflight(path=str(corrupt))
    assert result.ok is False
    assert any("could not be read" in problem for problem in result.problems)


def test_preflight_changes_nothing(plane):
    """The CLI exposes it and an operator will run it repeatedly before deciding."""
    _wire_all(plane)
    before = {b: s.as_row() for b, s in co.all_boundaries(plane).items()}

    co.preflight(path=plane)
    co.preflight(path=plane)

    after = {b: s.as_row() for b, s in co.all_boundaries(plane).items()}
    assert after == before


def test_a_disabled_live_trader_does_not_block_a_research_activation(plane):
    """5.9's second case: research services start with trading off."""
    _wire_all(plane)
    request = co.ActivationRequest(boundary=co.PROJECTION_READ,
                                   scope={"consumer": "macro"},
                                   consumer_id="macro", actor="op")

    result = co.preflight(boundary=co.PROJECTION_READ, request=request, path=plane)

    assert result.ok is True, result.problems
    assert any("live trader is 'disabled'" in warning for warning in result.warnings)


def test_the_activation_gate_does_not_retroactively_deny_the_legacy_route(plane):
    """5.9's first case: the gate constrains the new route only.

    Denying the existing legacy route would strand traffic on a route nobody
    qualified either.
    """
    _wire_all(plane)
    state = co.read_boundary(co.PROJECTION_READ, plane)
    assert state.route == co.ROUTE_LEGACY
    result = co.preflight(path=plane)
    assert result.ok is True, result.problems


def test_a_released_scope_warns_that_reactivation_needs_a_fresh_report(plane):
    _wire_all(plane)
    request = co.ActivationRequest(boundary=co.PROJECTION_READ,
                                   scope={"consumer": "macro"},
                                   consumer_id="macro", actor="op")
    co.record_activation(request=request, path=plane)
    co.release_activation(boundary=co.PROJECTION_READ,
                          scope_hash=request.scope_hash(), actor="op",
                          reason="qualification revoked", path=plane)

    result = co.preflight(boundary=co.PROJECTION_READ, request=request, path=plane)
    assert any("requires a fresh report" in w for w in result.warnings)


def test_an_activation_requires_wiring(plane):
    request = co.ActivationRequest(boundary=co.PROJECTION_READ,
                                   scope={"consumer": "macro"},
                                   consumer_id="macro", actor="op")
    with pytest.raises(co.CutoverError, match="no declared wiring"):
        co.record_activation(request=request, path=plane)


def test_only_the_target_route_is_activated_through_this_path(plane):
    """`legacy` is a boundary state, not an activation."""
    _wire_all(plane)
    request = co.ActivationRequest(boundary=co.PROJECTION_READ,
                                   scope={"consumer": "macro"},
                                   consumer_id="macro")
    with pytest.raises(co.CutoverError, match="not an activation"):
        co.record_activation(request=request, route=co.ROUTE_LEGACY, path=plane)


def test_active_scopes_excludes_released_ones(plane):
    _wire_all(plane)
    request = co.ActivationRequest(boundary=co.PROJECTION_READ,
                                   scope={"consumer": "macro"},
                                   consumer_id="macro")
    co.record_activation(request=request, path=plane)
    assert len(co.active_scopes(co.PROJECTION_READ, plane)) == 1

    co.release_activation(boundary=co.PROJECTION_READ,
                          scope_hash=request.scope_hash(), actor="op",
                          reason="done", path=plane)
    assert co.active_scopes(co.PROJECTION_READ, plane) == []


# --------------------------------------------------------------------------- #
# 5.7 — safe fallback
# --------------------------------------------------------------------------- #

def test_a_proven_live_fallback_is_allowed():
    verdict = cr.assess_fallback("legacy", consumer_id="macro", proof_valid=True,
                                 retired=False, available=True)
    assert verdict["verdict"] == cr.FALLBACK_OK


def test_an_unproven_fallback_is_blocked():
    """The audit found the existing dual-read comparison only records presence
    disagreements and never gated anything — so it is not a proof."""
    verdict = cr.assess_fallback("legacy", consumer_id="macro")
    assert verdict["verdict"] == cr.FALLBACK_BLOCKED
    assert verdict["reason_code"] == "fallback_proof_missing"
    assert "never gated" in verdict["reason"]


def test_a_retired_fallback_target_is_blocked():
    verdict = cr.assess_fallback("legacy", consumer_id="macro", proof_valid=True,
                                 retired=True)
    assert verdict["verdict"] == cr.FALLBACK_BLOCKED
    assert verdict["reason_code"] == "fallback_target_retired"


def test_an_unavailable_fallback_target_is_distinguished_from_retired():
    """A retired-but-working route and a live-but-broken one are different problems
    with different fixes."""
    retired = cr.assess_fallback("legacy", proof_valid=True, retired=True)
    broken = cr.assess_fallback("legacy", proof_valid=True, retired=False,
                                available=False)
    assert retired["verdict"] == cr.FALLBACK_BLOCKED
    assert broken["verdict"] == cr.FALLBACK_UNAVAILABLE
    assert retired["reason_code"] != broken["reason_code"]


def test_asserting_retirement_state_refuses_with_the_registry_detail():
    with pytest.raises(cr.FallbackUnsafe) as excinfo:
        cr.assert_retirement_state("legacy", retired=True)
    assert excinfo.value.reason_code == "fallback_target_retired"


def test_an_unregistered_route_is_not_retired():
    """A route with no tombstone is not retired — absence of a tombstone is not
    evidence of retirement."""
    assert cr.is_retired("no-such-route-identifier") is False


def test_asserting_retirement_state_passes_for_a_live_route():
    cr.assert_retirement_state("legacy", retired=False)


# --------------------------------------------------------------------------- #
# 5.6 — read routing
# --------------------------------------------------------------------------- #

def test_an_inactive_boundary_does_not_qualification_check(monkeypatch):
    """A consumer nobody asked to move must not be blocked by an unrelated
    revocation — that would make legacy reads depend on the new route's evidence."""
    called = {"n": 0}

    def _boom(**_kwargs):
        called["n"] += 1
        raise AssertionError("qualification must not be called")

    monkeypatch.setattr("ats.data.assurance.qualification", _boom)
    decision = cr.read_route(consumer_id="macro", target_boundary_active=False,
                             path="/nonexistent")

    assert decision.route == co.ROUTE_LEGACY
    assert called["n"] == 0


def test_an_unqualified_consumer_falls_back_when_the_fallback_is_safe(monkeypatch):
    monkeypatch.setattr("ats.data.assurance.qualification",
                        lambda **kwargs: {"status": "ineligible",
                                          "reasons": ["rollback_unverified:x"]})
    decision = cr.read_route(
        consumer_id="macro", target_boundary_active=True,
        fallback_check=lambda: {"verdict": cr.FALLBACK_OK}, path="/nonexistent")

    assert decision.route == co.ROUTE_LEGACY
    assert decision.qualified is False
    assert decision.qualification["reasons"] == ["rollback_unverified:x"]


def test_an_unqualified_consumer_with_no_safe_fallback_stops(monkeypatch):
    """5.7's point: a fallback to a broken or retired route produces confident wrong
    answers, which is worse than stopping."""
    monkeypatch.setattr("ats.data.assurance.qualification",
                        lambda **kwargs: {"status": "ineligible",
                                          "reasons": ["evidence_missing"]})
    with pytest.raises(cr.FallbackUnsafe) as excinfo:
        cr.read_route(
            consumer_id="macro", target_boundary_active=True,
            fallback_check=lambda: {"verdict": cr.FALLBACK_BLOCKED,
                                    "reason_code": "fallback_target_retired",
                                    "reason": "legacy projection was retired"},
            path="/nonexistent")

    assert excinfo.value.reason_code == "fallback_target_retired"
    assert excinfo.value.detail["qualification_reasons"] == ["evidence_missing"]


def test_a_revoked_qualification_is_reported_as_ineligible(monkeypatch):
    monkeypatch.setattr("ats.data.assurance.qualification",
                        lambda **kwargs: {"status": "ineligible",
                                          "reasons": ["evidence_revoked:macro"]})
    decision = cr.read_route(
        consumer_id="macro", target_boundary_active=True,
        fallback_check=lambda: {"verdict": cr.FALLBACK_OK}, path="/nonexistent")

    assert decision.qualification["status"] == "ineligible"
    assert "revoked" in decision.qualification["reasons"][0]


def test_an_undeclared_consumer_is_an_error():
    """A reader cannot be qualified for a consumer that is not declared."""
    with pytest.raises(cr.RouteUnavailable, match="no contract"):
        cr.read_route(consumer_id="not_a_consumer", target_boundary_active=True,
                      fallback_check=lambda: {"verdict": cr.FALLBACK_OK})


def test_the_gate_summary_counts_per_route_and_lists_gaps(monkeypatch):
    monkeypatch.setattr("ats.data.assurance.qualification",
                        lambda **kwargs: {"status": "ineligible",
                                          "reasons": ["evidence_missing"]})
    decisions = [
        cr.read_route(consumer_id="macro", target_boundary_active=True,
                      fallback_check=lambda: {"verdict": cr.FALLBACK_OK,
                                              "reason": "no proof"},
                      path="/nonexistent"),
        cr.read_route(consumer_id="fundamental", target_boundary_active=False,
                      path="/nonexistent"),
    ]

    summary = cr.gate_summary(decisions)
    assert summary["count"] == 2
    assert summary["qualified"] == 0
    assert summary["by_route"] == {"legacy": 2}
    assert any("macro" in gap for gap in summary["gaps"])


# --------------------------------------------------------------------------- #
# 5.8 — downstream re-verification
# --------------------------------------------------------------------------- #

def test_downstream_is_refused_when_qualification_was_revoked(monkeypatch):
    """The requirement's case, and the point is that it refuses rather than logs."""
    monkeypatch.setattr("ats.data.assurance.qualification",
                        lambda **kwargs: {"status": "ineligible",
                                          "reasons": ["evidence_revoked:macro"]})

    result = cr.reverify_downstream(consumer_id="macro", scope={"consumer": "macro"})
    assert result.approved is False
    assert "evidence_revoked" in result.reasons[0]


def test_assert_downstream_current_raises_with_every_reason(monkeypatch):
    monkeypatch.setattr("ats.data.assurance.qualification",
                        lambda **kwargs: {"status": "ineligible",
                                          "reasons": ["manifest_drift"]})
    with pytest.raises(cr.RouteUnavailable) as excinfo:
        cr.assert_downstream_current(consumer_id="macro",
                                     scope={"consumer": "macro"})
    assert "manifest_drift" in str(excinfo.value)


def test_downstream_is_approved_when_qualification_holds_and_the_snapshot_is_fresh(
        monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr("ats.data.assurance.qualification",
                        lambda **kwargs: {"status": "eligible", "reasons": []})

    result = cr.reverify_downstream(
        consumer_id="macro", scope={"consumer": "macro"},
        snapshot_as_of=datetime.now(timezone.utc).isoformat())
    assert result.approved is True


def test_a_stale_snapshot_is_refused_even_when_qualified(monkeypatch):
    """Qualification can hold while the snapshot it was projected on has moved on."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr("ats.data.assurance.qualification",
                        lambda **kwargs: {"status": "eligible", "reasons": []})

    result = cr.reverify_downstream(
        consumer_id="macro", scope={"consumer": "macro"},
        snapshot_as_of=(datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat())
    assert result.approved is False
    assert "re-assembly is required" in result.reasons[0]


def test_an_unparseable_snapshot_is_refused(monkeypatch):
    """Freshness that cannot be established is not freshness."""
    monkeypatch.setattr("ats.data.assurance.qualification",
                        lambda **kwargs: {"status": "eligible", "reasons": []})
    result = cr.reverify_downstream(consumer_id="macro", scope={"consumer": "macro"},
                                   snapshot_as_of="yesterday-ish")
    assert result.approved is False
    assert "could not be parsed" in result.reasons[0]
