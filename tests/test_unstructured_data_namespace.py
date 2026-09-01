"""Compatibility and boundary tests for the unified unstructured namespace."""


def test_unstructured_adapters_reexport_existing_provider_contracts():
    from ats.data.adapters.unstructured import articles, news, research, sec, transcripts
    from ats.data import news as legacy_news
    from ats.data import research as legacy_research
    from ats.data import sec as legacy_sec
    from ats.data import transcript as legacy_transcript

    assert sec.earnings_release_result is legacy_sec.earnings_release_result
    assert transcripts.fetch is legacy_transcript.fetch
    assert research.fetch_batch is legacy_research.fetch_batch
    assert news.fetch_news is legacy_news.fetch_news
    assert hasattr(articles, "semianalysis")
    assert hasattr(articles, "trendforce")


def test_unstructured_pipeline_and_store_surfaces_reexport_single_implementation():
    from ats.data.admission import admit as legacy_admit
    from ats.data.document_assets import ingest as legacy_ingest
    from ats.data.pipelines.unstructured.admission import admit
    from ats.data.pipelines.unstructured.documents import ingest
    from ats.data.products import UnstructuredDataProducts
    from ats.data.stores.unstructured.documents import store
    from ats.data.products import DataProducts

    assert admit is legacy_admit
    assert ingest is legacy_ingest
    assert UnstructuredDataProducts is DataProducts
    assert callable(store)
