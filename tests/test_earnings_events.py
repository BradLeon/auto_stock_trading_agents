import json
from datetime import date

from ats.data.earnings_calendar import EarningsPrint
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


def test_latest_event_resolves_mrvl_from_disclosed_calendar_print():
    from ats.data.earnings_events import resolve_latest_event

    print_ = EarningsPrint(
        "MRVL", date(2026, 8, 27), quarter=2, year=2027, eps_actual=0.45,
        sources=("finnhub",),
    )
    result = resolve_latest_event(
        "MRVL", as_of=date(2026, 8, 29), config_label="",
        calendar_fetcher=lambda *_a, **_k: print_, transcript_fetcher=lambda *_: None,
    )

    assert result.resolved
    assert result.event.entity == "MRVL"
    assert result.event.fiscal_label == "Q2 FY2027"
    assert result.event.report_date == date(2026, 8, 27)


def test_latest_event_can_take_period_from_structured_transcript_when_calendar_omits_it():
    from ats.data import defeatbeta
    from ats.data.earnings_events import resolve_latest_event

    print_ = EarningsPrint("TSM", date(2026, 7, 16), eps_actual=2.0)
    transcript = defeatbeta.Transcript(
        "TSM", 2026, 2, "2026-07-16", paragraphs=(
            defeatbeta.Paragraph(0, "Operator", "Welcome to the earnings call."),
        ),
    )
    result = resolve_latest_event(
        "TSM", as_of=date(2026, 7, 17), config_label="",
        calendar_fetcher=lambda *_a, **_k: print_, transcript_fetcher=lambda *_: transcript,
    )

    assert result.resolved
    assert result.event.fiscal_label == "Q2 FY2026"


def test_latest_event_does_not_bind_a_forecast_or_previous_quarter():
    from ats.data.earnings_events import resolve_latest_event

    forecast = EarningsPrint("MRVL", date(2026, 8, 27), quarter=2, year=2027)
    result = resolve_latest_event(
        "MRVL", as_of=date(2026, 8, 27), config_label="",
        calendar_fetcher=lambda *_a, **_k: forecast, transcript_fetcher=lambda *_: None,
    )

    assert result.status == "unresolved"
    assert result.event is None


def test_latest_event_persists_unresolved_entity_for_audit():
    from ats.data.earnings_events import resolve_latest_event

    store = get_store()
    result = resolve_latest_event(
        "MRVL", store=store, as_of=date(2026, 8, 27), config_label="",
        calendar_fetcher=lambda *_a, **_k: None, transcript_fetcher=lambda *_: None,
    )

    assert result.status == "unresolved"
    assert store.earnings_events("MRVL")[0]["status"] == "unresolved"
