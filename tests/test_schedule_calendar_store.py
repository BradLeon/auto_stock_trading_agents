from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from ats.data.stores.schedule_calendar import (ScheduleCalendarStore, ScheduleEventCandidate,
                                               earnings_identity, fomc_identity,
                                               macro_identity)


def _candidate(*, source="official", day="2026-10-14", time="08:30", status="planned",
               fetched="2026-09-24T12:00:00+00:00", source_ref="CPI-2026-09"):
    return ScheduleEventCandidate(
        source_id=source, source_url=f"https://{source}.example/calendar",
        source_event_ref=source_ref, event_type="cpi",
        stable_identity=macro_identity("CPI", "2026-09", "initial"),
        label="September CPI", reference_period="2026-09", event_date=day,
        local_time=time, timezone="America/New_York", time_precision="minute",
        market_session="intraday", status=status, fetched_at=fetched)


def test_calendar_identity_helpers_and_unknown_identity_quarantine(tmp_path):
    assert earnings_identity("nvda", 2026, 3) == "earnings:NVDA:FY2026Q3:release"
    assert earnings_identity("NVDA", None, None) is None
    assert fomc_identity(2026, 9) == "fomc:2026-09:statement"
    assert macro_identity("CPI", "2026-08", "initial") == "macro:CPI:2026-08:initial"
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    unknown = ScheduleEventCandidate(source_id="test", event_type="earnings",
                                     event_date="2026-10-20", time_precision="date")
    result = store.submit_candidate(unknown)
    assert result["review_status"] == "pending_identity"
    with pytest.raises(ValueError, match="identity is insufficient"):
        store.publish_candidate(result["candidate_id"])


def test_duplicate_candidates_are_idempotent_and_versions_are_as_of(tmp_path):
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    first = _candidate()
    accepted = store.submit_candidate(first)
    event_v1 = store.publish_candidate(accepted["candidate_id"],
                                       at=datetime(2026, 9, 24, 12, tzinfo=timezone.utc))
    replay = _candidate(fetched="2026-09-25T12:00:00+00:00")
    assert replay.candidate_id == first.candidate_id
    assert store.submit_candidate(replay)["duplicate"] is True

    reschedule = _candidate(day="2026-10-15", fetched="2026-09-26T12:00:00+00:00")
    candidate = store.submit_candidate(reschedule)
    assert candidate["review_status"] == "candidate"  # same source can revise its own date
    event_v2 = store.publish_candidate(candidate["candidate_id"],
                                       at=datetime(2026, 9, 26, 12, tzinfo=timezone.utc))
    assert event_v1["event_version"] == 1
    assert event_v2["event_version"] == 2
    before = store.events(as_of=datetime(2026, 9, 25, tzinfo=timezone.utc))
    after = store.events(as_of=datetime(2026, 9, 27, tzinfo=timezone.utc))
    assert before[0]["event_date"] == "2026-10-14"
    assert after[0]["event_date"] == "2026-10-15"
    assert event_v2["utc_at"] == "2026-10-15T12:30:00+00:00"


def test_cross_source_conflicts_fail_closed_until_manual_adjudication(tmp_path):
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    first = store.submit_candidate(_candidate(source="bls"))
    store.publish_candidate(first["candidate_id"])
    disagree = store.submit_candidate(_candidate(source="mirror", day="2026-10-15"))
    assert disagree["review_status"] == "conflict"
    with pytest.raises(ValueError, match="unresolved"):
        store.publish_candidate(disagree["candidate_id"])
    approved = store.publish_candidate(disagree["candidate_id"], allow_conflict=True,
                                       actor="operator", reason="primary calendar correction")
    assert approved["event_version"] == 2
    assert approved["event_date"] == "2026-10-15"
    assert approved["quality_status"] == "ok"


def test_audited_manual_override_resolves_event_conflict(tmp_path):
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    first = store.submit_candidate(_candidate(source="bls"))
    store.publish_candidate(first["candidate_id"])
    conflict = store.submit_candidate(_candidate(source="mirror", day="2026-10-15"))
    assert conflict["review_status"] == "conflict"

    corrected = store.override_event(
        first["payload"]["event_id"], patch={"event_date": "2026-10-16"},
        actor="calendar-operator", reason="verified primary schedule update",
        evidence={"source": "official"})

    assert corrected["quality_status"] == "ok"
    assert corrected["event_version"] == 2
    assert store.candidates(event_id=corrected["event_id"], review_status="conflict") == []
    assert corrected["overrides"][0]["actor"] == "calendar-operator"
    withdrawn = store.withdraw_override(
        corrected["event_id"], actor="calendar-operator", reason="reopen source review",
        evidence={"ticket": "CAL-17"})
    assert withdrawn["quality_status"] == "conflict"
    assert store.candidates(event_id=corrected["event_id"], review_status="conflict")


def test_manual_override_and_withdrawal_append_versions_and_audit(tmp_path):
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    item = store.submit_candidate(_candidate())
    store.publish_candidate(item["candidate_id"],
                            at=datetime(2026, 9, 24, 12, tzinfo=timezone.utc))
    moved = store.override_event(
        item["payload"]["event_id"], patch={"event_date": "2026-10-16"},
        actor="alice", reason="verified agency notice", evidence={"url": "https://example"},
        at=datetime(2026, 9, 25, 12, tzinfo=timezone.utc))
    assert moved["event_version"] == 2
    restored = store.withdraw_override(
        item["payload"]["event_id"], actor="alice", reason="withdraw correction",
        evidence={"ticket": "CAL-4"}, at=datetime(2026, 9, 26, 12, tzinfo=timezone.utc))
    assert restored["event_version"] == 3
    assert restored["event_date"] == "2026-10-14"
    assert len(store.events(as_of=datetime(2026, 9, 25, 13, tzinfo=timezone.utc))) == 1
    assert len(store.events(as_of=datetime(2026, 9, 26, 13, tzinfo=timezone.utc))) == 1


def test_date_precision_never_fabricates_utc_and_dst_is_zone_aware():
    date_only = ScheduleEventCandidate(source_id="fed", event_type="fomc",
                                       stable_identity=fomc_identity(2026, 9),
                                       event_date="2026-09-16", time_precision="date")
    assert date_only.utc_at == ""
    dst = ScheduleEventCandidate(source_id="bls", event_type="cpi",
                                 stable_identity=macro_identity("CPI", "2026-03", "initial"),
                                 event_date="2026-03-11", local_time="08:30",
                                 timezone="America/New_York", time_precision="minute",
                                 market_session="intraday")
    assert dst.utc_at == "2026-03-11T12:30:00+00:00"
    with pytest.raises(ValidationError, match="must not invent"):
        ScheduleEventCandidate(source_id="fed", event_type="fomc", event_date="2026-09-16",
                               local_time="14:00", time_precision="date")


def test_source_failure_is_recorded_without_removing_last_event(tmp_path):
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    item = store.submit_candidate(_candidate())
    store.publish_candidate(item["candidate_id"])
    run_id = store.record_source_run("bls-cpi", at=datetime(2026, 9, 26, tzinfo=timezone.utc))
    store.finish_source_run(run_id, status="failed", error_code="network",
                            error_detail="unavailable")
    assert store.latest_events()[0]["event_date"] == "2026-10-14"
    run = store.source_runs(source_id="bls-cpi")[0]
    assert run["status"] == "failed"
    assert run["error_code"] == "network"


def test_source_cancellation_appends_version_without_deleting_prior_plan(tmp_path):
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    published_at = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    planned_candidate = _candidate()
    planned_row = store.submit_candidate(planned_candidate)
    planned = store.publish_candidate(planned_row["candidate_id"], at=published_at)
    cancelled_candidate = planned_candidate.model_copy(update={"status": "cancelled"})
    cancelled_row = store.submit_candidate(cancelled_candidate)
    cancelled = store.publish_candidate(cancelled_row["candidate_id"],
                                        at=published_at + timedelta(minutes=1))

    assert cancelled["event_id"] == planned["event_id"]
    assert cancelled["event_version"] == planned["event_version"] + 1
    assert cancelled["status"] == "cancelled"
    assert store.events(as_of=published_at)[0]["status"] == "planned"
    assert store.latest_events(include_cancelled=True)[0]["status"] == "cancelled"


def test_release_state_requires_admitted_material_and_creates_new_version(tmp_path):
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    item = store.submit_candidate(_candidate())
    planned = store.publish_candidate(item["candidate_id"],
                                      at=datetime(2026, 9, 24, 12, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="admitted material"):
        store.confirm_release(planned["event_id"], expected_version=1,
                              admitted_materials=[{"ref": "doc-v1", "kind": "filing",
                                                  "status": "candidate"}],
                              source_id="bls-document")
    released = store.confirm_release(
        planned["event_id"], expected_version=1,
        admitted_materials=[{"ref": "doc-v1", "kind": "official_release",
                             "status": "admitted"}],
        source_id="bls-document", at=datetime(2026, 10, 14, 12, tzinfo=timezone.utc))
    assert released["status"] == "released"
    assert released["event_version"] == 2
    assert released["release_confirmations"][0]["materials"] == [
        {"ref": "doc-v1", "kind": "official_release"}]
    with pytest.raises(ValueError, match="version changed"):
        store.confirm_release(planned["event_id"], expected_version=1,
                              admitted_materials=[{"ref": "doc-v2", "kind": "filing",
                                                  "status": "admitted"}],
                              source_id="another")


def test_late_admitted_transcript_appends_a_followup_release_version(tmp_path):
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    candidate = _candidate()
    saved = store.submit_candidate(candidate)
    planned = store.publish_candidate(saved["candidate_id"])
    first = store.confirm_release(
        planned["event_id"], expected_version=planned["event_version"],
        admitted_materials=[{"ref": "filing-v1", "kind": "filing", "status": "admitted"}],
        source_id="platform", at=datetime(2026, 10, 14, 12, tzinfo=timezone.utc))
    followup = store.confirm_release(
        first["event_id"], expected_version=first["event_version"],
        admitted_materials=[{"ref": "filing-v1", "kind": "filing", "status": "admitted"},
                           {"ref": "transcript-v1", "kind": "transcript",
                            "status": "admitted"}],
        source_id="platform", at=datetime(2026, 10, 15, 12, tzinfo=timezone.utc))

    assert first["event_version"] == planned["event_version"] + 1
    assert followup["event_version"] == first["event_version"] + 1
    assert {item["ref"] for item in followup["release_confirmations"][0]["materials"]} == {
        "filing-v1", "transcript-v1"}
    assert store.events(as_of=datetime(2026, 10, 14, 12, tzinfo=timezone.utc))[0][
        "event_version"] == first["event_version"]
