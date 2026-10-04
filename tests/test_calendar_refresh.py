import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ats.data.calendar_refresh import (
    parse_bea_schedule_html,
    parse_bls_ics,
    parse_fomc_html,
    refresh_schedule_calendar,
)
from ats.data.stores.schedule_calendar import ScheduleCalendarStore
from ats.data.stores.structured.artifacts import ArtifactStore

FIXTURES = Path(__file__).parent / "fixtures" / "calendar"


def test_federal_reserve_fixture_keeps_meeting_identity_and_date_precision():
    candidates = parse_fomc_html((FIXTURES / "fomc_2026.html").read_text(), years=[2026, 2027])
    september = next(item for item in candidates
                     if item.event_id == "fomc:2026-09:statement")
    assert september.event_date == date(2026, 9, 16)
    assert september.time_precision == "date"
    assert september.utc_at == ""
    assert {item.event_id for item in candidates} >= {
        "fomc:2026-09:meeting_start", "fomc:2026-09:statement",
        "fomc:2026-09:press_conference", "fomc:2026-12:statement",
    }
    assert len(candidates) == 13


def test_bls_ics_fixture_parses_cpi_nfp_local_and_utc_times():
    source = (FIXTURES / "bls_2026.ics").read_text()
    candidates = parse_bls_ics(source)
    assert {item.event_type for item in candidates} == {"cpi", "nfp"}
    cpi = next(item for item in candidates if item.event_type == "cpi")
    assert cpi.event_id == "cpi:macro:CPI:2026-08:initial"
    assert cpi.reference_period == "2026-08"
    assert cpi.local_time == "08:30"
    assert cpi.utc_at == "2026-09-11T12:30:00+00:00"
    cancelled = parse_bls_ics(source.replace("STATUS:CONFIRMED", "STATUS:CANCELLED"))
    assert all(item.status == "cancelled" for item in cancelled)


def test_bea_fixture_distinguishes_gdp_estimates_from_monthly_pce():
    candidates = parse_bea_schedule_html((FIXTURES / "bea_2026.html").read_text(), year=2026)
    identities = {item.event_id for item in candidates}
    assert "gdp:macro:GDP:2026-Q2:third" in identities
    assert "gdp:macro:GDP:2026-Q3:advance" in identities
    assert "pce:macro:PCE:2026-08:initial" in identities
    pce = next(item for item in candidates if item.event_type == "pce")
    assert pce.utc_at == "2026-09-30T12:30:00+00:00"


def test_refresh_idempotency_and_failure_preserve_last_good_calendar(monkeypatch, tmp_path):
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    fixture = (FIXTURES / "fomc_2026.html").read_text()
    monkeypatch.setattr("ats.data.calendar_refresh._fetch_text", lambda _: fixture)
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    first = refresh_schedule_calendar(source_ids=["federal_reserve_fomc"], now=now,
                                      store=store, artifact_store=artifacts)
    second = refresh_schedule_calendar(source_ids=["federal_reserve_fomc"],
                                       now=datetime(2026, 9, 25, 12, tzinfo=timezone.utc),
                                       store=store, artifact_store=artifacts)
    assert first["sources"][0]["published"] == 13
    run = next(item for item in store.source_runs(source_id="federal_reserve_fomc")
               if item["source_run_id"] == first["sources"][0]["source_run_id"])
    provenance = json.loads(run["provenance_json"])
    raw_ref = provenance["raw_artifact"]
    assert artifacts.read(raw_ref["relative_path"]).decode() == fixture
    assert first["sources"][0]["source_run_id"] == run["source_run_id"]
    candidate_id = store.candidates(event_id="fomc:2026-09:statement")[0]["candidate_id"]
    observation = store.candidate_observations(candidate_id=candidate_id)[0]
    assert observation["source_run_id"] == run["source_run_id"]
    assert observation["raw_artifact_id"] == raw_ref["blob_id"]
    assert second["sources"][0]["published"] == 0
    assert len(store.latest_events()) == 13

    def unavailable(_):
        raise OSError("network unavailable")

    monkeypatch.setattr("ats.data.calendar_refresh._fetch_text", unavailable)
    failed = refresh_schedule_calendar(
        source_ids=["federal_reserve_fomc"],
        now=datetime(2026, 9, 26, 12, tzinfo=timezone.utc), store=store,
        artifact_store=artifacts)
    assert failed["sources"][0]["status"] == "failed"
    assert len(store.latest_events()) == 13
    assert store.source_runs(source_id="federal_reserve_fomc")[0]["status"] == "failed"


def test_earnings_provider_actuals_do_not_bypass_document_release_admission(monkeypatch):
    from ats.data.calendar_refresh import _earnings_candidates

    monkeypatch.setattr("ats.data.earnings_calendar._finnhub_window", lambda *_: [{
        "date": date(2026, 10, 14), "year": 2026, "quarter": 3,
        "hour": "bmo", "eps_actual": 1.23, "rev_actual": 4_500_000_000,
    }])
    candidates, errors = _earnings_candidates(
        "finnhub_earnings", now=datetime(2026, 9, 24, tzinfo=timezone.utc),
        symbols=["NVDA"])

    assert errors == []
    assert len(candidates) == 1
    assert candidates[0].status == "planned"
    assert "eps_actual" not in candidates[0].metadata
    assert "rev_actual" not in candidates[0].metadata


def test_parser_drift_fails_closed():
    with pytest.raises(ValueError, match="parser drift"):
        parse_fomc_html("<html>calendar layout changed</html>", years=[2026])
    with pytest.raises(ValueError, match="no CPI"):
        parse_bls_ics("BEGIN:VCALENDAR\nEND:VCALENDAR")
    with pytest.raises(ValueError, match="no GDP"):
        parse_bea_schedule_html("<table><tr><td>layout changed</td></tr></table>")


def test_calendar_parser_rejection_keeps_fetched_raw_and_does_not_publish(monkeypatch, tmp_path):
    raw = "<html>calendar layout changed</html>"
    monkeypatch.setattr("ats.data.calendar_refresh._fetch_text", lambda _: raw)
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    artifacts = ArtifactStore(tmp_path / "artifacts")

    result = refresh_schedule_calendar(
        source_ids=["federal_reserve_fomc"],
        now=datetime(2026, 9, 24, 12, tzinfo=timezone.utc),
        store=store, artifact_store=artifacts)

    run = store.source_runs(source_id="federal_reserve_fomc")[0]
    provenance = json.loads(run["provenance_json"])
    assert result["sources"][0]["status"] == "failed"
    assert run["status"] == "failed"
    assert artifacts.read(provenance["raw_artifact"]["relative_path"]).decode() == raw
    assert store.latest_events() == []
    assert store.candidate_observations(source_run_id=run["source_run_id"]) == []


def test_calendar_refresh_cli_branch_uses_module_os_without_local_import_error(
        monkeypatch, capsys):
    from ats.runtime import cli

    monkeypatch.setattr("ats.data.persistent_queue.require_queue_worker", lambda _: None)
    monkeypatch.setattr("ats.data.calendar_refresh.refresh_schedule_calendar", lambda **_: {
        "sources": [{"source_id": "federal_reserve_fomc", "status": "complete"}],
        "manual_overlay": [], "release_reconciliation": {"status": "not_requested"},
        "release_ready_events": [], "as_of": "2026-09-24T12:00:00+00:00",
    })

    assert cli.run_data("calendar-refresh", source="federal_reserve_fomc") == 0
    assert '"status": "succeeded"' in capsys.readouterr().out


def test_custom_manual_event_identity_survives_date_revision(tmp_path):
    from ats.data.calendar_refresh import _manual_overlay_candidates

    config = tmp_path / "config"
    config.mkdir()
    source = config / "events.yaml"
    source.write_text(
        "events:\n  - date: 2027-01-05\n    kind: industry_conf\n"
        "    label: CES 2027\n    stable_id: ces-2027\n    triggers: [sector:ai_hardware]\n",
        encoding="utf-8")
    first = _manual_overlay_candidates(config)[0]
    source.write_text(
        "events:\n  - date: 2027-01-06\n    kind: industry_conf\n"
        "    label: CES 2027\n    stable_id: ces-2027\n    triggers: [sector:ai_hardware]\n",
        encoding="utf-8")
    revised = _manual_overlay_candidates(config)[0]

    assert revised.event_id == first.event_id
    assert revised.event_date != first.event_date


def test_bls_official_uid_without_reference_month_and_us_eastern_timezone():
    body = "\n".join([
        "BEGIN:VCALENDAR", "BEGIN:VEVENT", "UID:official-cpi-uid", "SEQUENCE:1",
        "DTSTART;TZID=US-Eastern:20261014T083000", "SUMMARY:Consumer Price Index",
        "END:VEVENT", "END:VCALENDAR",
    ])
    original = parse_bls_ics(body)[0]
    revised = parse_bls_ics(body.replace("20261014", "20261015"))[0]
    assert original.reference_period == ""
    assert original.metadata["reference_period_status"] == "not_provided"
    assert original.event_id == revised.event_id == "cpi:bls:CPI:official-cpi-uid"
    assert original.utc_at == "2026-10-14T12:30:00+00:00"
    assert original.timezone == "America/New_York"


def test_fomc_next_year_footer_note_is_not_a_current_year_meeting():
    body = ("<h4>2027 FOMC Meetings</h4><div>January 26-27 March 16-17*</div>"
            "<p>Note: A two-day meeting is scheduled for January 25-26, 2028.</p>")
    rows = parse_fomc_html(body, years=[2027])
    january = [row for row in rows if row.event_id == "fomc:2027-01:statement"]
    assert len(january) == 1
    assert january[0].event_date == date(2027, 1, 27)
