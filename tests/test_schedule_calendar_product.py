from datetime import datetime, timedelta, timezone

from ats.data.products.calendar import ScheduleCalendarProduct
from ats.data.stores.schedule_calendar import ScheduleCalendarStore, ScheduleEventCandidate


def test_one_fresh_source_does_not_hide_another_stale_source(tmp_path):
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    now = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    for source, stamp in (("bls", now - timedelta(days=3)), ("bea", now)):
        run = store.record_source_run(source, at=stamp)
        store.finish_source_run(run, status="complete", discovered=0, published=0, at=stamp)
    result = ScheduleCalendarProduct(store).snapshot(as_of=now)
    assert result["quality"]["status"] == "stale"
    assert result["quality"]["source_freshness"]["bls"]["status"] == "stale"
    assert result["quality"]["source_freshness"]["bea"]["status"] == "ok"


def test_calendar_product_exposes_as_of_lineage_quality_and_refresh_freshness(tmp_path):
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    candidate = ScheduleEventCandidate(
        source_id="bls", source_url="https://www.bls.gov/schedule/news_release/cpi.htm",
        source_event_ref="cpi-2026-09", event_type="cpi",
        stable_identity="macro:CPI:2026-08:initial", label="August CPI",
        reference_period="2026-08", event_date="2026-09-11", local_time="08:30",
        timezone="America/New_York", time_precision="minute", market_session="intraday")
    stored = store.submit_candidate(candidate, at=now)
    store.publish_candidate(stored["candidate_id"], at=now)
    source_run = store.record_source_run("bls", at=now)
    store.finish_source_run(source_run, status="complete", discovered=1, published=1, at=now)
    product = ScheduleCalendarProduct(store)
    snapshot = product.snapshot(as_of=now)
    assert snapshot["product"] == "schedule_calendar"
    assert snapshot["quality"]["status"] == "ok"
    assert snapshot["events"][0]["status"] == "planned"
    assert snapshot["lineage"]["event_sources"][snapshot["events"][0]["event_id"]][0][
        "source_id"] == "bls"


def test_calendar_product_fails_quality_closed_on_stale_or_conflicted_calendar(tmp_path):
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    candidate = ScheduleEventCandidate(source_id="bls", event_type="cpi",
                                       stable_identity="macro:CPI:2026-08:initial",
                                       event_date="2026-09-11", time_precision="date")
    first = store.submit_candidate(candidate, at=now)
    store.publish_candidate(first["candidate_id"], at=now)
    second = store.submit_candidate(candidate.model_copy(update={"source_id": "mirror",
                                                                  "event_date": __import__(
                                                                      "datetime").date(2026, 9, 12)}),
                                     at=now)
    assert second["review_status"] == "conflict"
    snapshot = ScheduleCalendarProduct(store).snapshot(as_of=now)
    assert snapshot["quality"]["status"] == "conflict"
    assert snapshot["quality"]["conflicting_candidates"] == 1


def test_calendar_quality_is_stale_without_successful_refresh(tmp_path):
    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    snapshot = ScheduleCalendarProduct(store).snapshot()
    assert snapshot["quality"]["status"] == "stale"


def test_calendar_quality_ignores_future_failure_and_clears_after_recovery(tmp_path):
    from datetime import timedelta

    store = ScheduleCalendarStore(tmp_path / "calendar.sqlite")
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    first = store.record_source_run("bls", at=now)
    store.finish_source_run(first, status="complete", at=now)
    failed = store.record_source_run("bls", at=now + timedelta(hours=1))
    store.finish_source_run(failed, status="failed", error_code="HTTP403",
                            at=now + timedelta(hours=1))
    recovered = store.record_source_run("bls", at=now + timedelta(hours=2))
    store.finish_source_run(recovered, status="complete", at=now + timedelta(hours=2))
    product = ScheduleCalendarProduct(store)
    historical = product.snapshot(as_of=now)
    assert historical["quality"]["status"] == "ok"
    assert len(historical["lineage"]["source_runs"]) == 1
    assert product.snapshot(as_of=now + timedelta(hours=1))["quality"]["status"] == "degraded"
    assert product.snapshot(as_of=now + timedelta(hours=2))["quality"]["status"] == "ok"
