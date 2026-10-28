"""Preview admission, immutable metadata revisions, and extraction cache evidence."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ats.data.articles.material_quality import completeness_assessment, interpretation_fingerprint
from ats.data.pipelines.unstructured.article_metadata import reconcile_article_metadata
from ats.data.stores.unstructured import PlatformUnstructuredRepository
from ats.data import document_assets

NOW = datetime(2026, 10, 12, tzinfo=UTC)
FACTS = ("Microsoft increased capacity by 10%. Nvidia supplied GPUs for the new sites. "
         "Additional power capacity is expected next quarter. ") * 4


def test_substantive_short_preview_and_long_marketing_are_distinct():
    preview = FACTS + "Subscribe to unlock the rest"
    assert len(preview) < 2000
    assert completeness_assessment(preview)["completeness"] == "partial"
    marketing = "Join our exclusive community for more exciting insights. " * 100
    assert completeness_assessment(marketing + "Subscribe to unlock the rest")["completeness"] == "teaser"


@pytest.mark.parametrize("quoted", [
    'The author quotes an advertisement: "Subscribe to unlock the rest". End of discussion.',
    '> Subscribe to unlock the rest\nEnd of discussion.',
    'Related article: Subscribe to unlock the rest. End of discussion.',
])
def test_other_post_and_explicit_quote_do_not_prove_this_post_is_truncated(quoted):
    result = completeness_assessment(FACTS + "\n" + quoted)
    assert result["completeness"] == "full" and result["ignored_markers"]


def test_weak_access_hint_and_old_partial_are_not_promoted_to_full():
    result = completeness_assessment(FACTS + " Read the full post")
    assert result["completeness"] == "unknown" and result["conflict"]
    assert completeness_assessment(FACTS, stored="partial")["completeness"] == "partial"


def test_stored_partial_with_only_a_teaser_is_filtered_without_an_access_marker():
    result = completeness_assessment('Microsoft: subscribe for our exclusive analysis.', stored='partial')
    assert result['completeness'] == 'teaser'
    assert result['reason'] == 'insufficient_visible_preview'


def test_short_partial_admission_uses_explicit_source_policy_and_teaser_never_passes():
    from ats.data.pipelines.unstructured import article_ingest as ingest
    from ats.data.pipelines.unstructured.source_acceptance import load_policy
    from ats.schemas.chain import ArticleRef

    source = ingest._article_sources()["semianalysis"]
    policy = load_policy(source.id)
    ref = ArticleRef(url="https://fixture/article", slug="a", title="Capacity report", published_at=NOW.date())
    body = FACTS + "Subscribe to unlock the rest"
    material = ingest.Material(ref, body, {"completeness": "full"})
    candidate = ingest._candidate(source, material, body, discovered_at=NOW, policy=policy)
    assert candidate.min_chars == 400 and candidate.completeness == "partial"
    validation, state = ingest._admission(candidate, material, source=source, policy=policy,
        human_review_approved=False, source_eligible=True)
    assert state == "accepted"
    teaser = ingest.Material(ref, "Join the community. " * 300 + "Subscribe to unlock the rest", {})
    candidate = ingest._candidate(source, teaser, teaser.body, discovered_at=NOW, policy=policy)
    _, state = ingest._admission(candidate, teaser, source=source, policy=policy,
        human_review_approved=False, source_eligible=True)
    assert state == "quarantined"


def test_stored_full_repair_keeps_body_and_historical_metadata(tmp_path):
    repo = PlatformUnstructuredRepository(tmp_path / "repair.sqlite", writable=True)
    old = NOW - timedelta(days=2)
    body = FACTS + "Subscribe to unlock the rest"
    try:
        doc = document_assets.ingest(entity="SEMIANALYSIS", key="repair", doc_type="research_article",
            text=body, source="semianalysis", external_id="a", title="", published_at="",
            min_chars=1, now=old, store=repo)
        before = repo.document_materials_at(as_of=old)[0]
        preview = reconcile_article_metadata("semianalysis", now=NOW, repository=repo)
        assert preview["rows"][0]["metadata"]["completeness"] == "partial"
        assert repo.document_materials_at(as_of=NOW)[0]["completeness"] == "full"
        result = reconcile_article_metadata("semianalysis", now=NOW, repository=repo, dry_run=False)
        assert result["rows"][0]["status"] == "revised"
        after = repo.document_materials_at(as_of=NOW)[0]
        assert after["completeness"] == "partial" and "MSFT" in after["related_entities"]
        assert after["published_at"] == "" and result["rows"][0]["gaps"] == ["title", "published_at"]
        assert repo.document_materials_at(as_of=old)[0]["completeness"] == "full"
        assert before["version"]["version_id"] == after["version"]["version_id"]
        assert document_assets.read_document(doc.document_id, store=repo) == body
        assert len(repo.document_versions(doc.document_id)) == 1
        again = reconcile_article_metadata("semianalysis", now=NOW+timedelta(hours=1),
            repository=repo, dry_run=False)
        assert again["rows"][0]["status"] == "no_change"
    finally:
        repo.close()


def test_field_recovery_requires_exact_version_and_body_span(tmp_path):
    repo = PlatformUnstructuredRepository(tmp_path / "fields.sqlite", writable=True)
    body = "Nvidia expands capacity\n" + FACTS
    try:
        doc = document_assets.ingest(entity="SEMIANALYSIS", key="fields", doc_type="research_article",
            text=body, source="semianalysis", min_chars=1, now=NOW-timedelta(days=1), store=repo)
        version = repo.latest_document_version(doc.document_id)["version_id"]
        evidence = {doc.document_id: {"title": {"version_id": version, "start": 0,
            "end": len("Nvidia expands capacity"), "text": "Nvidia expands capacity"}}}
        result = reconcile_article_metadata("semianalysis", now=NOW, repository=repo,
            dry_run=False, field_evidence=evidence)
        assert result["rows"][0]["metadata"]["title"] == "Nvidia expands capacity"
        assert result["rows"][0]["gaps"] == ["published_at"]
        evidence[doc.document_id]["title"]["version_id"] = "wrong-version"
        with pytest.raises(ValueError, match="version mismatch"):
            reconcile_article_metadata("semianalysis", now=NOW, repository=repo,
                field_evidence=evidence)
    finally:
        repo.close()


def test_legacy_index_history_is_preserved_exactly_before_first_metadata_revision(tmp_path):
    repo = PlatformUnstructuredRepository(tmp_path / "legacy.sqlite", writable=True)
    old = NOW-timedelta(days=1)
    try:
        doc = document_assets.ingest(entity="SEMIANALYSIS", key="legacy", doc_type="research_article",
            text=FACTS + "Subscribe to unlock the rest", source="semianalysis", min_chars=1,
            now=old, store=repo)
        document_assets.ingest(entity="SEMIANALYSIS", key="same-time-peer", doc_type="research_article",
            text=FACTS, source="semianalysis", min_chars=1, now=old, store=repo)
        # The migrated legacy store has a body version and index, but no publication snapshot.
        repo.conn.execute("DELETE FROM data_document_publications")
        repo.conn.commit()
        original = repo.document_materials_at(as_of=old)
        assert original[0]["metadata_history"] == "legacy_current_only"
        reconcile_article_metadata("semianalysis", now=NOW, repository=repo, dry_run=False)
        assert repo.document_materials_at(as_of=old) == original
        current = next(row for row in repo.document_materials_at(as_of=NOW)
                       if row["document_id"] == doc.document_id)
        assert current["completeness"] == "partial"
        assert current["metadata_history"] == "publication_snapshot"
    finally:
        repo.close()


def test_extraction_quotes_must_resolve_inside_visible_input(monkeypatch):
    from ats.agents.information import extract
    from ats.schemas.research import Article

    art = Article(id="doc", source="semianalysis", title="Preview", body="\u034f\u00ad" + FACTS,
                  published_at=NOW, completeness="partial", document_version_id="doc@body")
    valid = SimpleNamespace(ticker="MSFT", direction="neutral", impact_path="direct",
                            summary="Capacity changed", evidence_quote="Microsoft increased capacity", confidence=.8)
    invented = SimpleNamespace(**{**vars(valid), "evidence_quote": "Hidden paid-section forecast"})
    monkeypatch.setattr(extract, "run_structured", lambda *a, **k: SimpleNamespace(insights=[valid, invented]))
    insights = extract._extract(art, "MSFT", {"MSFT"}, 40000)
    assert len(insights) == 1
    span = insights[0].evidence_locator
    assert art.body[span["start"]:span["end"]] == valid.evidence_quote
    assert not extract._extract(art, "MSFT", {"MSFT"}, 10)


def test_target_extraction_rejects_blank_fact_summary_even_with_real_quote(monkeypatch):
    from ats.agents.information import extract
    from ats.schemas.research import Article
    art = Article(id='doc',source='semianalysis',title='Preview',body=FACTS,
                  published_at=NOW,completeness='partial',document_version_id='doc@body')
    from ats.agents.information.outputs import InformationEvidenceBatchView
    def model(role,schema,context,**kwargs):
        if schema is InformationEvidenceBatchView:
            return schema(evidence=[dict(core={'first_chunk':1,'last_chunk':1},source_actor='SemiAnalysis',
                source_kind='reported_fact',target_role='subject',
                information_topic='capacity',information_quality='substantive',materiality=.5)])
        return schema(source_fact='微软产能增加',summary='  ',source_actor='SemiAnalysis',source_kind='reported_fact',
            association_type='direct_update',association_reason='产能变化',information_topic='capacity',
            information_quality='substantive',materiality=.5,confidence=.8)
    monkeypatch.setattr(extract,'run_structured',model)
    with pytest.raises(RuntimeError,match='extraction failed'):
        extract._extract(art,'MSFT',{'MSFT'},40000,assess_materiality=True,target='MSFT')


def test_bound_extractor_uses_native_scope_instead_of_corpus_scan(monkeypatch):
    from dataclasses import asdict
    from ats.agents.information import assemble
    from ats.data.products.unstructured import AdmittedDocument

    document = AdmittedDocument(document_id="doc", version_id="doc@body", source="semianalysis",
        source_url="https://fixture/article", document_type="research_article", entity="SEMIANALYSIS",
        title="Capacity", published_at=NOW.isoformat(), fetched_at=NOW.isoformat(),
        completeness="partial", content_hash="body", text=FACTS)
    context = SimpleNamespace(identity=SimpleNamespace(scope={"entities": ["MSFT"]}))
    monkeypatch.setattr("ats.workflow.runtime_reads.current_read_context", lambda: context)
    captured = []
    monkeypatch.setattr("ats.data.consumer_api.read_input", lambda **kwargs:
        captured.append(kwargs) or SimpleNamespace(status="partial", payload=[asdict(document)]))
    monkeypatch.setattr("ats.data.products.unstructured.admitted_documents", lambda **kwargs:
        pytest.fail("bound extractor scanned the corpus"))
    monkeypatch.setattr(assemble, "evaluation_now", lambda tz: NOW)
    articles = assemble.admitted_articles(object(), since=NOW-timedelta(days=1))
    assert len(articles) == 1 and articles[0].document_version_id == "doc@body"
    assert captured[0]["consumer"] == "information"
    assert captured[0]["scope"]["entities"] == ["MSFT"]


def test_extractor_cannot_read_under_another_roles_bound_context(monkeypatch):
    from ats.agents.information import assemble
    from ats.workflow.cutover_routing import RouteUnavailable

    context = SimpleNamespace(identity=SimpleNamespace(consumer_id="fundamental", scope={"entities": ["MSFT"]}))
    monkeypatch.setattr("ats.workflow.runtime_reads.current_read_context", lambda: context)
    with pytest.raises(RouteUnavailable, match="consumer escapes"):
        assemble.admitted_articles(object(), since=NOW-timedelta(days=1))


def test_entity_evidence_is_stable_across_process_hash_seeds():
    import json
    import os
    import subprocess
    import sys

    code = "from ats.data.articles.material_quality import association_evidence; import json; print(json.dumps(association_evidence({'body':'NVIDIA Nvidia Google GCP Amazon Microsoft'}, ['NVDA','GOOG','AMZN','MSFT']), sort_keys=True))"
    outputs = [subprocess.check_output([sys.executable, "-c", code],
        env={**os.environ, "PYTHONHASHSEED": str(seed)}, text=True) for seed in (1, 2, 42)]
    assert len({json.dumps(json.loads(value), sort_keys=True) for value in outputs}) == 1


def test_real_extraction_path_reuses_then_invalidates_on_same_body_metadata_revision(monkeypatch):
    from ats.agents.information import extract, assemble
    from ats.memory import get_store

    store = get_store()
    doc = document_assets.ingest(entity="SEMIANALYSIS", key="cache-integration", doc_type="research_article",
        text=FACTS*8, source="semianalysis", title="Capacity evidence", published_at=NOW.isoformat(),
        min_chars=1, now=NOW, store=store)
    calls = []
    item = SimpleNamespace(ticker="MSFT", direction="neutral", impact_path="direct",
        summary="Capacity changed", evidence_quote="Microsoft increased capacity", confidence=.8)
    monkeypatch.setattr(extract, "run_structured", lambda *a, **k:
        calls.append(a[2]) or SimpleNamespace(insights=[item]))
    monkeypatch.setattr(extract, "_maybe_push", lambda *a: pytest.fail("unrequested notification"))
    monkeypatch.setattr(assemble, "evaluation_now", lambda tz: NOW+timedelta(hours=2))
    monkeypatch.setattr(extract, "evaluation_now", lambda tz: NOW+timedelta(hours=2))
    assert len(extract.run.__wrapped__(use_llm=True, since=NOW-timedelta(days=1))) == 1
    assert extract.run.__wrapped__(use_llm=True, since=NOW-timedelta(days=1)) == []
    assert len(calls) == 1
    store.data_store().revise_document_metadata(doc.document_id,
        metadata={"completeness": "partial"}, known_at=NOW+timedelta(hours=1))
    assert len(extract.run.__wrapped__(use_llm=True, since=NOW-timedelta(days=1))) == 1
    assert len(calls) == 2 and "completeness=partial" in calls[-1]
    envelopes = store.task_projection_envelopes(agent_role="information_brief")
    assert len(envelopes) == 2
    assert all(row["payload"]["source_evidence"] for row in envelopes)
    assert any("未取得隐藏内容" in " ".join(row["payload"]["unverified"]) for row in envelopes)


def test_metadata_and_extraction_config_changes_invalidate_reuse():
    from ats.agents.information.extract import processing_version
    from ats.schemas.research import Article

    original = interpretation_fingerprint(version_id="doc@body", title="A", published_at=NOW.isoformat(),
                                          completeness="full", quality={})
    corrected = interpretation_fingerprint(version_id="doc@body", title="A", published_at=NOW.isoformat(),
                                           completeness="partial", quality={})
    assert original != corrected
    art = Article(id="doc", title="A", source="semianalysis", body=FACTS, published_at=NOW,
                  interpretation_key=original)
    assert processing_version(art, {"article_chars": 40000}) != processing_version(
        art.model_copy(update={"interpretation_key": corrected}), {"article_chars": 40000})
    assert processing_version(art, {"article_chars": 40000}) != processing_version(art, {"article_chars": 12000})


def test_visible_prefix_excludes_footer_entities_and_preserves_raw_evidence(monkeypatch):
    import hashlib
    from ats.data.articles.material_quality import visible_analysis_view
    from ats.data.pipelines.unstructured.article_metadata import proposed_metadata
    from ats.agents.information import extract
    from ats.schemas.research import Article

    body = FACTS + "\nSubscribe to unlock the rest\nRecommended article: Marvell grew revenue."
    view = visible_analysis_view(body, source="semianalysis")
    assert "Marvell" not in view.text
    assert view.original_hash == hashlib.sha256(body.encode()).hexdigest()
    start = view.text.index("Microsoft")
    locator = view.locator(start, start + len("Microsoft"))
    assert body[locator["start"]:locator["end"]] == "Microsoft"
    row = {"source": "semianalysis", "completeness": "full", "version": {"version_id": "doc@v"}}
    metadata = proposed_metadata(row, body, entities=["MSFT", "MRVL"])
    assert metadata["completeness"] == "partial"
    assert metadata["related_entities"] == ["MSFT"]
    calls = []
    monkeypatch.setattr(extract, "run_structured", lambda *args, **kwargs:
        calls.append(args[2]) or SimpleNamespace(insights=[]))
    art = Article(id="doc", title="Capacity", source="semianalysis", body=body,
                  published_at=NOW, completeness="partial")
    assert extract._extract(art, "MSFT", {"MSFT"}, 40000) == []
    assert "Marvell" not in calls[0]
    quoted = FACTS + '\nRelated article: "Subscribe to unlock the rest" Marvell grew revenue.'
    assert "Marvell" in visible_analysis_view(quoted, source="semianalysis").text
    assert "Marvell" in visible_analysis_view(body, source="trendforce_news").text


def test_failed_processing_is_retryable_but_success_is_reused(tmp_path):
    repo = PlatformUnstructuredRepository(tmp_path / "processing.sqlite", writable=True)
    try:
        doc = document_assets.ingest(entity="SEMIANALYSIS", key="processing", doc_type="research_article",
            text=FACTS, source="semianalysis", min_chars=1, now=NOW, store=repo)
        version = repo.begin_document_processing(doc.document_id, "information", "test")
        assert version
        assert not repo.begin_document_processing(doc.document_id, "information", "test")
        repo.finish_document_processing(version, "information", "test", ok=False)
        assert repo.begin_document_processing(doc.document_id, "information", "test", retry_failed=True) == version
        repo.finish_document_processing(version, "information", "test", ok=True)
        assert not repo.begin_document_processing(doc.document_id, "information", "test")
    finally:
        repo.close()
