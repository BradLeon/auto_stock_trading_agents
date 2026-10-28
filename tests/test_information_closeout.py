"""Finite source-selection closeout cases; no provider or model network calls."""
from datetime import UTC, datetime

import pytest

from ats.agents.information import extract, selection
from ats.agents.information.outputs import InformationInsightBatchView
from ats.data.articles.material_quality import visible_analysis_view
from ats.schemas.research import Article


def article(key, body):
    return Article(id=key, document_version_id=key+'@v', source='ibkr_news',
        title='Market update', body=body, published_at=datetime(2026,10,12,tzinfo=UTC))


def response(ids):
    return InformationInsightBatchView(insights=[dict(ticker='MSFT', source_fact='微软投资OpenAI。',
        summary='来源报道微软投资OpenAI。',source_actor='原出版者',source_kind='reported_fact',
        association_type='business_related',association_reason='目标投资关系',
        information_topic='investment',information_quality='substantive',materiality=.5,
        evidence_span_id=ids[0],supporting_evidence_span_ids=ids[1:])])


def evidence_choice(quote, **kwargs):
    text=kwargs.get('text',quote)
    def reference(needle):
        start=text.find(needle)
        if start<0:return {'first_chunk':9999,'last_chunk':9999}
        chunks=extract.source_chunks(text)
        first=next(i for i,(left,right) in enumerate(chunks,1) if left<=start<right)
        last=next(i for i,(left,right) in enumerate(chunks,1) if left<start+len(needle)<=right)
        return {'first_chunk':first,'last_chunk':last}
    return dict(core=reference(quote),source_actor='原出版者',source_kind='reported_fact',
        target_role=kwargs.get('target_role','subject'),
        context_ranges=[reference(part) for part in kwargs.get('context_quotes',[])],
        information_topic='investment',information_quality='substantive',materiality=.5)


def test_statement_crossing_span_boundary_keeps_one_exact_contiguous_locator():
    body=' '.join(['context']*160)+' Microsoft invested in OpenAI, which is valued at $1.4 trillion. '
    art=article('crossing',body);view=visible_analysis_view(body,title=art.title,source=art.source)
    ids=list(extract.evidence_spans(view.text))
    assert len(ids)==2
    result=extract.validate_extraction(art,view,response(ids),{'MSFT'},40000,assess_materiality=True)[0]
    assert result.evidence_quote==body.rstrip()
    loc=result.evidence_locator
    assert body[loc['start']:loc['end']]==result.evidence_quote
    assert 'Microsoft invested' in result.evidence_quote and '$1.4 trillion' in result.evidence_quote


@pytest.mark.parametrize('kind',['gap','duplicate','outside_input'])
def test_multispan_cannot_join_distant_or_unread_evidence(kind):
    art=article('bad','Microsoft invested in OpenAI. '*160)
    view=visible_analysis_view(art.body,title=art.title,source=art.source)
    ids=list(extract.evidence_spans(view.text))
    batch=response([ids[0],ids[2]] if kind=='gap' else [ids[0],ids[0]] if kind=='duplicate' else [ids[0],ids[-1]])
    with pytest.raises(ValueError,match='adjacent|unlocated'):
        extract.validate_extraction(art,view,batch,{'MSFT'},400 if kind=='outside_input' else 40000,assess_materiality=True)


def test_original_url_and_equal_target_paragraphs_merge_macro_updated_wire():
    target='Nvidia stock fell 2% to 229.28. NVDA reached 243.37 on Tuesday.'
    footer='\n\nView this article at Investors.com\n\nhttps://www.investors.com/same-article/\n'
    arts=[article('a','Oil price commentary.\n\n'+target+footer),
          article('b','A different geopolitical development.\n\n'+target+footer),
          article('new','Oil price commentary.\n\n'+target.replace('229.28','230.12')+footer)]
    rows,_=selection.rank_articles(arts,symbol='NVDA',universe={'NVDA'},publishers=[])
    picked=selection.choose(rows,max_articles=None,exploration_articles=0,cached_versions=set())
    assert len(picked)==2 and 'new' in {r.article.id for r in picked}
    assert len(rows)==3  # source versions are retained


def test_same_target_paragraph_from_different_original_source_is_not_forced_duplicate():
    body='Nvidia announced a new product.\n\nView this article at News\n\nhttps://example.com/a'
    arts=[article('a',body),article('b',body.replace('/a','/b'))]
    rows,_=selection.rank_articles(arts,symbol='NVDA',universe={'NVDA'},publishers=[])
    assert len(selection.choose(rows,max_articles=None,exploration_articles=0,cached_versions=set()))==2


def test_published_evidence_discloses_full_document_but_truncated_model_input(monkeypatch):
    from ats.agents.information import target_research
    art=article('long','Microsoft invested in OpenAI. '*160)
    view=visible_analysis_view(art.body,title=art.title,source=art.source)
    ids=list(extract.evidence_spans(view.text[:1200]))
    insight=extract.validate_extraction(art,view,response(ids),{'MSFT'},1200,assess_materiality=True)[0]
    captured={}
    monkeypatch.setattr(target_research,'publish_information_brief',lambda store,payload,**kw:captured.update(payload) or 'brief')
    target_research._publish(None,'MSFT',[(art,insight,'cache')])
    ev=captured['source_evidence'][0]
    assert ev['completeness']=='full' and ev['input_truncated'] is True and ev['visible_input_chars']==1200


def test_information_processing_does_not_require_ingestion_lease_or_write_platform(monkeypatch):
    from ats.memory.store import TradingMemory
    from ats.data import persistent_queue
    store=TradingMemory(':memory:')
    try:
        neutral=store.data_store().conn.total_changes
        monkeypatch.setattr(persistent_queue,'require_queue_worker',lambda *a:pytest.fail('analyst requested ingestion lease'))
        assert store.begin_information_processing('doc','MSFT','p',version_id='doc@v')=='doc@v'
        assert store.begin_information_processing('doc','MSFT','p',version_id='doc@v') is None
        store.finish_information_processing('doc@v','MSFT','p',ok=False)
        assert store.begin_information_processing('doc','MSFT','p',version_id='doc@v',retry_failed=True)=='doc@v'
        store.finish_information_processing('doc@v','MSFT','p',ok=True)
        assert store.begin_information_processing('doc','MSFT','p',version_id='doc@v',retry_failed=True) is None
        assert store.begin_information_processing('doc','NVDA','p',version_id='doc@v')=='doc@v'
        assert store.data_store().conn.total_changes==neutral
    finally:store.close()


def test_evidence_first_recap_cannot_see_wrong_neighbor_or_first_pass_summary(monkeypatch):
    from ats.agents.information.outputs import InformationEvidenceBatchView, InformationEvidenceRecapView
    quote='Microsoft invested in OpenAI, which is valued at $1.4 trillion.'
    neighbor='Nvidia revenue grew 120% and market capitalization reached $6 trillion.'
    art=article('binding',quote+'\n\n'+neighbor)
    calls=[]
    def model(role,schema,context,**kwargs):
        calls.append(context)
        if schema is InformationEvidenceBatchView:
            assert neighbor in context
            return schema(evidence=[evidence_choice(quote)])
        assert schema is InformationEvidenceRecapView
        assert quote in context and neighbor not in context and '$6 trillion' not in context
        assert 'evidence_span_id' not in schema.model_fields
        return schema(source_fact='微软投资的OpenAI估值为1.4万亿美元。',
            summary='来源报道微软投资OpenAI，其估值为1.4万亿美元。',source_actor='原出版者',
            source_kind='reported_fact',association_type='business_related',association_reason='微软投资关系',
            information_topic='investment',information_quality='substantive',materiality=.5,confidence=.9)
    monkeypatch.setattr(extract,'run_structured',model)
    diagnostics={}
    result=extract._extract(art,'MSFT',{'MSFT'},40000,assess_materiality=True,target='MSFT',diagnostics=diagnostics)[0]
    assert result.evidence_quote==quote and result.ticker=='MSFT'
    assert art.body[result.evidence_locator['start']:result.evidence_locator['end']]==quote
    assert diagnostics['model_invocations']==2 and len(calls)==2


def test_all_selected_ranges_validate_before_any_recap(monkeypatch):
    from ats.agents.information.outputs import InformationEvidenceBatchView
    art=article('invented','Microsoft invested in OpenAI.')
    calls=[]
    def model(role,schema,context,**kwargs):
        calls.append(context)
        return InformationEvidenceBatchView(evidence=[evidence_choice(art.body),evidence_choice('Microsoft bought Nvidia.',text=art.body)])
    monkeypatch.setattr(extract,'run_structured',model)
    with pytest.raises(ValueError,match='unlocated source range'):
        extract._extract(art,'MSFT',{'MSFT'},40000,assess_materiality=True,target='MSFT')
    assert len(calls)==1


def test_evidence_dispatch_deadline_prevents_another_model_call(monkeypatch):
    monkeypatch.setattr(extract.time,'monotonic',lambda:10)
    monkeypatch.setattr(extract,'run_structured',lambda *a,**kw:pytest.fail('dispatch after deadline'))
    with pytest.raises(TimeoutError):
        extract._extract(article('late','Microsoft capacity increased.'),'MSFT',{'MSFT'},40000,
            assess_materiality=True,target='MSFT',deadline=9)


def test_source_ranges_preserve_original_whitespace_quotes_and_numeric_units():
    from ats.agents.information.outputs import InformationSourceRangeView
    text='Microsoft \n \ninvested $10 billion in OpenAI. The component is 6" wide.'
    chunks=extract.source_chunks(text)
    assert ''.join(text[start:end] for start,end in chunks)==text
    quote,start=extract.resolve_source_range(text,InformationSourceRangeView(first_chunk=1,last_chunk=len(chunks)),max_chars=1600)
    assert quote==text and start==0
    assert '$10 billion' in quote and '6"' in quote


def test_experimental_strategy_cannot_reuse_daily_extraction_cache():
    art=article('strategy','Microsoft announced new capacity.').model_copy(update={'interpretation_key':'same-source'})
    cfg={'article_chars':40000,'universe_card':'MSFT'}
    assert extract.processing_version(art,cfg)!=extract.processing_version(art,{**cfg,'evidence_first':True})


def test_daily_configuration_does_not_implicitly_enable_additional_calls(monkeypatch):
    from types import SimpleNamespace
    from ats.agents.information import target_research
    quote='Microsoft announced new capacity.'
    art=article('daily',quote)
    monkeypatch.setattr(selection,'candidates',lambda *a,**kw:([selection.Candidate(art,0,'target_title')],
        {'universe':['MSFT'],'universe_card':'- MSFT (target)'}))
    flags=[]
    def capture(*a,**kw):
        flags.append(kw['evidence_first']);return []
    monkeypatch.setattr(extract,'_extract',capture)
    monkeypatch.setattr(target_research,'_publish_empty',lambda *a,**kw:'empty')
    store=SimpleNamespace(information_extraction_cache=lambda **kw:None,
        begin_information_processing=lambda *a,**kw:'version',finish_information_processing=lambda *a,**kw:None,
        save_task_projection=lambda **kw:'cache')
    target_research.run(store,'MSFT',config={'article_chars':40000,'max_articles_per_run':None},
        since=datetime(2026,10,5,tzinfo=UTC),use_llm=True)
    assert flags==[False]


def test_article_level_product_association_survives_excerpt_without_company_name(monkeypatch):
    from ats.agents.information.outputs import InformationEvidenceBatchView,InformationEvidenceRecapView
    quote='Vera Rubin delivers 67x throughput per TCO compared with GB300 at 170 TPS.'
    art=article('ownership','NVIDIA makes Vera Rubin and GB300.\n\n'+quote)
    def model(role,schema,context,**kwargs):
        if schema is InformationEvidenceBatchView:
            assert 'NVIDIA makes' in context
            return schema(evidence=[evidence_choice(quote,text=art.body,target_role='product_owner',context_quotes=['NVIDIA makes Vera Rubin and GB300.'])])
        assert schema is InformationEvidenceRecapView and 'NVIDIA makes' in context
        assert not {'association_type','association_reason','source_kind','source_actor'} & schema.model_fields.keys()
        return schema(source_fact='在170 TPS下，Rubin每TCO吞吐量为GB300的67倍。',
            summary='来源对比Rubin与GB300：170 TPS下每TCO吞吐量为67倍。',confidence=.9)
    monkeypatch.setattr(extract,'run_structured',model)
    result=extract._extract(art,'NVDA',{'NVDA'},40000,assess_materiality=True,target='NVDA')[0]
    assert result.association_type=='direct_update'
    assert result.evidence_quote==quote and result.target_role=='product_owner'
    assert result.evidence_context[0]['quote']=='NVIDIA makes Vera Rubin and GB300.'


@pytest.mark.parametrize('source',['ibkr_news','trendforce_news','semianalysis'])
def test_core_context_contract_preserves_column_meaning_without_source_specific_parser(monkeypatch,source):
    from ats.agents.information.outputs import InformationEvidenceBatchView,InformationEvidenceRecapView
    header='Person Date Sold Shares Price Remaining Holdings'
    core='Executive 10/01/26 10,000 262.05 207,941'
    art=article('table','Microsoft insider transaction.\n'+header+'\n'+core).model_copy(update={'source':source})
    def model(role,schema,context,**kwargs):
        if schema is InformationEvidenceBatchView:
            return schema(evidence=[evidence_choice(core,text=art.body,context_quotes=[header])])
        assert header in context and core in context
        return schema(summary='2026年10月1日卖出10,000股，价格262.05，剩余持股207,941股。',confidence=.9)
    monkeypatch.setattr(extract,'run_structured',model)
    result=extract._extract(art,'MSFT',{'MSFT'},40000,assess_materiality=True,target='MSFT')[0]
    assert result.summary==result.source_fact
    assert any(header in part['quote'] for part in result.evidence_context)
    for part in result.evidence_context:
        assert art.body[part['locator']['start']:part['locator']['end']]==part['quote']
    assert result.evidence_quote==core


@pytest.mark.parametrize('role,bucket',[('subject','adopted'),('product_owner','adopted'),
    ('named_comparison','adopted'),('explicit_business_party','adopted'),('incidental','background'),('absent','unrelated')])
def test_recap_cannot_change_article_relevance_or_create_interpretation_reason(monkeypatch,role,bucket):
    from ats.agents.information.outputs import InformationEvidenceBatchView,InformationEvidenceRecapView
    from ats.agents.information.triage import reading_bucket
    art=article('roles','Microsoft was mentioned in this source statement.')
    def model(agent,schema,context,**kwargs):
        if schema is InformationEvidenceBatchView:return schema(evidence=[evidence_choice(art.body,target_role=role)])
        return schema(summary='来源提及微软。',confidence=.8,association_type='unrelated',association_reason='therefore bullish')
    monkeypatch.setattr(extract,'run_structured',model)
    result=extract._extract(art,'MSFT',{'MSFT'},40000,assess_materiality=True,target='MSFT')[0]
    assert reading_bucket(result,'MSFT')==bucket and 'bullish' not in result.association_reason
    assert result.source_fact==result.summary and result.target_role==role


@pytest.mark.parametrize('bad_context',['Microsoft invested $11 billion.','Hidden full article is unavailable.'])
def test_unlocated_context_cannot_support_a_published_fact(monkeypatch,bad_context):
    from ats.agents.information.outputs import InformationEvidenceBatchView
    art=article('context','Microsoft invested $10 billion.')
    calls=[]
    def model(role,schema,context,**kwargs):
        calls.append(context)
        return InformationEvidenceBatchView(evidence=[evidence_choice(art.body,text=art.body,context_quotes=[bad_context])])
    monkeypatch.setattr(extract,'run_structured',model)
    with pytest.raises(ValueError,match='unlocated'):
        extract._extract(art,'MSFT',{'MSFT'},40000,assess_materiality=True,target='MSFT')
    assert len(calls)==1


def test_unrelated_paragraph_and_old_span_boundaries_do_not_change_bound_recap(monkeypatch):
    from ats.agents.information.outputs import InformationEvidenceBatchView
    core='Microsoft invested $10 billion in OpenAI.'
    support='Microsoft is the investor; this is a source report.'
    summaries=[];prompts=[]
    def model(role,schema,context,**kwargs):
        if schema is InformationEvidenceBatchView:return schema(evidence=[evidence_choice(core,text=art.body,context_quotes=[support])])
        prompts.append(context)
        return schema(summary='报道微软向OpenAI投资100亿美元。',confidence=.9)
    monkeypatch.setattr(extract,'run_structured',model)
    for padding in ('','Unrelated company commentary. '*70):
        art=article('same',padding+support+'\n\n'+core)
        result=extract._extract(art,'MSFT',{'MSFT'},40000,assess_materiality=True,target='MSFT')[0]
        summaries.append(result.summary)
        assert art.body[result.evidence_locator['start']:result.evidence_locator['end']]==core
    assert summaries[0]==summaries[1] and prompts[0]==prompts[1]


@pytest.mark.parametrize('source',['ibkr_news','trendforce_news','semianalysis'])
def test_structural_parent_supplies_table_layout_when_selector_omits_context(monkeypatch,source):
    from ats.agents.information.outputs import InformationEvidenceBatchView
    header='PERSON ACTION AMOUNT PRICE REMAINING'
    core='A Sale 20 15.50 909'
    body='Reported company transactions.\n\n'+header+'\n\n'+core+'\nB Sale 10 12.50 100'
    art=article('layout',body).model_copy(update={'source':source})
    def model(role,schema,context,**kw):
        if schema is InformationEvidenceBatchView:
            return schema(evidence=[evidence_choice(core,text=body)])
        assert header in context and core in context
        return schema(summary='来源记录A卖出20，价格15.50，剩余909。',confidence=.9)
    monkeypatch.setattr(extract,'run_structured',model)
    result=extract._extract(art,'MSFT',{'MSFT'},40000,assess_materiality=True,target='MSFT')[0]
    assert any(header in p['quote'] and p['binding']=='complete_source_parent' for p in result.evidence_context)
    for p in result.evidence_context:
        assert body[p['locator']['start']:p['locator']['end']]==p['quote']


@pytest.mark.parametrize('prefix',['Oct. 12, 2022','Sept. 3, 2021','Dr. A. Smith'])
def test_abbreviations_are_not_interpreted_as_semantic_boundaries(prefix):
    body=f'Its {prefix} report gave a value of 909. Another report follows.'
    ranges=extract.source_chunks(body)
    assert any(body[left:right].startswith('Its '+prefix) for left,right in ranges)
    start=body.index('909')
    assert any(prefix in quote for quote,_ in extract.structural_context(body,start,start+3))


def test_oversize_source_parent_cannot_be_silently_cut_to_context_budget():
    body='Column layout\n\n'+('row 1 2 3\n'*500)
    with pytest.raises(ValueError,match='complete source parent'):
        extract.structural_context(body,body.index('row'),body.index('row')+9)
