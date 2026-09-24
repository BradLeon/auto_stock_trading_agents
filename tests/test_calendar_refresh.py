from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ats.data.calendar_refresh import (parse_bea_schedule_html, parse_bls_ics,
                                       parse_fomc_html, refresh_schedule_calendar)
from ats.data.stores.schedule_calendar import ScheduleCalendarStore

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
    fixture = (FIXTURES / "fomc_2026.html").read_text()
    monkeypatch.setattr("ats.data.calendar_refresh._fetch_text", lambda _: fixture)
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    first = refresh_schedule_calendar(source_ids=["federal_reserve_fomc"], now=now, store=store)
    second = refresh_schedule_calendar(source_ids=["federal_reserve_fomc"],
                                       now=datetime(2026, 9, 25, 12, tzinfo=timezone.utc),
                                       store=store)
    assert first["sources"][0]["published"] == 13
    assert second["sources"][0]["published"] == 0
    assert len(store.latest_events()) == 13

    def unavailable(_):
        raise OSError("network unavailable")

    monkeypatch.setattr("ats.data.calendar_refresh._fetch_text", unavailable)
    failed = refresh_schedule_calendar(
        source_ids=["federal_reserve_fomc"],
        now=datetime(2026, 9, 26, 12, tzinfo=timezone.utc), store=store)
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
