"""9.2/13.1 actual CLI, scoped coordinator, projections and dependent writes.

External qualification/enforcement/signoff are explicit isolated fixtures. SQL
permissions, coordinator, projection reader, business entries and guards are real.
"""
import copy
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_phase_f_research_gate import seed_research, small_plan

from ats.agents.chief import assemble
from ats.decision.repository import DecisionAuditRepository
from ats.memory import get_store
from ats.runtime import cli
from ats.workflow import batch_manifest as bm
from ats.workflow import cutover as plane
from ats.workflow import cutover_wiring as wiring
from ats.workflow import joint_cutover as joint
from ats.workflow import read_cutover as executor
from ats.workflow import read_deployment as deployment
from ats.workflow import read_recovery as recovery
from ats.workflow import runtime_reads as reads
from ats.workflow import scoped_routes as scopes
from ats.workflow.business_replay_inputs import implementation_hashes
from ats.workflow.consumer_reads import trace_reads
from ats.workflow.cutover_routing import RouteUnavailable
from ats.workflow.isolation import isolated_run, verified_isolation_root


def put(path, body):
    path.write_text(json.dumps(body))
    return {"reference": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def policy(who, root):
    artifact = put(root / (who.key + '-stop.json'), {"version": recovery.VERSION,
        "identity": who.as_row(), "strategy": "stop",
        "dependency_hashes": recovery.dependency_hashes(who.consumer_id),
        "valid_until": (datetime.now(UTC)+timedelta(hours=1)).isoformat()})
    recovery.register(who, **artifact, actor='fixture', reason='pre-run explicit safe stop')


def batch_for(who, root, envelopes, name='read-one', route='target', edges=None):
    req = {"batch_id": name, "workflow": who.consumer_id+'-read', "schedules": [],
        "members": [{"boundary": plane.PROJECTION_READ, "identity": who.as_row(),
                     "route": route, "expected_generation": scopes.resolve_route(plane.PROJECTION_READ, who)['generation']}]}
    routes = {b: scopes.resolve_route(b, who)['route'] for b in plane.SIX_BOUNDARIES}
    routes[plane.PROJECTION_READ] = route
    artifact = put(root/(name+'-compat.json'), {"identity": who.as_row(), "workflow": req['workflow'],
        "routes": routes, "implementation": implementation_hashes(), "report_id": 'fixture-accepted',
        "valid_until": (datetime.now(UTC)+timedelta(hours=1)).isoformat(),
        "edges": edges if edges is not None else [{"boundaries": ['projection_read','dispatcher_schedule'],
            "mixed_compatible": True, "reference": 'isolated-cross-version-schema'}]})
    refs = [{'role': e.agent_role, 'scope_kind': e.scope.kind, 'scope_id': e.scope.id,
             'projection_id': e.projection_id, 'content_hash': e.content_hash} for e in envelopes]
    return bm.CutoverBatch(batch_id=name, batch_class=bm.RESEARCH_READ,
        owner='isolated-read-operator', old_route=scopes.resolve_route(plane.PROJECTION_READ,who)['route'], new_route=route, scope={'consumers': [who.consumer_id], 'read_request': req,
               'compatibility_proofs': {who.key: artifact}, 'projection_refs': {who.key: refs}},
        qualification_scope=who.scope, shadow_report_id='fixture-accepted',
        observation_window='isolated drill', success_criteria='actual refs preserved',
        stop_conditions='any required input invalid', fallback_route='disabled')


def invoke(action, batch, auth='DEP', *extra):
    return cli.main(['read',action,'--batch-id',batch.batch_id,'--authorisation',auth,
                     '--actor','isolated-operator',*extra])


def authorize(batch, auth='DEP'):
    return invoke('authorize',batch,auth,'--scope',','.join(batch.consumers),
        '--authorised-by','fixture-human','--issued-by','fixture-operator',
        '--valid-until',(datetime.now(UTC)+timedelta(hours=1)).isoformat())


@pytest.fixture
def setup(tmp_path, monkeypatch):
    with isolated_run('read-executor',root=tmp_path/'iso'):
        wiring.bootstrap_wired()
        store = get_store()
        now = datetime.now(UTC)
        entities = reads.configured_entities('sector','ai_hardware')
        who = reads.business_identity('sector',kind='sector',scope_id='ai_hardware',entities=entities,
            explicit={'kind':'sector','id':'ai_hardware','entities':entities,
                      'time_range':{'start':(now-timedelta(minutes=2)).isoformat(),
                                    'end':(now+timedelta(minutes=2)).isoformat()}})
        from ats.agent.task_projection import ProjectionScope
        from ats.config import load_sector_config
        plan = {'sector_allocation':{'role':'sector_allocation','scopes':[ProjectionScope(kind='sector',id='ai_hardware')]},
                'layer_analysis':{'role':'layer_analysis','scopes':[ProjectionScope(kind='layer',id=l.key)
                    for l in load_sector_config('ai_hardware').layers]}}
        envs = seed_research(store,plan)
        root = verified_isolation_root()
        policy(who,root)
        # These adapters establish no production qualification or signoff.
        monkeypatch.setattr('ats.workflow.boundary_evidence.assert_enforced',lambda *a,**k:None)
        monkeypatch.setattr('ats.data.assurance.qualification',lambda **kw:{'status':'eligible'})
        monkeypatch.setattr('ats.workflow.shadow_reports.check_batch_report',lambda *a,**k:(True,[]))
        batch = batch_for(who,root,envs)
        bm.declare_batch(batch)
        yield SimpleNamespace(store=store,who=who,root=root,envs=envs,batch=batch)


def test_actual_cli_scope_commit_plan_restart_and_runtime_read(setup, monkeypatch, capsys, record_property):
    s=setup
    assert authorize(s.batch)==0
    before = plane.all_boundaries()
    assert invoke('switch',s.batch)==0
    assert scopes.resolve_route(plane.PROJECTION_READ,s.who)['generation']==0
    assert invoke('switch',s.batch,'DEP','--apply')==0
    assert plane.all_boundaries()==before
    assert scopes.resolve_route(plane.PROJECTION_READ,s.who)['generation']==1
    assert invoke('switch',s.batch,'DEP','--apply')==0
    other=scopes.RouteIdentity(s.who.domain_id,s.who.consumer_id,s.who.contract_version,
        {**s.who.scope,'id':'another-sector'})
    assert scopes.resolve_route(plane.PROJECTION_READ,other)['generation']==0
    with trace_reads() as rows:
        assert cli.run_sector_html('ai_hardware',read_scope=s.who.as_row()) in {0,1}
    assert any(s.envs[0].projection_id in r['refs'] for r in rows)
    code='''import json,sys
from ats.workflow.scoped_routes import RouteIdentity,resolve_route
from ats.workflow import cutover
print(json.dumps(resolve_route(cutover.PROJECTION_READ,RouteIdentity(**json.loads(sys.argv[1])))))'''
    child=subprocess.run(['uv','run','--offline','--no-sync','python','-c',code,json.dumps(s.who.as_row())],
        capture_output=True,text=True,env=os.environ.copy(),check=False)
    assert child.returncode==0,child.stderr
    assert json.loads(child.stdout)['generation']==1
    record_property('actual_read_deployment',json.dumps({'root':str(s.root),'history':executor.run_history(),
        'runtime_reads':rows,'restarted_route':json.loads(child.stdout)}))


@pytest.mark.parametrize('gap',['no_report','unsigned','no_checker','missing_projection','hash','wrong_scope',
    'old_product_scope','no_compatibility','incompatible','expired_auth','missing_enforcement','no_policy'])
def test_actual_cli_audit_counterexamples_refuse_without_routes(setup, monkeypatch, gap):
    s=setup;b=s.batch
    if gap=='wrong_scope': b.qualification_scope={**b.qualification_scope,'id':'other'}
    if gap=='old_product_scope': b.scope={'consumers':['sector']}
    if gap not in {'wrong_scope','old_product_scope'}: assert authorize(b)==0
    if gap=='no_report': b.shadow_report_id=''
    if gap=='unsigned':
        from ats.workflow import shadow_reports
        monkeypatch.setattr(shadow_reports,'check_batch_report',shadow_reports.__dict__.get('_original_checker',
            __import__('ats.workflow.acceptance_reports',fromlist=['check_batch_report']).check_batch_report))
    if gap=='no_checker': monkeypatch.setattr('ats.workflow.read_deployment.projection_check',None)
    if gap=='missing_projection':
        s.store.conn.execute('DELETE FROM task_projection_envelopes WHERE projection_id=?',(s.envs[-1].projection_id,));s.store.conn.commit()
    if gap=='hash': b.scope['projection_refs'][s.who.key][0]['content_hash']='wrong'
    if gap=='no_compatibility': b.scope['compatibility_proofs']={}
    if gap=='incompatible':
        artifact=b.scope['compatibility_proofs'][s.who.key];p=Path(artifact['reference']);body=json.loads(p.read_text())
        body['edges'][0]['mixed_compatible']=False;b.scope['compatibility_proofs'][s.who.key]=put(p,body)
    if gap=='expired_auth':
        monkeypatch.setattr(executor,'_parse',lambda x:datetime.now(UTC)-timedelta(hours=1))
    if gap=='missing_enforcement':
        def no(*a,**k):raise plane.CutoverError('declared but not enforced')
        monkeypatch.setattr('ats.workflow.boundary_evidence.assert_enforced',no)
    if gap=='no_policy': monkeypatch.setattr(recovery,'current_policy',lambda *a,**k:None)
    bm.declare_batch(b)
    assert invoke('switch',b,'DEP','--apply')!=0
    assert scopes.resolve_route(plane.PROJECTION_READ,s.who)['generation']==0
    assert executor.run_history()[0]['changed']==0


def test_qualification_withdrawn_at_commit_freezes_scope_and_never_opens(setup, monkeypatch):
    s=setup;assert authorize(s.batch)==0
    calls=[];withdrawn=False
    def q(**kw):
        calls.append(kw);assert kw['scope']==s.who.scope
        return {'status':'ineligible' if withdrawn else 'eligible'}
    monkeypatch.setattr('ats.data.assurance.qualification',q)
    original_execute=joint.execute
    def during_commit(*args,**kwargs):
        def checkpoint(phase):
            nonlocal withdrawn
            if phase=='prepared':withdrawn=True
        return original_execute(*args,checkpoint=checkpoint,**kwargs)
    monkeypatch.setattr(joint,'execute',during_commit)
    assert invoke('switch',s.batch,'DEP','--apply')!=0
    assert len(calls)>=3
    _,request=deployment.requests(s.batch)[0]
    assert not joint.inspect(request['batch_id'])['can_run']
    with pytest.raises(RouteUnavailable,match='frozen'): reads.gate_read(s.who)


def test_stop_restore_actual_sector_preserves_history_and_old_worker(setup, record_property):
    s=setup;assert authorize(s.batch)==0;assert invoke('switch',s.batch,'DEP','--apply')==0
    original=[dict(r) for r in s.store.conn.execute('SELECT * FROM task_projection_envelopes')]
    with reads.bind_read(s.who):
        stop=batch_for(s.who,s.root,s.envs,name='stop-read',route='disabled');bm.declare_batch(stop)
        assert authorize(stop,'STOP')==0;assert invoke('switch',stop,'STOP','--apply')==0
        with pytest.raises(RouteUnavailable):cli.run_sector_html('ai_hardware',read_scope=s.who.as_row())
        with pytest.raises(wiring.BoundaryWriteRefused):
            DecisionAuditRepository(s.store).record_approval(approval_id='late',cycle_id='late',revision_no=1,
                decision_hash='hash',decision='approved',reviewer='fixture',idempotency_key='late')
    restored=batch_for(s.who,s.root,s.envs,name='restore-read');bm.declare_batch(restored)
    assert authorize(restored,'RESTORE')==0
    assert invoke('switch',restored,'RESTORE','--apply')==0
    with trace_reads() as trace:
        cli.run_sector_html('ai_hardware',read_scope=s.who.as_row())
    assert scopes.resolve_route(plane.PROJECTION_READ,s.who)['generation']==3
    assert [dict(r) for r in s.store.conn.execute('SELECT * FROM task_projection_envelopes')]==original
    assert s.store.conn.execute('SELECT count(*) FROM boss_approvals').fetchone()[0]==0
    record_property('actual_sector_recovery',json.dumps({'root':str(s.root),'routes':scopes.route_history(plane.PROJECTION_READ,s.who),'trace':trace}))


def test_no_target_keeps_stop_and_report_revocation_refuses_restore(setup, monkeypatch):
    s=setup
    stop=batch_for(s.who,s.root,s.envs,name='stop',route='disabled');bm.declare_batch(stop)
    assert authorize(stop,'STOP')==0;assert invoke('switch',stop,'STOP','--apply')==0
    restored=batch_for(s.who,s.root,s.envs,name='restore');bm.declare_batch(restored)
    assert authorize(restored,'RESTORE')==0
    monkeypatch.setattr('ats.workflow.shadow_reports.check_batch_report',lambda *a,**k:(False,['revoked']))
    assert invoke('switch',restored,'RESTORE','--apply')!=0
    assert scopes.resolve_route(plane.PROJECTION_READ,s.who)['route']=='disabled'
    with pytest.raises(RouteUnavailable):cli.run_sector_html('ai_hardware',read_scope=s.who.as_row())


def test_partial_qualification_never_changes_other_scope_or_global_flags(setup,monkeypatch):
    s=setup
    business={'kind':'entity','id':'COHR','entities':['COHR'],'time_range':s.who.scope['time_range']}
    who=[reads.business_identity(c,kind='entity',scope_id='COHR',entities=['COHR'],explicit=business)
         for c in ('technical','information')]
    envs=seed_research(s.store,{k:v for k,v in small_plan().items() if k in {'technical_review','information_brief'}})
    batches=[]
    for n,i in enumerate(who):
        policy(i,s.root)
        own=[e for e in envs if e.agent_role==('technical_review' if n==0 else 'information_brief')]
        batches.append(batch_for(i,s.root,own,name='group-'+str(n)))
    combined=batches[0];combined.batch_id='partial'
    combined.scope['consumers']=['technical','information']
    req=combined.scope['read_request'];req['batch_id']='partial'
    req['members']+=batches[1].scope['read_request']['members']
    combined.scope['projection_refs'].update(batches[1].scope['projection_refs'])
    combined.scope['compatibility_proofs'].update(batches[1].scope['compatibility_proofs'])
    for artifact in combined.scope['compatibility_proofs'].values():
        p=Path(artifact['reference']);body=json.loads(p.read_text());body['workflow']=req['workflow']
        artifact.update(put(p,body))
    bm.declare_batch(combined);assert authorize(combined)==0
    called=[]
    def q(**kw):
        called.append(kw)
        return {'status':'eligible' if kw['consumer_id']=='technical' else 'ineligible'}
    monkeypatch.setattr('ats.data.assurance.qualification',q)
    before=plane.all_boundaries()
    assert invoke('switch',combined,'DEP','--apply')==2
    assert scopes.resolve_route(plane.PROJECTION_READ,who[0])['generation']==1
    assert scopes.resolve_route(plane.PROJECTION_READ,who[1])['generation']==0
    assert plane.all_boundaries()==before
    assert all(k['scope']==business for k in called)


def test_scope_qualification_revocation_latches_and_does_not_resurrect(setup,monkeypatch):
    s=setup;assert authorize(s.batch)==0;assert invoke('switch',s.batch,'DEP','--apply')==0
    monkeypatch.setattr('ats.data.assurance.qualification',lambda **k:{'status':'ineligible','reasons':['revoked']})
    with pytest.raises(recovery.ReadStopped):cli.run_sector_html('ai_hardware',read_scope=s.who.as_row())
    assert recovery.current_policy(s.who)['stopped']
    monkeypatch.setattr('ats.data.assurance.qualification',lambda **k:{'status':'eligible'})
    with pytest.raises(recovery.ReadStopped):cli.run_sector_html('ai_hardware',read_scope=s.who.as_row())
    assert get_store().get_task_projection(s.envs[0].projection_id)['content_hash']==s.envs[0].content_hash


def test_authorization_cannot_widen_or_move_another_boundary(setup):
    s=setup;assert authorize(s.batch)==0
    changed=copy.deepcopy(s.batch)
    changed.scope['read_request']['members'].append({'boundary':'approval_lifecycle',
        'identity':s.who.as_row(),'route':'target','expected_generation':0})
    bm.declare_batch(changed)
    assert invoke('switch',changed,'DEP','--apply')!=0
    original=executor.read_authorisation('DEP')
    with pytest.raises(executor.DeploymentAuthorizationError,match='immutable'):
        executor.write_authorisation(executor.DeploymentAuthorization(original.reference,
            original.authorised_by,original.issued_by,('sector','trader'),original.valid_until))
    assert scopes.resolve_route(plane.APPROVAL_LIFECYCLE,s.who)['generation']==0


from test_phase_f_trader_a import execution as _execution
from test_phase_f_trader_a import price_clock as _price_clock
from test_phase_f_trader_a import ready_state

execution=_execution
price_clock=_price_clock


def test_chief_and_trader_real_stops_keep_complete_approved_revision_and_fills(execution,monkeypatch,record_property):
    from test_phase_f_joint_cutover import gates as fixture_gates
    from test_phase_f_trade_stop import reconcile
    from test_phase_f_trade_switch_drill import submit

    from ats.execution.state_api import get_internal_state
    from ats.graph import chief
    monkeypatch.setattr('ats.workflow.boundary_evidence.assert_enforced',lambda *a,**k:None)
    monkeypatch.setattr('ats.data.assurance.qualification',lambda **k:{'status':'eligible'})
    monkeypatch.setattr('ats.workflow.shadow_reports.check_batch_report',lambda *a,**k:(True,[]))
    state=ready_state(execution,cycle='read-preservation')
    submit(execution,state)
    order=execution.broker.accepted[0]
    execution.broker.simulate_fill(order.order_id,shares=order.qty,price=100.1)
    reconcile(execution,datetime.now(UTC))
    root=verified_isolation_root();point=datetime.now(UTC)
    who=reads.business_identity('chief',kind='portfolio',scope_id='portfolio',
        entities=reads.configured_entities('portfolio','portfolio'),explicit={
            'kind':'portfolio','id':'portfolio','entities':reads.configured_entities('portfolio','portfolio'),
            'time_range':{'start':(point-timedelta(minutes=2)).isoformat(),'end':(point+timedelta(minutes=2)).isoformat()}})
    plan=small_plan();monkeypatch.setattr(assemble,'projection_query_plan',lambda:plan)
    envs=seed_research(execution.store,plan);policy(who,root)
    batch=batch_for(who,root,envs,name='chief-read');batch.scope['fundamental_mode']='routine'
    bm.declare_batch(batch);assert authorize(batch)==0;assert invoke('switch',batch,'DEP','--apply')==0
    with reads.bind_read(who),trace_reads() as rows:
        context=assemble.build()
    assert all(e.projection_id in context.as_context() and e.content_hash in context.as_context() for e in envs)
    trader=reads.business_identity('trader',kind='decision',scope_id=state.cycle_id,entities=['AAPL'],
        explicit={'kind':'decision','id':state.cycle_id,'entities':['AAPL'],'time_range':who.scope['time_range']})
    before={t:[dict(r) for r in execution.store.conn.execute('SELECT * FROM '+t)] for t in (
        'decision_revisions','decision_risk_reviews','boss_approvals','trades','fills')}
    g=fixture_gates();g['compatibility']=lambda w,i,r:{'workflow':w,'identity':i.as_row(),'valid':True,
        'reference':'fixture-read-dependency','edges':[{'boundaries':['projection_read','dispatcher_schedule'],
        'mixed_compatible':True,'reference':'fixture-read-schema'}]}
    def req(name,target):
        return {'batch_id':name,'workflow':'chief-read-dependency','schedules':[],
            'members':[{'boundary':'projection_read','identity':i.as_row(),'route':target,
                'expected_generation':scopes.resolve_route(plane.PROJECTION_READ,i)['generation']} for i in [who,trader]]}
    with reads.bind_read(who) as old:
        stopped=joint.execute(req('stop-dependent-read','disabled'),gates=g,actor='fixture',reason='actual dependency stop',mode='isolated')
        with pytest.raises(RouteUnavailable): cli.run_chief(offline=True,use_llm=False,read_scope=who.as_row())
        with pytest.raises(RouteUnavailable): chief.trader(state,read_scope=trader.as_row())
    assert len(execution.broker.accepted)==1
    assert {t:[dict(r) for r in execution.store.conn.execute('SELECT * FROM '+t)] for t in before}==before
    assert get_internal_state(execution.store).fills
    target=req('restore-dependent-read','target')
    restored=joint.execute(target,gates=g,actor='fixture',reason='accepted isolated projections and state',mode='isolated')
    token=reads._CURRENT.set(old)
    try:
        with pytest.raises(Exception,match='joint authority'):
            DecisionAuditRepository(execution.store).record_approval(approval_id='stale',cycle_id=state.cycle_id,
                revision_no=state.revision_no,decision_hash=state.revision_hash,decision='approved',
                reviewer='fixture',idempotency_key='stale')
    finally: reads._CURRENT.reset(token)
    with reads.bind_read(who): assert assemble.build().as_context()==context.as_context()
    child_code='''import json,os,sys
from ats.execution.broker_write_guard import startup
from ats.workflow.scoped_routes import RouteIdentity
from ats.workflow.runtime_reads import gate_read
from ats.workflow import boundary_evidence
import ats.data.assurance
startup(caller='read-restart',mode='isolated')
boundary_evidence.assert_enforced=lambda *a,**k:None
ats.data.assurance.qualification=lambda **k:{'status':'eligible'}
from ats.memory import get_store
from ats.decision.repository import DecisionAuditRepository
from ats.execution.state_api import get_internal_state
who=RouteIdentity(**json.loads(sys.argv[1]))
print(json.dumps({'route':gate_read(who).route,'revision':DecisionAuditRepository(get_store()).read_chain(sys.argv[2])['revisions'][0]['decision_hash'],'fills':len(get_internal_state(get_store()).fills)}))'''
    child=subprocess.run(['uv','run','--offline','--no-sync','python','-c',child_code,json.dumps(who.as_row()),state.cycle_id],
        capture_output=True,text=True,env=os.environ.copy(),check=False)
    assert child.returncode==0,child.stderr
    resumed=json.loads(child.stdout);assert resumed['route']=='target' and resumed['revision']==state.revision_hash and resumed['fills']>0
    record_property('actual_chief_read_stop_restore',json.dumps({'root':str(root),'stop':stopped,'restore':restored,
        'read_trace':rows,'before':before,'restarted':resumed}))
