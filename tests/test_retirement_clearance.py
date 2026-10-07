"""Consumer zero, reconciliation and tombstone consistency (tasks 12.1–12.5).

The load-bearing property across most of these is that a **partial result never
reads as a pass**. Three ways that could happen, each with a test:

- reconciling one metric out of seven (12.2) — the summary line would say
  "reconciled" and the unreconciled purposes could drift unnoticed
- a consumer whose state could not be determined being counted as zero (12.1)
- the old/new comparison path still reading the legacy route while we assert
  "nobody reads it" (12.1) — the measuring instrument is part of what it measures

And one the other way: a tombstone that says `retired` while still readable must
**fail** (12.4), because a false "done" is worse than a pending one.
"""

import pytest

from ats.workflow import retirement_clearance as rc


# --- 12.1 consumer zero ------------------------------------------------------

def test_a_governed_consumer_is_zero():
    verdicts = rc.verify_consumer_zero(
        "sector_reviews", ["agents.sector.review"],
        access_reader=lambda c: {"legacy_reads": [], "reason": "no legacy read"})
    assert verdicts[0].verdict == rc.ZERO
    assert verdicts[0].blocks_exit is False


def test_a_consumer_still_on_the_legacy_path_blocks_exit():
    verdicts = rc.verify_consumer_zero(
        "chief.legacy_table_direct_reads", ["agents.chief.assemble"],
        access_reader=lambda c: {"legacy_reads": ["ats.execution.state_api"],
                                 "reason": "still reads the old model"})
    assert verdicts[0].verdict == rc.NOT_ZERO
    assert verdicts[0].blocks_exit is True
    # The reader supplied both a reason and the legacy reads it saw; both are kept,
    # because "still reads the old model" is only actionable next to which module.
    assert "old model" in verdicts[0].reason
    assert verdicts[0].evidence["legacy_reads"] == ["ats.execution.state_api"]


def test_no_reader_at_all_is_partial_not_zero():
    """Unknown is not zero — the whole criterion turns on what it cannot see."""
    verdicts = rc.verify_consumer_zero("x", ["agents.sector.review"])
    assert verdicts[0].verdict == rc.PARTIAL
    assert verdicts[0].blocks_exit is True
    assert "not zero" in verdicts[0].reason


def test_the_comparison_path_being_live_keeps_the_entry_pending():
    """The instrument that proves "nobody reads it" is itself still reading it."""
    verdicts = rc.verify_consumer_zero(
        "sector_reviews", ["agents.sector.review"],
        access_reader=lambda c: {"legacy_reads": []},
        comparison_path_still_live=True)
    assert verdicts[0].verdict == rc.PARTIAL
    assert verdicts[0].reason.startswith("the old/new comparison")


def test_the_access_reader_path_does_not_need_the_module_mapping():
    """An access reader answers for a named consumer directly.

    The mapping only exists for the scanner path, which takes role ids. Asserted
    so the two paths stay independent — a future change that made the reader path
    require a mapping would break every caller that reports its own reading.
    """
    verdicts = rc.verify_consumer_zero(
        "x", ["some.consumer.that.is.not.mapped"],
        access_reader=lambda c: {"legacy_reads": []})
    assert verdicts[0].verdict == rc.ZERO


def test_an_unmapped_module_path_is_rejected_rather_than_guessed():
    from ats.workflow import intake_verification as iv

    with pytest.raises(KeyError) as excinfo:
        rc.role_for("agents.unknown.thing")
    assert "agents.unknown.thing" in str(excinfo.value)

    verdicts = rc.verify_consumer_zero("x", ["agents.unknown.thing"],
                                      scan=iv.scan_consumer_access)
    assert verdicts[0].verdict == rc.PARTIAL


def test_every_role_shaped_registered_path_maps_to_a_known_role():
    """A role path that lost its mapping would make an entry unverifiable.

    Only *role-shaped* paths are checked: the registry also names `chain.*`,
    `memory.store.*`, `runtime.*` and even `tests/*`, which are cleared by the
    access reader rather than by scanning imports. Requiring a role mapping for
    those would be asking the wrong question of them.
    """
    import yaml
    from ats.config import REPO_ROOT

    registry = yaml.safe_load(
        (REPO_ROOT / "config" / "workflow" / "legacy_retirement.yaml").read_text(
            encoding="utf-8"))
    role_prefixes = ("agents.", "trader.", "execution.")

    checked = 0
    for entry in registry["retired"]:
        for consumer in entry.get("consumers") or ():
            if not consumer.startswith(role_prefixes):
                continue
            if consumer in rc.MODULE_TO_ROLE:
                rc.role_for(consumer)  # raises when mapped but broken
                checked += 1
    assert checked >= 6, (
        "expected the registry's role-shaped consumers to be mapped; a drop here "
        "means the mapping stopped covering them")


# --- 12.2 reconciliation -----------------------------------------------------

USES = ("write", "admission", "read", "lineage", "completeness",
        "reconciliation", "rollback")


def _all_agree():
    return {use: {"agrees": True, "detail": f"{use} matched"} for use in USES}


def test_reconciling_every_use_passes():
    verdict = rc.reconcile("x", _all_agree(), required_uses=USES)
    assert verdict.verdict == rc.RECONCILED
    assert verdict.required_uses == USES


def test_reconciling_only_one_metric_is_refused():
    """12.2's explicit prohibition — and the reason it matters is that a summary
    line would otherwise report a pass."""
    verdict = rc.reconcile("x", {"read": {"agrees": True}}, required_uses=USES)
    assert verdict.verdict == rc.NOT_RECONCILED
    assert "1 of 7" in verdict.reason
    assert "Reconciling one metric proves one metric" in verdict.reason


def test_a_disagreeing_use_blocks_reconciliation_and_is_named():
    uses = _all_agree()
    uses["lineage"] = {"agrees": False, "detail": "old had 3 rows, new has 2"}
    verdict = rc.reconcile("x", uses, required_uses=USES)
    assert verdict.verdict == rc.NOT_RECONCILED
    assert "lineage" in verdict.reason


def test_reading_retired_data_is_never_reconciled():
    """The old path's own input is gone, so agreement cannot be established."""
    verdict = rc.reconcile("x", _all_agree(), required_uses=USES,
                           retired_data_readable=True)
    assert verdict.verdict == rc.NOT_RECONCILED
    assert "retired" in verdict.reason


# --- 12.3 rollback window ----------------------------------------------------

def test_an_undrilled_boundary_is_refused():
    assert rc.verify_rollback_window(drilled=False) == rc.WINDOW_REFUSED
    assert rc.verify_rollback_window(drilled=True,
                                     drill_reference="") == rc.WINDOW_REFUSED


def test_a_triggered_stop_condition_keeps_the_window_pending():
    """The rollback worked, but the window was not clean."""
    assert rc.verify_rollback_window(drilled=True, drill_reference="drill-1",
                                     stop_conditions=("差异未接受",),
                                     triggered=("差异未接受",)) == rc.WINDOW_REFUSED


def test_a_drilled_clean_window_passes():
    assert rc.verify_rollback_window(drilled=True, drill_reference="drill-1",
                                     stop_conditions=("差异未接受",),
                                     triggered=()) == rc.WINDOW_OK


# --- 12.4 tombstone vs behaviour ---------------------------------------------

def test_a_retired_tombstone_that_is_still_readable_fails():
    """A false "done" is worse than a pending one."""
    verdict = rc.check_tombstone_behaviour(
        "sector_reviews", "retired", readable_by=("agents.sector.review",))
    assert verdict.verdict == rc.INCONSISTENT
    assert "worse than a pending one" in verdict.reason


def test_a_pending_tombstone_with_readers_is_consistent():
    verdict = rc.check_tombstone_behaviour(
        "sector_reviews", "pending", readable_by=("agents.sector.review",))
    assert verdict.verdict == rc.CONSISTENT


def test_a_retired_tombstone_nobody_reads_is_consistent():
    verdict = rc.check_tombstone_behaviour("sector_reviews", "retired")
    assert verdict.verdict == rc.CONSISTENT


# --- 12.5 fallback target pre-check ------------------------------------------

def test_a_retired_fallback_target_is_refused_before_the_call():
    called = []

    ok, reason = rc.precheck_fallback_target(
        "legacy", is_retired=lambda r: r == "legacy",
        is_available=lambda r: called.append(r) or True)
    assert ok is False
    assert called == [], "the availability probe must not run for a retired target"
    assert "not attempted" in reason


def test_an_unavailable_target_is_refused():
    ok, reason = rc.precheck_fallback_target("legacy", is_retired=lambda r: False,
                                            is_available=lambda r: False)
    assert ok is False
    assert "not available" in reason


def test_a_live_available_target_passes():
    ok, _reason = rc.precheck_fallback_target("legacy", is_retired=lambda r: False,
                                             is_available=lambda r: True)
    assert ok is True


def test_an_unnamed_target_is_refused():
    ok, reason = rc.precheck_fallback_target("", is_retired=lambda r: False)
    assert ok is False
    assert "no fallback target" in reason


# --- 12.6 per-entry decision -------------------------------------------------

def _clean_entry():
    return dict(
        zero_verdicts=[rc.ZeroVerdict("x", "agents.sector.review", rc.ZERO)],
        reconcile_verdict=rc.reconcile("x", _all_agree(), required_uses=USES),
        window=rc.WINDOW_OK,
        tombstone=rc.check_tombstone_behaviour("x", "pending"),
        required_uses=USES)


def test_an_entry_meeting_every_criterion_may_exit():
    decision = rc.decide_entry("x", **_clean_entry())
    assert decision.may_exit is True
    assert decision.proposed_status == "retired"
    assert decision.missing == ()


@pytest.mark.parametrize("break_it,expected", [
    (lambda kw: kw.update(zero_verdicts=[
        rc.ZeroVerdict("x", "agents.sector.review", rc.NOT_ZERO)]),
     "still on the legacy path"),
    (lambda kw: kw.update(window=rc.WINDOW_REFUSED), "rollback"),
    (lambda kw: kw.update(reconcile_verdict=rc.reconcile(
        "x", {"read": {"agrees": True}}, required_uses=USES)), "reconciliation"),
    (lambda kw: kw.update(tombstone=rc.check_tombstone_behaviour(
        "x", "retired", readable_by=("agents.sector.review",))), "tombstone"),
])
def test_any_missing_criterion_keeps_the_entry_pending_and_says_which(break_it,
                                                                     expected):
    kwargs = _clean_entry()
    break_it(kwargs)
    decision = rc.decide_entry("x", **kwargs)
    assert decision.may_exit is False
    assert decision.proposed_status == "pending"
    assert expected in decision.reason, decision.reason


def test_the_decision_does_not_modify_the_registry():
    """It reports; applying is a separate deliberate step."""
    before = dict(_clean_entry())
    rc.decide_entry("x", **before)
    assert before == _clean_entry()


# --- the report --------------------------------------------------------------

def test_the_report_names_every_pending_entry_and_what_it_waits_on():
    kwargs = _clean_entry()
    kwargs["window"] = rc.WINDOW_REFUSED
    decision = rc.decide_entry("sector_reviews", **kwargs)
    text = rc.render_report([decision])

    assert "`sector_reviews`" in text
    assert "rollback not drilled" in text
    assert "只登记不退出" in text or "保持 pending" in text
