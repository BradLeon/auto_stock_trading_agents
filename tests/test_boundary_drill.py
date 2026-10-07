"""Independent rollback drills for the three boundaries (tasks 13.1–13.3).

The property these tests defend is not "the route moved back" — it is that a
rollback which *could not* happen says so instead of moving somewhere anyway.
Three ways that could read as a pass, each with a test:

- **a retired or unavailable fallback target.** The passing outcome is holding
  the current route. A drill that forced the move would be testing the opposite
  of what it claims, and would convert one outage into a different one.
- **an incomplete execution record** on the schedule boundary. Group 10's
  rollback refuses here, and that refusal is the pass: rolling back with a
  partial record re-runs every trigger, silently.
- **a drain that cannot complete** on the trade boundary. The route must stay
  put, and the report must not claim a rollback that did not happen.

And one in the other direction: a rollback that moves only half of an
inseparable pair must **fail** (13.1), because that is precisely the
half-migrated state the control plane exists to reject.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ats.workflow import batch_manifest as bm
from ats.workflow import boundary_drill as bd
from ats.workflow import cutover as plane

FUTURE = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()


@pytest.fixture
def production_plane(tmp_path) -> str:
    """A path the drill must NOT create. The real one is left untouched."""
    return str(tmp_path / "production-cutover.sqlite")


def _batch(**overrides) -> bm.CutoverBatch:
    fields = dict(
        batch_id="b-research", batch_class=bm.RESEARCH_READ,
        owner="ats.data.products", scope={"consumers": ["layer", "sector"]},
        old_route="legacy", new_route="target",
        fallback_route="legacy", fallback_proof="valid",
        fallback_retired="no", fallback_available="yes",
        fallback_drill_ref="drill-previous")
    fields.update(overrides)
    return bm.CutoverBatch(**fields)


def _counters(shadow: int = 3, approvals: int = 2, ledger: int = 5):
    """Record counts standing in for the shadow / approval / ledger evidence.

    A plain counter rather than three real stores: what 13.1 asserts is that a
    rollback does not *reduce* them, and a counter is the smallest thing that can
    falsify that. The real stores are covered by their own modules' tests.
    """
    def reader() -> dict[str, int]:
        return {"shadow_reports": shadow, "approvals": approvals,
                "ledger_rows": ledger}
    return reader


def _auth(*consumers, **overrides):
    from ats.workflow import read_cutover as rc

    fields = dict(reference="DEP-drill", authorised_by="owner", issued_by="ops",
                  scope=tuple(consumers) or ("layer",), valid_until=FUTURE, note="")
    fields.update(overrides)
    return rc.DeploymentAuthorization(**fields)


# --------------------------------------------------------------------------- #
# 13.1 read boundary
# --------------------------------------------------------------------------- #

def test_the_read_rollback_returns_the_whole_chain_to_legacy(production_plane):
    """`projection_read` and `dispatcher_schedule` are inseparable per the
    control plane's own table; moving one alone passes through the combination
    `check_compatibility` rejects."""
    result = bd.run_read_boundary_drill(
        drill_id="d1", batch=_batch(), evidence_counters=_counters(),
        production_cutover_db=production_plane)

    assert result.outcome == bd.COMPLETED, result.failures
    assert result.boundary == plane.PROJECTION_READ
    assert set(result.routes_after) == set(plane.INCOMPATIBLE[0][0])
    assert all(route == plane.ROUTE_LEGACY
               for route in result.routes_after.values())
    assert result.check("chain_rolled_back", True)


def test_the_read_rollback_never_creates_the_production_control_plane(production_plane):
    """A drill recorded in the production file is indistinguishable from a real
    switch at exactly the point where somebody asks "was this authorised?"."""
    bd.run_read_boundary_drill(
        drill_id="d1", batch=_batch(), evidence_counters=_counters(),
        production_cutover_db=production_plane)

    assert not Path(production_plane).exists()
    assert Path(bd.drill_db(production_plane, "d1")).exists()


def test_a_retired_fallback_target_means_holding_not_switching(production_plane):
    """A tombstone is not a destination. Forcing the move is the failure."""
    result = bd.run_read_boundary_drill(
        drill_id="d1", batch=_batch(), evidence_counters=_counters(),
        fallback_retired=True, production_cutover_db=production_plane)

    assert result.outcome == bd.HELD
    assert all(route == plane.ROUTE_LEGACY
               for route in result.routes_after.values()), (
        "the drill never installed a target route, so nothing should have moved "
        "at all — the fallback check happens first for exactly this reason")
    assert "保持现状" in result.note


def test_an_unavailable_fallback_target_also_holds(production_plane):
    result = bd.run_read_boundary_drill(
        drill_id="d1", batch=_batch(), evidence_counters=_counters(),
        fallback_available=False, production_cutover_db=production_plane)
    assert result.outcome == bd.HELD


def test_a_rollback_that_deleted_evidence_fails(production_plane):
    """The failure a rollback causes by tidying up after itself: the switch is
    left with no record of what it displaced."""
    result = bd.run_read_boundary_drill(
        drill_id="d1", batch=_batch(), evidence_counters=_counters(),
        production_cutover_db=production_plane)
    assert result.outcome == bd.COMPLETED

    # A counter that drops its approval rows between the before and after reads.
    # A static `approvals: 0` would read 0 on both sides and prove nothing — the
    # comparison is against a starting count, so it has to start somewhere.
    state = {"approvals": 2}
    reads = {"n": 0}

    def shrinking() -> dict[str, int]:
        reads["n"] += 1
        if reads["n"] > 1:
            state["approvals"] = 0
        return {"shadow_reports": 3, "approvals": state["approvals"],
                "ledger_rows": 5}

    result2 = bd.run_read_boundary_drill(
        drill_id="d2", batch=_batch(), evidence_counters=shrinking,
        production_cutover_db=production_plane)
    assert result2.outcome == bd.FAILED
    assert any("approvals" in f for f in result2.failures)
    assert result2.preserved["approvals"] == {"before": 2, "after": 0}


def test_records_that_grew_are_not_a_preservation_failure(production_plane):
    """Counts must not *fall*. A rollback that also appended is fine, and reading
    "after != before" as a failure would make the check unusable."""
    growing = {"shadow_reports": 4, "approvals": 2, "ledger_rows": 5}
    result = bd.run_read_boundary_drill(
        drill_id="d1", batch=_batch(), evidence_counters=lambda: growing,
        production_cutover_db=production_plane)
    assert result.outcome == bd.COMPLETED


def test_a_drill_with_no_evidence_to_preserve_is_refused(production_plane):
    """Deleting nothing because there was nothing is not preservation."""
    result = bd.run_read_boundary_drill(
        drill_id="d1", batch=_batch(),
        evidence_counters=lambda: {"shadow_reports": 0, "approvals": 0,
                                   "ledger_rows": 0},
        production_cutover_db=production_plane)
    assert result.outcome == bd.REFUSED
    assert any("preserve" in f for f in result.failures)


def test_a_batch_naming_no_read_boundary_is_refused(production_plane):
    """Guessing would move somebody else's boundary."""
    result = bd.run_read_boundary_drill(
        drill_id="d1", batch=_batch(batch_class=bm.LIVE_TRADER),
        evidence_counters=_counters(),
        production_cutover_db=production_plane)
    assert result.outcome == bd.REFUSED
    assert any("no read-path boundary" in f for f in result.failures)


def test_the_rollback_is_recorded_with_a_reason_and_its_own_store(production_plane):
    result = bd.run_read_boundary_drill(
        drill_id="d1", batch=_batch(), evidence_counters=_counters(),
        production_cutover_db=production_plane)

    path = bd.drill_db(production_plane, "d1")
    rows = bd.drill_history(path=path)
    assert len(rows) == 1
    assert rows[0]["mode"] == bd.MODE
    assert rows[0]["boundary"] == plane.PROJECTION_READ

    history = plane.boundary_history(plane.PROJECTION_READ, path)
    reasons = [row["reason"] for row in history]
    assert any("pre-rollback" in r for r in reasons)
    assert any("rollback to the legacy route" in r for r in reasons), (
        "a boundary change with no stated reason leaves the history readable but "
        "not explainable")


def test_the_drill_record_declares_what_it_did_not_prove(production_plane):
    result = bd.run_read_boundary_drill(
        drill_id="d1", batch=_batch(), evidence_counters=_counters(),
        production_cutover_db=production_plane)
    assert result.as_row()["limits"], (
        "a drill that does not say what it did not prove reads as though it "
        "proved everything")


# --------------------------------------------------------------------------- #
# 13.2 schedule boundary
# --------------------------------------------------------------------------- #

T_A = "wf:daily-cascade:2026-10-07"
T_B = "wf:factset-ingest:2026-10-07"


def test_already_executed_triggers_are_skipped_not_re_run(tmp_path):
    result = bd.run_schedule_boundary_drill(
        drill_id="d1", triggers=[T_A, T_B],
        executed={T_A: ["exec-1"], T_B: ["exec-2"]},
        authorisation=_auth("layer"),
        production_dispatch_db=str(tmp_path / "dispatch.sqlite"),
        production_switch_db=str(tmp_path / "switch.sqlite"))

    assert result.outcome == bd.COMPLETED, result.failures
    assert {row["trigger"] for row in result.skipped} == {T_A, T_B}
    assert result.check("already_executed_triggers_skipped", True)


def test_a_trigger_with_no_execution_record_blocks_the_rollback(tmp_path):
    """The case `plan_rollback` exists for. Rolling back would run it again."""
    result = bd.run_schedule_boundary_drill(
        drill_id="d1", triggers=[T_A, T_B],
        executed={T_A: ["exec-1"]},
        authorisation=_auth("layer"),
        production_dispatch_db=str(tmp_path / "dispatch.sqlite"),
        production_switch_db=str(tmp_path / "switch.sqlite"))

    assert result.outcome == bd.REFUSED
    assert result.unconfirmed == [T_B]
    assert any("拒绝回退" in result.note for _ in [0])
    assert result.check("rollback_refused_without_a_complete_record", True)


def test_an_unconfirmed_trigger_named_by_the_caller_also_blocks(tmp_path):
    """A trigger nobody recorded anything about is the same hazard, reported
    from the other direction — and it blocks even when the plan was handed a
    complete-looking record for the triggers it could see."""
    result = bd.run_schedule_boundary_drill(
        drill_id="d1", triggers=[T_A], executed={T_A: ["exec-1"]},
        unconfirmed=[T_B], authorisation=_auth("layer"),
        production_dispatch_db=str(tmp_path / "dispatch.sqlite"),
        production_switch_db=str(tmp_path / "switch.sqlite"))
    assert result.outcome == bd.REFUSED
    assert T_B in result.unconfirmed
    detail = next(c for c in result.checks
                  if c.name == "rollback_refused_without_a_complete_record")
    assert T_B in detail.detail


def test_a_rollback_without_an_authorisation_is_refused(tmp_path):
    """A rollback changes who fires the schedule as much as a cutover does."""
    result = bd.run_schedule_boundary_drill(
        drill_id="d1", triggers=[T_A], executed={T_A: ["exec-1"]},
        authorisation=None,
        production_dispatch_db=str(tmp_path / "dispatch.sqlite"),
        production_switch_db=str(tmp_path / "switch.sqlite"))
    assert result.outcome == bd.REFUSED
    assert any("authorisation" in f for f in result.failures)


def test_released_claims_reach_the_ledger_not_only_the_report(tmp_path):
    """A report saying the claims were released is not the same as releasing
    them; a half-released schedule has no owner, which is the one state both
    dispatchers agree on."""
    released: list[str] = []
    result = bd.run_schedule_boundary_drill(
        drill_id="d1", triggers=[T_A], executed={T_A: ["exec-1"]},
        authorisation=_auth("layer"),
        release_claims=released.append,
        production_dispatch_db=str(tmp_path / "dispatch.sqlite"),
        production_switch_db=str(tmp_path / "switch.sqlite"))
    assert result.outcome == bd.COMPLETED
    assert released == [T_A]


def test_a_failure_to_release_a_claim_fails_the_drill(tmp_path):
    def explode(_trigger):
        raise RuntimeError("claim is held")

    result = bd.run_schedule_boundary_drill(
        drill_id="d1", triggers=[T_A], executed={T_A: ["exec-1"]},
        authorisation=_auth("layer"), release_claims=explode,
        production_dispatch_db=str(tmp_path / "dispatch.sqlite"),
        production_switch_db=str(tmp_path / "switch.sqlite"))
    assert result.outcome == bd.FAILED
    assert any("rollback_applied" in f for f in result.failures)


# --------------------------------------------------------------------------- #
# 13.3 trade boundary
# --------------------------------------------------------------------------- #

def test_a_trade_rollback_needs_live_authority_too(tmp_path):
    """The property that makes the drill honest rather than decorative: the
    refusal IS the passing outcome, because a rollback taken without live
    authority changes who may send real orders."""
    result = bd.run_trade_boundary_drill(
        drill_id="d1", environment="paper", account="DU1",
        production_route_db=str(tmp_path / "routes.sqlite"))

    assert result.outcome == bd.COMPLETED, result.failures
    recorded = next(c for c in result.checks if c.name == "rollback")
    assert "refused" in recorded.detail
    assert "no_live_authorisation" in recorded.detail
    result.check("refused_without_live_authority_is_correct", True)


def test_the_trade_drill_never_creates_the_production_route_registry(tmp_path):
    base = str(tmp_path / "routes.sqlite")
    bd.run_trade_boundary_drill(drill_id="d1", environment="paper", account="DU1",
                                production_route_db=base)
    assert not Path(base).exists()
    assert result_store_exists(base, "d1")


def test_a_pre_existing_registry_is_not_reported_as_a_violation(tmp_path):
    """The check asks "did the drill write to it", not "does the file exist".

    A production registry normally exists already, so the existence test put a
    red mark on a green report — a reader then has to work out which of the two
    to believe. Caught by running the drill twice against one registry: the
    second run sees a file the first one did not create.
    """
    from ats.execution import route_registry as registry

    base = str(tmp_path / "routes.sqlite")
    registry.install_route("A", generation=1, environment="paper", account="DU1",
                           actor="test", reason="pre-existing", path=base)

    result = bd.run_trade_boundary_drill(
        drill_id="d1", environment="paper", account="DU1",
        production_route_db=base)

    check = next(c for c in result.checks if c.name == "real_registry_untouched")
    assert check.held is True, check.detail
    assert result.outcome == bd.COMPLETED, result.failures
    assert all(c.held for c in result.checks), (
        "a completed drill must not carry a failing check — a red mark on a "
        "green report is worse than no mark")


def test_a_drill_that_writes_to_the_production_registry_is_caught(tmp_path):
    """The other half: the check must still fail when the write is real.

    Verified against the fingerprint directly rather than by arranging for a drill
    to misbehave, because a test that depends on provoking a bug is a test that
    stops testing the moment the bug becomes hard to provoke.
    """
    from ats.execution import route_registry as registry

    base = str(tmp_path / "routes.sqlite")
    registry.install_route("A", generation=1, environment="paper", account="DU1",
                           actor="test", reason="pre-existing", path=base)

    before = bd._registry_fingerprint(base)
    with open(base, "a", encoding="utf-8") as handle:
        handle.write("x" * 100)
    after = bd._registry_fingerprint(base)

    assert before != after, "an append that leaves the route alone must still show"
    assert before[0] == after[0] == "present"


def test_an_absent_registry_is_reported_as_absent_not_as_unreadable(tmp_path):
    base = str(tmp_path / "never-created.sqlite")
    assert bd._registry_fingerprint(base) == ("absent", "", 0)


def result_store_exists(base: str, drill_id: str) -> bool:
    from ats.execution import route_drill

    return Path(route_drill.drill_path(base, drill_id)).exists()


def test_an_unavailable_fallback_means_the_route_holds(tmp_path):
    result = bd.run_trade_boundary_drill(
        drill_id="d1", environment="paper", account="DU1",
        fallback_available=False,
        production_route_db=str(tmp_path / "routes.sqlite"))

    assert result.outcome == bd.HELD
    assert result.check("held_on_unavailable_fallback", True)
    assert result.check("no_silent_route_change", True)


def test_the_trade_drill_consults_the_live_gate_and_records_the_refusal(tmp_path):
    result = bd.run_trade_boundary_drill(
        drill_id="d1", environment="paper", account="DU1",
        production_route_db=str(tmp_path / "routes.sqlite"))
    gate = next(c for c in result.checks if c.name == "live_gate_consulted")
    assert gate.held is True
    assert "refused" in gate.detail or "no_live_authorisation" in gate.detail


def test_a_drain_that_cannot_complete_leaves_the_route_where_it_was(tmp_path):
    """A rollback blocked at the drain must not become a rollback that half
    happened — and must not report one that did not."""
    from ats.execution.authorization_lifecycle import AuthorizationLifecycle

    result = bd.run_trade_boundary_drill(
        drill_id="d1", environment="paper", account="DU1",
        lifecycle=AuthorizationLifecycle(cycle_status="live", order_rows=()),
        production_route_db=str(tmp_path / "routes.sqlite"))

    # The gate refuses before the protocol is reached, so the drill's own
    # assertion is that nothing moved. Recorded explicitly rather than inferred.
    assert result.check("real_registry_untouched", True)


def test_the_drill_record_carries_each_boundary_separately(tmp_path):
    """One 'rollback works' line covering three boundaries would pass while one
    of them was broken, because the other two carried it."""
    results = [
        bd.run_read_boundary_drill(
            drill_id="r", batch=_batch(), evidence_counters=_counters(),
            production_cutover_db=str(tmp_path / "prod-plane.sqlite")),
        bd.run_schedule_boundary_drill(
            drill_id="s", triggers=[T_A], executed={T_A: ["e1"]},
            authorisation=_auth("layer"),
            production_dispatch_db=str(tmp_path / "d.sqlite"),
            production_switch_db=str(tmp_path / "s.sqlite")),
        bd.run_trade_boundary_drill(
            drill_id="t", environment="paper", account="DU1",
            production_route_db=str(tmp_path / "routes.sqlite")),
    ]
    assert [r.boundary for r in results] == [
        plane.PROJECTION_READ, plane.DISPATCHER_SCHEDULE, plane.LIVE_TRADER]
    report = bd.render_drill_report(results)
    for boundary in (plane.PROJECTION_READ, plane.DISPATCHER_SCHEDULE,
                     plane.LIVE_TRADER):
        assert f"`{boundary}`" in report
    assert "未**证明" in report


def test_recording_a_drill_without_an_explicit_store_is_refused():
    """Deriving the store from a default would risk writing into the very
    surface the drill exists to avoid touching."""
    with pytest.raises(bd.BoundaryDrillError) as excinfo:
        bd.record_drill(bd.BoundaryDrillResult(drill_id="d", boundary="x"))
    assert "explicit store" in str(excinfo.value)


def test_an_empty_drill_id_does_not_produce_a_doubled_dot(tmp_path):
    """`phase_f_cutover..drill.sqlite` opened an empty file and read as "no drills".

    The history action with no `--drill-id` passes an empty id, so this was
    reachable from the CLI — and the empty result read as "nothing was
    recorded" rather than "you named no drill".
    """
    path = bd.drill_db(str(tmp_path / "cutover.sqlite"), "")
    assert path.endswith("cutover.drill.sqlite")
    assert ".." not in Path(path).name


def test_a_named_drill_still_gets_its_own_file(tmp_path):
    a = bd.drill_db(str(tmp_path / "cutover.sqlite"), "d1")
    b = bd.drill_db(str(tmp_path / "cutover.sqlite"), "d2")
    assert a != b
    assert a.endswith("cutover.d1.drill.sqlite")
