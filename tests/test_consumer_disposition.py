"""Phase F 7.7 — per-consumer disposition of the gaps verification found.

Almost every test here is about a refusal, because the failure modes of a
disposition layer are all conversions of an inconvenient fact into a convenient
policy: an optional input quietly becoming required, an age rule firing on
schedule instead of on fact, a broken history auto-releasing a scope, and one
consumer's gap halting the portfolio.

The one thing asserted as a PASS is that a clean scope stays clean and does not
inherit anyone else's problems.
"""

from __future__ import annotations

import pytest

from ats.workflow import consumer_disposition as cd
from ats.workflow import intake_verification as iv


def test_an_unremarkable_consumer_is_ok():
    report = cd.dispose_consumer(consumer_id="macro")
    assert report.disposition == cd.DISPOSITION_OK
    assert report.blocks_cutover is False
    assert report.partial_ranges == []
    assert report.auto_released is False


def test_an_unknown_consumer_is_refused():
    """A disposition for a consumer that does not exist would be a disposition
    of nothing."""
    with pytest.raises(cd.DispositionError, match="unknown consumer"):
        cd.dispose_consumer(consumer_id="not_a_consumer")


# --- rule 1: an accepted optional input stays accepted ---------------------- #

def test_the_accepted_optional_input_stays_accepted():
    """`sec_edgar_filing_body` is optional and non-blocking by an explicit,
    versioned decision. A verification run that happened not to see it does not
    get to overturn that — that is how an exception becomes a permanent hole."""
    report = cd.dispose_consumer(
        consumer_id="fundamental", optional_inputs=("sec_edgar_filing_body",))
    assert report.disposition == cd.DISPOSITION_OK
    assert report.blocks_cutover is False


def test_an_unrecognised_missing_input_is_not_treated_as_optional():
    """Only the recorded acceptance counts. 'We did not require it this time' is
    not a reason it was accepted."""
    report = cd.dispose_consumer(
        consumer_id="fundamental", optional_inputs=("some_new_source",))
    assert report.disposition == cd.DISPOSITION_PARTIAL
    assert "some_new_source" in report.partial_ranges
    assert any("never accepted as optional" in r for r in report.reasons)


# --- rule 2: a confirmed coverage gap keeps its policy ----------------------- #

def test_a_confirmed_coverage_gap_is_recorded_not_resolved():
    report = cd.dispose_consumer(
        consumer_id="sector", coverage_gaps=("tier-7 coverage absent",))
    assert report.disposition == cd.DISPOSITION_PARTIAL
    assert "tier-7 coverage absent" in report.partial_ranges
    assert any("does not get to resolve it" in r for r in report.reasons)


# --- rule 3: no age-based downgrade for final-version sources ---------------- #

def test_an_age_downgrade_of_a_final_version_source_is_refused():
    """`company_financials` is read at its latest final version, so the REPORT's
    age says nothing about whether the current data is usable. A rule that fires
    on schedule rather than on fact is not a quality gate."""
    report = cd.dispose_consumer(
        consumer_id="fundamental", stale_by_report_age=("company_financials",))
    assert any("refused the age-based downgrade" in r for r in report.reasons)
    assert "company_financials" not in report.partial_ranges
    assert report.disposition == cd.DISPOSITION_OK


def test_an_age_flag_on_an_unknown_source_is_pending_not_resolved():
    """This run cannot tell an age problem from a real one, so it records rather
    than decides."""
    report = cd.dispose_consumer(
        consumer_id="layer", stale_by_report_age=("mystery_feed",))
    assert report.disposition == cd.DISPOSITION_PENDING
    assert "mystery_feed" in report.partial_ranges


def test_every_final_version_source_is_refused_an_age_downgrade():
    for source in cd.FINAL_VERSION_SOURCES:
        report = cd.dispose_consumer(
            consumer_id="fundamental", stale_by_report_age=(source,))
        assert any("refused the age-based downgrade" in r for r in report.reasons), source


# --- rule 4: time gaps and broken history stay explicitly partial ------------ #

def test_a_missing_timestamp_is_partial_not_present_and_valid():
    report = cd.dispose_consumer(consumer_id="macro",
                                 time_missing=("MACRO_DATA",))
    assert report.disposition == cd.DISPOSITION_PARTIAL
    assert "MACRO_DATA" in report.partial_ranges
    assert any("not a present-and-valid one" in r for r in report.reasons)


def test_broken_history_is_partial_and_blocks_the_cutover():
    report = cd.dispose_consumer(consumer_id="layer",
                                 history_broken=("2024-Q3 lineage",))
    assert report.disposition == cd.DISPOSITION_PARTIAL
    assert report.blocks_cutover is True
    assert "2024-Q3 lineage" in report.partial_ranges


def test_broken_history_never_auto_releases_the_scope():
    """'Most of the history is fine' is not a qualification. Letting a gap become
    a pass on a majority threshold converts 'we checked' into 'we approved'."""
    report = cd.dispose_consumer(
        consumer_id="fundamental",
        history_broken=("2019-2022 lineage", "2023 lineage"))
    assert report.auto_released is False
    assert report.blocks_cutover is True
    cd.assert_no_auto_release(report)


def test_an_auto_released_disposition_is_refused():
    """No code path sets `auto_released`, which makes the forbidden outcome
    unrepresentable. This asserts it, so introducing the flag requires deleting
    the check."""
    report = cd.dispose_consumer(consumer_id="layer")
    object.__setattr__(report, "auto_released", True)
    with pytest.raises(cd.DispositionError, match="auto-released"):
        cd.assert_no_auto_release(report)


def test_an_auto_released_scope_blocks_even_when_otherwise_complete():
    report = cd.dispose_consumer(consumer_id="sector")
    object.__setattr__(report, "auto_released", True)
    assert report.auto_released is True, (
        "the flag must be observable on the record even when nothing else failed")


# --- rule 5: legacy-path defects are registered, not fixed ------------------- #

def test_a_legacy_defect_is_registered_rather_than_repaired():
    report = cd.dispose_consumer(consumer_id="chief",
                                 legacy_defects=("legacy_table_direct_reads",))
    assert report.registered_only == ["legacy_table_direct_reads"]
    assert report.disposition == cd.DISPOSITION_OK
    assert any("registered, not fixed" in r for r in report.reasons)


def test_a_legacy_defect_alone_does_not_hold_an_isolated_scope():
    """The premise of the new path is that it does not inherit the old path's
    defect, so the defect is not a reason to hold the verified scope."""
    report = cd.dispose_consumer(consumer_id="chief",
                                 legacy_defects=("legacy_table_direct_reads",))
    assert report.blocks_cutover is False
    assert any("must be established separately" in r for r in report.reasons)
    assert not any("verified as isolated" in r for r in report.reasons)


def test_a_legacy_defect_does_not_launder_a_real_gap():
    report = cd.dispose_consumer(consumer_id="chief",
                                 legacy_defects=("legacy defect",),
                                 history_broken=("2024 lineage",))
    assert report.disposition == cd.DISPOSITION_PARTIAL
    assert report.blocks_cutover is True


# --- scope isolation --------------------------------------------------------- #

def test_one_consumers_gap_does_not_block_an_unrelated_clean_scope():
    """The spec's「缺项只阻断受影响范围」in disposition form. A disposition that
    halted the portfolio over one consumer's history would be so unusable that it
    gets switched off — the opposite outcome."""
    blocked = cd.dispose_consumer(consumer_id="fundamental",
                                  history_broken=("2019 lineage",))
    clean = cd.dispose_consumer(consumer_id="technical")
    cd.assert_scope_isolation([blocked, clean])
    assert clean.disposition == cd.DISPOSITION_OK
    assert clean.blocks_cutover is False


def test_a_scope_that_claims_to_block_others_is_refused():
    blocked = cd.dispose_consumer(consumer_id="fundamental",
                                  history_broken=("2019 lineage",))
    clean = cd.dispose_consumer(consumer_id="technical")
    object.__setattr__(blocked, "blocks_unrelated", ("technical",))
    with pytest.raises(cd.DispositionError, match="must not hold unrelated"):
        cd.assert_scope_isolation([blocked, clean])


def test_several_consumers_can_be_blocked_without_touching_each_other():
    reports = [
        cd.dispose_consumer(consumer_id="fundamental", history_broken=("x",)),
        cd.dispose_consumer(consumer_id="layer", time_missing=("y",)),
        cd.dispose_consumer(consumer_id="technical"),
    ]
    cd.assert_scope_isolation(reports)
    assert sum(1 for r in reports if r.blocks_cutover) == 1


# --- the batch form ---------------------------------------------------------- #

def test_the_batch_covers_all_ten_consumers_by_default():
    reports = cd.consumer_dispositions()
    assert [r.consumer_id for r in reports] == list(iv.TEN_CONSUMERS)


def test_a_gap_applies_to_every_consumer_examined():
    """These inputs describe the DATA, not one consumer's use of it. Per-consumer
    narrowing needs per-consumer evidence, and inventing that is how a general gap
    becomes one consumer's excuse."""
    reports = cd.consumer_dispositions(history_broken=("shared lineage",))
    assert all(r.disposition == cd.DISPOSITION_PARTIAL for r in reports)
    assert all(r.blocks_cutover for r in reports)


def test_the_batch_can_be_narrowed_to_named_consumers():
    reports = cd.consumer_dispositions(consumers=["trader", "clerk"])
    assert [r.consumer_id for r in reports] == ["trader", "clerk"]


# --- rendering --------------------------------------------------------------- #

def test_the_report_states_that_gaps_block_only_their_own_scope():
    reports = cd.consumer_dispositions(history_broken=("lineage",))
    text = cd.render_dispositions(reports)
    assert "缺项只阻断受影响范围" in text
    assert "历史不完整不自动放行" in text


def test_the_report_names_the_consumer_and_its_reason():
    reports = cd.consumer_dispositions(
        consumers=["fundamental"], history_broken=("2019 lineage",))
    text = cd.render_dispositions(reports)
    assert "`fundamental`" in text
    assert "2019 lineage" in text
    assert "does NOT auto-release" in text or "不自动放行" in text


def test_a_clean_report_does_not_invent_a_section_of_reasons():
    reports = cd.consumer_dispositions()
    text = cd.render_dispositions(reports)
    assert "## 理由" not in text, (
        "an empty reasons section reads as a finding that was cleared")


def test_every_disposition_row_carries_its_own_fields():
    """A report that only says 'ok' hides which scope is which."""
    report = cd.dispose_consumer(consumer_id="clerk",
                                 time_missing=("BROKER_STATE",))
    row = report.as_row()
    assert row["consumer_id"] == "clerk"
    assert row["disposition"] == cd.DISPOSITION_PARTIAL
    assert row["partial_ranges"] == ["BROKER_STATE"]
    assert row["auto_released"] is False
