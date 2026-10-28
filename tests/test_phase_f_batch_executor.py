"""8.1/8.2/8.4 formal batch plan and actual independent schedule application."""
import copy

import pytest
from test_phase_f_read_executor import authorize, invoke, put
from test_phase_f_read_executor import setup as _setup

from ats.runtime import cli
from ats.workflow import batch_manifest as bm
from ats.workflow import cutover as plane
from ats.workflow import joint_cutover as joint
from ats.workflow import read_cutover as executor
from ats.workflow import schedule_runtime as rt
from ats.workflow import scoped_routes as scopes
from ats.workflow.business_replay_inputs import implementation_hashes
from ats.workflow.run_contracts import TriggerContext

setup=_setup


def authority_snapshot(s):
    return {'routes':scopes.route_history(plane.PROJECTION_READ,s.who),
        'schedule':scopes.route_history(plane.DISPATCHER_SCHEDULE,s.who),
        'globals':plane.all_boundaries(),'owners':rt.snapshot()['owners'],
        'projections':[dict(r) for r in s.store.conn.execute('SELECT * FROM task_projection_envelopes')]}


def test_actual_batch_plan_uses_same_gates_without_mutating_authorities(setup):
    s=setup;s.batch.scope['deployment_authorisation']='DEP';bm.declare_batch(s.batch)
    assert authorize(s.batch)==0
    before=authority_snapshot(s)
    assert cli.main(['batch','show','--batch-id',s.batch.batch_id])==0
    assert authority_snapshot(s)==before
    assert invoke('plan',s.batch)==0
    assert authority_snapshot(s)==before
    assert invoke('switch',s.batch,'DEP','--apply')==0


@pytest.mark.parametrize('gap',['no_auth','no_projection','no_compatibility','qualification','enforcement','report','generation'])
def test_actual_plan_fails_closed_and_commit_agrees(setup,monkeypatch,gap):
    s=setup;s.batch.scope['deployment_authorisation']='DEP'
    if gap!='no_auth':assert authorize(s.batch)==0
    if gap=='no_projection':s.batch.scope['projection_refs'][s.who.key]=[]
    if gap=='no_compatibility':s.batch.scope['compatibility_proofs']={}
    if gap=='qualification':monkeypatch.setattr('ats.data.assurance.qualification',lambda **k:{'status':'ineligible'})
    if gap=='enforcement':
        def refuse(*a,**k):raise plane.CutoverError('fixture inventory absent')
        monkeypatch.setattr('ats.workflow.boundary_evidence.assert_enforced',refuse)
    if gap=='report':monkeypatch.setattr('ats.workflow.shadow_reports.check_batch_report',lambda *a,**k:(False,['unsigned']))
    if gap=='generation':
        monkeypatch.setattr(scopes, 'resolve_route', lambda *a, **k: {'route':'legacy','generation':37})
    bm.declare_batch(s.batch);before=authority_snapshot(s)
    assert cli.main(['batch','show','--batch-id',s.batch.batch_id])!=0
    assert invoke('plan',s.batch)!=0
    assert invoke('switch',s.batch,'DEP','--apply')!=0
    assert authority_snapshot(s)==before


def test_independent_schedule_cli_changes_only_explicit_owner_scope(setup):
    s=setup;workflow='sector-review';scope={'kind':s.who.scope['kind'],'id':s.who.scope['id']}
    rt.execute(workflow,scope,TriggerContext(kind='manual',trigger_id='seed-schedule'),lambda:{'fixture':'seed'},owner='legacy')
    batch=copy.deepcopy(s.batch);batch.batch_class=bm.SCHEDULE
    req=batch.scope['read_request'];req['workflow']=workflow
    req['members'][0]['boundary']=plane.DISPATCHER_SCHEDULE
    req['schedules']=[{'workflow':workflow,'scope':scope,'from_owner':'legacy','to_owner':'dispatcher',
        'expected_generation':1,'dispositions':{}}]
    artifact=batch.scope['compatibility_proofs'][s.who.key]
    import json
    from pathlib import Path
    proof=json.loads(Path(artifact['reference']).read_text())
    proof['workflow']=workflow;proof['routes'][plane.PROJECTION_READ]='legacy';proof['routes'][plane.DISPATCHER_SCHEDULE]='target'
    proof['implementation']=implementation_hashes()
    batch.scope['compatibility_proofs'][s.who.key]=put(s.root/'schedule-compat.json',proof)
    batch.scope['deployment_authorisation']='SCHEDULE';bm.declare_batch(batch)
    before=authority_snapshot(s)
    assert authorize(batch,'SCHEDULE')==0
    assert cli.main(['batch','show','--batch-id',batch.batch_id])==0
    assert authority_snapshot(s)==before
    assert invoke('switch',batch,'SCHEDULE','--apply')==0
    assert scopes.resolve_route(plane.PROJECTION_READ,s.who)['generation']==0
    assert scopes.resolve_route(plane.DISPATCHER_SCHEDULE,s.who)['generation']==1
    assert rt.current_owner(workflow,scope)=='dispatcher'
    assert plane.all_boundaries()==before['globals']
    assert invoke('switch',batch,'SCHEDULE','--apply')==0


def test_plan_rejects_actual_schedule_owner_drift_without_route_writes(setup):
    s=setup;workflow='sector-review';scope={'kind':s.who.scope['kind'],'id':s.who.scope['id']}
    rt.execute(workflow,scope,TriggerContext(kind='manual',trigger_id='owner-drift'),lambda:{'fixture':'seed'},owner='legacy')
    batch=copy.deepcopy(s.batch);batch.batch_class=bm.SCHEDULE
    req=batch.scope['read_request'];req['workflow']=workflow
    req['members'][0]['boundary']=plane.DISPATCHER_SCHEDULE
    req['schedules']=[{'workflow':workflow,'scope':scope,'from_owner':'legacy','to_owner':'dispatcher',
        'expected_generation':38,'dispositions':{}}]
    import json
    from pathlib import Path
    artifact=batch.scope['compatibility_proofs'][s.who.key]
    proof=json.loads(Path(artifact['reference']).read_text())
    proof['workflow']=workflow;proof['routes'][plane.PROJECTION_READ]='legacy'
    proof['routes'][plane.DISPATCHER_SCHEDULE]='target';proof['implementation']=implementation_hashes()
    batch.scope['compatibility_proofs'][s.who.key]=put(s.root/'owner-drift-compat.json',proof)
    batch.scope['deployment_authorisation']='SCHEDULE';bm.declare_batch(batch)
    assert authorize(batch,'SCHEDULE')==0
    before=authority_snapshot(s)
    assert invoke('plan',batch,'SCHEDULE')!=0
    assert invoke('switch',batch,'SCHEDULE','--apply')!=0
    assert authority_snapshot(s)==before


def test_exact_authorization_registration_idempotent_but_never_widens(setup):
    s=setup;assert authorize(s.batch)==0
    auth=executor.read_authorisation('DEP')
    from ats.workflow.read_deployment import authorize as record_exact
    record_exact(s.batch,auth,actor='isolated-operator')
    request=copy.deepcopy(s.batch.scope['read_request']);request['batch_id']='unrequested'
    who,_original=__import__('ats.workflow.read_deployment',fromlist=['requests']).requests(s.batch)[0]
    with pytest.raises(plane.CutoverError,match='immutable'):
        joint.record_authorization(request,reference=auth.reference+':'+who.key,valid_until=auth.valid_until,
            actor='isolated-operator',reason='explicit read deployment')
