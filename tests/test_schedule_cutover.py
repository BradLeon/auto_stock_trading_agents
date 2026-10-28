"""Schedule-path cutover and rollback (tasks 10.1–10.3).

The safety property under test is **exclusivity over time**, not a static
verdict. Two dispatchers firing one logical trigger means the work runs twice —
twice the ledger writes, a reconciliation that disagrees with itself — so the
module's job is to refuse when ownership is not provably sole, and to name what
it could not determine rather than resolve it by assumption.

Two distinctions are pinned because collapsing either is a silent failure:

- **conflict vs unknown.** A conflict means two live holders: stop one. Unknown
  means nobody recorded it: a human has to find out whether it ran. Treating
  unknown as finished strands the trigger; treating it as unfinished runs it
  twice.
- **"no execution record" vs "did not execute".** A missing record is not
  evidence of absence. `plan_rollback` reports such triggers as `unconfirmed` and
  refuses the rollback, because a rollback that re-runs everything is worse than
  no rollback at all.
"""

from datetime import datetime, timedelta, timezone

import pytest

from ats.workflow import schedule_cutover as sc

NOW = datetime.now(timezone.utc)
RECENT = (NOW - timedelta(minutes=5)).isoformat()
OLDER = (NOW - timedelta(hours=2)).isoformat()
FUTURE = (NOW + timedelta(days=30)).isoformat()

T_A = "wf:daily-cascade:2026-10-07"
T_B = "wf:factset-ingest:2026-10-07"  # two legacy jobs share this schedule


class _Auth:
    """Minimal stand-in — the authorisation type is read_cutover's."""

    def __init__(self, complete: bool = True):
        self.is_complete = complete


def _holders(mapping):
    """Records per trigger. A missing key raises `KeyError`, which the module
    reads as "nobody holds it" — the same thing a lookup table does."""
    def reader(trigger):
        if trigger not in mapping:
            raise KeyError(trigger)
        return list(mapping[trigger])
    return reader


# --- 10.2 sole ownership -----------------------------------------------------

def test_a_single_holder_is_sole_ownership():
    report = sc.assert_sole_ownership(
        [T_A], holders_reader=_holders({T_A: [{"owner": "legacy",
                                               "claimed_at": RECENT}]}))
    assert report.clean is True
    assert report.checked[0].verdict == sc.SOLE


def test_two_holders_of_one_trigger_is_a_conflict():
    """The case the whole module exists for."""
    report = sc.assert_sole_ownership([T_B], holders_reader=_holders({
        T_B: [{"owner": "legacy", "claimed_at": RECENT},
              {"owner": "phase-e", "claimed_at": RECENT}]}))

    assert report.clean is False
    conflict = report.conflicts[0]
    assert conflict.trigger == T_B
    assert len(conflict.holders) == 2


def test_a_trigger_the_ledger_never_heard_of_is_not_verified():
    """The reading this module exists to prevent.

    An empty ledger with a non-empty expected set is not "no conflicts" — it is
    "nothing was examined". Treating a missing key as "unclaimed, therefore
    fine" turns an untouched ledger into a clean report, which is exactly how a
    cutover proceeds on evidence nobody gathered.
    """
    report = sc.assert_sole_ownership([T_A, T_B], holders_reader=_holders({}))
    assert report.clean is False
    assert sorted(report.missing_from_ledger) == [T_A, T_B]
    assert report.checked == []


def test_the_empty_ledger_summary_says_no_verification_not_no_conflict():
    """"0 checked, clean" reads as an all-clear. It must not."""
    text = sc.assert_sole_ownership([T_A], holders_reader=_holders({})).summary()
    assert "无从核验" in text
    assert "不是「无冲突」" in text
    assert "不构成任何保证" in text


def test_a_partially_known_set_blocks_on_the_missing_half():
    def reader(trigger):
        if trigger == T_A:
            return [{"owner": "legacy", "claimed_at": RECENT, "status": "claimed"}]
        raise KeyError(trigger)

    report = sc.assert_sole_ownership([T_A, T_B], holders_reader=reader)
    assert report.clean is False
    assert report.missing_from_ledger == [T_B]
    assert report.checked[0].verdict == sc.SOLE


def test_a_conflict_names_both_holders_and_when_they_claimed():
    """10.2 requires reporting "双方与时间".

    A conflict report that says only "there is a conflict" leaves the operator
    with nothing to act on — and the two holders are exactly what identifies
    which path to stop.
    """
    report = sc.assert_sole_ownership([T_B], holders_reader=_holders({
        T_B: [{"owner": "legacy", "claimed_at": "2026-10-07T00:00:00+00:00"},
              {"owner": "phase-e", "claimed_at": "2026-10-07T00:01:00+00:00"}]}))

    detail = report.conflicts[0].detail
    assert "legacy" in detail and "phase-e" in detail
    assert "00:00:00" in detail and "00:01:00" in detail


def test_a_published_claim_does_not_count_as_a_live_holder():
    """Ownership is a property of time.

    A claim that ended and stayed in the ledger must not read as a conflict, or
    the check becomes unusable after the first rollback — which is precisely when
    it is needed most. Field names follow group 4's real schema (`status` /
    `finished_at`), which is what this module reads.
    """
    report = sc.assert_sole_ownership([T_A], holders_reader=_holders({
        T_A: [{"owner": "legacy", "claimed_at": OLDER,
               "status": "published", "finished_at": RECENT},
              {"owner": "phase-e", "claimed_at": RECENT}]}))
    assert report.clean is True
    assert report.checked[0].verdict == sc.SOLE


def test_an_unreadable_release_time_is_treated_as_live():
    """An unreadable release time is not evidence that the claim ended.

    Assuming it did is how two owners end up running at once — so the safe
    reading is the blocking one.
    """
    report = sc.assert_sole_ownership([T_A], holders_reader=_holders({
        T_A: [{"owner": "legacy", "claimed_at": OLDER,
               "status": "published", "finished_at": "not-a-date"},
              {"owner": "phase-e", "claimed_at": RECENT}]}))
    assert report.clean is False
    assert report.conflicts


def test_an_unreadable_claim_log_is_unknown_not_free():
    """"I could not check" must not read as "no conflict"."""
    def exploding(trigger):
        raise RuntimeError("the claim table is locked")

    report = sc.assert_sole_ownership([T_A], holders_reader=exploding)
    assert report.clean is False
    assert report.unknowns[0].trigger == T_A
    assert "locked" in report.unknowns[0].detail


def test_a_known_but_unclaimed_trigger_is_sole():
    """Registered in the ledger, held by nobody — at most one owner, which holds.

    Distinct from the missing-key case: here the ledger knows the trigger and
    records no holder, which is a clean answer.
    """
    def reader(trigger):
        if trigger == T_A:
            return []  # known, unclaimed
        raise KeyError(trigger)

    report = sc.assert_sole_ownership([T_A], holders_reader=reader)
    assert report.clean is True
    assert report.checked[0].verdict == sc.SOLE
    assert report.missing_from_ledger == []


def test_conflict_and_unknown_are_reported_separately():
    """They need different responses: stop a path vs go and find out."""
    def reader(trigger):
        if trigger == T_A:
            raise RuntimeError("claim log unreadable for this one")
        return [{"owner": "a", "claimed_at": RECENT},
                {"owner": "b", "claimed_at": RECENT}]

    report = sc.assert_sole_ownership([T_A, T_B], holders_reader=reader)
    assert [v.trigger for v in report.conflicts] == [T_B]
    assert [v.trigger for v in report.unknowns] == [T_A]
    assert len(report.blocking) == 2


def test_the_summary_says_which_is_which():
    """An operator reading the summary must not have to know the vocabulary."""
    def reader(trigger):
        if trigger == T_A:
            raise RuntimeError("unreadable")
        return [{"owner": "a", "claimed_at": RECENT},
                {"owner": "b", "claimed_at": RECENT}]

    text = sc.assert_sole_ownership([T_A, T_B], holders_reader=reader).summary()
    assert "所有权冲突" in text
    assert "状态未知" in text
    assert "跑两次" in text or "漏跑" in text


# --- 10.3 rollback deduplication ---------------------------------------------

def test_an_executed_trigger_is_skipped_by_identity(tmp_path):
    db = str(tmp_path / "s.sqlite")
    sc.record_execution(T_A, execution_ref="exec-1", actor="test", path=db)

    plan = sc.plan_rollback([T_A, T_B], executed=sc.executed_triggers(db))
    skipped = [s["trigger"] for s in plan["skipped_as_already_executed"]]
    assert skipped == [T_A]
    assert plan["unconfirmed"] == [T_B]


def test_dedup_never_approximates_by_a_time_window(tmp_path):
    """10.3's explicit prohibition.

    Skipping "anything in the last hour" would also skip triggers that were
    merely *scheduled* in that window but never ran — leaving a genuine gap
    unfilled, which surfaces much later as a missing report with no trace.
    """
    db = str(tmp_path / "s.sqlite")
    sc.record_execution(T_A, execution_ref="exec-1", path=db)

    # T_B was claimed around the same time but has no execution record — the
    # shape a time-window heuristic would wrongly treat as already done.
    plan = sc.plan_rollback([T_A, T_B], executed=sc.executed_triggers(db))

    assert [s["trigger"] for s in plan["skipped_as_already_executed"]] == [T_A]
    assert T_B in plan["unconfirmed"], (
        "a claimed-but-unrecorded trigger was treated as already executed")


def test_an_unrecorded_trigger_makes_dedup_unsound_and_blocks_the_rollback(tmp_path):
    """"Nothing to skip" is only good news when the record is complete."""
    db = str(tmp_path / "s.sqlite")
    sc.record_execution(T_A, execution_ref="exec-1", path=db)

    plan = sc.plan_rollback([T_A, T_B], executed=sc.executed_triggers(db))
    assert plan["dedup_sound"] is False
    assert plan["unconfirmed"] == [T_B]

    result = sc.perform_rollback(triggers=[T_A, T_B],
                                 executed=sc.executed_triggers(db),
                                 authorisation=_Auth(), apply=True, path=db)
    assert result["action"] == sc.REFUSED
    assert result["applied"] is False
    assert "run them again" in " ".join(result["problems"])


def test_a_rollback_with_a_complete_record_proceeds_and_records(tmp_path):
    db = str(tmp_path / "s.sqlite")
    for trigger in (T_A, T_B):
        sc.record_execution(trigger, execution_ref=f"exec-{trigger}", path=db)

    released: list[str] = []
    result = sc.perform_rollback(
        triggers=[T_A, T_B], executed=sc.executed_triggers(db),
        authorisation=_Auth(), apply=True, path=db,
        release_claims=released.append)

    assert result["action"] == sc.ROLLBACK
    assert result["applied"] is True
    assert sorted(released) == sorted([T_A, T_B])
    assert sc.rollback_history(path=db)


def test_a_rollback_records_the_skipped_set_even_when_it_only_decides(tmp_path):
    """"The skipped set is the reading an operator needs before applying."""
    db = str(tmp_path / "s.sqlite")
    sc.record_execution(T_A, execution_ref="exec-1", path=db)
    sc.record_execution(T_B, execution_ref="exec-2", path=db)

    result = sc.perform_rollback(triggers=[T_A, T_B],
                                 executed=sc.executed_triggers(db),
                                 authorisation=_Auth(), path=db)
    assert result["applied"] is False
    assert len(result["skipped"]) == 2
    assert sc.rollback_history(path=db) == [], (
        "a decide-only rollback must not be recorded as performed")


def test_a_rollback_without_an_authorisation_refuses(tmp_path):
    """A rollback changes who fires the schedule just as much as a cutover."""
    db = str(tmp_path / "s.sqlite")
    sc.record_execution(T_A, execution_ref="exec-1", path=db)
    result = sc.perform_rollback(triggers=[T_A], executed=sc.executed_triggers(db),
                                 authorisation=None, apply=True, path=db)
    assert result["action"] == sc.REFUSED
    assert result["applied"] is False


def test_a_half_released_schedule_is_reported_not_left_silently(tmp_path):
    """A schedule with no owner is the one state both dispatchers agree on."""
    db = str(tmp_path / "s.sqlite")
    for trigger in (T_A, T_B):
        sc.record_execution(trigger, execution_ref="exec", path=db)

    calls: list[str] = []

    def flaky(trigger):
        calls.append(trigger)
        if trigger == T_B:
            raise RuntimeError("the owner record is locked")

    result = sc.perform_rollback(triggers=[T_A, T_B],
                                 executed=sc.executed_triggers(db),
                                 authorisation=_Auth(), apply=True, path=db,
                                 release_claims=flaky)
    assert result["action"] == sc.REFUSED
    assert result["applied"] is False
    assert any("half-released" in p for p in result["problems"])


# --- 10.1/10.4 the cutover sequence -----------------------------------------

def test_a_cutover_without_an_authorisation_refuses_before_doing_anything():
    """Checked first, so an unauthorised run is not reported as a clean dry-run."""
    calls: list[str] = []
    result = sc.perform_cutover(
        triggers=[T_A], inventory=[{"trigger": T_A}], dispositions={},
        authorisation=None, freeze=lambda: calls.append("freeze"), apply=True)

    assert result["action"] == sc.REFUSED
    assert calls == [], "the freeze ran before the authorisation was checked"


def test_an_undeclared_disposition_stops_the_cutover():
    """An undeclared trigger is one nobody decided about."""
    result = sc.perform_cutover(
        triggers=[T_A, T_B], inventory=[{"trigger": T_A}, {"trigger": T_B}],
        dispositions={T_A: (sc.ALREADY_RUN, "")},
        authorisation=_Auth(), apply=True)

    assert result["action"] == sc.REFUSED
    assert T_B in " ".join(result["problems"])


def test_voiding_without_a_reason_stops_the_cutover():
    """Voiding is a decision someone made; recording it needs its reason."""
    result = sc.perform_cutover(
        triggers=[T_A], inventory=[{"trigger": T_A}],
        dispositions={T_A: (sc.VOID, "   ")},
        authorisation=_Auth(), apply=True)

    assert result["action"] == sc.REFUSED
    assert "without a reason" in " ".join(result["problems"])


def test_the_step_order_is_freeze_inventory_dispose_handover():
    """Freezing after inventory would let a trigger start between the two and
    never be dispositioned."""
    order: list[str] = []

    result = sc.perform_cutover(
        triggers=[T_A], inventory=[{"trigger": T_A}],
        dispositions={T_A: (sc.CARRIED_OVER, "carried to the new owner")},
        authorisation=_Auth(), actor="test", apply=True,
        freeze=lambda: order.append("freeze"),
        hand_over=lambda inv: order.append("hand_over"),
        mark_published=lambda t: order.append(f"publish:{t}"))

    assert order == ["freeze", "hand_over"]
    assert result["steps"] == ["freeze_new_claims", "inventory_unfinished",
                               "declare_dispositions", "hand_over_to_new_owner",
                               "mark_dispositions_published"]
    assert result["action"] == sc.CUTOVER
    assert result["applied"] is True


def test_a_failed_freeze_stops_before_inventory():
    def broken():
        raise RuntimeError("the freeze flag is locked")

    result = sc.perform_cutover(triggers=[T_A], inventory=[], dispositions={},
                                authorisation=_Auth(), apply=True, freeze=broken)
    assert result["action"] == sc.REFUSED
    assert "freezing new claims failed" in " ".join(result["problems"])


def test_a_decide_only_cutover_changes_nothing():
    """The runbook has an operator re-run this before deciding."""
    calls: list[str] = []
    result = sc.perform_cutover(
        triggers=[T_A], inventory=[{"trigger": T_A}],
        dispositions={T_A: (sc.CARRIED_OVER, "carried")},
        authorisation=_Auth(), freeze=lambda: calls.append("freeze"))

    assert result["applied"] is False
    assert result["action"] == sc.FROZEN
    assert "re-checked immediately before applying" in result["detail"]


# --- the report --------------------------------------------------------------

def test_the_report_leads_with_the_blocking_ownership_findings():
    report = sc.assert_sole_ownership([T_B], holders_reader=_holders({
        T_B: [{"owner": "legacy", "claimed_at": RECENT},
              {"owner": "phase-e", "claimed_at": RECENT}]}))
    cutover = {"action": sc.REFUSED, "applied": False, "trigger_count": 1,
               "steps": [], "problems": ["no auditable deployment authorisation"]}

    text = sc.render_report(report, cutover)
    assert text.index("所有权冲突") < text.index("## 切换")
    assert "no auditable deployment authorisation" in text
    assert "单一所有者" in text
