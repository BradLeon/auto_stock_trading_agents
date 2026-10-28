"""Six-surface shadow comparison with declared tolerance (Phase F 3.3).

The comparison is only worth something if `matched` means something. That is the
whole reason tolerated surfaces exist: a role rewrite rephrases every sentence,
and under byte equality every sentence reads as a divergence — which trains
operators to accept divergences, at which point the comparison stops being
evidence.

Three of these tests are the cases the task names, and two are ways the
schedule-omission surface fails silently.
"""

import pytest

from ats.workflow import shadow_compare as sc


# --------------------------------------------------------------------------- #
# the six surfaces exist and are all reached
# --------------------------------------------------------------------------- #

def test_a_comparison_reaches_all_six_surfaces():
    """A surface nobody looked at must appear, not be missing from the dict."""
    result = sc.compare_all(run_id="r1", left={}, right={},
                            expected_triggers={"t1"})
    assert set(result.surfaces) == set(sc.SIX_SURFACES)


def test_no_surface_is_ever_omitted_from_the_report():
    """`as_row` carries per-surface verdicts, not a tally.

    A summary count is how three-matched-one-diverged-two-skipped becomes "mostly
    fine", which is the outcome the report exists to prevent.
    """
    result = sc.compare_all(run_id="r1",
                            left={"risk_verdict": {"verdict": "pass"}},
                            right={"risk_verdict": {"verdict": "pass"}},
                            expected_triggers={"t1"})
    row = result.as_row()

    assert len(row["surfaces"]) == 6
    assert set(row["verdicts"]) == set(sc.SIX_SURFACES)
    assert not any("total" in key for key in row)


# --------------------------------------------------------------------------- #
# exact surfaces
# --------------------------------------------------------------------------- #

def test_an_identical_input_snapshot_is_matched():
    result = sc.compare_input_snapshot({"doc: 1"}, {"doc: 1"})
    assert result.verdict == sc.MATCHED


def test_a_different_input_snapshot_blocks_attribution():
    """If the runs saw different inputs, no downstream difference is attributable."""
    result = sc.compare_input_snapshot({"doc: 1"}, {"doc: 2"})
    assert result.verdict == sc.DIVERGED
    assert "no downstream difference can be attributed" in result.reason


def test_a_missing_risk_verdict_is_not_compared_not_matched():
    """The distinction the whole report rests on."""
    result = sc.compare_risk_verdict(None, {"verdict": "pass"})
    assert result.verdict == sc.NOT_COMPARED
    assert "no risk verdict" in result.reason


def test_a_differing_risk_verdict_is_diverged_with_both_sides_named():
    result = sc.compare_risk_verdict({"verdict": "pass", "round_no": 1},
                                     {"verdict": "reject", "round_no": 1})
    assert result.verdict == sc.DIVERGED
    assert result.details


def test_a_counterproposal_difference_is_diverged():
    """A counterproposal is the risk surface doing its job; hiding it hides risk."""
    result = sc.compare_risk_verdict(
        {"verdict": "revised", "counterproposal": {"qty": 100}},
        {"verdict": "revised", "counterproposal": {"qty": 200}})
    assert result.verdict == sc.DIVERGED


def test_an_approval_that_took_a_different_number_of_rounds_is_diverged():
    """Comparing only approved/not-approved hides the multi-round loop's behaviour."""
    same_outcome = sc.compare_approval_chain({"verdict": "approved", "round_no": 1},
                                             {"verdict": "approved", "round_no": 2})
    assert same_outcome.verdict == sc.DIVERGED

    same_rounds = sc.compare_approval_chain({"verdict": "approved", "round_no": 2},
                                            {"verdict": "approved", "round_no": 2})
    assert same_rounds.verdict == sc.MATCHED


def test_a_differing_trade_attribution_is_diverged():
    result = sc.compare_trade_attribution({"o1": "chained"}, {"o1": "unattributed"})
    assert result.verdict == sc.DIVERGED


def test_an_attribution_record_is_compared_whole_not_projected():
    """A regression guard on the comparison basis, not the verdict.

    An attribution row carries none of `verdict`/`status`/`action`, so a
    projection onto those keys yields the same empty value on both sides and a
    misattributed order compares as matched. Verifying the verdict surfaces
    separately would not catch this — the projection is correct for them.
    """
    assert sc.compare_trade_attribution(
        {"order_id": "o1", "origin": "chained"},
        {"order_id": "o1", "origin": "unattributed"}).verdict == sc.DIVERGED
    assert sc.compare_trade_attribution(
        {"order_id": "o1", "origin": "chained"},
        {"order_id": "o1", "origin": "chained"}).verdict == sc.MATCHED


# --------------------------------------------------------------------------- #
# the case the task names: the new schedule omits what the old one ran
# --------------------------------------------------------------------------- #

def test_the_new_schedule_omitting_an_old_run_is_diverged_with_the_trigger_named():
    """Task 3.3's first named case.

    The omission is listed with its trigger identity and time, and it reaches the
    report as its own surface — not folded into the analysis-output comparison.
    """
    result = sc.compare_schedule_omission(
        expected=["t1@09:00", "t2@09:30"],
        old_run=["t1@09:00", "t2@09:30"],
        new_run=["t1@09:00"])

    assert result.verdict == sc.DIVERGED
    omitted = [d for d in result.details if d.get("path") == "new"]
    assert [d["trigger"] for d in omitted] == ["t2@09:30"]
    # The old path ran everything, so the omission is named as the new path's.
    assert "0 omitted by the old path" in result.reason
    assert all(d["path"] == "new" for d in result.details if d.get("omitted"))


def test_the_omission_surface_is_independent_of_the_analysis_output():
    """A missed trigger is not a content difference and must not be merged with one."""
    result = sc.compare_all(
        run_id="r1",
        left={sc.SCHEDULE_OMISSION: ["t1", "t2"], sc.ANALYST_OUTPUT: {"a", "b"}},
        right={sc.SCHEDULE_OMISSION: ["t1"], sc.ANALYST_OUTPUT: {"a", "b"}},
        expected_triggers=["t1", "t2"])

    assert result.verdict_for(sc.SCHEDULE_OMISSION) == sc.DIVERGED
    assert result.verdict_for(sc.ANALYST_OUTPUT) == sc.MATCHED


def test_both_paths_omitting_the_same_trigger_is_still_diverged():
    """Task 3.3's second named case, and the one a naive comparison misses.

    If omissions were judged by whether the two paths agree, a trigger both
    dropped would read as agreement — and the reason it was dropped would never be
    looked for.
    """
    result = sc.compare_schedule_omission(
        expected=["t1", "t2", "t3"],
        old_run=["t1"],
        new_run=["t1"])

    assert result.verdict == sc.DIVERGED
    assert "missed by BOTH" in result.reason
    assert {d["trigger"] for d in result.details if d.get("path") == "old"} == {"t2", "t3"}
    assert {d["trigger"] for d in result.details if d.get("path") == "new"} == {"t2", "t3"}


def test_without_an_independent_expected_set_the_surface_is_not_compared():
    """Agreement between two paths cannot establish that anything ran.

    This is why `expected_triggers` is a parameter and not derived from the runs:
    the union would make a shared miss invisible by construction.
    """
    result = sc.compare_all(run_id="r1",
                            left={sc.SCHEDULE_OMISSION: ["t1"]},
                            right={sc.SCHEDULE_OMISSION: ["t1"]})
    surface = result.surfaces[sc.SCHEDULE_OMISSION]

    assert surface.verdict == sc.NOT_COMPARED
    assert "expected trigger set" in surface.reason


def test_a_trigger_either_path_ran_that_was_not_expected_is_reported():
    """Unexpected extra runs are a difference too, and an informative one."""
    result = sc.compare_schedule_omission(
        expected=["t1"], old_run=["t1", "t1-retry"], new_run=["t1"])

    assert result.verdict == sc.DIVERGED
    unexpected = [d for d in result.details if d.get("unexpected")]
    assert unexpected and unexpected[0]["trigger"] == "t1-retry"


# --------------------------------------------------------------------------- #
# tolerated surfaces: legal change is not divergence, but excess is
# --------------------------------------------------------------------------- #

def test_a_rephrased_analysis_is_within_the_declared_allowance():
    """The requirement's core: LLM and role restructuring may legitimately differ."""
    # 30 points, 26 carried over and 4 re-expressed. Symmetric difference is
    # 8, union is 34, so the distance is 8/34 ~= 0.235 — inside the declared 0.34.
    # The arithmetic is spelled out so the test cannot drift into passing for the
    # wrong reason if the allowance is ever retuned.
    left = {f"point-{i}" for i in range(30)}
    right = {f"point-{i}" for i in range(26)} | {f"reworded-{i}" for i in range(4)}

    result = sc.compare_analyst_output(left, right)
    assert result.verdict == sc.MATCHED
    assert result.observed_divergence == pytest.approx(8 / 34, abs=0.01)
    assert result.observed_divergence <= result.allowance


def test_a_changed_analysis_beyond_the_allowance_is_diverged_and_not_default_accepted():
    """Task 3.3's fourth named case.

    Legal change is not a free pass: beyond the declared allowance the surface is
    `diverged` and acceptance has to come from the declared authority (task 3.4).
    """
    left = {f"point-{i}" for i in range(20)}
    right = {f"different-{i}" for i in range(20)}

    result = sc.compare_analyst_output(left, right)
    assert result.verdict == sc.DIVERGED
    assert result.observed_divergence == pytest.approx(1.0)
    assert result.observed_divergence > result.allowance
    assert "not accepted by default" in result.reason


def test_the_allowance_is_a_declared_number_not_an_implicit_zero():
    """An allowance of zero would make this a rephrasing detector."""
    tolerance = sc.DEFAULT_TOLERANCES[sc.ANALYST_OUTPUT]
    assert tolerance.allowance > 0
    assert tolerance.note


def test_an_unknown_tolerance_metric_is_rejected_rather_than_defaulted():
    """A typo'd metric must not silently compare as zero divergence."""
    tolerance = sc.Tolerance(metric="jacard_distance", allowance=0.5)
    with pytest.raises(ValueError, match="unknown tolerance metric"):
        tolerance.evaluate({"a"}, {"b"})


def test_a_missing_analyst_output_is_not_compared():
    result = sc.compare_analyst_output(None, {"a"})
    assert result.verdict == sc.NOT_COMPARED


# --------------------------------------------------------------------------- #
# evidence usability
# --------------------------------------------------------------------------- #

def test_a_required_surface_that_was_not_compared_fails_evidence():
    """`not-compared` on a required surface is not agreement."""
    result = sc.compare_all(run_id="r1", left={}, right={},
                            expected_triggers={"t1"})
    ok, problems = result.usable_as_evidence(required=[sc.RISK_VERDICT])

    assert ok is False
    assert any(sc.RISK_VERDICT in problem for problem in problems)


def test_a_fully_compared_batch_passes_the_evidence_check():
    sides = {
        # `compare_all` reads the input packet from the `_packet` key: it is the
        # thing that must match before any other surface means anything.
        "_packet": {"doc: 1"},
        sc.INPUT_SNAPSHOT: {"doc: 1"}, sc.SCHEDULE_OMISSION: ["t1"],
        sc.ANALYST_OUTPUT: {"a"}, sc.RISK_VERDICT: {"verdict": "pass"},
        sc.APPROVAL_CHAIN: {"verdict": "approved", "round_no": 1},
        sc.TRADE_ATTRIBUTION: {"o1": "chained"},
    }
    result = sc.compare_all(run_id="r1", left=sides, right=dict(sides),
                            expected_triggers=["t1"])

    assert result.usable_as_evidence(required=sc.SIX_SURFACES) == (True, [])
    assert result.diverged == []


def test_only_four_surfaces_tolerate_change():
    """Guards against widening the tolerance to make a divergence disappear.

    A risk verdict, an approval and an order's attribution are facts; treating
    them as rephrasable would be a licence to trade on an unenforced rule.
    """
    assert sc.EXACT_SURFACES == {
        sc.INPUT_SNAPSHOT, sc.RISK_VERDICT, sc.APPROVAL_CHAIN, sc.TRADE_ATTRIBUTION}
    assert sc.TOLERATED_SURFACES == {sc.SCHEDULE_OMISSION, sc.ANALYST_OUTPUT}
    assert sc.EXACT_SURFACES & sc.TOLERATED_SURFACES == frozenset()


def test_every_tolerated_surface_has_a_named_acceptance_authority():
    """Task 3.4's prerequisite: someone must be able to accept a difference."""
    for surface in sc.TOLERATED_SURFACES:
        assert sc.DEFAULT_ACCEPTANCE_AUTHORITY.get(surface)
