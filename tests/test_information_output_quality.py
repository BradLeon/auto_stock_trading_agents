from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from ats.agent.task_projection import EnvelopeValidationError
from ats.agents.information import extract, target_research
from ats.agents.information.briefs import assert_no_advice
from ats.agents.information.outputs import InformationInsightBatchView
from ats.data.articles.material_quality import visible_analysis_view
from ats.schemas.research import Article


@pytest.mark.parametrize('text', [
    'Form 4披露：高管以273美元卖出1,100股。',
    '报道指出Nvidia股价接近杯柄买入点。',
    'The article describes a cup-with-handle buy point.',
    '历史记录显示总裁、董事长、CFO等多位高管多次卖出股票。',
    'SpaceX is aiming to raise funding to buy Nvidia chips.',
])
def test_historical_and_technical_descriptions_are_not_orders(text):
    assert_no_advice([text])


@pytest.mark.parametrize('text', [
    '建议在买入点买入NVDA。', '我们应该卖出股票。',
    '高管建议卖出1,100股。', 'You should sell at the buy point.',
    '目标仓位为10%。', 'Buy NVDA.', 'We recommend buy.',
])
def test_actual_recommendations_remain_rejected(text):
    with pytest.raises(EnvelopeValidationError):
        assert_no_advice([text])


def batch(path, *, quote='Nvidia expects chip demand to grow.'):
    return InformationInsightBatchView(insights=[dict(ticker='MRVL', direction='neutral',
        impact_path='competitive', summary='同行的需求预测可能提供行业背景，尚未确认MRVL订单。',
        source_fact='Nvidia预计芯片需求增长。', source_kind='forecast', source_actor='Nvidia',
        association_type='business_related', association_reason='行业需求来源信息',
        information_topic='demand', information_quality='substantive',
        relation_path=path, evidence_quote=quote,
        evidence_span_id=f'ev:0:{len("Nvidia expects chip demand to grow.")}', materiality=.3, confidence=.4)])


@pytest.mark.parametrize('path', [['NVDA','MU','MRVL'], ['TSM','MRVL'], [], ['NVDA','NVDA','MRVL']])
def test_association_context_is_not_a_required_static_graph_route(path):
    art=Article(id='doc',source='trendforce_news',title='Demand',
        published_at=datetime.now(timezone.utc), body='Nvidia expects chip demand to grow.')
    view=visible_analysis_view(art.body,title=art.title,source=art.source)
    insights=extract.validate_extraction(art,view,batch(path),{'MRVL','NVDA','TSM','MU'},40000,
        assess_materiality=True,universe_card='- MRVL (target)\n- NVDA (peer of MRVL)\n- TSM (upstream of MRVL)')
    assert insights[0].ticker=='MRVL'
    assert insights[0].relation_path==[]


def test_source_prediction_stays_separate_from_issuer_inference(monkeypatch):
    art=Article(id='doc',document_version_id='doc@v',publication_id='pub',source='trendforce_news',title='Demand',
        published_at=datetime.now(timezone.utc),body='Nvidia expects chip demand to grow.')
    view=visible_analysis_view(art.body,title=art.title,source=art.source)
    insight=extract.validate_extraction(art,view,batch(['NVDA','MRVL']),{'MRVL','NVDA'},40000,
        assess_materiality=True,universe_card='- MRVL (target)\n- NVDA (peer of MRVL)')[0]
    captured={}
    def publish(store,payload,**kw):captured.update(payload);return 'brief'
    monkeypatch.setattr(target_research,'publish_information_brief',publish)
    target_research._publish(None,'MRVL',[(art,insight,'cache')])
    assert captured['fact_changes']==['来源信息（Nvidia）：Nvidia预计芯片需求增长。']
    assert all('MRVL订单' not in fact for fact in captured['fact_changes'])
    assert captured['impact_candidates']==[]
    assert captured['source_evidence'][0]['statement_type']=='forecast'


def test_quote_paraphrase_is_still_rejected():
    art=Article(id='doc',source='trendforce_news',title='Demand',
        published_at=datetime.now(timezone.utc),body='Nvidia expects chip demand to grow.')
    view=visible_analysis_view(art.body,title=art.title,source=art.source)
    with pytest.raises(ValueError,match='unlocated'):
        extract.validate_extraction(art,view,batch(['NVDA','MRVL'],quote='Nvidia expects ... demand to grow.'),
            {'MRVL','NVDA'},40000,assess_materiality=True)


def test_source_category_is_metadata_not_keyword_based_admission():
    art=Article(id='doc',source='trendforce_news',title='Demand',
        published_at=datetime.now(timezone.utc),body='Nvidia expects chip demand to grow.')
    view=visible_analysis_view(art.body,title=art.title,source=art.source)
    response=batch(['NVDA','MRVL'])
    response.insights[0].source_kind='reported_fact'
    insight=extract.validate_extraction(art,view,response,{'MRVL','NVDA'},40000,
        assess_materiality=True,universe_card='- NVDA (peer of MRVL)')[0]
    assert '预计' in insight.source_fact
    assert insight.evidence_quote==art.body


def test_one_source_fact_does_not_multiply_when_multiple_implications_are_published(monkeypatch):
    from ats.schemas.research import Insight
    art=Article(id='doc',document_version_id='doc@v',publication_id='pub',source='trendforce_news',
        title='Demand',published_at=datetime.now(timezone.utc),body='Nvidia expects chip demand to grow.')
    shared=dict(article_id='doc',evidence_quote=art.body,evidence_locator={'start':0,'end':len(art.body)},
        source_fact='Nvidia预计芯片需求增长。',source_kind='forecast',source_actor='Nvidia',confidence=.4,materiality=.3)
    a=Insight(**shared,ticker='NVDA',summary='来源预测',impact_path='direct')
    b=Insight(**shared,ticker='MRVL',summary='同行背景，MRVL订单未确认',impact_path='competitive',relation_path=['NVDA','MRVL'])
    captured={}
    monkeypatch.setattr(target_research,'publish_information_brief',lambda store,payload,**kw:captured.update(payload) or 'brief')
    target_research._publish(None,'MRVL',[(art,a,'cache'),(art,b,'cache')])
    assert len(captured['fact_changes'])==1
    assert {e['fact_index'] for e in captured['source_evidence']}=={0}
    assert captured['impact_candidates']==[]


def test_quote_bytes_are_resolved_from_span_not_generated_by_model():
    art=Article(id='doc',source='trendforce_news',title='Demand',
        published_at=datetime.now(timezone.utc),body='Nvidia expects chip demand to grow.')
    view=visible_analysis_view(art.body,title=art.title,source=art.source)
    response=batch(['NVDA','MRVL'],quote='')
    insight=extract.validate_extraction(art,view,response,{'NVDA','MRVL'},40000,
        assess_materiality=True,universe_card='- NVDA (peer of MRVL)')[0]
    assert insight.evidence_quote==art.body
    assert art.body[insight.evidence_locator['start']:insight.evidence_locator['end']]==art.body
    response.insights[0].evidence_span_id='ev:1:7'
    with pytest.raises(ValueError,match='unlocated'):
        extract.validate_extraction(art,view,response,{'NVDA','MRVL'},40000,assess_materiality=True)


def test_span_ids_cover_truncated_input_and_preserve_repeated_source_offsets():
    text='Nvidia expanded capacity. '*80
    spans=extract.evidence_spans(text[:500])
    assert ''.join(text[start:end] for start,end in spans.values())==text[:500]
    assert all(0<=start<end<=500 and end-start<=extract.EVIDENCE_SPAN_CHARS for start,end in spans.values())


def test_source_contract_does_not_request_investment_direction_or_causal_path():
    fields=InformationInsightBatchView.model_json_schema()['$defs']['InformationInsightView']['properties']
    assert not {'direction','impact_path','relation_path'} & fields.keys()
    assert {'association_type','association_reason','information_topic','information_quality'} <= fields.keys()


@pytest.mark.parametrize('association,quality,expected', [
    ('direct_update','substantive','adopted'),
    ('business_related','substantive','adopted'),
    ('background','substantive','background'),
    ('unrelated','substantive','unrelated'),
    ('direct_update','thin','thin'),
])
def test_reading_triage_routes_background_and_thin_content_without_investment_verdict(association,quality,expected):
    from ats.agents.information.triage import reading_bucket
    from ats.schemas.research import Insight
    item=Insight(article_id='source',ticker='MSFT',association_type=association,
        information_quality=quality,summary='带来源归属的预测或观点')
    assert reading_bucket(item,'MSFT')==expected


def test_bound_source_channel_allows_reported_transactions_but_rejects_own_advice():
    assert_no_advice(['来源报告A卖出100股；B卖出200股。'],source_statement=True)
    assert_no_advice(['某分析师建议买入该股。'],source_statement=True)
    with pytest.raises(EnvelopeValidationError):
        assert_no_advice(['我们应该卖出该股。'],source_statement=True)


@pytest.mark.parametrize('tamper',['none','fact','summary','impact','headline'])
def test_source_evidence_channel_does_not_exempt_system_fields(tamper):
    from ats.agents.information.briefs import publish_information_brief
    from ats.agent.task_projection import ProjectionScope
    recap='卖出100股，剩余909股。'
    statement=f'来源信息（出版者）：{recap}'
    payload=dict(entity='MSFT',headline='材料更新',summary=statement,relevance='medium',
        fact_changes=[statement],unverified=['来源需核验'],confidence=.8,
        sources=['publisher'],entities=['MSFT'],impact_candidates=[],
        source_evidence=[dict(fact_index=0,source_actor='出版者',source_summary=recap,
            evidence_quote='Sale 100 Remaining 909',document_ref='doc@v',publication_id='pub',
            extraction_projection_id='cache',locator={'start':0,'end':22})])
    if tamper=='fact':payload['fact_changes']=['建议卖出100股。']
    if tamper=='summary':payload['summary']='我们应该卖出100股。'
    if tamper=='impact':payload['impact_candidates']=['建议卖出100股。']
    if tamper=='headline':payload['headline']='建议卖出100股。'
    saved=[]
    store=SimpleNamespace(save_task_projection_envelope=lambda envelope:saved.append(envelope))
    if tamper=='none':
        publish_information_brief(store,payload,scope=ProjectionScope(kind='entity',id='MSFT'),as_of='2026-10-12T12:00:00+00:00')
        assert len(saved)==1
    else:
        with pytest.raises(EnvelopeValidationError):
            publish_information_brief(store,payload,scope=ProjectionScope(kind='entity',id='MSFT'),as_of='2026-10-12T12:00:00+00:00')
        assert not saved
