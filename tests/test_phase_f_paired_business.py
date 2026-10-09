"""Actual legacy/new business entry acceptance; only external interfaces are fixtures."""
import copy
import json
import os
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from test_phase_f_research_gate import business_inputs as research_business_inputs
from test_phase_f_research_gate import research_isolation

from ats.workflow import paired_business as paired
from ats.workflow import shadow_replay as replay
from ats.workflow.isolation import verified_isolation_root


@pytest.fixture(name='isolated')
def isolated_run(tmp_path):
    yield from research_isolation.__wrapped__(tmp_path)


@pytest.fixture(name='business_inputs')
def input_fixture(monkeypatch, isolated, tmp_path):
    yield from research_business_inputs.__wrapped__(isolated, monkeypatch, tmp_path)


def request(tasks=None, risk=False):
    body={'tasks': tasks or ['technical-review'],
          'scope': {'kind':'entity','id':'COHR'},
          'trigger': {'kind':'manual','trigger_id':'same-business-trigger'},
          'logical_time':datetime.now(UTC).replace(microsecond=0).isoformat(),
          'task_inputs':{'fundamental-routine':{'use_llm':False}}}
    if risk:
        body['risk']={'cycle_id':'paired-risk', 'orders':[{'symbol':'COHR','action':'buy','notional_usd':1000}]}
    return body


def test_actual_technical_pair_uses_frozen_price_history_and_clock(business_inputs, isolated, monkeypatch, record_property):
    root=verified_isolation_root();store=root/'inputs.sqlite'
    captured=paired.capture(request=request(), input_store=store, work_root=root/'capture', run_id='capture-technical')
    from ats.data.runtime import market_data
    monkeypatch.setattr(market_data,'fetch_close_history_many',lambda *a,**k:pytest.fail('replay queried provider'))
    result=paired.run_pair(input_store=store,input_hash=captured['input_hash'],root=root/'pair',pair_id='technical-pair')
    assert result['left']['projections'] and result['right']['projections']
    value=replay.load_inputs(captured['input_hash'],path=store)
    assert 'src/ats/agents/base.py' in value.contents['model_config']['dependency_hashes']
    assert 'src/ats/skills/technical-analyst/SKILL.md' in value.contents['model_config']['dependency_hashes']
    point=value.packet.surfaces['logical_eval_time']
    assert all(p['as_of']==point for side in ['left','right'] for p in result[side]['projections'])
    assert all(result[side]['logical_time']==point for side in ['left','right'])
    record_property('actual_pair',json.dumps(result))
    with sqlite3.connect(store) as conn:
        runs=[json.loads(row[0]) for row in conn.execute('SELECT body FROM shadow_replay_runs')]
        assert len(runs)==2 and all(r['reads'] for r in runs)
        assert all(r['input_hash']==captured['input_hash'] for r in runs)


def test_actual_six_role_pair_does_not_requery_data_or_models(business_inputs, isolated, monkeypatch, record_property):
    root=verified_isolation_root();store=root/'six-inputs.sqlite'
    from ats.agents import base
    from ats.agents.information import extract
    original = base.run_structured
    def responses(agent, schema, context, **kwargs):
        if schema.__name__ == 'InsightBatchView':
            return schema(insights=[])
        return original(agent, schema, context, **kwargs)
    monkeypatch.setattr(extract, 'run_structured', responses)
    req=request(['sector-review','fundamental-routine','macro-review','technical-review'])
    req['scope']={'kind':'sector','id':'ai_hardware'}
    req['profile_id']='ai_hardware'
    req['task_inputs']['data_vintage_refs']=[business_inputs.document.sha256]
    captured=paired.capture(request=req,input_store=store,work_root=root/'six-capture',run_id='capture-six')
    def forbidden(*a,**k):raise AssertionError('replay reached data/model source')
    from ats.data.runtime import market_data
    monkeypatch.setattr(market_data,'_download_close_frame',forbidden)
    monkeypatch.setattr('ats.data.products.get_platform_data_products',forbidden)
    monkeypatch.setattr('ats.agents.base.run_structured',forbidden)
    result=paired.run_pair(input_store=store,input_hash=captured['input_hash'],root=root/'six-pair',pair_id='six-pair')
    roles={'information_brief','layer_analysis','sector_allocation','fundamental_expectation_update','macro_review','technical_review'}
    assert all(roles <= {p['agent_role'] for p in result[side]['projections']} for side in ['left','right'])
    record_property('actual_six_role_pair',json.dumps(result))


def test_actual_risk_old_adapter_and_revision_review_share_frozen_portfolio(business_inputs, isolated, monkeypatch, record_property):
    from ats.schemas.portfolio import PortfolioSnapshot
    root=verified_isolation_root();store=root/'risk-inputs.sqlite'
    req=request(risk=True)
    portfolio=PortfolioSnapshot(as_of=datetime.fromisoformat(req['logical_time']),net_liquidation=1e6,
        cash=1e6,daily_pnl=0,gross_exposure=0,positions=[])
    monkeypatch.setattr('ats.trader.portfolio.snapshot',lambda:portfolio.model_copy(deep=True))
    captured=paired.capture(request=req,input_store=store,work_root=root/'risk-capture',run_id='capture-risk')
    monkeypatch.setattr('ats.trader.portfolio.snapshot',lambda:pytest.fail('replay queried broker'))
    result=paired.run_pair(input_store=store,input_hash=captured['input_hash'],root=root/'risk-pair',pair_id='risk-pair')
    assert result['left']['risk']['entry']=='ats.risk.checks.pre_trade'
    assert result['right']['risk']['entry']=='ats.risk.checks.review_revision'
    assert result['right']['risk']['review']['basis']['portfolio_snapshot_id']=='pf:'+req['logical_time']
    record_property('actual_risk_pair',json.dumps(result))


def test_event_mode_actual_pair_preserves_admitted_release_and_published_score(business_inputs, isolated, monkeypatch, record_property):
    from datetime import timedelta

    from ats.agents.information import extract
    from ats.data import document_assets
    from ats.data.stores.unstructured import get_platform_unstructured_store
    monkeypatch.setattr(extract,'run_structured',lambda agent,schema,context,**kw:schema(insights=[]))
    repo=get_platform_unstructured_store();now=datetime.now(UTC)-timedelta(seconds=2)
    release=document_assets.ingest(entity='COHR',key='pair-release',doc_type='company_release',
        text='Q3 FY2026 results revenue 120 earnings 3. '*30,source='ibkr_news',source_url='fixture:pair-release',
        title='Q3 FY2026 results',period='Q3 FY2026',published_at=now.isoformat(),now=now,min_chars=1,store=repo)
    repo.close();assert release
    root=verified_isolation_root();store=root/'event-inputs.sqlite'
    req=request(['fundamental-event'])
    req['task_inputs']['fundamental-event']={'use_llm':False,'fiscal_label':'Q3 FY2026',
        'trigger':'earnings_release','material_state':'admitted','admitted_material_refs':[release.document_id]}
    captured=paired.capture(request=req,input_store=store,work_root=root/'event-capture',run_id='capture-event')
    result=paired.run_pair(input_store=store,input_hash=captured['input_hash'],root=root/'event-pair',pair_id='event-pair')
    for side in ['left','right']:
        assert any(p['agent_role']=='fundamental_event_review' for p in result[side]['projections'])
        with sqlite3.connect(os.path.join(result[side]['side_root'],'memory.sqlite')) as conn:
            assert conn.execute('SELECT count(*) FROM pead_score_runs').fetchone()[0]==1
    record_property('actual_event_pair',json.dumps(result))


def test_actual_cli_new_process_pair_and_proof_use_durable_packet(business_inputs, isolated, monkeypatch, tmp_path, record_property):
    import subprocess

    root=verified_isolation_root();store=root/'cli-inputs.sqlite'
    captured=paired.capture(request=request(),input_store=store,work_root=root/'cli-capture',run_id='capture-cli')
    args=['uv','run','--offline','--no-sync','ats','shadow','run-pair','--isolation-root',str(root),
          '--input-store',str(store),'--packet-hash',captured['input_hash'],'--pair-id','actual-cli-pair']
    result=subprocess.run(args,env=os.environ.copy(),capture_output=True,text=True,check=False)
    assert result.returncode==0,result.stderr+result.stdout
    output=json.loads(result.stdout)
    _value,runs=replay.verify_execution_evidence(output['proof'],report=SimpleNamespace(packet_hash=captured['input_hash']),scope=request()['scope'])
    assert len(runs)==2 and all(r['reads'] for r in runs)
    record_property('cli_argv',json.dumps(args));record_property('actual_cli_result',result.stdout)
    with sqlite3.connect(store) as conn:
        assert conn.execute("SELECT count(*) FROM paired_business_events WHERE pair_id='actual-cli-pair'").fetchone()[0]==4
        with pytest.raises(sqlite3.IntegrityError,match='append-only'):
            conn.execute('DELETE FROM paired_business_events')


@pytest.mark.parametrize('damage',['missing_input','fingerprint','scope','reused_destination'])
def test_pair_refuses_uncaptured_drifted_scope_or_existing_claims(business_inputs,isolated,tmp_path,damage,record_property):
    root=verified_isolation_root();store=root/(damage+'-inputs.sqlite')
    captured=paired.capture(request=request(),input_store=store,work_root=root/(damage+'-capture'),run_id='capture-'+damage)
    value=replay.load_inputs(captured['input_hash'],path=store)
    target=root/(damage+'-pair')
    if damage=='reused_destination':
        paired.run_pair(input_store=store,input_hash=captured['input_hash'],root=target,pair_id='existing')
        with pytest.raises(ValueError,match='new directory'):
            paired.run_pair(input_store=store,input_hash=captured['input_hash'],root=target,pair_id='retry')
    else:
        content=copy.deepcopy(value.contents)
        if damage=='missing_input':content['model_config']['rows']={}
        elif damage=='fingerprint':content['model_config']['dependency_hashes']={}
        else:content['ruleset_version']['request']['scope']['id']='NVDA'
        damaged=replay.freeze_inputs(run_id='damaged',consumer_id=value.packet.consumer_id,batch_class=value.packet.batch_class,
            scope=value.packet.scope,logical_eval_time=value.packet.surfaces['logical_eval_time'],contents=content,reads=value.reads)
        identity=replay.save_inputs(damaged,path=store)
        with pytest.raises((ValueError,PermissionError)):
            paired.run_pair(input_store=store,input_hash=identity,root=target,pair_id='damaged')
    with sqlite3.connect(store) as conn:
        events=[json.loads(row[0]) for row in conn.execute('SELECT body FROM paired_business_events')] if conn.execute("SELECT 1 FROM sqlite_master WHERE name='paired_business_events'").fetchone() else []
    record_property('refusal_events',json.dumps(events))
