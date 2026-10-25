"""Governed L1 observers as chain evidence sources (hermetic: packets are hand-built, nothing is fetched).

The adapter must not recompute a trend or invent a reading: direction is the observer's own status, a gap is a gap, and
every reading arrives with its speaker, concept and a re-checkable evidence line.
"""

from datetime import datetime, timezone

from ats.chain import sources
from ats.data.sources import governed_observer as g
from ats.memory import get_store
from ats.schemas.chain import SourceDef

NOW = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)


def _production_packet():
    axis = lambda axis_id, status, value, **kw: {            # noqa: E731
        "axis_id": axis_id, "axis_label": axis_id, "source_id": f"src_{axis_id}", "trend_status": status,
        "headline_value": value, "headline_unit": "percent", "period": "2026-08",
        "statistical_unit": "firms", "denominator": "all firms", **kw}
    return {"status": "ok", "snapshot_manifest": {"snapshot_id": "abcdef0123456789"}, "axis_overview": [
        axis("enterprise_breadth", "expanding", 22.4, period="107", trend_net_change_pp=1.2),
        axis("worker_persistence", "stable", 39.2),
        axis("paid_procurement", "contracting", 56.1),
        axis("task_production", "insufficient_history", 68.7)]}


def test_direction_is_the_observers_own_status_and_gaps_produce_no_point():
    points = {p.concept: p for p in g.production_points(_production_packet())}
    assert points["enterprise_ai_adoption_breadth"].direction == "up"
    assert points["worker_ai_usage_persistence"].direction == "flat"
    assert points["paid_ai_procurement"].direction == "down"
    # insufficient history is a gap, not "flat": the axis simply produces nothing
    assert "task_production_depth" not in points


def test_each_reading_carries_its_speaker_and_a_recheckable_span():
    point = next(p for p in g.production_points(_production_packet()) if p.concept == "enterprise_ai_adoption_breadth")
    assert (point.entity, point.stance, point.observation_type) == ("US_CENSUS_BTOS", "regulator", "regulatory")
    assert point.period == "W107"
    for needle in ("22.4", "expanding", "+1.20pp", "firms", "all firms", "abcdef012345"):
        assert needle in point.evidence_span


def test_revenue_is_spoken_by_the_identity_that_reported_it():
    packet = {"status": "ok", "companies": [{
        "entity_id": "openai", "company": "OpenAI", "trend": {
            "status": "expanding", "comparable_point_count": 5, "first_period": "2025-12", "last_period": "2026-09"},
        "history_rows": [
            {"period": "2026-06", "period_start": "2026-06-01", "value_usd_bn": 30.0,
             "observation_identity": "third_party_estimate", "source_id": "sacra_public_company_profiles"},
            {"period": "2026-09", "period_start": "2026-09-01", "value_usd_bn": 50.0,
             "observation_identity": "third_party_estimate", "source_id": "sacra_public_company_profiles"}]}]}
    (point,) = g.commercialization_points(packet)
    assert (point.entity, point.source_entity, point.value, point.period) == ("OPENAI", "SACRA", 50.0, "2026-09")
    assert (point.stance, point.observation_type, point.direction) == ("regulator", "research", "up")


def test_a_company_without_a_governed_trend_produces_nothing():
    packet = {"status": "ok", "companies": [{
        "entity_id": "anthropic", "trend": {"status": "insufficient_history"},
        "history_rows": [{"period": "2026-07", "value_usd_bn": 65.0}]}]}
    assert g.commercialization_points(packet) == []


def test_raw_capability_counts_come_from_the_packet():
    packet = {"status": "ok", "claim_id": "ai_frontier_raw_capability",
              "a": {"status": "confirmed_expansion", "benchmarks": [
                  {"status": "confirmed_expansion", "current": {"release_date": "2026-10-09"}},
                  {"status": "not_extended", "current": {"release_date": "2026-09-01"}}]},
              "b": {"status": "not_crossed", "benchmarks": [{"status": "confirmed_crossing", "current": {}}]}}
    points = {p.concept: p for p in g.raw_capability_points(packet)}
    assert points["capability_frontier_extension"].value == 1.0
    assert points["capability_frontier_extension"].direction == "up"
    assert points["capability_threshold_crossing"].direction == "flat"
    assert {p.period for p in points.values()} == {"2026-10"}


def test_an_observer_that_is_not_ok_yields_no_points(monkeypatch):
    monkeypatch.setattr(g, "_observe", lambda observer: {"status": "unavailable"})
    assert g.fetch(observer=g.PRODUCTION) == []


def test_collect_publishes_the_reading_each_observation_cites(monkeypatch):
    """The evidence store only admits a fact that cites a published document version; for a governed reading the
    document is the reading line itself, and a changed print appends a version instead of rewriting the old one."""
    source = SourceDef(id="l1_ai_production_adoption", label="L1 production", adapter="governed_observer",
                       entity="US_CENSUS_BTOS", entities=["US_RPS_GENAI", "RAMP_AI_INDEX"], stance="regulator",
                       observation_type="regulatory", cadence="monthly",
                       concepts=["enterprise_ai_adoption_breadth", "paid_ai_procurement"], direction_from=["yoy"])
    points = g.production_points(_production_packet())
    monkeypatch.setattr(sources, "load_sources", lambda: [source])
    monkeypatch.setattr(sources, "fetch", lambda *a, **k: points)
    store = get_store()
    source_ids = {"l1_ai_production_adoption"}
    assert sources.collect(store, now=NOW, source_ids=source_ids)["l1_ai_production_adoption"] == 3

    rows = {r["concept"]: r for r in store.observations(entity="US_CENSUS_BTOS", limit=20)}
    row = rows["enterprise_ai_adoption_breadth"]
    version = store.latest_document_version(row["document_id"])
    assert version and version["fetched_at"] <= NOW.isoformat()

    again = sources.collect(store, now=NOW, source_ids=source_ids)
    assert again["l1_ai_production_adoption"] == 0                  # unchanged reading: nothing new, nothing lost
    assert len(store.document_versions(row["document_id"])) == 1
