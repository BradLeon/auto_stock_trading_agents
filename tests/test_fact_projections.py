"""Shared facts stay neutral while task interpretations remain versioned."""

from datetime import datetime, timezone

from ats.data import research as research_data
from ats.data import source_cache
from ats.memory import get_store
from ats.schemas.chain import Observation
from ats.schemas.research import Insight

NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)


def _observation(*, row_id: str, concept: str, direction: str = "up") -> Observation:
    return Observation(
        id=row_id, document_id="SEMIANALYSIS:article-1:article",
        source_url="https://example.test/article-1", entity="AMD",
        source_entity="SEMIANALYSIS", metric="customer_configuration_change",
        concept=concept, period="2026Q3", observation_type="research",
        stance="regulator", direction=direction, value=None, unit="",
        evidence_span="Meta requested a lower-HBM configuration for the named program.",
        observed_at=NOW)


def test_multiple_task_links_share_one_neutral_fact():
    store = get_store()
    store.save_observation(_observation(row_id="obs-a", concept="customer_mix"))
    store.save_observation(_observation(row_id="obs-b", concept="hbm_demand",
                                        direction="down"))

    facts = store.facts(entity="AMD")
    projections = store.fact_projections(profile="evidence_observer")

    assert len(facts) == 1
    assert "concept" not in facts[0] and "direction" not in facts[0]
    assert {p["concept"] for p in projections} == {"customer_mix", "hbm_demand"}
    assert {p["direction"] for p in projections} == {"up", "down"}
    assert {p["fact_id"] for p in projections} == {facts[0]["fact_id"]}


def test_reprocessing_retires_old_fact_views_but_keeps_history():
    store = get_store()
    obs = _observation(row_id="obs-a", concept="customer_mix")
    store.save_observation(obs)
    assert store.supersede_document_observations(obs.document_id, "SEMIANALYSIS", at=NOW) == 1
    assert store.facts(entity="AMD") == []
    assert store.fact_projections(profile="evidence_observer") == []

    store.save_observation(obs)
    assert len(store.facts(entity="AMD")) == 1
    assert len(store.facts(entity="AMD", include_superseded=True)) == 1
    assert len(store.fact_projections(profile="evidence_observer")) == 1


def test_pead_insight_is_a_projection_of_the_exact_document_version():
    store = get_store()
    article_id = "imap:<message-1@example.test>"
    doc = source_cache.store(
        "SEMIANALYSIS", research_data.article_slug(article_id), "article",
        "A source paragraph with enough meaning for a consumer.", source="newsletter:SemiAnalysis",
        external_id=article_id, title="Customer configuration", published_at=NOW.isoformat(),
        min_chars=1)
    assert doc is not None
    store.save_document(doc)
    insight = Insight(article_id=article_id, ticker="AMD", direction="bearish",
                      impact_path="demand", summary="Lower configuration may reduce value",
                      evidence_quote="requested a lower configuration", confidence=0.8)

    store.save_insights(article_id, [insight], profile_version="prompt-v2")

    rows = store.task_projections(profile="pead_research", target_id="AMD")
    assert len(rows) == 1
    assert rows[0]["profile_version"] == "prompt-v2"
    assert rows[0]["input_kind"] == "document_version"
    assert rows[0]["input_ref"] == store.latest_document_version(doc.document_id)["version_id"]


def test_projection_versions_coexist_without_overwrite():
    store = get_store()
    for version in ("v1", "v2"):
        store.save_task_projection(
            profile="pead_research", profile_version=version,
            input_kind="document_version", input_ref="doc@hash",
            target_type="entity", target_id="AMD", payload={"version": version},
            created_at=NOW.isoformat())

    assert {r["profile_version"] for r in store.task_projections(
        profile="pead_research", target_id="AMD")} == {"v1", "v2"}
