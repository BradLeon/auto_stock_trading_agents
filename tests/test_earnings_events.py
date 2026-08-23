import json

from ats.data.earnings_events import EventEvidence, resolve_event
from ats.memory import get_store


def test_calendar_event_resolves_report_date_and_complete_fiscal_period():
    result = resolve_event("TSMC", [
        EventEvidence("calendar", "2026-07-16", 2026, 2, reference="calendar:TSM"),
        EventEvidence("config", fiscal_label="Q2 FY2026", reference="config:TSM"),
        EventEvidence("filing", "2026-07-17", reference="filing:6-k"),
    ])

    assert result.resolved
    assert result.event.entity == "TSM"
    assert result.event.fiscal_label == "Q2 FY2026"
    assert str(result.event.report_date) == "2026-07-16"


def test_config_period_conflict_is_explicit_not_silently_overridden():
    result = resolve_event("TSM", [
        EventEvidence("calendar", "2026-07-16", 2026, 2),
        EventEvidence("config", fiscal_label="Q1 FY2026"),
    ])

    assert result.status == "conflict"
    assert [(c.field, c.conflicting_source) for c in result.conflicts] == [
        ("fiscal_period", "config")
    ]


def test_unrelated_filing_date_is_reported_as_event_conflict():
    result = resolve_event("TSM", [
        EventEvidence("calendar", "2026-07-16", 2026, 2),
        EventEvidence("filing", "2026-08-08"),
    ])

    assert result.status == "conflict"
    assert result.conflicts[0].field == "report_date"


def test_missing_quarter_is_unresolved_even_when_year_and_date_exist():
    result = resolve_event("SKHY", [
        EventEvidence("calendar", "2026-07-29", fiscal_label="Q FY2026"),
        EventEvidence("config", fiscal_label="TODO"),
    ])

    assert result.status == "unresolved"
    assert result.unresolved_fields == ("fiscal_period",)


def test_event_resolution_and_conflicts_are_persisted_for_audit():
    store = get_store()
    result = resolve_event("TSM", [
        EventEvidence("calendar", "2026-07-16", 2026, 2),
        EventEvidence("config", fiscal_label="Q1 FY2026"),
    ])

    event_id = store.save_earnings_event(result)
    row = store.earnings_events("TSM")[0]

    assert row["event_id"] == event_id
    assert row["status"] == "conflict"
    assert json.loads(row["conflicts_json"])[0]["field"] == "fiscal_period"
