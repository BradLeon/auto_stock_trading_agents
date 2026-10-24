from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ats.agents.information import assemble, extract, selection, target_research
from ats.schemas.research import Article
from ats.workflow.runtime_reads import consumer_entities
from ats.workflow.cutover_routing import RouteUnavailable

NOW = datetime(2026, 10, 12, tzinfo=UTC)
CONFIG = {'publisher_entities': ['SEMIANALYSIS', 'TRENDFORCE'], 'candidate_limit': 120,
          'max_articles_per_run': 8, 'exploration_articles': 2, 'article_chars': 40000,
          'total_article_chars': 320000, 'dispatch_seconds': 180, 'evidence_first': True}


def article(key, source='semianalysis', title='Capacity', body='Microsoft increased capacity by 10%.', **kwargs):
    return Article(id=key, document_version_id=key+'@v', title=title, source=source,
                   published_at=NOW, body=body, interpretation_key=key, **kwargs)


def bound(monkeypatch, publishers=True):
    entities = ['MSFT', 'NVDA'] + (CONFIG['publisher_entities'] if publishers else [])
    context = SimpleNamespace(route='target', identity=SimpleNamespace(consumer_id='information',
                                   scope={'kind': 'entity', 'id': 'MSFT', 'entities': entities}))
    monkeypatch.setattr(selection, 'current_read_context', lambda: context)
    monkeypatch.setattr(assemble, 'signal_chain_universe', lambda targets: ('MSFT target; NVDA supplier',
                                                                        {'MSFT': ['MSFT'], 'NVDA': ['MSFT']}))
    return context


def test_information_scope_never_expands_to_peers_or_publishers(tmp_path):
    (tmp_path/'pead').mkdir()
    (tmp_path/'pead'/'MSFT.yaml').write_text('signal_chain:\n  - symbol: NVDA\n')
    (tmp_path/'pead.yaml').write_text('research:\n  publisher_entities: [SEMIANALYSIS, TRENDFORCE]\n')
    assert consumer_entities('information', 'entity', 'MSFT', config_dir=tmp_path) == ['MSFT']
    assert consumer_entities('fundamental', 'entity', 'MSFT', config_dir=tmp_path) == ['MSFT']
    (tmp_path/'pead.yaml').write_text('research:\n  publisher_entities: [UNREGISTERED]\n')
    assert consumer_entities('information', 'entity', 'MSFT', config_dir=tmp_path) == ['MSFT']


@pytest.mark.parametrize('source', ['semianalysis', 'trendforce_news'])
@pytest.mark.parametrize('field,rank', [('title', 0), ('lede', 1), ('subtitle', 1), ('body', 2), ('none', 4)])
def test_publisher_sources_require_explicit_target_match(monkeypatch, source, field, rank):
    bound(monkeypatch)
    kwargs = {'title': 'Industry capacity', 'body': 'Vera Rubin uses a new interconnect architecture.',
              'source_fields': {}}
    if field in ('title', 'body'):
        kwargs[field] = 'Microsoft capacity changed.'
    elif field != 'none':
        kwargs['source_fields'][field] = 'Microsoft capacity changed.'
    art = article('fixture', source=source, **kwargs)
    calls = []
    monkeypatch.setattr(assemble, 'admitted_articles', lambda store, **kw: calls.append(kw) or [art])
    rows, report = selection.candidates(object(), 'MSFT', since=NOW-timedelta(days=7), config=CONFIG)
    assert len(rows) == (0 if field=='none' else 1)
    if rows:
        assert rows[0].rank == rank
    assert len(calls)==1 and calls[0]['entities'] == ['MSFT']
    assert all(call['limit'] == 120 for call in calls)


def test_target_only_scope_is_sufficient_without_peer_or_publisher_access(monkeypatch):
    context=bound(monkeypatch, publishers=False)
    context.identity.scope['entities']=['MSFT']
    monkeypatch.setattr(assemble, 'admitted_articles', lambda *a, **k: [article('direct')])
    rows,report=selection.candidates(object(), 'MSFT', since=NOW-timedelta(days=7), config=CONFIG)
    assert len(rows)==1 and report['universe']==['MSFT']


def test_legacy_exploration_setting_cannot_reserve_or_fill_weak_candidate_slots():
    rows = [selection.Candidate(article(f'd{i}', body=f'Microsoft capacity project {i}.'), 0, 'target_title') for i in range(20)]
    rows += [selection.Candidate(article(f's{i}', body=f'TPU technology project {i}.'), 4, 'publisher_exploration') for i in range(10)]
    rows += [selection.Candidate(article('t', source='trendforce_news', body='HBM technology.'), 4, 'publisher_exploration')]
    chosen = selection.choose(rows, max_articles=8, exploration_articles=2, cached_versions={'d0@v'})
    assert len(chosen) == 8
    assert all(row.rank < 3 for row in chosen)
    assert all(row.article.id != 'd0' for row in chosen)


def test_target_path_extracts_caches_and_publishes_exact_evidence(monkeypatch):
    from ats.memory import get_store
    from ats.data import document_assets

    store = get_store()
    body = 'Microsoft increased capacity by 10%. Nvidia supplied GPUs. ' * 30
    doc = document_assets.ingest(entity='SEMIANALYSIS', key='target-cache', doc_type='research_article',
        text=body, source='semianalysis', title='Capacity', published_at=NOW.isoformat(),
        min_chars=1, now=NOW, store=store)
    version = store.latest_document_version(doc.document_id)['version_id']
    art = article(doc.document_id, body=body, completeness='partial').model_copy(update={'document_version_id': version})
    context = bound(monkeypatch)
    monkeypatch.setattr(assemble, 'admitted_articles', lambda store, **kw: [art])
    monkeypatch.setattr(target_research, 'evaluation_now', lambda tz: NOW)
    monkeypatch.setattr(extract, 'evaluation_now', lambda tz: NOW)
    calls = []
    from ats.agents.information.outputs import InformationEvidenceBatchView, InformationEvidenceRecapView
    quote = 'Microsoft increased capacity by 10%.'
    def model(role, schema, context, **kwargs):
        calls.append(context)
        if schema is InformationEvidenceBatchView:
            return schema(evidence=[dict(core={'first_chunk':1,'last_chunk':1},source_kind='reported_fact',source_actor='SemiAnalysis',
                target_role='subject',information_topic='capacity',
                information_quality='substantive',materiality=.2)])
        return InformationEvidenceRecapView(summary='来源报道产能上升10%。', source_fact='产能上升10%。',
            source_kind='reported_fact', source_actor='SemiAnalysis', association_type='direct_update',
            association_reason='微软产能更新', information_topic='capacity', information_quality='substantive',
            confidence=.95, materiality=.2)
    monkeypatch.setattr(extract, 'run_structured', model)
    first = target_research.run(store, 'MSFT', config=CONFIG, since=NOW-timedelta(days=7), use_llm=True)
    second = target_research.run(store, 'MSFT', config=CONFIG, since=NOW-timedelta(days=7), use_llm=True)
    assert first['model_calls'] == 1 and second['model_calls'] == 0 and second['cache_hits'] == 1
    assert len(calls) == 2 and first['model_invocations'] == 2
    payload = store.get_task_projection(second['projection_id'])['payload']
    assert payload['relevance'] == 'low' and payload['confidence'] == .95
    assert payload['fact_changes'] == ['来源信息（SemiAnalysis）：来源报道产能上升10%。']
    assert any('partial' in s for s in payload['unverified'])
    evidence = payload['source_evidence'][0]
    assert evidence['document_ref'] == version
    assert body[evidence['locator']['start']:evidence['locator']['end']] == quote
    assert store.task_projection_envelopes(agent_role='fundamental_expectation_update') == []
    # Same body, changed interpretation metadata must not reuse the full/partial cache.
    changed = art.model_copy(update={'interpretation_key': 'changed-title-policy'})
    monkeypatch.setattr(assemble, 'admitted_articles', lambda store, **kw: [changed])
    third = target_research.run(store, 'MSFT', config=CONFIG, since=NOW-timedelta(days=7), use_llm=True)
    assert third['model_calls'] == 1 and third['model_invocations'] == 2 and len(calls) == 4


@pytest.mark.parametrize('count_cap', [8, None])
def test_character_budget_defers_before_model_or_processing_claim(monkeypatch, count_cap):
    bound(monkeypatch)
    art = article('oversize', body='Microsoft capacity. '*100)
    monkeypatch.setattr(assemble, 'admitted_articles', lambda store, **kw: [art])
    store = SimpleNamespace(information_extraction_cache=lambda **kw: None,
        begin_document_processing=lambda *a, **kw: pytest.fail('claim beyond budget'))
    monkeypatch.setattr(extract, 'run_structured', lambda *a, **kw: pytest.fail('model beyond budget'))
    result = target_research.run(store, 'MSFT', config={**CONFIG, 'max_articles_per_run': count_cap, 'total_article_chars': 100},
        since=NOW-timedelta(days=7), use_llm=True)
    assert result['model_calls'] == 0 and result['deferred'] == 1 and result['status'] == 'pending'


def test_no_model_selection_never_claims_or_writes(monkeypatch):
    bound(monkeypatch)
    monkeypatch.setattr(assemble, 'admitted_articles', lambda store, **kw: [article('probe')])
    store = SimpleNamespace(information_extraction_cache=lambda **kw: None)
    result = target_research.run(store, 'MSFT', config=CONFIG, since=NOW-timedelta(days=7), use_llm=False)
    assert result['status'] == 'selected_no_model' and result['model_calls'] == 0


def test_entry_uses_bounded_target_path_without_corpus_or_old_summary(monkeypatch):
    from ats.agents.information import entry
    context = bound(monkeypatch)
    monkeypatch.setattr('ats.workflow.runtime_reads.current_read_context', lambda: context)
    monkeypatch.setattr(target_research, 'run', lambda *a, **kw: {'status': 'selected_no_model'})
    monkeypatch.setattr(extract, 'run', lambda **kw: pytest.fail('corpus extraction'))
    monkeypatch.setattr('ats.agents.information.documents.run_document_pass',
                        lambda *a, **kw: pytest.fail('old summary path'))
    result = entry.run_information_target.__wrapped__('MSFT', store=object(), use_llm=False)
    assert result['selection']['status'] == 'selected_no_model'


def test_empty_and_failed_extraction_are_distinct_and_failure_retries(monkeypatch):
    from ats.memory import get_store
    from ats.data import document_assets
    store = get_store()
    body = 'Microsoft TPU interconnect architecture uses new power delivery. '*30
    doc = document_assets.ingest(entity='TRENDFORCE', key='empty-target-cache', doc_type='research_article',
        text=body, source='trendforce_news', title='TPU architecture', published_at=NOW.isoformat(),
        min_chars=1, now=NOW, store=store)
    version = store.latest_document_version(doc.document_id)['version_id']
    art = article(doc.document_id, source='trendforce_news', body=body).model_copy(update={'document_version_id': version})
    bound(monkeypatch)
    monkeypatch.setattr(assemble, 'admitted_articles', lambda store, **kw: [art])
    monkeypatch.setattr(target_research, 'evaluation_now', lambda tz: NOW)
    calls = []
    def model(*a, **kw):
        calls.append(a[2])
        if len(calls) == 1:
            raise RuntimeError('model unavailable')
        from ats.agents.information.outputs import InformationEvidenceBatchView
        return InformationEvidenceBatchView(quotes=[], empty_reason='只有背景，无可用目标陈述')
    monkeypatch.setattr(extract, 'run_structured', model)
    with pytest.raises(RuntimeError, match='extraction failed'):
        target_research.run(store, 'MSFT', config=CONFIG, since=NOW-timedelta(days=7), use_llm=True)
    assert store.task_projection_envelopes(agent_role='information_brief') == []
    success = target_research.run(store, 'MSFT', config=CONFIG, since=NOW-timedelta(days=7), use_llm=True)
    reuse = target_research.run(store, 'MSFT', config=CONFIG, since=NOW-timedelta(days=7), use_llm=True)
    assert success['status'] == reuse['status'] == 'no_relevant_change'
    assert reuse['cache_hits'] == 1 and len(calls) == 2
    payload = store.get_task_projection(reuse['projection_id'])['payload']
    assert '本次已核读' in payload['fact_changes'][0]
    assert '不断言市场没有重要消息' in payload['unverified'][0]
    assert payload['source_evidence'][0]['statement_type'] == 'bounded_inspection_result'


def test_future_interpretation_cache_is_not_visible_to_earlier_run():
    from ats.memory.store import TradingMemory
    store = TradingMemory(':memory:')
    try:
        store.save_task_projection(profile='information_extraction', profile_version='p',
            input_kind='document_version', input_ref='doc@v', target_type='entity', target_id='MSFT',
            payload={'insights': []}, created_at=(NOW+timedelta(hours=1)).isoformat())
        assert store.information_extraction_cache(input_ref='doc@v', processor='p', target='MSFT', as_of=NOW) is None
        assert store.information_extraction_cache(input_ref='doc@v', processor='p', target='MSFT',
            as_of=NOW+timedelta(hours=2)) is not None
    finally:
        store.close()


def test_syndicated_equal_facts_do_not_inflate_confidence_or_fact_count(monkeypatch):
    from ats.schemas.research import Insight
    from ats.data.articles.material_quality import visible_analysis_view
    rows = []
    for key in ('wire-a', 'wire-b'):
        art = article(key, publication_id=key+'-publication')
        view = visible_analysis_view(art.body, title=art.title, source=art.source)
        insight = Insight(article_id=key, ticker='MSFT', direction='neutral', impact_path='direct',
            summary='Capacity increased 10%', evidence_quote=art.body, confidence=.6, materiality=.8,
            evidence_locator=view.locator(0,len(view.text)))
        rows.append((art,insight,key+'-cache'))
    captured = {}
    def publish(store,payload,**kwargs):
        captured.update(payload)
        return 'brief'
    monkeypatch.setattr(target_research,'publish_information_brief',publish)
    target_research._publish(object(),'MSFT',rows)
    assert captured['fact_changes']==['MSFT: Capacity increased 10%']
    assert captured['confidence']==.6 and captured['source_count']==1
    assert captured['source_evidence'][0]['publication_id']=='wire-a-publication'
    assert any('独立佐证' in row for row in captured['unverified'])


def test_peer_cannot_be_used_as_another_output_target_under_current_scope(monkeypatch):
    bound(monkeypatch)
    with pytest.raises(RouteUnavailable, match='output target differs'):
        selection.candidates(object(), 'NVDA', since=NOW-timedelta(days=7), config=CONFIG)


def test_dispatch_deadline_stops_before_claim(monkeypatch):
    bound(monkeypatch)
    monkeypatch.setattr(assemble, 'admitted_articles', lambda store, **kw: [article('late')])
    times = iter([0, 181, 182])
    monkeypatch.setattr(target_research.time, 'monotonic', lambda: next(times))
    store = SimpleNamespace(information_extraction_cache=lambda **kw: None,
        begin_document_processing=lambda *a, **kw: pytest.fail('claim after deadline'))
    result = target_research.run(store, 'MSFT', config=CONFIG, since=NOW-timedelta(days=7), use_llm=True)
    assert result['model_calls'] == 0 and result['items'][0]['status'] == 'deferred_budget'


@pytest.mark.parametrize('publisher,source', [('SEMIANALYSIS', 'semianalysis'), ('TRENDFORCE', 'trendforce_news')])
@pytest.mark.parametrize('matched', [False, True])
def test_native_target_channel_reads_publisher_material_only_when_target_is_indexed(monkeypatch, publisher, source, matched):
    from ats.memory import get_store
    from ats.data import document_assets
    from ats.data import consumer_api
    from ats.workflow import runtime_reads
    from ats.workflow.runtime_reads import ReadContext, business_identity

    store = get_store()
    doc = document_assets.ingest(entity=publisher, key='native-'+source, doc_type='research_article',
        text=('Microsoft announced new capacity. ' if matched else 'TPU architecture has power constraints. ')*40,
        related_entities=('MSFT',) if matched else (), source=source,
        title='Industry architecture', published_at=NOW.isoformat(), min_chars=1, now=NOW, store=store)
    identity = business_identity('information', kind='entity', scope_id='MSFT',
        entities=['MSFT', 'NVDA', *CONFIG['publisher_entities']], as_of=NOW)
    context = ReadContext(identity, mode='isolated', route='target', cutoff=NOW)
    monkeypatch.setattr(runtime_reads, 'gate_read', lambda identity: SimpleNamespace(route='target'))
    monkeypatch.setattr(selection, 'current_read_context', lambda: context)
    monkeypatch.setattr(assemble, 'evaluation_now', lambda tz: NOW)
    monkeypatch.setattr(assemble, 'signal_chain_universe', lambda targets: ('MSFT + NVDA', {'MSFT':['MSFT'], 'NVDA':['MSFT']}))
    native = consumer_api.read_input
    products = SimpleNamespace(unstructured=store.data_store())
    captured = []
    def read(**kwargs):
        captured.append(kwargs['scope'])
        return native(**kwargs, products=products)
    monkeypatch.setattr(consumer_api, 'read_input', read)
    token = runtime_reads._CURRENT.set(context)
    try:
        rows, report = selection.candidates(store, 'MSFT', since=NOW-timedelta(days=7), config=CONFIG)
    finally:
        runtime_reads._CURRENT.reset(token)
    assert len(rows) == (1 if matched else 0)
    if rows:
        assert rows[0].article.id==doc.document_id and rows[0].reason=='target_visible_body'
    assert len(captured)==1 and captured[0]['entities']==['MSFT']


def test_near_duplicate_reuses_cache_without_rereading_other_version():
    body=' '.join(f'word{i}' for i in range(140))
    rows=[selection.Candidate(article('wire-new',source='ibkr_news',body=body),0,'target_title'),
          selection.Candidate(article('wire-old',source='ibkr_news',body=body+' Existing commentary.'),0,'target_title'),
          selection.Candidate(article('direct',source='trendforce_news',body='Microsoft released a new local inference platform.'),0,'target_title')]
    chosen=selection.choose(rows,max_articles=2,exploration_articles=0,cached_versions=set())
    assert {r.article.id for r in chosen}=={'wire-new','direct'}
    assert len(rows)==3  # no deletion of update versions from candidate inventory
    expanded=selection.choose(rows,max_articles=3,exploration_articles=0,cached_versions=set())
    assert {r.article.id for r in expanded}=={'wire-new','direct'}
    later=selection.choose(rows,max_articles=2,exploration_articles=0,
        cached_versions={'wire-new@v','direct@v'})
    assert {r.article.id for r in later} == {'wire-new', 'direct'}


def test_equivalent_content_reuses_cached_original_despite_title_and_source_changes():
    rows = [selection.Candidate(article('a-new', source='trendforce_news',
                title='New headline', body='Microsoft capacity increased 10%.'), 0, 'target_title'),
            selection.Candidate(article('z-cached', source='ibkr_news',
                title='Original headline', body='Microsoft capacity increased 10%.'), 2, 'target_visible_body')]
    chosen = selection.choose(rows, max_articles=None, exploration_articles=0,
                              cached_versions={'z-cached@v'})
    assert [r.article.document_version_id for r in chosen] == ['z-cached@v']
    # A real update is distinct even when a cached version has the same headline.
    rows.append(selection.Candidate(article('update', title='Original headline',
                body='Microsoft capacity increased 20%.'), 0, 'target_title'))
    chosen = selection.choose(rows, max_articles=None, exploration_articles=0,
                              cached_versions={'z-cached@v'})
    assert {r.article.id for r in chosen} == {'z-cached', 'update'}


@pytest.mark.parametrize('entity', ['MRVL', 'MSFT', 'NVDA', 'TSM'])
def test_independent_research_capture_trigger_ids_preserve_same_flow_identity(entity):
    import importlib.util
    from ats.config import REPO_ROOT
    spec = importlib.util.spec_from_file_location('research_capture_trigger',
                                                 REPO_ROOT / 'scripts/capture_phase_f_research.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    research_trigger_id = module.research_trigger_id
    information = research_trigger_id(entity, with_routine=False)
    routine = research_trigger_id(entity, with_routine=True)
    assert information != routine
    assert information == research_trigger_id(entity, with_routine=False)
    assert routine == research_trigger_id(entity, with_routine=True)


def test_identical_syndication_consumes_one_read_and_numeric_update_remains_available():
    rows=[selection.Candidate(article('a',source='ibkr_news',body='Microsoft capacity increased 10%.'),0,'target_title'),
          selection.Candidate(article('b',source='trendforce_news',body='Microsoft capacity increased 10%.'),0,'target_title'),
          selection.Candidate(article('c',source='trendforce_news',body='Microsoft capacity increased 20%.'),0,'target_title')]
    chosen=selection.choose(rows,max_articles=8,exploration_articles=2,cached_versions=set())
    assert len(chosen)==2
    assert {r.article.body for r in chosen}=={'Microsoft capacity increased 10%.','Microsoft capacity increased 20%.'}


def test_same_title_near_body_with_new_numbers_is_not_suppressed():
    common=' '.join(f'context{i}' for i in range(120))
    rows=[selection.Candidate(article('ten',title='Microsoft capacity update',
        body=common+' Microsoft increased capacity by 10%.'),0,'target_title'),
        selection.Candidate(article('fifteen',title='Microsoft capacity update',
        body=common+' Microsoft increased capacity by 15%.'),0,'target_title')]
    chosen=selection.choose(rows,max_articles=8,exploration_articles=0,cached_versions=set())
    assert {r.article.id for r in chosen}=={'ten','fifteen'}


def test_wire_footer_timestamp_alone_does_not_consume_a_second_read():
    body='Microsoft increased capacity by 10%.\n\n(END) Dow Jones Newswires\n'
    rows=[selection.Candidate(article('earlier',source='ibkr_news',body=body+'October 10, 2026 10:00 ET'),0,'target_title'),
          selection.Candidate(article('later',source='ibkr_news',body=body+'October 11, 2026 12:00 ET'),0,'target_title')]
    assert len(selection.choose(rows,max_articles=8,exploration_articles=0,cached_versions=set()))==1
    assert len(rows)==2


def test_uncapped_selection_reads_all_distinct_direct_material_and_keeps_dedup():
    rows=[selection.Candidate(article(str(i),body=f'Microsoft capacity increased {i}%.'),0,'target_title')
          for i in range(12)]
    rows.append(selection.Candidate(article('duplicate',body='Microsoft capacity increased 0%.'),0,'target_title'))
    assert len(selection.choose(rows,max_articles=8,exploration_articles=0,cached_versions=set()))==8
    assert len(selection.choose(rows,max_articles=None,exploration_articles=0,cached_versions=set()))==12


def test_cached_quote_reuses_its_original_occurrence_not_first_equal_text(monkeypatch):
    from ats.schemas.research import Insight
    from ats.data.articles.material_quality import visible_analysis_view
    bound(monkeypatch)
    phrase='Microsoft increased capacity. '
    art=article('repeat',body=phrase*20)
    view=visible_analysis_view(art.body,title=art.title,source=art.source)
    start=len(phrase)*4
    quote=view.text[start:start+len(phrase)]
    insight=Insight(article_id=art.id,ticker='MSFT',summary='Capacity increased',evidence_quote=quote,
        evidence_locator=view.locator(start,start+len(quote)),confidence=.5,materiality=.2)
    cache={'projection_id':'cached','payload':{'insights':[insight.model_dump(mode='json')]}}
    store=SimpleNamespace(information_extraction_cache=lambda **kw:cache)
    monkeypatch.setattr(assemble,'admitted_articles',lambda *a,**kw:[art])
    monkeypatch.setattr(target_research,'_publish',lambda *a,**kw:'brief')
    monkeypatch.setattr(extract,'run_structured',lambda *a,**kw:pytest.fail('cache must reuse'))
    result=target_research.run(store,'MSFT',config=CONFIG,since=NOW-timedelta(days=7),use_llm=True)
    assert result['cache_hits']==1 and result['model_calls']==0


def test_cached_supporting_context_must_resolve_as_well_as_core_quote(monkeypatch):
    from ats.schemas.research import Insight
    from ats.data.articles.material_quality import visible_analysis_view
    bound(monkeypatch)
    art=article('context-cache',body='Microsoft increased capacity. Units are gigawatts.')
    view=visible_analysis_view(art.body,title=art.title,source=art.source)
    core='Microsoft increased capacity.'
    insight=Insight(article_id=art.id,ticker='MSFT',summary='来源更新产能。',evidence_quote=core,
        evidence_locator=view.locator(0,len(core)),materiality=.2,
        evidence_context=[{'quote':'Units are dollars.','locator':view.locator(len(core)+1,len(view.text))}])
    cache={'projection_id':'cached','payload':{'insights':[insight.model_dump(mode='json')]}}
    store=SimpleNamespace(information_extraction_cache=lambda **kw:cache)
    monkeypatch.setattr(assemble,'admitted_articles',lambda *a,**kw:[art])
    monkeypatch.setattr(extract,'run_structured',lambda *a,**kw:pytest.fail('no model on corrupt cache'))
    with pytest.raises(ValueError,match='cache evidence'):
        target_research.run(store,'MSFT',config=CONFIG,since=NOW-timedelta(days=7),use_llm=True)


def test_quiet_window_publishes_an_explicit_brief_instead_of_nothing(monkeypatch):
    """ASML: its only recent filing left the look-back window, so there were no candidates.  The task used to
    publish nothing, which the Dispatcher counted as a failed (missing) task."""
    from ats.memory import get_store

    store = get_store()
    bound(monkeypatch)
    monkeypatch.setattr(assemble, 'admitted_articles', lambda store, **kw: [])
    monkeypatch.setattr(target_research, 'evaluation_now', lambda tz: NOW)
    monkeypatch.setattr(extract, 'run_structured', lambda *a, **k: pytest.fail('no model call without material'))
    report = target_research.run(store, 'MSFT', config=CONFIG, since=NOW - timedelta(days=7), use_llm=True)
    assert report['status'] == 'published_no_candidates' and report['model_calls'] == 0
    payload = store.get_task_projection(report['projection_id'])['payload']
    assert payload['entity'] == 'MSFT' and payload['relevance'] == 'low' and payload['confidence'] == 0.0
    assert not payload.get('source_evidence') and not payload.get('source_count')
    assert '7 天' in payload['summary'] and payload['unverified']

    # without a model the run still publishes nothing (selected_no_model path is unchanged)
    offline = target_research.run(store, 'MSFT', config=CONFIG, since=NOW - timedelta(days=7), use_llm=False)
    assert offline['projection_id'] == '' and offline['status'] == 'selected_no_model'
