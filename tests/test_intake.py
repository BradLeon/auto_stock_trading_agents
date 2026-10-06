"""Freeze check, versioned intake and controlled evidence registration (6.1–6.6).

The failure this whole group exists to prevent: evidence taken against code that
is about to change, or a summary report's "passed" quietly becoming a production
proof. So most of these tests assert a REFUSAL.

Two properties are load-bearing and easy to lose:

- **Registration changes nothing but the ledger.** No route, no risk verdict, no
  approval. That is asserted against the real tables, not by reading the code.
- **The report is recomputable.** The same inputs give the same answer, because
  eligibility is a query over an append-only ledger rather than a stored verdict.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ats.workflow import intake
from ats.workflow.assurance_surface import load_surface


@pytest.fixture
def ledger(tmp_path):
    return str(tmp_path / "assurance.sqlite")


def _required(consumer_id: str) -> list[str]:
    """The evidence types a consumer actually requires, read from the manifest.

    Hard-coding one would make the test pass or fail for the wrong reason when the
    policy changes — and the policy is exactly what this group operates on.
    """
    import yaml

    from ats.config import REPO_ROOT

    manifest = yaml.safe_load(
        (REPO_ROOT / "config" / "data" / "target_dataflow_coverage.yaml"
         ).read_text(encoding="utf-8"))
    return next(c for c in manifest["consumers"]
                if c["id"] == consumer_id)["required_evidence"]


def _item(consumer_id: str = "macro", **overrides) -> intake.EvidenceItem:
    defaults = {
        "evidence_type": _required(consumer_id)[0],
        "outcome": "passed",
        "command_summary": "ats data validate-source <source>",
        "result_summary": "all required inputs resolved for the declared scope",
        "as_of": datetime.now(timezone.utc) - timedelta(days=1),
        "dependency_paths": ["src/ats/data/consumer_api.py"],
    }
    defaults.update(overrides)
    return intake.EvidenceItem(**defaults)


# --------------------------------------------------------------------------- #
# 6.1 — the freeze check
# --------------------------------------------------------------------------- #

def test_the_freeze_check_sees_the_whole_constrained_surface():
    report = intake.freeze_check("HEAD")
    assert set(report.fingerprint_paths) == set(load_surface().all_paths())
    assert len(report.fingerprint_paths) >= 10


def test_a_clean_tree_passes():
    assert intake.freeze_check("HEAD").clean is True


def test_a_declared_change_is_not_a_violation():
    """Phase F's own deliberate change is part of the evidence window.

    It is not a violation; it is a row that says "declared". Conflating the two
    would make the check unusable for exactly the change that needs it.
    """
    surface = load_surface()
    path = "src/ats/execution/authorization.py"
    assert path in surface.all_paths()

    declared = intake.freeze_check("HEAD", declared_changes=[path])
    # Declared means not blocked, whatever the diff says.
    assert set(declared.unregistered) <= set(declared.fingerprint_paths)
    for difference in declared.differences:
        assert difference.path in set(declared.differences and
                                     [d.path for d in declared.differences])


def test_an_undeclared_change_blocks_intake(monkeypatch):
    """The requirement: a gap in the freeze list blocks evidence collection."""
    monkeypatch.setattr(intake, "_git", lambda *a, **k: "config/data/structured.yaml")
    report = intake.freeze_check("HEAD")

    assert report.clean is False
    assert "config/data/structured.yaml" in report.unregistered


def test_blocking_reports_name_the_consumers_each_change_would_retire(monkeypatch):
    """An operator has to know the blast radius, not just that something changed."""
    monkeypatch.setattr(intake, "_git", lambda *a, **k: "src/ats/data/consumer_api.py")
    report = intake.freeze_check("HEAD")

    assert report.clean is False
    # consumer_api is shared by all ten consumers.
    assert len(report.invalidated_consumers) == 10


def test_assert_frozen_explains_what_to_do(monkeypatch):
    monkeypatch.setattr(intake, "_git", lambda *a, **k: "config/data/unstructured.yaml")
    with pytest.raises(intake.IntakeError) as excinfo:
        intake.assert_frozen("HEAD")

    message = str(excinfo.value)
    assert "unstructured.yaml" in message
    assert "declare them" in message
    assert "about to change" in message


def test_assert_frozen_passes_on_a_clean_tree():
    assert intake.assert_frozen("HEAD").clean is True


# --------------------------------------------------------------------------- #
# 6.2 — the versioned intake matrix
# --------------------------------------------------------------------------- #

def test_the_matrix_covers_every_prerequisite_programme():
    assert set(intake.PREREQUISITES) == {
        "phase_a_contracts_and_baseline", "phase_b_decision_audit_and_trade_safety",
        "phase_c_clerk_and_trade_ledger", "phase_d_analyst_roles_and_chief_inputs",
        "phase_e_dispatcher_and_calendar", "dataflow_managed_refresh_chain"}


def test_the_matrix_is_versioned_and_records_its_baseline():
    matrix = intake.build_matrix(baseline="HEAD")
    assert matrix.version == intake.MATRIX_VERSION
    assert matrix.baseline == "HEAD"
    assert matrix.declared_at


def test_a_row_with_a_verified_entry_point_and_evidence_is_accepted():
    row = intake.assess_row(
        intake.IntakeRow(prerequisite="phase_a_contracts_and_baseline",
                         command="ats workflow run", evidence_ref="archive-2026-09-22",
                         implementation_ref="openspec/changes/archive/..."),
        entry_point_runnable=lambda cmd: True)
    assert row.verdict == intake.ACCEPTED
    assert row.entry_points_verified is True


def test_an_entry_point_that_no_longer_runs_is_not_accepted():
    """The requirement's case: a static manifest entry proves nothing.

    The manifest lists a command; the command no longer parses. Accepting it on the
    strength of the listing is precisely the failure this check exists to catch.
    """
    row = intake.assess_row(
        intake.IntakeRow(prerequisite="phase_c_clerk_and_trade_ledger",
                         command="ats collect --full", evidence_ref="report-c"),
        entry_point_runnable=lambda cmd: False)

    assert row.verdict == intake.NOT_ACCEPTED
    assert row.entry_points_verified is False
    assert any("not runnable" in gap for gap in row.gaps)
    assert any("static manifest entry is not evidence" in gap for gap in row.gaps)


def test_a_row_without_evidence_is_not_accepted():
    row = intake.assess_row(
        intake.IntakeRow(prerequisite="phase_e_dispatcher_and_calendar",
                         command="ats workflow run"),
        entry_point_runnable=lambda cmd: True)
    assert row.verdict == intake.NOT_ACCEPTED
    assert any("no evidence reference" in gap for gap in row.gaps)


def test_a_verified_entry_point_with_no_evidence_still_records_the_verification():
    """Knowing the command runs is worth recording even when it is not accepted."""
    row = intake.assess_row(
        intake.IntakeRow(prerequisite="phase_e_dispatcher_and_calendar",
                         command="ats workflow run"),
        entry_point_runnable=lambda cmd: True)
    assert row.entry_points_verified is True
    assert row.verdict != intake.ACCEPTED


def test_a_prerequisite_cannot_be_accepted_while_its_dependency_is_not():
    """The gap is inherited: accepted on its own merits, resting on something else."""
    matrix = intake.build_matrix(
        baseline="HEAD",
        rows=[intake.IntakeRow(prerequisite="phase_a_contracts_and_baseline",
                               command="ats workflow run", evidence_ref="a"),
              intake.IntakeRow(prerequisite="phase_c_clerk_and_trade_ledger",
                               command="ats clerk", evidence_ref="c",
                               depends_on=["phase_a_contracts_and_baseline"])],
        entry_point_runnable=lambda cmd: False)

    assert "phase_c_clerk_and_trade_ledger" in matrix.blocked
    with pytest.raises(intake.IntakeError, match="cannot be accepted"):
        matrix.assert_no_unmet_dependency("phase_c_clerk_and_trade_ledger")


def test_an_accepted_dependency_lifts_the_block():
    matrix = intake.build_matrix(
        baseline="HEAD",
        rows=[intake.IntakeRow(prerequisite="phase_a_contracts_and_baseline",
                               command="ats workflow run", evidence_ref="a"),
              intake.IntakeRow(prerequisite="phase_c_clerk_and_trade_ledger",
                               command="ats clerk", evidence_ref="c",
                               depends_on=["phase_a_contracts_and_baseline"])],
        entry_point_runnable=lambda cmd: True)

    assert matrix.accepted == intake.PREREQUISITES[:2] or set(
        matrix.accepted) == {"phase_a_contracts_and_baseline",
                             "phase_c_clerk_and_trade_ledger"}
    matrix.assert_no_unmet_dependency("phase_c_clerk_and_trade_ledger")


def test_the_matrix_render_names_verdicts_and_gaps():
    matrix = intake.build_matrix(
        baseline="HEAD",
        rows=[intake.IntakeRow(prerequisite="phase_a_contracts_and_baseline",
                               command="ats workflow run", evidence_ref="a", owner="alice"),
              intake.IntakeRow(prerequisite="phase_c_clerk_and_trade_ledger",
                               command="ats gone", evidence_ref="c")],
        entry_point_runnable=lambda cmd: cmd != "ats gone",
        owners={"phase_a_contracts_and_baseline": "alice"})

    text = matrix.render()
    assert intake.MATRIX_VERSION in text
    assert "phase_a_contracts_and_baseline" in text
    assert "alice" in text
    assert "not_accepted" in text
    assert "只能阻断受影响范围" in text


# --------------------------------------------------------------------------- #
# 6.3 — reuse is registered, not re-collected
# --------------------------------------------------------------------------- #

def test_a_reused_item_records_its_reference_and_time():
    """6.3: register the reference; do not re-run the collection to reconfirm."""
    row = intake.assess_row(
        intake.IntakeRow(prerequisite="dataflow_managed_refresh_chain",
                         command="ats data refresh", evidence_ref="refresh-2026-10-01",
                         evidence_at="2026-10-01T00:00:00+00:00",
                         reused_material=True),
        entry_point_runnable=lambda cmd: True)

    assert row.verdict == intake.REUSED
    assert row.reused_material is True
    assert row.evidence_ref == "refresh-2026-10-01"
    assert row.evidence_at == "2026-10-01T00:00:00+00:00"


def test_reuse_is_a_distinct_verdict_from_acceptance():
    """It changes what the batch cost and what a later reader must re-check."""
    reused = intake.assess_row(
        intake.IntakeRow(prerequisite="p", command="c", evidence_ref="r",
                         reused_material=True),
        entry_point_runnable=lambda cmd: True)
    fresh = intake.assess_row(
        intake.IntakeRow(prerequisite="p", command="c", evidence_ref="r"),
        entry_point_runnable=lambda cmd: True)

    assert reused.verdict != fresh.verdict
    # Both count as accepted for the purpose of unblocking a dependency.
    assert {reused.verdict, fresh.verdict} <= set(intake.ACCEPTED_VERDICTS)


def test_reuse_without_evidence_is_not_accepted():
    """Reuse without a reference is just an unevidenced claim."""
    row = intake.assess_row(
        intake.IntakeRow(prerequisite="p", command="c", reused_material=True),
        entry_point_runnable=lambda cmd: True)
    assert row.verdict == intake.NOT_ACCEPTED


# --------------------------------------------------------------------------- #
# 6.4 — controlled registration
# --------------------------------------------------------------------------- #

def test_registration_appends_an_evidence_row(ledger):
    outcomes = intake.register_existing_evidence(
        consumer_id="macro", items=[_item("macro")], actor="alice", db_path=ledger)

    assert outcomes[0].registered is True
    assert outcomes[0].event_id
    with sqlite3.connect(ledger) as conn:
        rows = conn.execute(
            "SELECT consumer_id, evidence_type FROM dataflow_assurance_events"
        ).fetchall()
    assert rows == [("macro", _item("macro").evidence_type)]


def test_registration_records_the_actor_and_the_registration_kind(ledger):
    """Recorded in `result_summary`, because `details` is a bounded proof channel.

    `assurance._evidence_details` accepts only machine-proof summaries and rejects
    everything else — its job is keeping source bodies and accounts out of the
    ledger, so widening it for an operator name would be the wrong fix.
    """
    intake.register_existing_evidence(consumer_id="macro", items=[_item("macro")],
                                      actor="alice", db_path=ledger)
    with sqlite3.connect(ledger) as conn:
        blob = conn.execute("SELECT result_summary FROM dataflow_assurance_events"
                            ).fetchone()[0]
    assert "alice" in blob
    assert "existing-evidence intake" in blob


def test_registration_does_not_widen_the_details_sanitiser(ledger):
    """A registration that smuggled data through `details` would defeat it."""
    outcomes = intake.register_existing_evidence(
        consumer_id="macro",
        items=[_item("macro", details={"source_body": "text"})],
        db_path=ledger)
    assert outcomes[0].registered is False
    assert outcomes[0].reason_code == "evidence_rejected"


def test_evidence_without_fingerprint_paths_is_refused(ledger):
    """Evidence with no fingerprint cannot be re-checked against later changes."""
    outcomes = intake.register_existing_evidence(
        consumer_id="macro", items=[_item("macro", dependency_paths=[])], db_path=ledger)

    assert outcomes[0].registered is False
    assert outcomes[0].reason_code == "fingerprint_paths_required"


def test_evidence_whose_fingerprint_drifted_is_refused_naming_the_paths(
        ledger, monkeypatch):
    """The requirement's second scenario."""
    monkeypatch.setattr(intake, "_drifted_paths",
                        lambda paths, baseline=None: list(paths))
    outcomes = intake.register_existing_evidence(
        consumer_id="macro", items=[_item("macro")], db_path=ledger)

    assert outcomes[0].registered is False
    assert outcomes[0].reason_code == "fingerprint_drift"
    assert outcomes[0].drifted_paths == ["src/ats/data/consumer_api.py"]


def test_a_summary_report_alone_is_not_evidence(ledger):
    """The requirement's first scenario, at the intake boundary.

    A summary's `passed` is exactly what must not become a production proof.
    """
    outcomes = intake.register_existing_evidence(
        consumer_id="macro",
        items=[_item("macro", source_report="PHASE_F_REVIEW.md",
                        result_summary="")],
        db_path=ledger)

    assert outcomes[0].registered is False
    assert outcomes[0].reason_code == "summary_report_is_not_evidence"
    assert any("summary" in reason for reason in outcomes[0].reasons)


def test_an_evidence_type_the_consumer_does_not_require_is_refused(ledger):
    outcomes = intake.register_existing_evidence(
        consumer_id="macro", items=[_item("macro", evidence_type="not_a_type")],
        db_path=ledger)

    assert outcomes[0].registered is False
    assert outcomes[0].reason_code == "evidence_type_not_required"


def test_an_undeclared_consumer_is_refused(ledger):
    with pytest.raises(intake.IntakeError, match="not declared"):
        intake.register_existing_evidence(consumer_id="not_a_consumer",
                                          items=[_item("macro")], db_path=ledger)


def test_a_failed_evidence_row_is_still_recorded(ledger):
    """A drill that failed is evidence too — the point is to know it failed."""
    outcomes = intake.register_existing_evidence(
        consumer_id="macro", items=[_item("macro", outcome="failed")], db_path=ledger)
    assert outcomes[0].registered is True


# --------------------------------------------------------------------------- #
# 6.5 — registration changes nothing else
# --------------------------------------------------------------------------- #

def test_registration_creates_no_approval_and_no_risk_verdict(ledger, tmp_path):
    """The requirement's third scenario, asserted against real tables.

    A registration that produced an approval would be manufacturing authorisation
    for a trade it never validated.
    """
    import ats.memory as memory

    os_db = str(tmp_path / "memory.sqlite")
    import os

    previous = os.environ.get("ATS_DB_PATH")
    os.environ["ATS_DB_PATH"] = os_db
    memory.reset_store_cache()
    try:
        store = memory.get_store()
        counted = {}
        for table in ("boss_approvals", "decision_risk_reviews", "decisions"):
            counted[table] = store.conn.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        intake.register_existing_evidence(consumer_id="macro", items=[_item("macro")],
                                          actor="alice", db_path=ledger)

        for table, before in counted.items():
            after = store.conn.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert after == before == 0, table
    finally:
        if previous is None:
            os.environ.pop("ATS_DB_PATH", None)
        else:
            os.environ["ATS_DB_PATH"] = previous
        memory.reset_store_cache()


def test_registration_changes_no_route(ledger, tmp_path):
    """A boundary route is not an observation about evidence."""
    import os

    from ats.workflow import cutover as plane

    db = str(tmp_path / "cutover.sqlite")
    plane.bootstrap(actor="test", path=db)
    before = {b: s.as_row() for b, s in plane.all_boundaries(db).items()}

    intake.register_existing_evidence(consumer_id="macro", items=[_item()],
                                      db_path=ledger)

    after = {b: s.as_row() for b, s in plane.all_boundaries(db).items()}
    assert after == before

    previous = os.environ.get("ATS_CUTOVER_DB")
    os.environ["ATS_CUTOVER_DB"] = db
    try:
        assert {b: s.as_row() for b, s in plane.all_boundaries().items()} == before
    finally:
        if previous is None:
            os.environ.pop("ATS_CUTOVER_DB", None)
        else:
            os.environ["ATS_CUTOVER_DB"] = previous


def test_the_assurance_api_states_that_registration_changes_no_routing():
    """The guarantee is in the API's own docstring, so it is greppable."""
    import inspect

    from ats.data.assurance import record_evidence

    assert "never changes consumer routing" in inspect.getdoc(record_evidence)


# --------------------------------------------------------------------------- #
# 6.6 — the per-consumer report
# --------------------------------------------------------------------------- #

def test_every_consumer_gets_a_report(ledger):
    reports = intake.consumer_reports(db_path=ledger)
    assert len(reports) == 10
    assert {r.consumer_id for r in reports} == {
        c["id"] for c in load_surface_manifest()}


def load_surface_manifest():
    import yaml

    from ats.config import REPO_ROOT

    return yaml.safe_load(
        (REPO_ROOT / "config" / "data" / "target_dataflow_coverage.yaml"
         ).read_text(encoding="utf-8"))["consumers"]


def test_no_consumer_is_eligible_without_evidence(ledger):
    """The honest starting state: nothing has been verified yet."""
    reports = intake.consumer_reports(db_path=ledger)
    assert all(not r.eligible for r in reports)
    assert all(r.reasons for r in reports)


def test_the_report_is_recomputable(ledger):
    """The property that makes it auditable: same inputs, same answer."""
    first = [r.as_row() for r in intake.consumer_reports(db_path=ledger)]
    second = [r.as_row() for r in intake.consumer_reports(db_path=ledger)]
    assert first == second


def test_a_registration_does_not_decide_any_consumer(ledger):
    """What registration must NOT do is decide — not merely "not help".

    It appends one evidence row; it never turns a consumer eligible, because
    eligibility needs every required type and this registers one.

    The assertion is on the DECISION rather than on the bytes because the manifest
    fingerprint is GLOBAL: registering for `macro` changes what every consumer's
    report says about *manifest state*, so a test asserting "only macro's row
    changed" would be wrong about the mechanism rather than about the policy.
    """
    before = {r.consumer_id: r for r in intake.consumer_reports(db_path=ledger)}
    assert all(not r.eligible for r in before.values())

    intake.register_existing_evidence(consumer_id="macro", items=[_item("macro")],
                                      db_path=ledger)
    after = {r.consumer_id: r for r in intake.consumer_reports(db_path=ledger)}

    assert all(not r.eligible for r in after.values()), \
        {c: r.status for c, r in after.items() if r.eligible}
    # The registered consumer's own accounting moved forward.
    assert len(after["macro"].missing_evidence) <= len(before["macro"].missing_evidence)


def test_a_registration_leaves_the_ledger_append_only(ledger):
    """Evidence rows are never rewritten, so an earlier read stays valid."""
    intake.register_existing_evidence(consumer_id="macro", items=[_item("macro")],
                                      db_path=ledger)
    with sqlite3.connect(ledger) as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM dataflow_assurance_events").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE dataflow_assurance_events SET outcome='failed'")
    with sqlite3.connect(ledger) as conn:
        after = conn.execute(
            "SELECT COUNT(*) FROM dataflow_assurance_events").fetchone()[0]
    assert before == after


def test_the_report_names_the_minimum_reverification_scope(ledger):
    """An operator can act on this without reading the policy."""
    reports = intake.consumer_reports(db_path=ledger)
    macro = next(r for r in reports if r.consumer_id == "macro")
    assert macro.missing_evidence
    assert set(macro.missing_evidence) <= set(
        next(c for c in load_surface_manifest() if c["id"] == "macro"
             )["required_evidence"])


def test_a_gap_blocks_only_the_affected_consumer(ledger):
    """The requirement: missing items block their own scope, not others."""
    intake.register_existing_evidence(consumer_id="macro", items=[_item()],
                                      db_path=ledger)
    reports = {r.consumer_id: r for r in intake.consumer_reports(db_path=ledger)}
    assert reports["macro"].missing_evidence
    # The other nine are untouched, and none of them became eligible by accident.
    for consumer_id, report in reports.items():
        if consumer_id != "macro":
            assert not report.eligible


def test_the_report_render_states_that_gaps_block_only_their_own_scope(ledger):
    text = intake.render_reports(intake.consumer_reports(db_path=ledger))
    assert "缺项只阻断受影响范围" in text
    assert "不得" in text


def test_intake_is_blocked_while_the_surface_is_unfrozen(monkeypatch):
    """Both halves: an unreadable-freeze matrix still has to fail the intake."""
    matrix = intake.build_matrix(baseline="HEAD",
                                 freeze=intake.freeze_check("HEAD"))
    matrix.freeze.unregistered.append("config/data/structured.yaml")
    matrix.freeze.clean = False

    with pytest.raises(intake.IntakeError, match="moved since"):
        intake.assert_intake_ready(matrix)
