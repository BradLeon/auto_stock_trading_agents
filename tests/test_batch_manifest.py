"""Phase F 8.1–8.5 — per-batch cutover manifest and dry-run.

The manifest is where "switch a boundary" becomes "switch these consumers
together, watch this, and come back this way" — and the dry-run is the thing an
operator runs before every decision. So the tests are mostly about ways this
module could make a cutover *look* possible when it is not:

- a batch with no safe way back reported as merely "blocked" (which sends an
  operator to gather evidence for something that must not be scheduled at all);
- a fallback that was never drilled counting as available;
- a stored "ready" verdict outliving the qualification it was based on;
- a revocation answered with "fall back", which is self-defeating because the
  fallback is gated on the very qualification that just disappeared;
- the already-running collection path being given a switch action.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from ats.workflow import batch_manifest as bm
from ats.workflow import cutover as plane


@pytest.fixture
def plane_db(tmp_path):
    """A bootstrapped, fully wired control plane."""
    from ats.workflow import cutover_wiring as wiring

    path = str(tmp_path / "cutover.sqlite")
    wiring.bootstrap_wired(actor="test", path=path)
    return path


@pytest.fixture
def manifest(tmp_path):
    return str(tmp_path / "batches.sqlite")


def _qualified(status="eligible"):
    def _reader(consumer_id, scope):
        return {"status": status, "consumer_id": consumer_id, "reasons": []}
    return _reader


def _report_ok(batch):
    return True, []


def _batch(batch_id="b-research", batch_class=bm.RESEARCH_READ, **kw):
    defaults = dict(
        owner="ats.data.products", old_route="legacy", new_route="target",
        observation_window="2 trading days", success_criteria="无差异或差异已接受",
        stop_conditions="任一必需面出现未接受差异",
        fallback_route="legacy", fallback_proof="valid", fallback_retired="no",
        fallback_available="yes", fallback_drill_ref="drill-2026-10-01",
    )
    defaults.update(kw)
    return bm.CutoverBatch(batch_id=batch_id, batch_class=batch_class, **defaults)


# --------------------------------------------------------------------------- #
# 8.1 — the manifest
# --------------------------------------------------------------------------- #

def test_the_five_batch_classes_are_declared_as_data():
    """Spelled once so the manifest, the report and the runbook agree."""
    assert set(bm.BATCH_CLASSES) == {
        "collection_publish", "research_read", "schedule",
        "internal_state_approval", "live_trader"}


def test_every_batch_class_maps_to_at_least_one_boundary():
    for name in bm.BATCH_CLASSES:
        batch = _batch(batch_id=f"b-{name}", batch_class=name)
        assert batch.boundaries, name


def test_a_batch_must_declare_owner_window_success_and_stop(manifest):
    """The four fields that make a batch reviewable after the fact.

    An incident asks "was this supposed to stop?" — a batch that never declared a
    stop condition cannot answer, and neither can a reader of the manifest.
    """
    for field in ("owner", "observation_window", "success_criteria",
                  "stop_conditions"):
        kwargs = {field: ""}
        with pytest.raises(bm.BatchError, match=field):
            bm.declare_batch(_batch(**kwargs), path=manifest)


def test_a_batch_without_routes_is_refused(manifest):
    with pytest.raises(bm.BatchError, match="old and the new route"):
        bm.declare_batch(_batch(old_route="", new_route=""),
                         path=manifest)


def test_an_unknown_batch_class_is_refused(manifest):
    with pytest.raises(bm.BatchError, match="unknown batch class"):
        bm.declare_batch(_batch(batch_class="vibes"), path=manifest)


def test_declaring_a_batch_twice_updates_rather_than_duplicates(manifest):
    bm.declare_batch(_batch(), path=manifest)
    bm.declare_batch(_batch(observation_window="5 trading days"), path=manifest)
    assert len(bm.list_batches(path=manifest)) == 1
    assert bm.read_batch("b-research", path=manifest).observation_window \
        == "5 trading days"


def test_reading_an_unknown_batch_is_an_error_not_an_empty_one(manifest):
    with pytest.raises(bm.BatchError, match="no batch"):
        bm.read_batch("never-declared", path=manifest)


def test_the_batch_carries_its_boundaries_and_consumers(manifest):
    """A batch that does not say which consumers move cannot be gated per scope."""
    batch = bm.declare_batch(_batch(), path=manifest)
    assert batch.boundaries == ("projection_read",)
    assert set(batch.consumers) == {"layer", "information", "sector",
                                   "fundamental", "macro", "technical"}


# --------------------------------------------------------------------------- #
# 8.3 — the already-running collection path
# --------------------------------------------------------------------------- #

def test_the_running_collection_path_is_verified_in_place_not_switched(manifest):
    batch = bm.declare_batch(_batch(
        batch_id="b-collection", batch_class=bm.COLLECTION_PUBLISH,
        old_route="", new_route="", direct_verification=True), path=manifest)
    result = bm.dry_run_batch(batch, path=manifest)

    assert result.outcome == bm.VERIFIED_IN_PLACE
    assert result.switchable is True
    assert result.checks["already_in_production"] is True


def test_a_direct_verification_batch_must_not_declare_a_route_change(manifest):
    """Recording a route pair implies a switch that should not happen."""
    with pytest.raises(bm.BatchError, match="must not declare a route change"):
        bm.declare_batch(_batch(batch_id="b-collection",
                                batch_class=bm.COLLECTION_PUBLISH,
                                direct_verification=True), path=manifest)


def test_only_the_collection_path_may_be_direct_verification(manifest):
    with pytest.raises(bm.BatchError, match="direct-verification"):
        bm.declare_batch(_batch(direct_verification=True), path=manifest)


def test_the_collection_path_declares_no_consumers():
    """Verified in place means no consumer's read route moves."""
    assert _batch(batch_class=bm.COLLECTION_PUBLISH).consumers == ()


# --------------------------------------------------------------------------- #
# 8.2 — the dry-run changes nothing
# --------------------------------------------------------------------------- #

def test_the_dry_run_changes_no_route(manifest, plane_db, monkeypatch):
    """Structural, not a promise: there is no parameter that could move a route,
    and this asserts the routes are byte-identical afterwards."""
    bm.declare_batch(_batch(), path=manifest)
    before = {b: s.route for b, s in plane.all_boundaries(plane_db).items()}

    bm.dry_run(qualification_reader=_qualified(), report_checker=_report_ok,
               path=manifest, cutover_path=plane_db)

    assert {b: s.route for b, s in plane.all_boundaries(plane_db).items()} == before


def test_the_dry_run_writes_no_boundary_history(manifest, plane_db):
    bm.declare_batch(_batch(), path=manifest)
    before = sum(len(plane.boundary_history(b, plane_db))
                 for b in plane.SIX_BOUNDARIES)

    bm.dry_run(qualification_reader=_qualified(), path=manifest,
               cutover_path=plane_db)

    after = sum(len(plane.boundary_history(b, plane_db))
                for b in plane.SIX_BOUNDARIES)
    assert after == before


def test_a_ready_batch_dry_run_passes(manifest, plane_db):
    batch = bm.declare_batch(_batch(), path=manifest)
    result = bm.dry_run_batch(batch, qualification_reader=_qualified(),
                              report_checker=_report_ok, path=manifest,
                              cutover_path=plane_db)

    assert result.outcome == bm.READY
    assert result.switchable is True
    assert result.reasons == []
    assert result.checks["fallback_safe"] is True
    assert result.checks["qualified"] is True


def test_the_dry_run_is_repeatable(manifest):
    """An operator runs it in a loop; a second run must reach the same verdict."""
    batch = bm.declare_batch(_batch(), path=manifest)
    first = bm.dry_run_batch(batch, qualification_reader=_qualified(),
                             report_checker=_report_ok, path=manifest)
    second = bm.dry_run_batch(batch, qualification_reader=_qualified(),
                              report_checker=_report_ok, path=manifest)
    assert first.outcome == second.outcome
    assert first.checks == second.checks


# --- the fallback is the load-bearing gate --------------------------------- #

def test_a_batch_with_no_usable_fallback_is_not_switchable(manifest):
    """NOT `blocked`. Blocked means "a gate says no right now"; this means the
    batch has no safe way back and must not be scheduled at all."""
    batch = bm.declare_batch(_batch(fallback_proof="missing"), path=manifest)
    result = bm.dry_run_batch(batch, qualification_reader=_qualified(),
                              path=manifest)

    assert result.outcome == bm.NOT_SWITCHABLE
    assert result.switchable is False
    assert any("no safe way back" in r for r in result.reasons)


def test_a_retired_fallback_target_is_not_switchable(manifest):
    batch = bm.declare_batch(_batch(fallback_retired="yes"), path=manifest)
    result = bm.dry_run_batch(batch, qualification_reader=_qualified(),
                              path=manifest)
    assert result.outcome == bm.NOT_SWITCHABLE


def test_an_unavailable_fallback_is_not_switchable(manifest):
    batch = bm.declare_batch(_batch(fallback_available="no"), path=manifest)
    result = bm.dry_run_batch(batch, qualification_reader=_qualified(),
                              path=manifest)
    assert result.outcome == bm.NOT_SWITCHABLE


def test_an_undrilled_fallback_is_not_switchable(manifest):
    """An untested way back is not a way back."""
    batch = bm.declare_batch(_batch(fallback_drill_ref=""), path=manifest)
    result = bm.dry_run_batch(batch, qualification_reader=_qualified(),
                              report_checker=_report_ok, path=manifest)

    assert result.outcome == bm.NOT_SWITCHABLE
    assert result.checks["fallback_safe"] is True, (
        "the fallback IS available — what is missing is proof that it works")
    assert result.checks["fallback_drill_recorded"] is False


def test_the_fallback_is_checked_before_the_evidence(manifest):
    """Ordering is the operator's: if you cannot get back, gathering evidence is
    beside the point. Asserted through the outcome, not the reason order."""
    batch = bm.declare_batch(_batch(fallback_proof="missing",
                                    shadow_report_id="r1"),
                             path=manifest)
    result = bm.dry_run_batch(batch, qualification_reader=_qualified("ineligible"),
                              report_checker=lambda b: (False, ["no report"]),
                              path=manifest)
    assert result.outcome == bm.NOT_SWITCHABLE
    assert result.checks["fallback_safe"] is False


# --- cited evidence ------------------------------------------------------- #

def test_an_uncitable_shadow_report_blocks_the_batch(manifest):
    batch = bm.declare_batch(_batch(shadow_report_id="r-stale"), path=manifest)
    result = bm.dry_run_batch(
        batch, qualification_reader=_qualified(),
        report_checker=lambda b: (False, ["scope mismatch: the report covers abc"]),
        path=manifest)

    assert result.outcome == bm.BLOCKED
    assert result.report_citable is False
    assert any("scope mismatch" in r for r in result.reasons)


def test_a_citable_report_passes_the_check(manifest):
    batch = bm.declare_batch(_batch(shadow_report_id="r-ok"), path=manifest)
    result = bm.dry_run_batch(batch, qualification_reader=_qualified(),
                              report_checker=_report_ok, path=manifest)
    assert result.checks["shadow_report_citable"] is True
    assert result.report_citable is True


def test_a_missing_report_is_a_failed_check_not_a_skipped_one(manifest):
    """A batch citing nothing has no evidence behind it; that is a fail, not an
    absence of one."""
    batch = bm.declare_batch(_batch(), path=manifest)
    result = bm.dry_run_batch(batch, qualification_reader=_qualified(),
                              report_checker=_report_ok, path=manifest)
    assert result.checks["shadow_report_citable"] is False


def test_a_report_error_is_reported_rather_than_raised(manifest):
    """An unknown report id must read as "not citable plus why", not crash the
    whole dry-run."""
    from ats.workflow import shadow_reports

    batch = bm.declare_batch(_batch(shadow_report_id="r-missing"), path=manifest)
    result = bm.dry_run_batch(batch, qualification_reader=_qualified(),
                              path=manifest)
    assert result.outcome == bm.BLOCKED
    assert result.reasons


# --- qualification (8.4) --------------------------------------------------- #

def test_one_ineligible_consumer_holds_the_whole_batch(manifest):
    """They move together, so a single ineligible member holds the batch.
    Reporting per consumer would suggest the others could go alone."""
    batch = bm.declare_batch(_batch(), path=manifest)

    def _mixed(consumer_id, scope):
        return {"status": "eligible" if consumer_id != "macro" else "ineligible"}

    result = bm.dry_run_batch(batch, qualification_reader=_mixed,
                              report_checker=_report_ok, path=manifest)
    assert result.outcome == bm.BLOCKED
    assert result.qualification_status == "5/6 eligible"
    assert any("macro" in r for r in result.reasons)


def test_qualification_is_re_read_not_remembered(manifest, plane_db):
    """8.4: a stored ready verdict must not outlive its evidence. The same batch,
    same declaration, different current qualification."""
    batch = bm.declare_batch(_batch(), path=manifest)
    ready = bm.dry_run_batch(batch, qualification_reader=_qualified(),
                             report_checker=_report_ok, path=manifest,
                             cutover_path=plane_db)
    assert ready.outcome == bm.READY

    later = bm.dry_run_batch(batch, qualification_reader=_qualified("ineligible"),
                             report_checker=_report_ok, path=manifest,
                             cutover_path=plane_db)
    assert later.outcome == bm.BLOCKED


# --- authority unreadable -------------------------------------------------- #

def test_an_unreadable_authority_blocks_rather_than_reading_as_no_conflict(
        manifest, monkeypatch):
    """Treating an unreadable authority as 'no problem' is how two active routes
    get shipped."""
    batch = bm.declare_batch(_batch(), path=manifest)

    def _explode(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(plane, "all_boundaries", _explode)
    result = bm.dry_run_batch(batch, qualification_reader=_qualified(),
                              report_checker=_report_ok, path=manifest)

    assert result.outcome == bm.BLOCKED
    assert result.checks["boundaries_readable"] is False
    assert any("could not be read" in r for r in result.reasons)


def test_an_unwired_boundary_is_reported(manifest, tmp_path):
    """A boundary nothing reads cannot justify a cutover."""
    bare = str(tmp_path / "bare.sqlite")
    plane.bootstrap(actor="test", path=bare)   # no wiring
    batch = bm.declare_batch(_batch(), path=manifest)

    result = bm.dry_run_batch(batch, qualification_reader=_qualified(),
                              report_checker=_report_ok, path=manifest,
                              cutover_path=bare)
    assert result.outcome == bm.BLOCKED
    assert any("no declared wiring" in r for r in result.reasons)


# --------------------------------------------------------------------------- #
# dry-run bookkeeping
# --------------------------------------------------------------------------- #

def test_a_dry_run_is_recorded_even_when_blocked(manifest):
    """"We looked and it was blocked" is the reading an operator needs when the
    blocker is later resolved — and it distinguishes a re-check from a first look.
    """
    batch = bm.declare_batch(_batch(fallback_proof="missing"), path=manifest)
    bm.dry_run_batch(batch, qualification_reader=_qualified(), path=manifest)

    with bm._connect(manifest) as conn:
        rows = conn.execute(
            "SELECT * FROM cutover_batch_dryruns WHERE batch_id=?",
            (batch.batch_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["outcome"] == bm.NOT_SWITCHABLE


def test_dry_run_records_are_append_only(manifest):
    """Overwriting would make 'it was ready before we changed something'
    unreconstructable."""
    batch = bm.declare_batch(_batch(), path=manifest)
    bm.dry_run_batch(batch, qualification_reader=_qualified(),
                     report_checker=_report_ok, path=manifest)

    with bm._connect(manifest) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE cutover_batch_dryruns SET outcome='ready'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM cutover_batch_dryruns")


def test_dry_run_over_the_whole_manifest(manifest):
    bm.declare_batch(_batch(batch_id="b-research"), path=manifest)
    bm.declare_batch(_batch(batch_id="b-schedule", batch_class=bm.SCHEDULE),
                     path=manifest)

    results = bm.dry_run(qualification_reader=_qualified(),
                         report_checker=_report_ok, path=manifest)
    assert {r.batch_id for r in results} == {"b-research", "b-schedule"}


# --------------------------------------------------------------------------- #
# 8.4 — responding to drift
# --------------------------------------------------------------------------- #

def test_an_eligible_batch_holds(manifest):
    batch = _batch()
    response = bm.respond_to_drift(batch, qualification_status="eligible")
    assert response.action == bm.HOLD


def test_a_withdrawn_qualification_stops_and_never_falls_back(manifest):
    """Falling back is itself gated on the qualification that just disappeared, so
    answering a withdrawal with 'roll back' is self-defeating."""
    batch = _batch()
    response = bm.respond_to_drift(batch, qualification_status="ineligible",
                                   serving_traffic=True)
    assert response.action == bm.STOP
    assert "stops rather than falls back" in response.reason


def test_an_unrecognised_status_also_stops_and_says_why(manifest):
    """"I could not check" must not read as "probably fine", and must not be
    reported as a withdrawal it is not."""
    response = bm.respond_to_drift(_batch(), qualification_status="",
                                   serving_traffic=True)
    assert response.action == bm.STOP
    assert "cannot be read" in response.reason


def test_an_unknown_serving_state_holds_rather_than_assuming(manifest):
    """Advising a rollback for a batch that never switched sends the operator
    somewhere else entirely."""
    response = bm.respond_to_drift(_batch(), qualification_status="degraded",
                                   serving_traffic=None)
    assert response.action == bm.HOLD
    assert "not serving traffic" in response.reason or "unknown" in response.reason


def test_a_not_serving_batch_holds_rather_than_rolling_back(manifest):
    response = bm.respond_to_drift(_batch(), qualification_status="degraded",
                                   serving_traffic=False)
    assert response.action == bm.HOLD
    assert "nothing to roll back" in response.reason


def test_a_serving_batch_falls_back_when_the_fallback_is_proven(manifest):
    response = bm.respond_to_drift(_batch(), qualification_status="degraded",
                                   serving_traffic=True)
    assert response.action == bm.FALL_BACK
    assert "available and proven" in response.reason


def test_a_serving_batch_stops_when_the_fallback_is_unusable(manifest):
    batch = _batch(fallback_available="no")
    response = bm.respond_to_drift(batch, qualification_status="degraded",
                                   serving_traffic=True)
    assert response.action == bm.STOP
    assert "fallback is unusable" in response.reason


# --------------------------------------------------------------------------- #
# 8.5 — the report
# --------------------------------------------------------------------------- #

def test_the_summary_counts_per_outcome(manifest, plane_db):
    bm.declare_batch(_batch(batch_id="b-ok"), path=manifest)
    bm.declare_batch(_batch(batch_id="b-bad", fallback_proof="missing"),
                     path=manifest)
    bm.declare_batch(_batch(batch_id="b-collection",
                            batch_class=bm.COLLECTION_PUBLISH,
                            old_route="", new_route="",
                            direct_verification=True), path=manifest)

    results = bm.dry_run(qualification_reader=_qualified(),
                         report_checker=_report_ok, path=manifest,
                         cutover_path=plane_db)
    summary = bm.summarize(results)

    assert summary["total"] == 3
    assert summary["by_outcome"] == {bm.NOT_SWITCHABLE: 1, bm.READY: 1,
                                     bm.VERIFIED_IN_PLACE: 1}
    assert sorted(summary["switchable"]) == ["b-collection", "b-ok"]


def test_the_report_names_each_blocked_batch_and_its_reason(manifest):
    bm.declare_batch(_batch(batch_id="b-no-fallback",
                            fallback_proof="missing"), path=manifest)
    bm.declare_batch(_batch(batch_id="b-ok"), path=manifest)

    results = bm.dry_run(qualification_reader=_qualified(),
                         report_checker=_report_ok, path=manifest)
    report = bm.render_report(results, batches=bm.list_batches(path=manifest))

    assert "`b-no-fallback`" in report
    assert "no safe way back" in report
    assert "缺项只阻断受影响范围" in report


def test_the_report_states_that_dry_run_changes_nothing(manifest):
    bm.declare_batch(_batch(), path=manifest)
    results = bm.dry_run(qualification_reader=_qualified(),
                         report_checker=_report_ok, path=manifest)
    assert "不改变任何路由" in bm.render_report(results)


def test_the_report_states_that_full_trading_still_needs_everything(manifest):
    """Batch-level readiness is not authorisation to trade automatically."""
    bm.declare_batch(_batch(), path=manifest)
    results = bm.dry_run(qualification_reader=_qualified(),
                         report_checker=_report_ok, path=manifest)
    report = bm.render_report(results)
    assert "完整自动交易仍要求全部必需输入与审批链同时满足" in report


def test_the_manifest_render_flags_an_undrilled_fallback(manifest):
    batch = bm.declare_batch(_batch(fallback_drill_ref=""), path=manifest)
    text = bm.render_manifest([batch])
    assert "**未演练**" in text
    assert "not_switchable" in text


def test_the_manifest_render_lists_every_declared_field_that_matters(manifest):
    batch = bm.declare_batch(_batch(), path=manifest)
    text = bm.render_manifest([batch])
    for value in (batch.batch_id, batch.owner, batch.observation_window,
                  batch.stop_conditions, batch.fallback_drill_ref):
        assert value in text


def test_the_batch_row_carries_everything_a_switch_decision_needs(manifest):
    """A row that omits the fallback fields cannot answer 'what if it goes wrong'."""
    batch = bm.declare_batch(_batch(), path=manifest)
    row = batch.as_row()
    for key in ("boundaries", "consumers", "observation_window",
                "success_criteria", "stop_conditions", "fallback_route",
                "fallback_proof", "fallback_drill_ref", "shadow_report_id",
                "required_surfaces", "direct_verification"):
        assert key in row, key
    # JSON-serialisable: the CLI prints it.
    json.dumps(row, default=str)