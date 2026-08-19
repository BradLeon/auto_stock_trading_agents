"""Stable, consumer-facing contracts over the shared data platform."""

from datetime import date, datetime, timezone

from ats.data import source_cache
from ats.data_platform import DataProducts
from ats.memory import get_store
from ats.schemas.chain import Observation, SeriesPoint, SourceDef


NOW = datetime(2026, 8, 19, 2, 0, tzinfo=timezone.utc)


def _source() -> SourceDef:
    return SourceDef(
        id="ai_penetration", label="AI penetration", adapter="fixture",
        entity="AMD", cadence="quarterly", concepts=["ai_adoption"],
        direction_from=["mom"],
    )


def _document(store):
    text = (
        "Management said enterprise AI inference demand accelerated during the quarter. "
        "The cited customer program expanded to two additional regions. "
    ) * 30
    doc = source_cache.store(
        "AMD", "2026Q2", "transcript", text, source="company:earnings",
        source_url="https://example.test/amd-q2", external_id="call-2026-q2",
        title="AMD 2026 Q2 earnings call", published_at=NOW.isoformat(), min_chars=1,
    )
    assert doc is not None
    store.save_document(doc)
    return doc


def test_document_search_returns_exact_version_and_source_location():
    store = get_store()
    doc = _document(store)
    products = DataProducts(store)

    rows = products.search_documents(
        "inference demand", entity="amd", source_contains="earnings",
        published_since="2026-01-01",
    )

    assert rows
    hit = rows[0]
    assert hit["document_id"] == doc.document_id
    assert hit["version_id"] == store.latest_document_version(doc.document_id)["version_id"]
    assert hit["char_start"] < hit["char_end"]
    assert "inference demand" in hit["text"]


def test_indicator_series_hides_storage_tables_and_honours_filters():
    store = get_store()
    source = _source()
    store.register_data_source(source, at=NOW)
    store.save_measurement_points(source, [
        SeriesPoint(period="2026Q1", series="industry", value=18.0, unit="%",
                    published_at=date(2026, 4, 15)),
        SeriesPoint(period="2026Q2", series="industry", value=21.0, unit="%",
                    published_at=date(2026, 7, 15)),
    ], fetched_at=NOW)

    rows = DataProducts(store).indicator_series(
        source_id="ai_penetration", series="industry", entity="amd", since="2026Q2",
    )

    assert [(r["period"], r["value"], r["unit"]) for r in rows] == [
        ("2026Q2", 21.0, "%")
    ]


def test_claim_package_and_lineage_resolve_to_the_exact_document_version():
    store = get_store()
    doc = _document(store)
    store.save_observation(Observation(
        id="obs-product", document_id=doc.document_id,
        source_url=doc.source_url, entity="AMD", source_entity="AMD",
        metric="enterprise_inference_demand", concept="ai_adoption", period="2026Q2",
        observation_type="reported_actual", stance="supplier", direction="up",
        value=None, unit="", evidence_span="enterprise AI inference demand accelerated",
        observed_at=NOW,
    ))
    products = DataProducts(store)

    package = products.claim_evidence_package("ai_adoption")
    assert package["missing_facts"] == []
    evidence = package["evidence"][0]
    assert evidence["fact"]["document_id"] == doc.document_id

    lineage = products.lineage(evidence["projection_id"])
    assert lineage is not None
    assert lineage["document_version"]["content_hash"] == doc.sha256
    assert lineage["document"]["source_url"] == doc.source_url


def test_company_package_keeps_shared_facts_and_task_views_separate():
    store = get_store()
    doc = _document(store)
    version = store.latest_document_version(doc.document_id)
    store.save_task_projection(
        profile="pead_research", profile_version="prompt-v3",
        input_kind="document_version", input_ref=version["version_id"],
        target_type="entity", target_id="AMD", payload={"summary": "Demand improved"},
        created_at=NOW.isoformat(),
    )

    package = DataProducts(store).company_research_package("amd")

    assert package["entity"] == "AMD"
    assert package["facts"] == []
    assert package["pead_projections"][0]["profile_version"] == "prompt-v3"


def test_health_summarises_source_runs_and_document_processing():
    store = get_store()
    source = _source()
    store.register_data_source(source, at=NOW)
    run_id = store.begin_ingestion(source.id, kind="structured", at=NOW)
    store.finish_ingestion(run_id, status="unreachable", note="provider timeout", at=NOW)
    doc = _document(store)
    version_id = store.begin_document_processing(doc.document_id, "pead", "prompt-v1")
    assert version_id
    store.finish_document_processing(
        version_id, "pead", "prompt-v1", ok=False, note="model timeout", at=NOW.isoformat())

    health = DataProducts(store).health()

    assert health["structured_sources"][0]["status"] == "unreachable"
    assert health["document_sources"][0]["documents"] == 1
    assert health["processing"] == {
        "total": 1, "running": 0, "failed": 1, "succeeded": 0,
    }


def test_data_cli_exposes_products_as_json(capsys):
    import json

    from ats.runtime.cli import main

    store = get_store()
    _document(store)

    assert main(["data", "search", "inference demand", "--entity", "AMD"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["entity"] == "AMD"
    assert payload[0]["version_id"]
