"""Append-only shadow reports, dispositions and citation checks (3.4–3.6).

Three tasks in one file because they are one chain: a comparison produces
divergences, each divergence is accepted or fixed, and only a report with nothing
outstanding may be cited. The append-only property is what makes the chain
auditable — a rejection must not erase the conclusion it rejected, and a
re-comparison must not erase the `not-compared` it filled in.
"""

import sqlite3

import pytest

from ats.workflow import shadow_compare as sc
from ats.workflow import shadow_reports as sr


@pytest.fixture
def store(tmp_path):
    return tmp_path / "reports.sqlite"


def _clean_comparison() -> sc.ComparisonResult:
    sides = {
        "_packet": {"doc: 1"},
        sc.SCHEDULE_OMISSION: ["t1"],
        sc.ANALYST_OUTPUT: {"a", "b"},
        sc.RISK_VERDICT: {"verdict": "pass"},
        sc.APPROVAL_CHAIN: {"verdict": "approved", "round_no": 1},
        sc.TRADE_ATTRIBUTION: {"o1": "chained"},
    }
    return sc.compare_all(run_id="r1", left=sides, right=dict(sides),
                          expected_triggers=["t1"])


def _diverged_comparison() -> sc.ComparisonResult:
    """A risk divergence: compared exactly, so no acceptance path exists."""
    left = {"_packet": {"doc": 1}, sc.RISK_VERDICT: {"verdict": "pass"},
            sc.SCHEDULE_OMISSION: ["t1"]}
    right = {"_packet": {"doc": 1}, sc.RISK_VERDICT: {"verdict": "reject"},
             sc.SCHEDULE_OMISSION: ["t1"]}
    return sc.compare_all(run_id="r1", left=left, right=right,
                          expected_triggers={"t1"})


def _analyst_divergence() -> sc.ComparisonResult:
    left = {"_packet": {"doc": 1}, sc.SCHEDULE_OMISSION: ["t1"],
            sc.ANALYST_OUTPUT: {f"p-{i}" for i in range(20)}}
    right = {"_packet": {"doc": 1}, sc.SCHEDULE_OMISSION: ["t1"],
             sc.ANALYST_OUTPUT: {f"other-{i}" for i in range(20)}}
    return sc.compare_all(run_id="r1", left=left, right=right,
                          expected_triggers={"t1"})


def _record(store, result, *, report_id="rep-1", scope=None):
    return sr.record_comparison(
        report_id=report_id, run_id="r1", consumer_id="trader",
        batch_class="trading", scope=scope if scope is not None else {"p": ["x"]},
        packet_hash="p1", result=result, actor="ops", path=store)


# --------------------------------------------------------------------------- #
# 3.4 — an unaccepted difference blocks the report
# --------------------------------------------------------------------------- #

def test_an_unaccepted_risk_divergence_makes_the_report_uncitable(store):
    """The requirement's first scenario: unaccepted risk differences block.

    A risk divergence has no acceptance path at all (an exact surface), so the
    only way forward is to fix it and re-run. That leaves the report permanently
    uncitable until a clean comparison replaces it — which is the intent.
    """
    _record(store, _diverged_comparison())
    # A sign-off is impossible while it stands, so the citation check is reached
    # without one and must still report the unaccepted difference.
    with pytest.raises(sr.ShadowReportError):
        sr.record_signoff(report_id="rep-1", actor="owner", reason="reviewed",
                          path=store)

    ok, problems = sr.check_citable(
        report_id="rep-1", scope={"p": ["x"]}, path=store)
    assert ok is False
    assert any(sc.RISK_VERDICT in problem for problem in problems)


def test_a_signoff_is_refused_while_a_divergence_is_unaccepted(store):
    """Refused at the signature, not merely flagged at citation.

    Allowing the sign-off and pushing the check downstream would make the
    signature meaningless and put the operator in a worse position: they would
    have asserted the report is usable at a moment when nothing said it was.
    """
    _record(store, _diverged_comparison())

    with pytest.raises(sr.ShadowReportError, match="unaccepted"):
        sr.record_signoff(report_id="rep-1", actor="owner", reason="reviewed",
                          path=store)


def test_an_exact_surface_cannot_be_accepted_at_all(store):
    """A risk verdict is a fact; tolerance would be a licence to skip the rule.

    So there is no authority who may accept it — the only dispositions are
    "fix it and re-run" or "do not cut over".
    """
    _record(store, _diverged_comparison())

    with pytest.raises(sr.ShadowReportError, match="no acceptance authority"):
        sr.accept_divergence(report_id="rep-1", surface=sc.RISK_VERDICT,
                             actor="owner", authority="chief_owner",
                             reason="we accept it", path=store)


def test_a_tolerated_divergence_can_be_accepted_by_the_declared_authority(store):
    _record(store, _analyst_divergence())
    state = sr.read_state("rep-1", path=store)
    assert sc.ANALYST_OUTPUT in state.unaccepted

    sr.accept_divergence(report_id="rep-1", surface=sc.ANALYST_OUTPUT,
                         actor="owner", authority="chief_owner",
                         reason="role rewrite, checked against the new contract",
                         observed_divergence=1.0, allowance=0.34, path=store)

    assert sc.ANALYST_OUTPUT not in sr.read_state("rep-1", path=store).unaccepted


def test_the_wrong_authority_cannot_accept(store):
    """Otherwise the declared tolerance is a suggestion rather than a control."""
    _record(store, _analyst_divergence())

    with pytest.raises(sr.ShadowReportError, match="requires acceptance by"):
        sr.accept_divergence(report_id="rep-1", surface=sc.ANALYST_OUTPUT,
                             actor="anyone", authority="intern",
                             reason="looks fine", path=store)


def test_an_acceptance_without_a_reason_is_refused(store):
    """An unexplained acceptance is indistinguishable from a rubber stamp."""
    _record(store, _analyst_divergence())
    with pytest.raises(sr.ShadowReportError, match="must state a reason"):
        sr.accept_divergence(report_id="rep-1", surface=sc.ANALYST_OUTPUT,
                             actor="owner", authority="chief_owner",
                             reason="  ", path=store)


def test_an_acceptance_is_auditable_with_its_actor_authority_and_reason(store):
    _record(store, _analyst_divergence())
    sr.accept_divergence(report_id="rep-1", surface=sc.ANALYST_OUTPUT,
                         actor="owner", authority="chief_owner",
                         reason="verified against the restructured role contract",
                         path=store)

    rows = sr.acceptances("rep-1", path=store)
    assert len(rows) == 1
    assert rows[0]["accepted_by"] == "owner"
    assert rows[0]["authority"] == "chief_owner"
    assert "restructured role" in rows[0]["reason"]
    assert rows[0]["accepted_at"]


def test_accepting_a_surface_that_did_not_diverge_is_refused(store):
    _record(store, _clean_comparison())
    with pytest.raises(sr.ShadowReportError, match="not an unaccepted divergence"):
        sr.accept_divergence(report_id="rep-1", surface=sc.ANALYST_OUTPUT,
                             actor="owner", authority="chief_owner",
                             reason="just in case", path=store)


def test_a_revoked_acceptance_puts_the_difference_back(store):
    """Otherwise accepting and un-accepting would be a way to launder a divergence."""
    _record(store, _analyst_divergence())
    sr.accept_divergence(report_id="rep-1", surface=sc.ANALYST_OUTPUT,
                         actor="owner", authority="chief_owner",
                         reason="initial review", path=store)
    assert sr.read_state("rep-1", path=store).unaccepted == ()

    sr.revoke_acceptance(report_id="rep-1", surface=sc.ANALYST_OUTPUT,
                         actor="owner", reason="re-checked; the difference is real",
                         path=store)

    assert sc.ANALYST_OUTPUT in sr.read_state("rep-1", path=store).unaccepted
    ok, problems = sr.check_citable(report_id="rep-1", scope={"p": ["x"]},
                                    path=store)
    assert ok is False


def test_revoking_an_acceptance_keeps_the_acceptance_row(store):
    """Revocation supersedes; it does not erase. Both are in the record."""
    _record(store, _analyst_divergence())
    sr.accept_divergence(report_id="rep-1", surface=sc.ANALYST_OUTPUT,
                         actor="owner", authority="chief_owner",
                         reason="initial", path=store)
    sr.revoke_acceptance(report_id="rep-1", surface=sc.ANALYST_OUTPUT,
                         actor="owner", reason="withdrawn", path=store)

    assert len(sr.acceptances("rep-1", path=store)) == 1
    revocations = [e for e in sr.events("rep-1", path=store)
                   if e["event_type"] == "acceptance_revoked"]
    assert len(revocations) == 1


def test_revoking_without_an_active_acceptance_is_refused(store):
    _record(store, _analyst_divergence())
    with pytest.raises(sr.ShadowReportError, match="no active acceptance"):
        sr.revoke_acceptance(report_id="rep-1", surface=sc.ANALYST_OUTPUT,
                             actor="owner", reason="nothing to revoke", path=store)


# --------------------------------------------------------------------------- #
# 3.5 — append-only
# --------------------------------------------------------------------------- #

def test_the_event_log_rejects_updates(store):
    """Enforced by the database, not by convention."""
    _record(store, _clean_comparison())
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(store) as conn:
            conn.execute("UPDATE shadow_report_events SET actor='someone else'")


def test_the_event_log_rejects_deletes(store):
    _record(store, _clean_comparison())
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(store) as conn:
            conn.execute("DELETE FROM shadow_report_events")


def test_a_rejection_does_not_rewrite_the_conclusion(store):
    """The requirement's case, and the reason the store is append-only.

    Rewriting the conclusion would leave no record that the report was ever
    signed off — so "was this ever accepted?" becomes unanswerable.
    """
    _record(store, _analyst_divergence())
    sr.accept_divergence(report_id="rep-1", surface=sc.ANALYST_OUTPUT,
                         actor="owner", authority="chief_owner",
                         reason="reviewed", path=store)
    sr.record_signoff(report_id="rep-1", actor="owner", reason="looks right",
                      path=store)
    sr.record_rejection(report_id="rep-1", actor="reviewer",
                        reason="the accepted difference is too broad", path=store)

    state = sr.read_state("rep-1", path=store)
    assert state.status == sr.REJECTED
    # The comparison and its acceptance are both still there.
    assert state.unaccepted == ()
    types = [e["event_type"] for e in sr.events("rep-1", path=store)]
    assert types == ["comparison", "acceptance", "signoff", "rejection"]


def test_a_report_completed_by_a_second_pass_keeps_the_earlier_not_compared(store):
    """The requirement's case: filling in a surface must not erase the gap record."""
    first = sc.compare_all(run_id="r1", left={}, right={}, expected_triggers={"t1"})
    assert sc.RISK_VERDICT in first.not_compared
    _record(store, first)
    assert "not-compared" not in sr.read_state("rep-1", path=store).status

    sr.record_recomparison(
        report_id="rep-1", run_id="r1", consumer_id="trader",
        batch_class="trading", scope={"p": ["x"]}, packet_hash="p1",
        result=_clean_comparison(), actor="ops",
        reason="risk data arrived on the second pass", path=store)

    state = sr.read_state("rep-1", path=store)
    assert state.not_compared == ()

    history = sr.events("rep-1", path=store)
    assert [e["event_type"] for e in history] == ["comparison", "recomparison"]
    # The first pass's not-compared is still in the log, which is the point.
    assert sc.RISK_VERDICT in sc.compare_all(
        run_id="r1", left={}, right={}, expected_triggers={"t1"}).not_compared


def test_a_revocation_after_signoff_blocks_citation(store):
    """A report that once passed and was withdrawn does not pass because it passed."""
    _record(store, _clean_comparison())
    sr.record_signoff(report_id="rep-1", actor="owner", reason="ok", path=store)
    assert sr.check_citable(report_id="rep-1", scope={"p": ["x"]},
                            path=store)[0] is True

    sr.record_revocation(report_id="rep-1", actor="owner",
                         reason="a scheduler omission was found later",
                         path=store)

    ok, problems = sr.check_citable(report_id="rep-1", scope={"p": ["x"]},
                                    path=store)
    assert ok is False
    assert any("revoked" in problem for problem in problems)


def test_a_signoff_after_a_revocation_restores_the_report(store):
    """Derived state, so the newest sign-off governs — and the log shows both."""
    _record(store, _clean_comparison())
    sr.record_signoff(report_id="rep-1", actor="owner", reason="first", path=store)
    sr.record_revocation(report_id="rep-1", actor="owner", reason="withdrawn",
                         path=store)
    sr.record_signoff(report_id="rep-1", actor="owner",
                      reason="re-checked and re-confirmed", path=store)

    assert sr.check_citable(report_id="rep-1", scope={"p": ["x"]},
                            path=store)[0] is True
    assert len(sr.events("rep-1", path=store)) == 4


def test_a_rejection_without_a_reason_is_refused(store):
    _record(store, _clean_comparison())
    with pytest.raises(sr.ShadowReportError, match="must state a reason"):
        sr.record_rejection(report_id="rep-1", actor="reviewer", reason="",
                            path=store)


def test_an_unknown_report_is_an_error_not_an_empty_pass(store):
    """A typo'd report id must not read as "no problems found"."""
    with pytest.raises(sr.ShadowReportError, match="no shadow report"):
        sr.check_citable(report_id="typo", scope={}, path=store)


# --------------------------------------------------------------------------- #
# 3.6 — the four citation checks
# --------------------------------------------------------------------------- #

def test_a_signed_off_clean_report_is_citable(store):
    _record(store, _clean_comparison())
    sr.record_signoff(report_id="rep-1", actor="owner", reason="clean", path=store)

    ok, problems = sr.check_citable(
        report_id="rep-1", scope={"p": ["x"]},
        required_surfaces=sc.SIX_SURFACES, path=store)
    assert (ok, problems) == (True, [])


def test_a_never_signed_off_report_is_not_citable(store):
    _record(store, _clean_comparison())
    ok, problems = sr.check_citable(report_id="rep-1", scope={"p": ["x"]},
                                    path=store)
    assert ok is False
    assert any("never signed off" in problem for problem in problems)


def test_a_required_surface_that_is_not_compared_blocks_citation(store):
    """Not-compared is not agreement — the check the report most needs."""
    incomplete = sc.compare_all(run_id="r1", left={"_packet": {"d": 1}},
                                right={"_packet": {"d": 1}},
                                expected_triggers={"t1"})
    assert sc.RISK_VERDICT in incomplete.not_compared
    _record(store, incomplete)
    sr.record_signoff(report_id="rep-1", actor="owner", reason="partial", path=store)

    ok, problems = sr.check_citable(
        report_id="rep-1", scope={"p": ["x"]},
        required_surfaces=[sc.RISK_VERDICT], path=store)
    assert ok is False
    assert any(sc.RISK_VERDICT in problem for problem in problems)


def test_a_scope_mismatch_blocks_citation(store):
    """The report speaks for the scope it compared, not for any scope."""
    _record(store, _clean_comparison(), scope={"p": ["x"]})
    sr.record_signoff(report_id="rep-1", actor="owner", reason="clean", path=store)

    ok, problems = sr.check_citable(report_id="rep-1", scope={"p": ["y"]},
                                    path=store)
    assert ok is False
    assert any("scope mismatch" in problem for problem in problems)


def test_a_report_that_predates_a_code_change_is_not_citable(store):
    """The report compared code that no longer exists.

    This is the check that makes "re-run after a change" enforceable rather than
    advisory, and it is why the fingerprint is stored with the comparison.
    """
    _record(store, _clean_comparison())
    sr.record_signoff(report_id="rep-1", actor="owner", reason="clean", path=store)

    ok, problems = sr.check_citable(
        report_id="rep-1", scope={"p": ["x"]},
        code_hash="fingerprint-of-yesterday", path=store)
    assert ok is False
    assert any("predates a change" in problem for problem in problems)


def test_all_four_problems_are_reported_at_once(store):
    """An operator fixing a citation needs the whole list, not a scavenger hunt."""
    incomplete = sc.compare_all(run_id="r1", left={}, right={},
                                expected_triggers=None)
    _record(store, incomplete, scope={"p": ["x"]})

    ok, problems = sr.check_citable(
        report_id="rep-1", scope={"p": ["z"]},
        required_surfaces=[sc.RISK_VERDICT], code_hash="other", path=store)
    assert ok is False
    assert any("never signed off" in p for p in problems)
    assert any(sc.RISK_VERDICT in p for p in problems)
    assert any("scope mismatch" in p for p in problems)
    assert any("predates a change" in p for p in problems)


def test_assert_citable_raises_with_every_reason_listed(store):
    _record(store, _clean_comparison(), scope={"p": ["x"]})
    with pytest.raises(sr.ShadowReportError) as excinfo:
        sr.assert_citable(report_id="rep-1", scope={"p": ["y"]}, path=store)
    assert "never signed off" in str(excinfo.value)
    assert "scope mismatch" in str(excinfo.value)


def test_the_scope_fingerprint_is_order_insensitive(store):
    """A scope rebuilt by a different code path must still match."""
    assert sr.scope_fingerprint({"a": 1, "b": 2}) == \
        sr.scope_fingerprint({"b": 2, "a": 1})
    assert sr.scope_fingerprint({"a": 1}) != sr.scope_fingerprint({"a": 2})
