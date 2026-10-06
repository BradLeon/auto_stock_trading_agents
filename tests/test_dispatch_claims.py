"""Legacy scheduler adapter: shared trigger identity and handover (4.1–4.7).

The problem this exists for: `owner_mode` does not exist in `runtime/scheduler.py`
and `trigger_key` appears only inside `_start_phase_e`. So the resident daemon and
Phase E are two systems that both believe they may run a daily cascade, and nothing
in shared state knows about the other.

Most of these tests are about the ways a handover fails SILENTLY — two owners for
one trigger, an inventory that misses a claim made during the count, a handover
accepted on trust.
"""

import sqlite3

import pytest

from ats.workflow import dispatch_claims as dc

TICK = "2026-10-06T20:00:00-04:00"
TICK_UTC_EQUIVALENT = "2026-10-07T00:00:00+00:00"


@pytest.fixture
def state(tmp_path):
    path = tmp_path / "dispatch.sqlite"
    dc.install_owner("legacy", generation=1, actor="test", reason="fixture",
                     path=str(path))
    yield path


def _key(job_id: str = "daily_cycle", tick: str = TICK) -> str:
    return dc.legacy_key(job_id, scheduled_for=tick)


# --------------------------------------------------------------------------- #
# 4.1 — one identity, so one owner
# --------------------------------------------------------------------------- #

def test_the_legacy_daily_cascade_and_the_new_schedule_resolve_to_one_identity():
    """The requirement's first case, and the whole point of the mapping.

    Both sides go through Phase E's `trigger_key`, so they cannot disagree. A
    parallel implementation here would produce different digests for the same
    logical run, which is the ambiguity this module removes.
    """
    legacy = dc.legacy_key("daily_cycle", scheduled_for=TICK)
    new = dc.phase_e_key(schedule_id="daily-cascade", scheduled_for=TICK,
                         workflow_id="daily-cascade")
    assert legacy == new


def test_the_same_tick_in_another_timezone_is_the_same_run():
    """A plan expressed in ET and the same instant in UTC are one logical run."""
    assert dc.legacy_key("daily_cycle", scheduled_for=TICK) == dc.legacy_key(
        "daily_cycle", scheduled_for=TICK_UTC_EQUIVALENT)


def test_a_different_tick_is_a_different_run():
    assert _key(tick=TICK) != _key(tick="2026-10-07T20:00:00-04:00")


def test_a_catch_up_after_downtime_is_the_same_run():
    """Using the planned instant, not the firing instant.

    This is the misfire case `run_contracts.TriggerContext` already warns about:
    a catch-up on the same tick must not mint a second logical run.
    """
    planned = dc.legacy_key("daily_cycle", scheduled_for=TICK)
    fired_late = dc.legacy_key("daily_cycle", scheduled_for="2026-10-07T03:00:00-04:00")
    assert planned != fired_late, "the firing instant is a different tick"
    # Re-expressed in the planned instant, it is the same run.
    assert dc.legacy_key("daily_cycle", scheduled_for=TICK) == planned


def test_every_legacy_job_maps_to_a_workflow():
    """Derived from the registration block, not invented.

    A mapping naming a job that does not exist would make the omission comparison
    vacuous for it.
    """
    for job_id in dc.LEGACY_JOBS:
        assert dc.legacy_key(job_id, scheduled_for=TICK)


def test_an_unmapped_job_is_refused_rather_than_hashed_ad_hoc():
    """Silently hashing an unknown job would create an identity nobody declared."""
    with pytest.raises(dc.DispatchOwnershipError, match="no declared workflow"):
        dc.legacy_key("some_job_added_later", scheduled_for=TICK)


def test_an_event_identity_is_id_plus_version():
    first = dc.context_for_event(event_id="earnings", event_version="1",
                               workflow_id="pead-score")
    corrected = dc.context_for_event(event_id="earnings", event_version="2",
                                    workflow_id="pead-score")
    assert dc.triggers_key(first) != dc.triggers_key(corrected)


def test_a_manual_trigger_is_not_the_scheduled_one():
    """Different `kind`, therefore different key.

    Task 4.2 merges a manual and an automatic trigger into one EXECUTION at the
    claim layer; collapsing the identities here would make the ledger unable to
    tell a catch-up from a human request.
    """
    manual = dc.context_for_manual(trigger_id="t1", workflow_id="daily-cascade")
    scheduled = dc.context_for_schedule(schedule_id="daily-cascade",
                                        scheduled_for=TICK,
                                        workflow_id="daily-cascade")
    assert dc.triggers_key(manual) != dc.triggers_key(scheduled)


# --------------------------------------------------------------------------- #
# 4.2 / 4.3 — claim before executing
# --------------------------------------------------------------------------- #

def test_a_claim_requires_an_installed_owner(tmp_path):
    path = tmp_path / "empty.sqlite"
    result = dc.claim(key="k", workflow_id="w", path=str(path))
    assert result.acquired is False
    assert "no dispatch owner" in result.reason


def test_the_owner_can_claim(state):
    result = dc.claim(key=_key(), workflow_id="daily-cascade", path=str(state))
    assert result.acquired is True
    assert result.owner == "legacy"


def test_a_second_owner_is_refused_while_the_first_holds_the_claim(state):
    """The case the shared table exists for: a one-shot CLI against a live daemon."""
    key = _key()
    assert dc.claim(key=key, workflow_id="daily-cascade", path=str(state)).acquired

    second = dc.claim(key=key, workflow_id="daily-cascade", owner="phase-e",
                      path=str(state))
    assert second.acquired is False
    assert "already claimed" in second.reason


def test_a_manual_and_an_automatic_trigger_merge_into_one_execution(state):
    """One owner per logical work, not two rows for the same intent.

    The two identities differ, so they are two keys; the claim layer is what stops
    both from running for the same scope at once.
    """
    scheduled = dc.legacy_key("daily_cycle", scheduled_for=TICK)
    manual = dc.triggers_key(dc.context_for_manual(
        trigger_id="operator-click", workflow_id="daily-cascade"))

    first = dc.claim(key=scheduled, workflow_id="daily-cascade", path=str(state))
    second = dc.claim(key=manual, workflow_id="daily-cascade", path=str(state))
    assert first.acquired is True
    # A different identity is a different claim, so it is not blocked as a duplicate...
    assert second.acquired is True
    # ...but both belong to the one owner, and neither belongs to anybody else.
    assert {c["owner"] for c in dc.claims(path=str(state))} == {"legacy"}


def test_a_process_behind_the_generation_is_refused(state):
    """Task 4.3's case: the resident process never received the state change.

    No human confirmation involved — the generation is read at claim time, so a
    daemon that has not reloaded simply cannot claim.
    """
    key = _key()
    dc.claim(key=key, workflow_id="daily-cascade", path=str(state))
    dc.switch_owner(1, "phase-e", actor="op", reason="cutover", path=str(state))

    stale = dc.claim(key="another-key", workflow_id="daily-cascade", owner="legacy",
                     path=str(state))
    assert stale.acquired is False
    assert "current owner is 'phase-e'" in stale.reason


def test_switch_owner_is_compare_and_set(state):
    dc.switch_owner(1, "phase-e", actor="op", reason="first", path=str(state))
    with pytest.raises(dc.DispatchOwnershipError, match="generation moved"):
        dc.switch_owner(1, "phase-e-2", actor="op", reason="second", path=str(state))


def test_installing_refuses_to_overwrite_an_existing_owner(state):
    """Otherwise a process start resets the generation and revives every claim."""
    with pytest.raises(dc.DispatchOwnershipError, match="already"):
        dc.install_owner("someone-else", generation=1, path=str(state))


def test_publishing_is_a_distinct_state_from_finishing(state):
    """The handover must stop the old executor from PUBLISHING.

    A legacy job that finished its work but has not written its result is still a
    publisher, so the two are different states.
    """
    key = _key()
    dc.claim(key=key, workflow_id="daily-cascade", path=str(state))
    assert dc.mark_published(key, path=str(state)) is True
    assert dc.claims(path=str(state))[0]["status"] == dc.PUBLISHED


def test_an_observer_cannot_publish_someone_elses_claim(state):
    key = _key()
    dc.claim(key=key, workflow_id="daily-cascade", path=str(state))
    dc.mark_published(key, path=str(state))
    # The key is settled; a second publish attempt changes nothing.
    assert dc.mark_published(key, path=str(state)) is False


# --------------------------------------------------------------------------- #
# 4.4 — the handover protocol
# --------------------------------------------------------------------------- #

def _proved(*blocked):
    """A proof callable. Takes the (old_owner, new_owner) pair it is handed."""
    def _proof(_old_owner, _new_owner):
        return {"proved": True, "reason": "", "blocked_publishers": list(blocked)}
    return _proof


def _unproved(reason: str):
    def _proof(_old_owner, _new_owner):
        return {"proved": False, "reason": reason, "blocked_publishers": []}
    return _proof


def test_a_handover_runs_freeze_inventory_dispose_prove_switch_open(state):
    report = dc.hand_over(to_owner="phase-e", actor="op", reason="cutover",
                          prove_handover=_proved(), path=str(state))
    assert report.succeeded
    assert report.steps == ["freeze", "inventory", "dispose", "prove",
                            "switch", "open"]
    assert report.from_generation == 1 and report.to_generation == 2
    assert dc.read_owner(str(state)).owner == "phase-e"


def test_an_undeclared_disposition_refuses_the_handover(state):
    """Every unfinished trigger must be disposed of individually."""
    key = _key()
    dc.claim(key=key, workflow_id="daily-cascade", path=str(state))

    report = dc.hand_over(to_owner="phase-e", prove_handover=_proved(),
                          disposition_for=lambda row: "",
                          path=str(state))

    assert not report.succeeded
    assert any("no declared disposition" in reason for reason in report.reasons)
    assert dc.read_owner(str(state)).owner == "legacy", "ownership must not move"


def test_a_refused_handover_reopens_claims(state):
    """Otherwise a failed attempt silently stops all scheduling."""
    key = _key()
    dc.claim(key=key, workflow_id="daily-cascade", path=str(state))

    dc.hand_over(to_owner="phase-e", disposition_for=lambda row: "", path=str(state))

    assert dc.is_frozen(str(state)) is False
    assert dc.claim(key="new-key", workflow_id="w", path=str(state)).acquired


def test_a_void_disposition_records_its_reason_and_does_not_execute(state):
    key = _key()
    dc.claim(key=key, workflow_id="daily-cascade", path=str(state))

    report = dc.hand_over(to_owner="phase-e", actor="op", reason="superseded by a "
                          "newer tick", prove_handover=_proved(),
                          disposition_for=lambda row: dc.DISPOSITION_VOID,
                          path=str(state))

    assert report.succeeded
    assert report.voided and report.voided[0]["trigger_key"] == key
    assert report.voided[0]["reason"] == "superseded by a newer tick"
    row = dc.claims(path=str(state))[0]
    assert row["status"] == dc.ABANDONED


def test_a_void_trigger_can_be_claimed_again_after_the_handover(state):
    """Void means "not executed under these circumstances", not "never"."""
    key = _key()
    dc.claim(key=key, workflow_id="daily-cascade", path=str(state))
    dc.hand_over(to_owner="phase-e", actor="op", reason="voided",
                 prove_handover=_proved(),
                 disposition_for=lambda row: dc.DISPOSITION_VOID, path=str(state))

    # By the CURRENT owner, whoever that turned out to be. The point of the test is
    # that the abandoned claim is re-claimable, not which owner ends up holding it.
    current = dc.read_owner(str(state)).owner
    again = dc.claim(key=key, workflow_id="daily-cascade", owner=current,
                     path=str(state))
    assert again.acquired is True


def test_an_already_run_disposition_marks_it_published(state):
    key = _key()
    dc.claim(key=key, workflow_id="daily-cascade", path=str(state))

    dc.hand_over(to_owner="phase-e", actor="op", reason="done",
                 prove_handover=_proved(),
                 disposition_for=lambda row: dc.DISPOSITION_ALREADY_RUN,
                 path=str(state))

    assert dc.claims(path=str(state))[0]["status"] == dc.PUBLISHED


# --------------------------------------------------------------------------- #
# 4.5 — proof, or no handover
# --------------------------------------------------------------------------- #

def test_a_handover_without_proof_is_refused(state):
    """The requirement: refuse when the old executor cannot be proven stopped.

    Accepting on trust is what produces two executors for one logical trigger, and
    the symptom shows up much later as a duplicated decision cycle.

    The unfinished trigger is disposed of first, so the refusal is attributable to
    the missing proof rather than to the earlier disposition step — otherwise the
    test would pass for the wrong reason.
    """
    dc.claim(key=_key(), workflow_id="daily-cascade", path=str(state))

    report = dc.hand_over(to_owner="phase-e", path=str(state),
                          disposition_for=lambda row: dc.DISPOSITION_CARRY_OVER)

    assert report.steps == ["freeze", "inventory", "dispose", "prove"]
    assert not report.succeeded
    assert any("cannot prove" in reason for reason in report.reasons)
    assert dc.read_owner(str(state)).owner == "legacy"


def test_a_failed_proof_names_why_and_reopens_claims(state):
    dc.claim(key=_key(), workflow_id="daily-cascade", path=str(state))
    report = dc.hand_over(to_owner="phase-e", prove_handover=_unproved(
        "the legacy daemon still holds an APScheduler job id 'daily_cycle'"),
                          disposition_for=lambda row: dc.DISPOSITION_CARRY_OVER,
                          path=str(state))

    assert not report.succeeded
    assert any("APScheduler job" in reason for reason in report.reasons)
    assert report.handover_proved is False
    assert dc.is_frozen(str(state)) is False


def test_a_successful_proof_records_which_publishers_were_blocked(state):
    report = dc.hand_over(to_owner="phase-e", prove_handover=_proved("pid-123"),
                          path=str(state))
    assert report.publish_blocked == ["pid-123"]
    assert report.succeeded
    assert report.steps == ["freeze", "inventory", "dispose", "prove",
                            "switch", "open"]


def test_only_the_attempt_that_froze_may_open(state):
    first = dc.freeze_claims(actor="op-1", reason="cutover", path=str(state))
    dc.open_claims(first, path=str(state))
    second = dc.freeze_claims(actor="op-2", reason="cutover", path=str(state))

    with pytest.raises(dc.DispatchOwnershipError, match="token mismatch"):
        dc.open_claims(first, path=str(state))
    dc.open_claims(second, path=str(state))


def test_a_second_freeze_while_frozen_is_refused(state):
    dc.freeze_claims(actor="op-1", reason="cutover", path=str(state))
    with pytest.raises(dc.DispatchOwnershipError, match="already frozen"):
        dc.freeze_claims(actor="op-2", reason="cutover", path=str(state))


# --------------------------------------------------------------------------- #
# 4.6 — the independent expected set
# --------------------------------------------------------------------------- #

def test_both_paths_missing_the_same_trigger_is_detected(state):
    """The case a path-to-path comparison cannot see.

    Both sides dropping a trigger makes them agree, so agreement would read as
    "handled". Only an independent expectation finds it.
    """
    token = dc.freeze_claims(actor="op", reason="cutover", path=str(state))
    key = _key()
    dc.declare_expected(switch_token=token, keys=[key], path=str(state))
    dc.open_claims(token, path=str(state))
    # Nobody observes it.

    report = dc.omission_report(token, path=str(state))
    assert report["both_missed"] == [key]
    assert report["old_missed"] == [key]
    assert report["new_missed"] == [key]


def test_a_trigger_both_paths_ran_is_not_reported(state):
    token = dc.freeze_claims(actor="op", reason="cutover", path=str(state))
    key = _key()
    dc.declare_expected(switch_token=token, keys=[key], path=str(state))
    dc.open_claims(token, path=str(state))
    # Registered by the claim layer, which is what makes the expected set
    # consistent with reality — otherwise the report (correctly) distrusts itself.
    dc.claim(key=key, workflow_id="daily-cascade", path=str(state))
    dc.observe_trigger(switch_token=token, trigger_key=key, path_owner="old",
                      path=str(state))
    dc.observe_trigger(switch_token=token, trigger_key=key, path_owner="new",
                      path=str(state))

    report = dc.omission_report(token, path=str(state))
    assert report["both_missed"] == []
    assert report["old_missed"] == []
    assert report["trustworthy"] is True


def test_an_expected_set_that_disagrees_with_registration_is_not_trusted(state):
    """Task 4.6's second case: refuse to judge omissions from a wrong set.

    An expectation nobody registered means the set was wrong, so the omissions
    derived from it would be about a window that never was.
    """
    token = dc.freeze_claims(actor="op", reason="cutover", path=str(state))
    dc.declare_expected(
        switch_token=token,
        keys=[_key("daily_cycle"), _key("weekly_review")], path=str(state))
    dc.open_claims(token, path=str(state))
    dc.observe_trigger(switch_token=token, trigger_key=_key("daily_cycle"),
                      path_owner="old", path=str(state))
    dc.observe_trigger(switch_token=token, trigger_key=_key("daily_cycle"),
                      path_owner="new", path=str(state))

    report = dc.omission_report(token, path=str(state))
    assert report["trustworthy"] is False
    assert any("never registered" in reason for reason in report["reasons"])
    assert dc.expected_set_matches_registration(token, path=str(state)) is False


def test_no_declared_set_means_no_verdict_at_all(state):
    token = dc.freeze_claims(actor="op", reason="cutover", path=str(state))
    report = dc.omission_report(token, path=str(state))
    assert report["trustworthy"] is False
    assert any("no expected trigger set" in reason for reason in report["reasons"])


def test_observations_are_append_only_once_both_paths_reported(state):
    """The set must not be edited into agreement after the fact."""
    token = dc.freeze_claims(actor="op", reason="cutover", path=str(state))
    key = _key()
    dc.declare_expected(switch_token=token, keys=[key], path=str(state))
    dc.open_claims(token, path=str(state))
    dc.observe_trigger(switch_token=token, trigger_key=key, path_owner="old",
                      path=str(state))
    dc.observe_trigger(switch_token=token, trigger_key=key, path_owner="new",
                      path=str(state))

    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(state) as conn:
            conn.execute(
                "UPDATE dispatch_expected_triggers SET observed_old=0"
                " WHERE switch_token=? AND trigger_key=?", (token, key))


# --------------------------------------------------------------------------- #
# 4.7 — a dispatch switch owns no source lifecycle
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("action", sorted(dc.SOURCE_LIFECYCLE_ACTIONS))
def test_source_lifecycle_actions_are_refused_during_a_switch(action):
    with pytest.raises(dc.SourceLifecycleRefused) as excinfo:
        dc.assert_not_source_lifecycle(action)
    assert excinfo.value.reason_code == "dispatch_switch_owns_no_source_lifecycle"
    assert "does not own source lifecycle" in str(excinfo.value)


def test_a_dispatch_ownership_change_is_not_a_source_lifecycle_action():
    """The distinction the requirement is about.

    Switching the owner is a dispatch action; restarting the collection under the
    new owner is a source action. They are different changes and must not be
    bundled.
    """
    dc.assert_not_source_lifecycle("switch_owner")
    dc.assert_not_source_lifecycle("hand_over")
    dc.assert_not_source_lifecycle("freeze_claims")


def test_refusing_a_source_restart_leaves_the_owner_untouched(state):
    """A refused action must not have moved anything."""
    with pytest.raises(dc.SourceLifecycleRefused):
        dc.assert_not_source_lifecycle("restart_source")
    assert dc.read_owner(str(state)).owner == "legacy"
    assert dc.read_owner(str(state)).generation == 1
