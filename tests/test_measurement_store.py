"""Structured raw observations: immutable vintages and point-in-time truth."""

import json
from datetime import date, datetime, timedelta, timezone

from ats.chain import sources
from ats.memory import get_store
from ats.schemas.chain import SeriesPoint, SourceDef

T0 = datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc)


def _source(**kw):
    base = dict(id="model_price", label="Model token price", adapter="fixture",
                entity="OPENROUTER", cadence="daily", concepts=["token_economics"],
                direction_from=["mom"])
    return SourceDef(**{**base, **kw})


def test_raw_points_exclude_derivatives_and_are_idempotent():
    store = get_store()
    source = _source()
    point = SeriesPoint(period="2026-08-19", series="model-a/input", value=2.5,
                        unit="USD/M", yoy=0.40, mom=-0.10,
                        published_at=date(2026, 8, 19))

    assert store.save_measurement_points(source, [point], fetched_at=T0) == 1
    assert store.save_measurement_points(source, [point], fetched_at=T0) == 0

    rows = store.measurements(source_id=source.id)
    assert len(rows) == 1 and rows[0]["value"] == 2.5
    raw = json.loads(rows[0]["raw_payload"])
    assert "yoy" not in raw and "mom" not in raw


def test_revisions_coexist_and_as_of_does_not_look_ahead():
    store = get_store()
    source = _source()
    original = SeriesPoint(period="2026-07", series="penetration", value=20.0,
                           unit="%", published_at=date(2026, 8, 1))
    revised = original.model_copy(update={"value": 22.0})
    store.save_measurement_points(source, [original], fetched_at=T0)
    store.save_measurement_points(source, [revised], fetched_at=T0 + timedelta(days=2))

    latest = store.measurements(source_id=source.id, series="penetration")
    historical = store.measurements(
        source_id=source.id, series="penetration", as_of=T0 + timedelta(hours=1))
    vintages = store.measurements(source_id=source.id, series="penetration",
                                  latest_only=False)

    assert [r["value"] for r in latest] == [22.0]
    assert [r["value"] for r in historical] == [20.0]
    assert {r["value"] for r in vintages} == {20.0, 22.0}
    assert store.measurements(source_id=source.id, as_of=T0 - timedelta(seconds=1)) == []


def test_collect_records_source_health_and_raw_points_before_evidence(monkeypatch):
    source = _source()
    points = [SeriesPoint(period="2026-08-18", series="model-a/input", value=3.0,
                          unit="USD/M", mom=-0.25)]
    monkeypatch.setattr(sources, "load_sources", lambda: [source])
    monkeypatch.setattr(sources, "fetch", lambda *a, **k: points)
    store = get_store()

    result = sources.collect(store, now=T0)

    assert result[source.id] == 1
    assert [r["value"] for r in store.measurements(source_id=source.id)] == [3.0]
    run = store.ingestion_history(source.id)[0]
    assert run["status"] == "succeeded"
    assert run["discovered"] == 1 and run["accepted"] == 1


def test_unreachable_source_has_a_distinct_ingestion_status(monkeypatch):
    source = _source()
    monkeypatch.setattr(sources, "load_sources", lambda: [source])
    monkeypatch.setattr(sources, "fetch", lambda *a, **k: [])
    store = get_store()

    assert sources.collect(store, now=T0) == {source.id: -1}
    run = store.ingestion_history(source.id)[0]
    assert run["status"] == "unreachable" and run["accepted"] == 0
