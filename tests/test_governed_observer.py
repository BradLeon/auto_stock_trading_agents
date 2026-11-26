"""Governed L1 observers as chain evidence sources (hermetic: packets are hand-built, nothing is fetched).

The adapter must not recompute a trend or invent a reading: direction is the observer's own status, a gap is a gap, and
every reading arrives with its speaker, concept and a re-checkable evidence line.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ats.chain import sources
from ats.chain.corroborate import assess_layer
from ats.data.sources import governed_observer as g
from ats.memory import get_store
from ats.schemas.chain import ClaimDef, Concept, SeriesPoint, SourceDef, Witness

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
    for needle in ("22.4", "expanding", "+1.20pp", "firms", "all firms"):
        assert needle in point.evidence_span
    # The snapshot is lineage beside the reading: in the line it would make unchanged data look new every day.
    assert point.snapshot_id == "abcdef0123456789"
    assert "abcdef" not in point.evidence_span and "manifest" not in point.evidence_span


def test_routed_usage_names_its_own_snapshot():
    packet = {"status": "ok", "snapshot_manifest": {"snapshot_id": "revenue-snapshot"}, "companies": [],
              "openrouter": {"manifest": {"snapshot_id": "routed-snapshot"},
                             "facts": {"latest_complete_week": "2026-10-12", "latest_total_tokens": 1.6e14,
                                       "trend": {"status": "mixed", "change_rate": 0.338}}}}
    (point,) = g.commercialization_points(packet)
    assert point.snapshot_id == "routed-snapshot"
    assert "snapshot" not in point.evidence_span


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


def _commercialization_packet():
    return {"status": "ok", "companies": [{
        "entity_id": "openai", "company": "OpenAI", "trend": {"status": "expanding"},
        "history_rows": [{"period": "2026-09", "value_usd_bn": 50.0}]}],
        "openrouter": {"facts": {"latest_complete_week": "2026-10-12", "latest_total_tokens": 1.6e14,
                                 "trend": {"status": "mixed"}}}}


def _raw_capability_packet():
    return {"status": "ok", "claim_id": "ai_frontier_raw_capability", "b": {"status": "crossed"},
            "a": {"status": "expanding", "benchmarks": [{"current": {"release_date": "2026-10-09"}}]}}


@pytest.mark.parametrize("convert, packet", [
    (g.production_points, _production_packet), (g.commercialization_points, _commercialization_packet),
    (g.raw_capability_points, _raw_capability_packet)])
def test_no_observer_puts_the_snapshot_into_the_reading(convert, packet):
    first, second = (convert({**packet(), "snapshot_manifest": {"snapshot_id": name}})
                     for name in ("snapshot-one", "snapshot-two"))
    assert first and [p.evidence_span for p in first] == [p.evidence_span for p in second]
    assert {p.snapshot_id for p in first} == {"snapshot-one"} and {p.snapshot_id for p in second} == {"snapshot-two"}


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


# --- repeated collection: unchanged data stores nothing new and is not judged again ---------------------------------

SOURCE_ID = "l1_ai_production_adoption"
BREADTH = "enterprise_ai_adoption_breadth"
DOCUMENT = f"{SOURCE_ID}:W107_enterprise_breadth"


class _Ledger:
    """Collect one production packet per call, a day apart; `judged` records what the model would have been asked."""

    def __init__(self, monkeypatch):
        from ats.agents.evidence import adjudicator
        from ats.agents.evidence.outputs import AdjudicationView

        monkeypatch.delenv("ATS_NO_JUDGEMENT_CACHE", raising=False)
        self.monkeypatch = monkeypatch
        self.source = SourceDef(id=SOURCE_ID, label="L1 production", adapter="governed_observer",
                                entity="US_CENSUS_BTOS", entities=["US_RPS_GENAI", "RAMP_AI_INDEX"],
                                stance="regulator", observation_type="regulatory", cadence="monthly",
                                concepts=[BREADTH, "paid_ai_procurement"], direction_from=["yoy"])
        monkeypatch.setattr(sources, "load_sources", lambda: [self.source])
        self.judged: list[str] = []

        def answer(role, schema, context, **kwargs):             # stands in for the model: no request is sent
            self.judged.append(context)
            keys = [line.split("id=", 1)[1] for line in context.splitlines() if "id=" in line]
            return AdjudicationView(judgements=[
                {"cluster_key": key, "polarity": "support", "reason": "stub"} for key in keys])

        monkeypatch.setattr(adjudicator, "run_structured", answer)
        self.store = get_store()
        self.day = 0

    def collect(self, snapshot_id: str, breadth: float = 22.4) -> int:
        packet = _production_packet()
        packet["snapshot_manifest"] = {"snapshot_id": snapshot_id}
        packet["axis_overview"][0]["headline_value"] = breadth
        self.monkeypatch.setattr(sources, "fetch", lambda *a, **k: g.production_points(packet))
        self.day += 1
        return sources.collect(self.store, now=NOW + timedelta(days=self.day), source_ids={SOURCE_ID})[SOURCE_ID]

    def judge(self):
        claim = ClaimDef(id="production", kind="common", layer="L1_app", statement="adoption is widening",
                         concepts=[Concept(key=BREADTH, desc="breadth"),
                                   Concept(key="paid_ai_procurement", desc="paid")])
        rows = {entity: self.store.observations(entity=entity, limit=200)
                for entity in (self.source.entity, *self.source.entities)}
        return assess_layer(SimpleNamespace(claims=[claim]), rows)

    def sizes(self) -> dict[str, int]:
        conn = self.store.data_store().conn
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in
                ("data_document_versions", "data_document_publications", "data_evidence_facts",
                 "data_evidence_projections", "data_measurement_points")}

    def cited_body(self) -> str:
        """Body of the document version the live breadth fact cites."""
        (fact,) = self.store.facts(document_id=DOCUMENT)
        row = self.store.data_store().conn.execute(
            "SELECT text FROM data_document_chunks WHERE version_id=? ORDER BY ordinal",
            (fact["document_version_id"],)).fetchone()
        return row[0]


def test_unchanged_data_under_a_new_snapshot_adds_no_version_and_hits_the_judgement_cache(monkeypatch):
    ledger = _Ledger(monkeypatch)
    assert ledger.collect("snapshot-day-1") == 3
    ledger.judge()
    before = ledger.sizes()

    assert ledger.collect("snapshot-day-2") == 0
    (assessment,) = ledger.judge()

    assert ledger.sizes() == before
    assert len(ledger.judged) == 1                                   # the second review asked the model nothing
    assert assessment.support_score > 0 and "snapshot" not in ledger.judged[0]


def test_a_changed_reading_adds_a_version_and_is_judged_again(monkeypatch):
    ledger = _Ledger(monkeypatch)
    ledger.collect("snapshot-day-1")
    ledger.judge()
    before = ledger.sizes()

    ledger.collect("snapshot-day-2", breadth=23.1)
    ledger.judge()

    after = ledger.sizes()
    for table in ("data_document_versions", "data_evidence_facts", "data_measurement_points"):
        assert after[table] == before[table] + 1, table              # only the axis that moved
    assert len(ledger.judged) == 2 and "23.1" in ledger.judged[1]
    assert "23.1" in ledger.cited_body()


def test_a_reading_that_returns_to_an_earlier_value_cites_its_own_body(monkeypatch):
    """A -> B -> A: version A already exists and B is the latest, so without a new version the fact would cite B."""
    ledger = _Ledger(monkeypatch)
    ledger.collect("snapshot-day-1", breadth=22.4)
    ledger.judge()
    ledger.collect("snapshot-day-2", breadth=23.1)
    ledger.judge()
    replaced = ledger.store.latest_document_version(DOCUMENT)["version_id"]

    ledger.collect("snapshot-day-3", breadth=22.4)
    ledger.judge()
    body = ledger.cited_body()
    assert "22.4" in body and "23.1" not in body and f"取代版本 {replaced}" in body
    assert len(ledger.store.document_versions(DOCUMENT)) == 3
    assert len(ledger.judged) == 2                                   # same evidence as day 1: answered from cache

    before = ledger.sizes()
    ledger.collect("snapshot-day-4", breadth=22.4)                   # still A: nothing new
    assert ledger.sizes() == before

    for day, value in ((5, 23.1), (6, 23.1), (7, 22.4)):             # and it keeps working when it flips again
        ledger.collect(f"snapshot-day-{day}", breadth=value)
        assert str(value) in ledger.cited_body()
    assert len(ledger.store.document_versions(DOCUMENT)) == 5


def test_every_snapshot_stays_traceable_from_the_ledger(monkeypatch):
    ledger = _Ledger(monkeypatch)
    ledger.collect("snapshot-day-1")
    ledger.collect("snapshot-day-2")

    notes = [json.loads(row[0])["note"] for row in ledger.store.data_store().conn.execute(
        "SELECT metadata_json FROM data_document_publications WHERE document_id=?", (DOCUMENT,))]
    assert notes == ["governed_snapshot=snapshot-day-1"]             # the snapshot a version was first read from
    runs = [row["note"] for row in ledger.store.ingestion_history(SOURCE_ID)]
    assert sorted(runs) == ["governed_snapshot=snapshot-day-1", "governed_snapshot=snapshot-day-2"]
    payloads = [row[0] for row in ledger.store.data_store().conn.execute(
        "SELECT raw_payload FROM data_measurement_points")]
    assert payloads and not any("snapshot" in payload for payload in payloads)


def test_a_series_with_two_bases_per_document_keeps_publishing_both_bodies(monkeypatch):
    """yoy and mom share one document id: neither is "restored", and repeating the print adds no version."""
    source = SourceDef(id="tw_ic_exports", label="TW", adapter="tw_mof", entity="TW_IC_EXPORT", stance="regulator",
                       observation_type="regulatory", cadence="monthly", concepts=["supply_tightness"],
                       direction_from=["yoy", "mom"])
    monkeypatch.setattr(sources, "load_sources", lambda: [source])
    monkeypatch.setattr(sources, "fetch",
                        lambda *a, **k: [SeriesPoint(period="2026-06", value=100.0, yoy=0.3, mom=-0.05)])
    store = get_store()
    for day in (1, 2, 3):
        sources.collect(store, now=NOW + timedelta(days=day), source_ids={"tw_ic_exports"})
    assert len(store.document_versions("tw_ic_exports:2026-06")) == 2
    bodies = [row[0] for row in store.data_store().conn.execute("SELECT text FROM data_document_chunks")]
    assert bodies and not any("取代版本" in body for body in bodies)


def test_the_judge_receives_clusters_in_one_order_whatever_the_process():
    """The order is part of the prompt and so of the cache key; a set of names iterates differently per process."""
    names = [f"WITNESS_{letter}" for letter in "HCAGEBFD"]
    claim = ClaimDef(id="order", kind="common", layer="L1_app", concepts=[Concept(key=BREADTH, desc="breadth")],
                     witnesses=[Witness(entity=name, stance="regulator") for name in names])
    rows = {name: [{"id": name, "entity": name, "source_entity": name, "concept": BREADTH, "direction": "up",
                    "observation_type": "regulatory", "period": "2026-08", "evidence_span": name,
                    "observed_at": NOW.isoformat()}] for name in names}
    seen: list[str] = []
    assess_layer(SimpleNamespace(claims=[claim]), rows,
                 judge=lambda _claim, clusters: seen.extend(c.speaker for c in clusters) or [])
    assert seen == sorted(names)
