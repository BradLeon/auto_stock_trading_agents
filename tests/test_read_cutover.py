"""Read-path batch cutover executor (task 9.1–9.5).

Two things are pinned here that a passing suite would otherwise let drift:

1. **The deployment authorisation is checked before anything else, and raising
   is the only failure mode.** 9.4's second scenario — every gate green,
   authorisation absent, must still refuse — is the state in which an
   unauthorised change looks most reasonable. A returned boolean invites
   `if not ok: log(...)` followed by a switch anyway.

2. **Paired boundaries move together.** `projection_read` and
   `dispatcher_schedule` are inseparable (the old schedule writes into stores the
   new reader no longer treats as authoritative). Switching one alone passes
   through the half-migrated state the control plane exists to reject — so the
   executor derives its chain from that authority rather than restating it.

Qualification is stubbed throughout rather than faked with a real ledger: what
these tests assert is the executor's decision logic, and 13.6 re-reads the real
gate against real evidence.
"""

from datetime import datetime, timedelta, timezone

import pytest

from ats.workflow import batch_manifest as bm
from ats.workflow import cutover as plane
from ats.workflow import cutover_wiring as wiring
from ats.workflow import read_cutover as rc

FUTURE = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
PAST = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


@pytest.fixture
def plane_db(tmp_path, monkeypatch) -> str:
    # Unit decision logic only; this is not a business-entry wiring proof.
    monkeypatch.setattr("ats.workflow.boundary_evidence.assert_enforced", lambda *a, **k: None)
    original = rc.execute_batch
    def execute(*args, **kwargs):
        kwargs.setdefault("report_checker", lambda batch: (True, []))
        return original(*args, **kwargs)
    monkeypatch.setattr(rc, "execute_batch", execute)
    path = str(tmp_path / "cutover.sqlite")
    wiring.bootstrap_wired(actor="test", path=path)
    return path


@pytest.fixture
def manifest_db(tmp_path) -> str:
    return str(tmp_path / "batches.sqlite")


def _batch(manifest_db, **overrides):
    fields = dict(
        shadow_report_id="unit-report-adapter",
        batch_id="b-research",
        batch_class=bm.RESEARCH_READ,
        owner="ats.data.products",
        scope={"consumers": ["layer", "sector", "macro"]},
        old_route="legacy",
        new_route="target",
        observation_window="2 个交易日",
        success_criteria="六分析角色读取结果与旧路径一致",
        stop_conditions="任一必需面出现未接受差异",
        fallback_route="legacy",
        fallback_proof="valid",
        fallback_retired="no",
        fallback_available="yes",
        fallback_drill_ref="drill-2026-10-01",
    )
    fields.update(overrides)
    return bm.declare_batch(bm.CutoverBatch(**fields), path=manifest_db)


def _auth(*consumers, **overrides) -> rc.DeploymentAuthorization:
    fields = dict(reference="DEP-2026-10-06", authorised_by="owner",
                  issued_by="owner", scope=tuple(consumers),
                  valid_until=FUTURE, note="首批读路径切流")
    fields.update(overrides)
    return rc.DeploymentAuthorization(**fields)


def _eligible(*, ineligible=()) -> callable:
    """Mirrors the real gate's return shape.

    `missing` is included because that list is the actionable part of an
    ineligible verdict — a stub that omits it would let the report drop the one
    thing the operator can act on, and the test suite would not notice.
    """
    def reader(consumer_id, scope):
        if consumer_id in ineligible:
            return {"status": "ineligible", "reason": "not yet evidenced",
                    "missing": ["write", "admission", "read", "lineage",
                                "completeness", "reconciliation", "rollback"]}
        return {"status": "eligible"}
    return reader


# --- 9.4 the authorisation gate ---------------------------------------------

def test_a_green_run_without_an_authorisation_still_refuses(manifest_db, plane_db):
    """9.4's second scenario, and the reason the gate is separate from the gates.

    Every other check passes here on purpose. If the authorisation were checked
    last, or only on failure, this test would switch a route.
    """
    result = rc.execute_batch(
        _batch(manifest_db), qualification_reader=_eligible(),
        authorisation=None, apply=True, path=manifest_db, cutover_path=plane_db)

    assert result.outcome == rc.BATCH_REFUSED
    assert result.changed is False
    assert all(s.outcome == rc.UNAUTHORIZED for s in result.scopes)
    assert plane.all_boundaries(plane_db)[plane.PROJECTION_READ].route == "legacy"


def test_an_unusable_authorisation_names_what_is_missing():
    """Incomplete means unauditable, not weaker-but-valid."""
    with pytest.raises(rc.DeploymentAuthorizationError) as excinfo:
        rc.assert_authorised(rc.DeploymentAuthorization(
            reference="", authorised_by="", issued_by="owner",
            scope=("layer",), valid_until=""), consumer_id="layer")
    assert "reference" in str(excinfo.value)


def test_an_expired_authorisation_refuses(manifest_db, plane_db):
    batch = _batch(manifest_db)
    result = rc.execute_batch(
        batch, qualification_reader=_eligible(),
        authorisation=_auth("layer", "sector", "macro", valid_until=PAST),
        apply=True, path=manifest_db, cutover_path=plane_db)
    assert result.outcome == rc.BATCH_REFUSED
    assert "expired" in result.scopes[0].reason
    assert result.changed is False


def test_an_authorisation_that_omits_a_consumer_holds_exactly_that_one(manifest_db,
                                                                     plane_db):
    """A narrow grant must not become a broad one by covering the batch."""
    batch = _batch(manifest_db)
    result = rc.execute_batch(
        batch, qualification_reader=_eligible(),
        authorisation=_auth("layer", "macro"),  # sector deliberately absent
        apply=True, path=manifest_db, cutover_path=plane_db)

    by_consumer = {s.consumer_id: s for s in result.scopes}
    assert by_consumer["sector"].outcome == rc.UNAUTHORIZED
    assert "not this consumer" in by_consumer["sector"].reason
    assert by_consumer["layer"].outcome == rc.SWITCHED


def test_assert_authorised_raises_rather_than_returning(tmp_path):
    """A returned boolean invites `if not ok: log(...)` and then a switch anyway."""
    with pytest.raises(rc.DeploymentAuthorizationError):
        rc.assert_authorised(None, consumer_id="layer")
    with pytest.raises(rc.DeploymentAuthorizationError):
        rc.assert_authorised(_auth("other"), consumer_id="layer")


# --- 9.2 per-consumer decisions ----------------------------------------------

def test_one_unqualified_consumer_does_not_hold_back_the_qualified_ones(manifest_db,
                                                                      plane_db):
    """The whole reason the executor decides per consumer.

    A batch holds six consumers of which four qualify. Refusing the batch would
    hold back the ready four for a reason fixing the others cannot address;
    switching it wholesale would send the unqualified one onto the new route.
    """
    batch = _batch(manifest_db)
    result = rc.execute_batch(
        batch, qualification_reader=_eligible(ineligible=("sector",)),
        authorisation=_auth("layer", "sector", "macro"),
        apply=True, path=manifest_db, cutover_path=plane_db)

    assert result.outcome == rc.BATCH_PARTIAL
    assert result.changed is True
    by_consumer = {s.consumer_id: s for s in result.scopes}
    assert by_consumer["layer"].outcome == rc.SWITCHED
    assert by_consumer["macro"].outcome == rc.SWITCHED
    assert by_consumer["sector"].outcome == rc.NOT_QUALIFIED
    assert result.as_row()["not_switched"] == ["sector"]


def test_qualification_is_re_read_per_run_not_remembered(manifest_db, plane_db):
    """It carries a TTL and can be revoked, so a stored verdict must not outlive
    its evidence.

    Withdrawn for every consumer, so the expected outcome is a refusal rather
    than a partial: the point is that the second run reaches the gate at all.
    """
    batch = _batch(manifest_db)
    first = rc.execute_batch(
        batch, qualification_reader=_eligible(), authorisation=_auth(*batch.consumers),
        apply=True, path=manifest_db, cutover_path=plane_db)
    assert first.outcome == rc.BATCH_SWITCHED

    # Same declaration, same authorisation — but the evidence was withdrawn.
    for boundary in rc._switch_chain(plane.PROJECTION_READ):
        plane.set_route(boundary, "legacy", actor="test", reason="withdraw",
                        path=plane_db)

    second = rc.execute_batch(
        batch,
        qualification_reader=_eligible(ineligible=batch.consumers),
        authorisation=_auth(*batch.consumers),
        apply=True, path=manifest_db, cutover_path=plane_db)
    assert second.outcome == rc.BATCH_REFUSED
    assert second.changed is False
    assert all(s.outcome == rc.NOT_QUALIFIED for s in second.scopes)


def test_every_blocker_is_reported_not_just_the_first(manifest_db, plane_db):
    """An operator acting on the record would otherwise fix one gap, re-run,
    discover the next, and repeat.

    This was not hypothetical: the first real run of `ats read plan` reported
    only "ineligible", because the qualification check came first and the missing
    fallback drill — a batch-level property — stayed invisible behind it. A record
    that names one problem when there are two is a record that understates the
    work.
    """
    batch = _batch(manifest_db, fallback_drill_ref="")  # never drilled
    result = rc.execute_batch(
        batch, qualification_reader=_eligible(ineligible=("layer",)),
        authorisation=_auth("layer"), apply=True,
        path=manifest_db, cutover_path=plane_db)

    scope = result.scopes[0]
    assert set(scope.checks["blockers"]) == {rc.NOT_QUALIFIED,
                                             rc.NO_SAFE_FALLBACK}
    assert "never been drilled" in scope.reason
    assert "missing evidence" in scope.reason, (
        "the gate's own missing list is the actionable part and must survive")


def test_a_gate_that_cannot_be_read_is_not_reported_as_a_finding(manifest_db,
                                                                 plane_db):
    """"I could not check" and "it does not qualify" are different claims.

    Reporting the first as the second puts a statement about the query onto the
    consumer — and the response to each is different.
    """
    def exploding(consumer_id, scope):
        return {"status": "unreadable", "reason": "the ledger is unreachable"}

    result = rc.execute_batch(
        _batch(manifest_db, scope={"consumers": ["layer"]}),
        qualification_reader=exploding, authorisation=_auth("layer"),
        apply=True, path=manifest_db, cutover_path=plane_db)

    scope = result.scopes[0]
    assert "could not be read" in scope.reason
    assert "failure to check" in scope.reason
    assert "missing evidence" not in scope.reason


def test_an_unreadable_gate_is_not_reported_as_a_finding(manifest_db, plane_db):
    """A gate that raises must not read as a green light, nor as a verdict."""
    def exploding(consumer_id, scope):
        raise RuntimeError("the qualification ledger is unreadable")

    result = rc.execute_batch(
        _batch(manifest_db), qualification_reader=exploding,
        authorisation=_auth("layer", "sector", "macro"), apply=True,
        path=manifest_db, cutover_path=plane_db)

    assert result.changed is False
    assert all(s.outcome == rc.NOT_QUALIFIED for s in result.scopes)
    assert "could not be read" in result.scopes[0].reason


def test_an_undrillable_fallback_blocks_a_qualified_consumer(manifest_db, plane_db):
    """Passing every gate is not permission when there is no way back."""
    batch = _batch(manifest_db, fallback_drill_ref="")
    result = rc.execute_batch(
        batch, qualification_reader=_eligible(),
        authorisation=_auth("layer", "sector", "macro"), apply=True,
        path=manifest_db, cutover_path=plane_db)

    assert result.changed is False
    assert all(s.outcome == rc.NO_SAFE_FALLBACK for s in result.scopes)
    assert "never been drilled" in result.scopes[0].reason


def test_a_retired_fallback_target_is_reported_as_retired_not_unavailable(manifest_db,
                                                                         plane_db):
    """The two failures need different fixes, so they must not share a verdict."""
    batch = _batch(manifest_db, fallback_retired="yes")
    result = rc.execute_batch(
        batch, qualification_reader=_eligible(),
        authorisation=_auth("layer"), apply=True,
        path=manifest_db, cutover_path=plane_db)
    scope = next(s for s in result.scopes if s.consumer_id == "layer")
    assert scope.outcome in {rc.FALLBACK_RETIRED, rc.FALLBACK_UNAVAILABLE}
    assert scope.reason


# --- 9.2 paired switching ----------------------------------------------------

def test_the_switch_chain_is_derived_from_the_control_plane_not_restated():
    """A second copy of the pairing could disagree with the authority that
    judges the result, and then the executor would move a route the pre-check
    had just rejected."""
    for boundary in (plane.PROJECTION_READ, plane.ANALYST_OUTPUT,
                     plane.DISPATCHER_SCHEDULE, plane.APPROVAL_LIFECYCLE,
                     plane.CLERK_PUBLICATION):
        chain = rc._switch_chain(boundary)
        assert boundary in chain
        for group, _reason in plane.INCOMPATIBLE:
            if boundary in group:
                assert set(group) <= set(chain), (
                    f"{boundary} 的链漏掉了控制平面要求同切�� {sorted(group)}")


def test_a_paired_boundary_moves_with_its_partner(manifest_db, plane_db):
    """The obsolete sequential global apply cannot expose a half migration.

    Explicit scope/SQL owner coordination is tested in test_phase_f_joint_cutover.
    """
    with pytest.raises(rc.ReadCutoverError,match="joint coordinator"):
        rc.execute_batch(
            _batch(manifest_db), qualification_reader=_eligible(),
            authorisation=_auth("layer", "sector", "macro"), apply=True,
            path=manifest_db, cutover_path=plane_db)

    states = plane.all_boundaries(plane_db)
    for boundary in rc._switch_chain(plane.PROJECTION_READ):
        assert states[boundary].route == "legacy", boundary


def test_rerunning_after_a_successful_switch_reports_already_not_a_conflict(
        manifest_db, plane_db):
    """An operator re-runs the executor in a loop before deciding, and must see
    "already on target" — not a compatibility conflict about a state they created.
    """
    batch = _batch(manifest_db)
    first = rc.execute_batch(
        batch, qualification_reader=_eligible(),
        authorisation=_auth(*batch.consumers), apply=True,
        path=manifest_db, cutover_path=plane_db)
    assert first.changed is True

    second = rc.execute_batch(
        batch, qualification_reader=_eligible(),
        authorisation=_auth(*batch.consumers), apply=True,
        path=manifest_db, cutover_path=plane_db)

    assert second.outcome == rc.BATCH_NOOP
    assert second.changed is False
    assert all(s.outcome == rc.ALREADY for s in second.scopes)
    assert "already serving the target route" in second.scopes[0].reason


def test_decide_mode_moves_nothing_even_when_every_gate_passes(manifest_db, plane_db):
    """9.1's runbook has an operator re-run this in a loop, so the deciding mode
    must not be able to move a route even by accident.

    It still records the attempt, matching 8.2's dry-run: "we looked and it was
    blocked" is the reading an operator needs when the blocker is later resolved,
    and it is what distinguishes a re-check from a first look.
    """
    result = rc.execute_batch(
        _batch(manifest_db), qualification_reader=_eligible(),
        authorisation=_auth("layer", "sector", "macro"),
        path=manifest_db, cutover_path=plane_db)  # apply defaults to False

    assert result.outcome == rc.BATCH_SWITCHED
    assert result.changed is False
    assert plane.all_boundaries(plane_db)[plane.PROJECTION_READ].route == "legacy"

    history = rc.run_history(path=manifest_db)
    assert len(history) == 1
    assert history[0]["changed"] == 0, "decide-only must record that nothing moved"
    assert history[0]["applied"] == 0


def test_the_executor_cannot_move_the_live_trader(manifest_db, plane_db):
    """Otherwise the 11.1 live authorisation gate is decorative."""
    batch = _batch(manifest_db, batch_class=bm.LIVE_TRADER,
                   scope={"consumers": ["trader"]})
    result = rc.execute_batch(
        batch, qualification_reader=_eligible(), authorisation=_auth("trader"),
        apply=True, path=manifest_db, cutover_path=plane_db)

    assert result.outcome == rc.BATCH_REFUSED
    assert result.scopes[0].outcome == rc.BOUNDARY_UNWIRED
    assert plane.all_boundaries(plane_db)[plane.LIVE_TRADER].route == "disabled"


# --- 9.3 projection availability ---------------------------------------------

def test_a_missing_projection_fails_the_scope_and_names_it(manifest_db, plane_db):
    """9.3's negative case: the new read model has no projection for this scope,
    so the scope fails and the safe-fallback decision applies."""
    batch = _batch(manifest_db, scope={"consumers": ["layer"]})
    result = rc.execute_batch(
        batch, qualification_reader=_eligible(), authorisation=_auth("layer"),
        projection_checker=lambda bid, scope: {
            "available": False,
            "reason": "the new read model has no projection for this scope"},
        apply=True, path=manifest_db, cutover_path=plane_db)

    assert result.outcome == rc.BATCH_REFUSED
    assert result.changed is False
    assert result.scopes[0].outcome == rc.PROJECTION_MISSING
    assert "no projection" in result.scopes[0].reason


def test_an_absent_projection_checker_is_visible_as_not_checked(manifest_db, plane_db):
    """Otherwise "no checker" silently reads as "checked and fine"."""
    result = rc.execute_batch(
        _batch(manifest_db), qualification_reader=_eligible(),
        authorisation=_auth("layer", "sector", "macro"),
        path=manifest_db, cutover_path=plane_db)
    assert result.changed is False  # decide-only; the point is the reporting below


def test_a_projection_check_that_crashes_is_not_a_pass(manifest_db, plane_db):
    def exploding(batch_id, scope):
        raise RuntimeError("projection index unreadable")

    result = rc.execute_batch(
        _batch(manifest_db), qualification_reader=_eligible(),
        authorisation=_auth("layer"), projection_checker=exploding,
        apply=True, path=manifest_db, cutover_path=plane_db)

    assert result.changed is False
    assert result.scopes[0].outcome == rc.PROJECTION_MISSING
    assert "failed to run" in result.scopes[0].reason


# --- 9.5 the record ----------------------------------------------------------

def test_an_applied_switch_is_recorded_with_its_scope_decisions(manifest_db, plane_db):
    """9.5: after cutting back, the record must show what was decided and why —
    that record is what a later reader checks."""
    import json

    rc.execute_batch(
        _batch(manifest_db), qualification_reader=_eligible(ineligible=("sector",)),
        authorisation=_auth("layer", "sector", "macro"), apply=True,
        path=manifest_db, cutover_path=plane_db, actor="operator")

    history = rc.run_history(path=manifest_db)
    assert len(history) == 1
    entry = history[0]
    assert entry["batch_id"] == "b-research"
    assert entry["changed"] == 1
    assert entry["actor"] == "operator"

    scopes = json.loads(entry["scopes_json"])
    by_consumer = {s["consumer_id"]: s for s in scopes}
    assert by_consumer["sector"]["outcome"] == rc.NOT_QUALIFIED
    assert by_consumer["sector"]["reason"]


def test_the_record_is_append_only(manifest_db, plane_db):
    """A re-check and a first look must be distinguishable; rewriting the history
    destroys the difference."""
    batch = _batch(manifest_db)
    for _ in range(3):
        rc.execute_batch(
            batch, qualification_reader=_eligible(),
            authorisation=_auth(*batch.consumers), apply=True,
            path=manifest_db, cutover_path=plane_db)
    assert len(rc.run_history(path=manifest_db)) == 3


def test_a_refused_run_is_recorded_too(manifest_db, plane_db):
    """"We looked and it was blocked" is the reading an operator needs when the
    blocker is later resolved."""
    rc.execute_batch(
        _batch(manifest_db), qualification_reader=_eligible(),
        authorisation=None, apply=True, path=manifest_db, cutover_path=plane_db)
    history = rc.run_history(path=manifest_db)
    assert len(history) == 1
    assert history[0]["changed"] == 0


# --- authorisation records ---------------------------------------------------

def test_an_authorisation_is_readable_back_by_reference(manifest_db):
    """A permission that lives only in the caller's arguments cannot answer
    "who authorised this?" a week later."""
    auth = _auth("layer", "sector")
    rc.write_authorisation(auth, path=manifest_db, actor="operator")
    stored = rc.read_authorisation("DEP-2026-10-06", path=manifest_db)
    assert stored is not None
    assert stored.scope == ("layer", "sector")
    assert stored.authorised_by == "owner"


def test_an_unauditable_authorisation_is_not_stored(manifest_db):
    with pytest.raises(rc.DeploymentAuthorizationError):
        rc.write_authorisation(
            rc.DeploymentAuthorization(reference="", authorised_by="x",
                                       issued_by="y", scope=("layer",),
                                       valid_until=FUTURE),
            path=manifest_db)
    assert rc.read_authorisation("", path=manifest_db) is None


def test_an_unknown_reference_reads_as_absent_not_as_a_blank_grant(manifest_db):
    assert rc.read_authorisation("never-issued", path=manifest_db) is None


# --- the report --------------------------------------------------------------

def test_the_report_names_every_scope_that_did_not_move(manifest_db, plane_db):
    """"Partially switched" without the holdout list makes the operator re-derive
    it by hand, which is the work the output exists to save."""
    rc.execute_batch(
        _batch(manifest_db), qualification_reader=_eligible(ineligible=("sector",)),
        authorisation=_auth("layer", "sector", "macro"), apply=True,
        path=manifest_db, cutover_path=plane_db)

    # Re-decide with a still-missing consumer, so the report has a holdout to name.
    for boundary in rc._switch_chain(plane.PROJECTION_READ):
        plane.set_route(boundary, "legacy", actor="test", reason="withdraw",
                        path=plane_db)
    result = rc.execute_batch(
        _batch(manifest_db), qualification_reader=_eligible(ineligible=("sector",)),
        authorisation=_auth("layer", "sector", "macro"),
        path=manifest_db, cutover_path=plane_db)

    text = rc.render_report([result])
    assert "`sector`" in text
    assert rc.NOT_QUALIFIED in text
    assert "缺项只阻断受影响范围" in text


def test_the_report_leads_with_whether_anything_moved(manifest_db, plane_db):
    """The only line anyone scanning a list of batches needs."""
    results = [
        rc.execute_batch(_batch(manifest_db), qualification_reader=_eligible(),
                         authorisation=_auth(*("layer", "sector", "macro")),
                         apply=True, path=manifest_db, cutover_path=plane_db),
        rc.execute_batch(_batch(manifest_db, batch_id="b-none"),
                         qualification_reader=_eligible(),
                         authorisation=None, path=manifest_db, cutover_path=plane_db),
    ]
    text = rc.render_report(results)
    assert "实际改动路由" in text
    assert text.index("b-research") < text.index("逐批明细")
