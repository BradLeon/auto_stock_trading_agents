"""Selection regressions without provider calls or licensed-content fixtures."""
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from ats.data.articles.entity_association import entity_mentions, title_mentions_entity
from ats.data.articles.material_quality import analysis_view, association_evidence, page_fields
from ats.data.pipelines.unstructured import article_ingest
from ats.data.stores.unstructured import PlatformUnstructuredRepository
from ats.schemas.chain import ArticleRef


def test_mu_person_is_not_micron_but_financial_ticker_is():
    assert not title_mentions_entity("Poo Mu-ming discusses neuroscience", "MU")
    assert not title_mentions_entity("MU-ming discusses neuroscience", "MU")
    assert title_mentions_entity("NASDAQ: MU earnings rise", "MU")
    assert title_mentions_entity("$MU shares gain", "MU")
    assert title_mentions_entity("Micron expands capacity", "MU")
    assert not title_mentions_entity("amd is an ordinary lower-case token", "AMD")


def test_padding_navigation_and_repeated_title_do_not_change_original_or_locations():
    body = "\u034f" * 4000 + "Headline\nHeadline\nView in browser\nNvidia raises capacity.\n"
    view = analysis_view(body, title="Headline")
    assert view.text == "Headline\nNvidia raises capacity."
    assert len(body) > 4000 and len(view.text) < 50
    start = view.text.index("Nvidia")
    locator = view.locator(start, start + len("Nvidia"))
    assert body[locator["start"]:locator["end"]] == "Nvidia"
    assert view.original_hash == analysis_view(body).original_hash
    assert "Subscribe to unlock the rest" in analysis_view(
        "Reported boundary: Subscribe to unlock the rest").text


def test_page_title_is_original_and_slug_numbers_are_never_guessed():
    fields = page_fields('<meta property="og:title" content="Nvidia’s NVHBM: +10.7%">'
                         '<meta name="description" content="Microsoft capacity expands">')
    assert fields["title"] == "Nvidia’s NVHBM: +10.7%"
    assert fields["title_source"] == "html:og:title"
    assert fields["lede"] == "Microsoft capacity expands"
    assert page_fields("<p>body only</p>")["title_quality"] == "fallback"


@pytest.mark.parametrize("source", ["semianalysis", "trendforce_news"])
@pytest.mark.parametrize("field", ["title", "lede", "body"])
def test_both_publishers_use_company_evidence_from_each_field(source, field):
    evidence = association_evidence({field: "Microsoft adds datacenter capacity"}, ["MSFT"])
    assert evidence and {row["field"] for row in evidence} == {field}
    assert evidence[0]["entity"] == "MSFT"
    assert not association_evidence({field: "Vera Rubin HBM technical overview"}, ["NVDA"])


def test_budget_opportunity_covers_actual_configured_issuers_and_deduplicates(monkeypatch):
    source = article_ingest._article_sources()["ibkr_news"]
    symbols = source.params["symbols"]
    now = datetime(2026, 10, 12, tzinfo=UTC)
    refs = [ArticleRef(url=f"wire://noise/{i}", slug=f"noise-{i}",
                       title="Chip investment news", published_at=now.date()) for i in range(30)]
    refs += [ArticleRef(url=f"wire://direct/{symbol}", slug=symbol,
                       title=f"${symbol} shares: XPU Attach interview",
                       published_at=now.date()-timedelta(days=1)) for symbol in symbols]
    refs.append(refs[-1])
    calls = []
    adapter = SimpleNamespace(discover_with_status=lambda **_: (refs, {"status": "succeeded"}),
        fetch_body=lambda url: calls.append(url) or "Publisher supplied substantive body")
    monkeypatch.setattr(article_ingest.importlib, "import_module", lambda _: adapter)
    materials, status, _ = article_ingest._materials_from_adapter(source,
        {"request_budget": {"max_body_requests": len(symbols)}, "policy": {"lookback_days": 7}}, now=now)
    assert set(calls) == {f"wire://direct/{symbol}" for symbol in symbols}
    assert len(calls) == len(symbols)
    assert sum(row.status == "budget_deferred" for row in materials) == 30
    assert all(row["requests"] == 1 for row in status["entity_coverage"].values())


def test_retry_requests_and_time_limit_are_accounted(monkeypatch):
    source = article_ingest._article_sources()["trendforce_news"]
    ref = ArticleRef(url="https://fixture/article", slug="article", title="HBM demand",
                     published_at=date(2026, 10, 12))
    calls = []
    adapter = SimpleNamespace(discover_with_status=lambda **_: ([ref], {"status": "succeeded"}),
                              fetch_body=lambda url: calls.append(url) or "")
    monkeypatch.setattr(article_ingest.importlib, "import_module", lambda _: adapter)
    _, status, _ = article_ingest._materials_from_adapter(source,
        {"request_budget": {"max_body_requests": 2}, "policy": {"max_body_attempts": 4}},
        now=datetime(2026, 10, 12, tzinfo=UTC))
    assert len(calls) == status["body_requests"] == 2
    clock = iter([0, 5, 5])
    monkeypatch.setattr(article_ingest.time, "monotonic", lambda: next(clock))
    materials, status, _ = article_ingest._materials_from_adapter(source,
        {"request_budget": {"max_body_requests": 2, "max_elapsed_seconds": 1}},
        now=datetime(2026, 10, 12, tzinfo=UTC))
    assert status["body_requests"] == 0
    assert materials[0].reason == "body_elapsed_budget_exhausted"


def test_metadata_revision_preserves_old_asof_and_body_version(tmp_path):
    repo = PlatformUnstructuredRepository(tmp_path / "docs.sqlite", writable=True)
    old = datetime(2026, 10, 10, tzinfo=UTC)
    new = old + timedelta(days=1)
    doc = SimpleNamespace(document_id="newsletter", symbol="SEMIANALYSIS", period="",
        doc_type="research_article", source="semianalysis", source_url="fixture://source",
        path=None, sha256="abc123", text="Microsoft demand", fetched_at=old.isoformat(),
        related_entities=(), title="Theme", published_at=old.isoformat(), completeness="full")
    try:
        repo.save_document(doc)
        publication = repo.revise_document_metadata("newsletter", metadata={
            "related_entities": ["MSFT"], "completeness": "partial",
            "quality": {"parser_version": "v2"}}, known_at=new)
        assert publication
        assert repo.document_materials_at(as_of=old)[0]["related_entities"] == ["SEMIANALYSIS"]
        assert repo.document_materials_at(as_of=old)[0]["completeness"] == "full"
        current = repo.document_materials_at(as_of=new)[0]
        assert current["completeness"] == "partial"
        assert "MSFT" in current["related_entities"]
        assert len(repo.document_versions("newsletter")) == 1
        assert repo.documents(entity="MSFT")[0]["document_id"] == "newsletter"
        assert repo.revise_document_metadata("newsletter", metadata={
            "related_entities": ["MSFT"], "completeness": "partial",
            "quality": {"parser_version": "v2"}}, known_at=new+timedelta(hours=1)) == ""
        with pytest.raises(ValueError, match="precede"):
            repo.revise_document_metadata("newsletter", metadata={"title": "bad backdate"}, known_at=old)
    finally:
        repo.close()


def test_cached_full_body_frees_budget_for_deferred_candidate_and_is_rechecked(tmp_path, monkeypatch):
    from ats.data import document_assets

    source = article_ingest._article_sources()["trendforce_news"]
    now = datetime(2026, 10, 12, tzinfo=UTC)
    refs = [ArticleRef(url=f"https://fixture/{i}", slug=str(i), title=f"HBM capacity {i}",
                       published_at=now.date()) for i in range(2)]
    repo = PlatformUnstructuredRepository(tmp_path / "reuse.sqlite", writable=True)
    calls = []
    adapter = SimpleNamespace(discover_with_status=lambda **_: (refs, {"status": "succeeded"}),
        fetch_body=lambda url: calls.append(url) or "New HBM body " * 100)
    monkeypatch.setattr(article_ingest.importlib, "import_module", lambda _: adapter)
    policy = {"request_budget": {"max_body_requests": 1}}
    try:
        document_assets.ingest(entity="TRENDFORCE", key="cached", doc_type="article",
            text="Cached HBM body " * 100, source=source.id, external_id="0",
            title="Original title: 10.7%", published_at=now.date().isoformat(),
            min_chars=1, now=now, store=repo)
        materials, status, _ = article_ingest._materials_from_adapter(
            source, policy, now=now+timedelta(hours=1), store=repo)
        assert calls == [refs[1].url]
        assert status["cache_hits"] == 1 and status["body_requests"] == 1
        assert materials[0].provenance["title"] == "Original title: 10.7%"
        calls.clear()
        _, status, _ = article_ingest._materials_from_adapter(
            source, policy, now=now+timedelta(days=2), store=repo)
        assert calls == [refs[0].url] and status["cache_hits"] == 0
    finally:
        repo.close()


def test_original_title_and_body_are_parsed_from_one_request(monkeypatch):
    from ats.data.articles import trendforce

    calls = []
    html = '<h1>Microsoft capacity: +10.7%</h1><article class="presscenter"><p>Visible body.</p></article>'
    monkeypatch.setattr(trendforce, "_get", lambda url: calls.append(url) or html)
    body, fields = trendforce.fetch_material("https://fixture/page")
    assert calls == ["https://fixture/page"]
    assert body == "Visible body." and fields["title"] == "Microsoft capacity: +10.7%"


def test_model_context_discloses_partial_and_input_truncation(monkeypatch):
    from ats.agents.information import documents

    captured = []
    monkeypatch.setattr(documents, "run_structured", lambda *args, **kwargs:
        captured.append(args[2]) or SimpleNamespace(confidence=.6, expectation_changes=[],
            materiality=.1, event_summary="No material change", narrative_delta=""))
    article = SimpleNamespace(headline="Capacity", completeness="partial",
                              input_ref="doc@version", input_truncated=True)
    documents._llm_brief("MSFT", [{"published_at": "2026-10-12", "source": "fixture"}],
                         [(article, "Visible preview only")])
    assert "completeness=partial" in captured[0]
    assert "input_truncated=True" in captured[0] and "doc@version" in captured[0]
    assert "Full text" not in captured[0]


def test_missing_publication_time_cannot_use_fetch_time_for_freshness():
    from ats.agents.information.assemble import _within_window

    assert not _within_window("", datetime(2026, 10, 5, tzinfo=UTC))
    assert not _within_window("2030-01-01", datetime(2026, 10, 5, tzinfo=UTC))
    from ats.data.research import _msg_date

    assert _msg_date({"Date": "Mon, 12 Oct 2026 09:00:00"}) is None
    assert _msg_date({"Date": "Mon, 12 Oct 2026 09:00:00 +0800"}) is not None


def test_legacy_verified_label_cannot_restore_false_mu_association():
    source = article_ingest._article_sources()["ibkr_news"]
    ref = ArticleRef(url="ibkr-news://DJ/story", slug="story",
                     title="Financing research by Poo Mu-ming", published_at=date(2026, 10, 12))
    material = article_ingest.Material(ref, "Independent reporting. " * 80,
        {"entity_association": "title_verified", "title_verified_entities": "MU"})
    policy = {"policy": {"require_entity_verified": True, "minimum_body_chars": 600}}
    candidate = article_ingest._candidate(source, material, material.body,
        discovered_at=datetime(2026, 10, 12, tzinfo=UTC), policy=policy)
    assert "MU" not in candidate.related_entities
    validation, state = article_ingest._admission(candidate, material, source=source,
        policy=policy, human_review_approved=False, source_eligible=True)
    assert state == "quarantined"
    assert "entity_association_not_verified" in {issue.code for issue in validation.issues}


def test_ingestion_no_change_republishes_metadata_without_body_version(tmp_path, monkeypatch):
    now = datetime(2026, 10, 10, tzinfo=UTC)
    ref = ArticleRef(url="https://fixture/story", slug="story", title="HBM supply overview",
                     published_at=now.date())
    body = "Microsoft discusses data center capacity. " * 50
    fields = {"title": "HBM supply overview", "title_source": "html:h1", "title_quality": "original"}
    adapter = SimpleNamespace(discover_with_status=lambda **_: ([ref], {"status": "succeeded"}),
                              fetch_material=lambda _: (body, fields))
    monkeypatch.setattr(article_ingest.importlib, "import_module", lambda _: adapter)
    repo = PlatformUnstructuredRepository(tmp_path / "ingestion.sqlite", writable=True)
    try:
        first = article_ingest.ingest_article_source("trendforce_news", now=now, store=repo)
        assert first["accepted"] == 1
        doc_id = first["document_ids"][0]
        fields["title"] = "Nvidia HBM supply overview"
        article_ingest.ingest_article_source("trendforce_news", now=now+timedelta(days=2), store=repo)
        old = repo.document_materials_at(as_of=now)[0]
        new = repo.document_materials_at(as_of=now+timedelta(days=2))[0]
        assert "NVDA" not in old["related_entities"]
        assert "NVDA" in new["related_entities"] and new["title"] == fields["title"]
        assert old["version"]["version_id"] == new["version"]["version_id"]
        assert len(repo.document_versions(doc_id)) == 1
    finally:
        repo.close()


def test_limited_nonkeyword_exploration_does_not_expand_body_request_budget(monkeypatch):
    source = article_ingest._article_sources()['ibkr_news']
    now = datetime(2026, 10, 12, tzinfo=UTC)
    refs = [ArticleRef(url=f'wire://unknown/{i}', slug=str(i), title='Architecture discussion',
                      published_at=now.date()) for i in range(10)]
    calls = []
    adapter = SimpleNamespace(discover_with_status=lambda **_: (refs, {'status':'succeeded'}),
        fetch_body=lambda url: calls.append(url) or 'Microsoft capacity changed. '*30)
    monkeypatch.setattr(article_ingest.importlib, 'import_module', lambda _: adapter)
    materials, status, _ = article_ingest._materials_from_adapter(source,
        {'request_budget': {'max_body_requests': 2, 'max_exploration_candidates': 2}}, now=now)
    assert len(materials) == 10 and len(calls) == status['exploration_candidates'] == 2
    assert sum(row.status == 'budget_deferred' for row in materials) == 8
    assert status['body_requests'] == 2
    assert all(row['status'] == 'no_direct_title_candidate' for row in status['entity_coverage'].values())
    assert all(row.provenance['selection_reason'] == 'bounded_missing_entity_or_topic_exploration' for row in materials)


def test_ibkr_explicit_body_policy_requires_registered_issuer_span_not_query_lineage():
    from ats.data.pipelines.unstructured.source_acceptance import load_policy
    source = article_ingest._article_sources()['ibkr_news']
    policy = load_policy(source.id)
    assert policy['policy']['require_entity_verified'] is True
    assert policy['policy']['entity_evidence_fields'] == ['title', 'body']
    now = datetime(2026, 10, 12, tzinfo=UTC)
    ref = ArticleRef(url='wire://article', slug='a', title='Architecture discussion', published_at=now.date())
    for body, expected in [('Marvell developed a custom chip. '*40, 'accepted'),
                           ('Poo Mu-ming discussed neuroscience. '*40, 'quarantined'),
                           ('Industry product technology changed. '*40, 'quarantined')]:
        material = article_ingest.Material(ref, body, {'queried_entities':'MRVL, MU'})
        candidate = article_ingest._candidate(source, material, body, discovered_at=now, policy=policy)
        _, state = article_ingest._admission(candidate, material, source=source, policy=policy,
            human_review_approved=False, source_eligible=True)
        assert state == expected
        if state == 'accepted':
            mention = next(r for r in candidate.metadata['entity_evidence'] if r['entity'] == 'MRVL')
            assert mention['field'] == 'body' and body[mention['start']:mention['end']] == 'Marvell'


def test_discovery_time_is_included_in_dispatch_deadline(monkeypatch):
    source = article_ingest._article_sources()['ibkr_news']
    ref = ArticleRef(url='wire://mrvl', slug='mrvl', title='Marvell capacity', published_at=date(2026,10,12))
    adapter = SimpleNamespace(discover_with_status=lambda **_: ([ref], {'status':'succeeded'}),
        fetch_body=lambda url: pytest.fail('body dispatched after discovery deadline'))
    monkeypatch.setattr(article_ingest.importlib, 'import_module', lambda _: adapter)
    clock = iter([0, 301, 302])
    monkeypatch.setattr(article_ingest.time, 'monotonic', lambda: next(clock))
    materials, status, _ = article_ingest._materials_from_adapter(source,
        {'request_budget': {'max_body_requests': 10, 'max_elapsed_seconds': 300}},
        now=datetime(2026,10,12,tzinfo=UTC))
    assert materials[0].reason == 'body_elapsed_budget_exhausted'
    assert status['body_requests'] == 0 and status['entity_coverage']['MRVL']['status'] == 'deferred'
    assert status['elapsed_policy'] == 'discovery_inclusive_dispatch_deadline'


def test_page_fields_preserve_lede_without_title_and_only_explicit_subtitles():
    fields = page_fields('<meta name="description" content="Microsoft capacity expanded">'
                         '<h2 class="post-subtitle">Nvidia supplied the GPUs</h2>'
                         '<h2>Ordinary section heading</h2>')
    assert fields['title_quality'] == 'fallback'
    assert fields['lede'] == 'Microsoft capacity expanded'
    assert fields['subtitle'] == 'Nvidia supplied the GPUs'
    assert fields['subtitle_source'] == 'html:explicit_subtitle'
    assert 'subtitle' not in page_fields('<h2>Arbitrary heading</h2>') or not page_fields('<h2>Arbitrary heading</h2>')['subtitle']


def test_nested_subtitle_retains_text_after_inner_container():
    fields = page_fields('<div class="post-subtitle">Microsoft <div>expanded</div> capacity.</div>'
                         '<div>Unrelated footer</div>')
    assert fields['subtitle'] == 'Microsoft expanded capacity.'


def test_subtitle_association_has_exact_metadata_field_span():
    source = article_ingest._article_sources()['trendforce_news']
    now = datetime(2026,10,12,tzinfo=UTC)
    ref = ArticleRef(url='fixture://subtitle', slug='subtitle', title='Industry overview', published_at=now.date())
    subtitle = 'Microsoft raised data center capacity.'
    material = article_ingest.Material(ref, 'Industry capacity facts. '*100, {'subtitle':subtitle,
        'subtitle_source':'html:explicit_subtitle'})
    candidate = article_ingest._candidate(source, material, material.body, discovered_at=now, policy={})
    mention = next(row for row in candidate.metadata['entity_evidence'] if row['entity']=='MSFT')
    assert mention['field']=='subtitle' and subtitle[mention['start']:mention['end']]=='Microsoft'


def test_cached_exploration_does_not_permanently_hide_unprocessed_candidates(tmp_path, monkeypatch):
    from ats.data import document_assets
    source = article_ingest._article_sources()['ibkr_news']
    now = datetime(2026,10,12,tzinfo=UTC)
    refs = [ArticleRef(url=f'wire://explore/{i}', slug=str(i), title='Architecture discussion', published_at=now.date()) for i in range(4)]
    calls = []
    adapter = SimpleNamespace(discover_with_status=lambda **_: (refs, {'status':'succeeded'}),
        fetch_body=lambda url: calls.append(url) or 'Marvell developed a custom chip. '*30)
    monkeypatch.setattr(article_ingest.importlib, 'import_module', lambda _: adapter)
    repo = PlatformUnstructuredRepository(tmp_path/'exploration.sqlite', writable=True)
    try:
        document_assets.ingest(entity='DOWJONES', key='explore0', doc_type='article', source='ibkr_news',
            text='Marvell provided existing chip details. '*30, external_id='0', title=refs[0].title,
            published_at=now.isoformat(), min_chars=1, now=now, store=repo)
        materials, status, _ = article_ingest._materials_from_adapter(source,
            {'request_budget': {'max_body_requests': 1, 'max_exploration_candidates': 1}},
            now=now+timedelta(hours=1), store=repo)
        assert calls == [refs[1].url]
        assert status['cache_hits'] == 1 and status['exploration_candidates'] == 1
        assert sum(row.status == 'budget_deferred' for row in materials) == 2
    finally:
        repo.close()


def test_all_ibkr_configured_issuers_have_registered_company_names():
    from ats.config import load_entities
    source = article_ingest._article_sources()['ibkr_news']
    entities = load_entities()
    assert all(entities.get(symbol, {}).get('name') for symbol in source.params['symbols'])
    assert title_mentions_entity('Oracle expands its data center capacity', 'ORCL')
    assert not title_mentions_entity('The Oracle of Omaha discusses markets', 'ORCL')


def test_budget_deferred_is_not_a_quality_rejection_or_persisted_quarantine(tmp_path, monkeypatch):
    source = article_ingest._article_sources()['trendforce_news']
    now = datetime(2026,10,12,tzinfo=UTC)
    refs = [ArticleRef(url=f'fixture://defer/{i}', slug=str(i), title='HBM capacity', published_at=now.date()) for i in range(2)]
    adapter = SimpleNamespace(discover_with_status=lambda **_: (refs, {'status':'succeeded'}),
        fetch_body=lambda url: 'Microsoft increased capacity. '*100)
    monkeypatch.setattr(article_ingest.importlib, 'import_module', lambda _: adapter)
    from ats.data.pipelines.unstructured.source_acceptance import load_policy
    policy = load_policy(source.id)
    policy['request_budget']['max_body_requests'] = 1
    monkeypatch.setattr(article_ingest, 'load_policy', lambda source_id: policy)
    repo = PlatformUnstructuredRepository(tmp_path/'defer.sqlite', writable=True)
    try:
        result = article_ingest.ingest_article_source(source.id, now=now, store=repo)
        assert result['deferred'] == 1 and result['quarantined'] == 0 and result['accepted'] == 1
        assert result['status'] == 'partial'
        assert result['reason_codes'] == {'budget_deferred':1}
        assert len(repo.document_candidates(status='deferred')) == 1
        assert repo.document_candidates(status='quarantined') == []
    finally:
        repo.close()
