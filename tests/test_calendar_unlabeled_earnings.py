"""Earnings dates a source gives without a fiscal quarter, and the operator's label (ant-data-onboarding 3.2)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from ats.data import calendar_refresh
from ats.data.stores.schedule_calendar import ScheduleCalendarStore, unlabeled_earnings_identity
from ats.data.stores.structured.artifacts import ArtifactStore
from ats.workflow.routing import RouteError, resolve_event_route

PLACEHOLDER = unlabeled_earnings_identity("ANT")
LABELLED = "earnings:ANT:FY2026Q4:release"
EVIDENCE = {"source": "issuer announcement", "url": "https://example.test/ir"}
ADMITTED = [{"kind": "company_release", "status": "admitted", "ref": "doc@v1"}]


@pytest.fixture
def calendar(monkeypatch, tmp_path):
    """Refresh the real earnings adapters against canned provider rows."""
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    rows: dict[str, list[dict]] = {"finnhub": [], "yahoo": []}
    monkeypatch.setattr("ats.data.earnings_calendar._finnhub_window", lambda *_, **__: list(rows["finnhub"]))
    monkeypatch.setattr("ats.data.earnings_calendar._yf_prints", lambda *_: list(rows["yahoo"]))
    monkeypatch.setattr(calendar_refresh, "load_pead_global", lambda: {"targets": ["ANT"]})
    clock = iter(range(1, 60))

    def refresh(source: str = "finnhub_earnings_calendar", *, finnhub=None, yahoo=None):
        if finnhub is not None:
            rows["finnhub"] = finnhub
        if yahoo is not None:
            rows["yahoo"] = yahoo
        tick = next(clock)
        result = calendar_refresh.refresh_schedule_calendar(
            source_ids=[source], now=datetime(2026, 11, 1, 0, tick, tzinfo=timezone.utc),
            store=store, artifact_store=artifacts)
        assert result["sources"][0]["status"] != "failed", result
        return result["sources"][0]

    refresh.store = store
    return refresh


def _print(day: str, *, year=None, quarter=None, hour=""):
    return {"date": date.fromisoformat(day), "year": year, "quarter": quarter, "hour": hour}


def _current(store, *, cancelled: bool = False):
    return {e["event_id"]: e for e in store.latest_events(include_cancelled=cancelled)}


def _label(store, event_id: str = PLACEHOLDER, **changes):
    given = {"fiscal_year": 2026, "fiscal_quarter": 4, "actor": "operator", "reason": "fiscal year ends 12-31",
             "evidence": EVIDENCE, **changes}
    return store.label_earnings_event(event_id, **given)


def test_date_without_a_quarter_is_published_as_a_placeholder(calendar):
    run = calendar(finnhub=[_print("2027-02-18", hour="amc")])

    event = _current(calendar.store)[PLACEHOLDER]
    assert run["published"] == 1
    assert event["status"] == "planned" and event["event_date"] == "2027-02-18"
    assert "缺财季标签" in event["label"] and event["reference_period"] == ""
    assert event["payload"]["metadata"]["fiscal_label_status"] == "missing"
    assert calendar(finnhub=[_print("2027-02-18", hour="amc")])["published"] == 0      # rerun adds nothing
    assert _current(calendar.store)[PLACEHOLDER]["event_version"] == 1


def test_placeholder_is_never_released_or_routed(calendar):
    calendar(finnhub=[_print("2027-02-18")])
    store = calendar.store
    event = _current(store)[PLACEHOLDER]

    with pytest.raises(ValueError, match="fiscal label"):
        store.confirm_release(PLACEHOLDER, expected_version=event["event_version"],
                              admitted_materials=ADMITTED, source_id="test")
    for attempt in ({**event, "event_subtype": "plan"},
                    {**event, "event_subtype": "release", "status": "released"}):
        with pytest.raises(RouteError, match="lacks a fiscal label"):
            resolve_event_route(attempt, materials=ADMITTED)
    assert calendar_refresh.reconcile_earnings_release_materials(store=store) == []
    assert _current(store)[PLACEHOLDER]["status"] == "planned"


def test_label_publishes_a_manual_version_with_audit_and_retires_the_placeholder(calendar):
    calendar(finnhub=[_print("2027-02-18", hour="amc")])
    store = calendar.store

    event = _label(store)

    assert event["event_id"] == LABELLED and event["reference_period"] == "FY2026Q4"
    assert event["payload"]["source_id"] == "manual-override"
    assert event["payload"]["metadata"]["fiscal_label"] == "FY2026Q4"
    assert event["payload"]["metadata"]["labeled_from"] == f"{PLACEHOLDER}@1"
    assert event["event_date"] == "2027-02-18" and event["market_session"] == "amc"
    override = event["overrides"][0]
    assert override["actor"] == "operator" and override["evidence"] == EVIDENCE and override["active"]
    assert [s["source_id"] for s in event["sources"]] == ["finnhub_earnings_calendar"]
    assert set(_current(store)) == {LABELLED}
    old = _current(store, cancelled=True)[PLACEHOLDER]
    assert old["status"] == "cancelled" and old["payload"]["metadata"]["superseded_by"] == LABELLED
    route = resolve_event_route({**event, "event_subtype": "plan"})
    assert route.event_id == LABELLED
    released = store.confirm_release(LABELLED, expected_version=event["event_version"],
                                     admitted_materials=ADMITTED, source_id="test")
    assert released["status"] == "released" and released["payload"]["metadata"]["fiscal_label"] == "FY2026Q4"


def test_a_moved_date_without_a_quarter_follows_the_labelled_event(calendar):
    calendar(finnhub=[_print("2027-02-18")])
    _label(calendar.store)

    calendar(finnhub=[_print("2027-02-25")])

    events = _current(calendar.store)
    assert set(events) == {LABELLED}
    assert events[LABELLED]["event_date"] == "2027-02-25"
    assert events[LABELLED]["reference_period"] == "FY2026Q4"
    assert events[LABELLED]["payload"]["metadata"]["fiscal_label_status"] == "manual"


def test_a_source_that_later_states_the_quarter_replaces_the_placeholder(calendar):
    calendar(finnhub=[_print("2027-02-18")])

    calendar(finnhub=[_print("2027-02-19", year=2026, quarter=4)])

    events = _current(calendar.store)
    assert set(events) == {LABELLED}
    assert events[LABELLED]["payload"]["metadata"].get("fiscal_label_status", "") == ""


def test_a_stray_date_near_a_source_labelled_print_stays_parked(calendar):
    calendar(finnhub=[_print("2027-02-18", year=2026, quarter=4)])

    run = calendar("yahoo_earnings_calendar", finnhub=[_print("2027-02-18", year=2026, quarter=4)],
                   yahoo=[{"date": date(2027, 2, 24), "at": None}])

    assert run["quarantined"] == 1
    assert set(_current(calendar.store)) == {LABELLED}


def test_one_placeholder_per_issuer_takes_the_earliest_unlabelled_date(calendar):
    run = calendar(finnhub=[_print("2027-05-20"), _print("2027-02-18")])

    assert (run["published"], run["quarantined"]) == (1, 1)
    assert _current(calendar.store)[PLACEHOLDER]["event_date"] == "2027-02-18"


def test_the_next_unlabelled_print_reuses_a_retired_placeholder(calendar):
    calendar(finnhub=[_print("2027-02-18")])
    _label(calendar.store)

    calendar(finnhub=[_print("2027-05-13")])

    events = _current(calendar.store)
    assert set(events) == {LABELLED, PLACEHOLDER}
    assert events[PLACEHOLDER]["status"] == "planned" and events[PLACEHOLDER]["event_date"] == "2027-05-13"
    assert events[PLACEHOLDER]["quality_status"] == "ok"


def test_disagreeing_sources_must_be_settled_before_labelling(calendar):
    calendar(finnhub=[_print("2027-02-18")])
    calendar("yahoo_earnings_calendar", finnhub=[], yahoo=[{"date": date(2027, 2, 25), "at": None}])
    store = calendar.store
    assert _current(store)[PLACEHOLDER]["quality_status"] == "conflict"
    with pytest.raises(ValueError, match="disagree"):
        _label(store)

    store.override_event(PLACEHOLDER, patch={"event_date": "2027-02-25"}, actor="operator",
                         reason="issuer announcement", evidence=EVIDENCE)

    assert _label(store)["event_date"] == "2027-02-25"


@pytest.mark.parametrize("changes", [{"actor": ""}, {"reason": " "}, {"evidence": {}}, {"fiscal_quarter": 5}])
def test_label_requires_operator_reason_evidence_and_a_quarter(calendar, changes):
    calendar(finnhub=[_print("2027-02-18")])
    with pytest.raises(ValueError):
        _label(calendar.store, **changes)
    assert set(_current(calendar.store)) == {PLACEHOLDER}


def test_only_a_placeholder_can_be_labelled(calendar):
    calendar(finnhub=[_print("2027-02-18", year=2026, quarter=4)])
    with pytest.raises(ValueError, match="lacks a fiscal label"):
        _label(calendar.store, LABELLED)


def test_withdrawing_a_label_restores_the_placeholder(calendar):
    calendar(finnhub=[_print("2027-02-18")])
    store = calendar.store
    _label(store)

    store.withdraw_override(LABELLED, actor="operator", reason="wrong quarter", evidence=EVIDENCE)

    events = _current(store)
    assert set(events) == {PLACEHOLDER}
    assert events[PLACEHOLDER]["payload"]["metadata"]["fiscal_label_status"] == "missing"
    again = _label(store, fiscal_year=2027, fiscal_quarter=1)
    assert again["event_id"] == "earnings:ANT:FY2027Q1:release"


def test_register_enters_a_print_no_source_lists_and_sources_then_follow_it(calendar):
    store = calendar.store
    event = store.register_earnings_event("ant", fiscal_year=2026, fiscal_quarter=4, event_date="2027-02-18",
                                          actor="operator", reason="announced", evidence=EVIDENCE)
    assert event["event_id"] == LABELLED and event["status"] == "planned"
    assert event["overrides"][0]["evidence"] == EVIDENCE
    with pytest.raises(ValueError, match="already exists"):
        store.register_earnings_event("ANT", fiscal_year=2026, fiscal_quarter=4, event_date="2027-02-18",
                                      actor="operator", reason="again", evidence=EVIDENCE)

    calendar(finnhub=[_print("2027-02-18", hour="amc")])

    events = _current(store)
    assert set(events) == {LABELLED}
    assert events[LABELLED]["market_session"] == "amc" and events[LABELLED]["quality_status"] == "ok"

    store.register_earnings_event("SPCX", fiscal_year=2026, fiscal_quarter=4, event_date="2027-03-01",
                                  actor="operator", reason="announced", evidence=EVIDENCE)
    store.withdraw_override("earnings:SPCX:FY2026Q4:release", actor="operator", reason="entered in error",
                            evidence=EVIDENCE)
    assert "earnings:SPCX:FY2026Q4:release" not in _current(store)


def test_register_defers_to_an_open_placeholder(calendar):
    calendar(finnhub=[_print("2027-02-18")])
    with pytest.raises(ValueError, match="events label"):
        calendar.store.register_earnings_event(
            "ANT", fiscal_year=2026, fiscal_quarter=4, event_date="2027-02-18", actor="operator",
            reason="announced", evidence=EVIDENCE)


def test_placeholder_still_drives_filing_discovery(calendar):
    from ats.data.sources.sec_submissions import due_entities

    calendar(finnhub=[_print("2027-02-18")])

    assert due_entities(datetime(2027, 2, 17, 12, tzinfo=timezone.utc), days_before=3, days_after=3,
                        calendar_store=calendar.store) == ["ANT"]


def test_events_cli_labels_and_registers(calendar, monkeypatch, capsys):
    from ats.runtime import cli

    calendar(finnhub=[_print("2027-02-18")])
    monkeypatch.setattr("ats.data.stores.schedule_calendar.platform_data_db_path", lambda: calendar.store.path)

    assert cli.main(["events", "label", "--event-id", PLACEHOLDER, "--fiscal-label", "FY2026Q4",
                     "--actor", "operator", "--reason", "fiscal year ends 12-31",
                     "--evidence-json", '{"source": "424B4"}']) == 0
    assert cli.main(["events", "register", "--entity", "SPCX", "--date", "2027-03-01",
                     "--fiscal-label", "FY2026Q4", "--actor", "operator", "--reason", "announced",
                     "--evidence-json", '{"source": "announcement"}']) == 0

    assert set(_current(calendar.store)) == {LABELLED, "earnings:SPCX:FY2026Q4:release"}
    with pytest.raises(SystemExit):
        cli.main(["events", "register", "--entity", "SPCX", "--date", "2027-03-01", "--fiscal-label", "TODO",
                  "--actor", "operator", "--reason", "announced"])
    capsys.readouterr()
