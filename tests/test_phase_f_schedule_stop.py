"""10.2/10.3/13.2 new Dispatcher stop/restore against persistent execution history."""
import copy
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from test_phase_f_joint_cutover import gates
from test_phase_f_schedule_runtime import SCOPE, plan, tick
from test_phase_f_schedule_runtime import business_inputs as _business_inputs
from test_phase_f_schedule_runtime import schedule_isolation as _isolated

from ats.runtime import cli
from ats.workflow import cutover as plane
from ats.workflow import joint_cutover as joint
from ats.workflow import runtime_reads as reads
from ats.workflow import schedule_runtime as rt
from ats.workflow import scoped_routes as scopes
from ats.workflow.dispatcher import Dispatcher
from ats.workflow.store import WorkflowStore

business_inputs = _business_inputs
isolated = _isolated

def request(who, name, route):
    state=rt.check_owner('technical-review',SCOPE,rt.current_owner('technical-review',SCOPE))
    return {'batch_id':name,'workflow':'technical-review',
        'members':[{'boundary':b,'identity':who.as_row(),'route':route,
            'expected_generation':scopes.resolve_route(b,who)['generation']}
            for b in [plane.PROJECTION_READ,plane.DISPATCHER_SCHEDULE]],
        'schedules':[{'workflow':'technical-review','scope':SCOPE,'from_owner':state['owner'],
            'to_owner':'dispatcher' if route=='target' else 'legacy',
            'expected_generation':state['generation'],'dispositions':{}}]}


def apply(req, **kw):
    return joint.execute(req,gates=kw.get('gates',gates()),actor='fixture-operator',
        reason='isolated accepted new-version drill',mode='isolated')


def owner_row():
    return next(r for r in rt.snapshot()['owners'] if r['workflow']=='technical-review' and r['scope']==rt.canonical(SCOPE))


def test_actual_new_dispatcher_stop_restore_keeps_completed_trigger(business_inputs,isolated,monkeypatch,record_property):
    monkeypatch.setattr('ats.workflow.boundary_evidence.assert_enforced',lambda *a,**k:None)
    monkeypatch.setattr('ats.data.assurance.qualification',lambda **k:{'status':'eligible'})
    monkeypatch.setattr('ats.agents.technical.review.resolve_universe',lambda *a,**k: (['COHR'],[]))
    context=tick(); first_plan=plan(business_inputs,context,'new-before-stop')
    dispatcher=Dispatcher(workflow_store=WorkflowStore(str(isolated.path)),max_workers=1)
    first=dispatcher.dispatch(first_plan)
    assert first.complete,first.as_dict()
    who=reads.task_identity(first_plan,first_plan.tasks[0])
    initial=request(who,'activate-new','target');apply(initial)
    claims=copy.deepcopy(rt.snapshot()['claims'])
    projections=[dict(r) for r in isolated.conn.execute('SELECT * FROM task_projection_envelopes')]
    stop=request(who,'stop-new','disabled');apply(stop)
    restore=copy.deepcopy(stop);restore['batch_id']='restore-accepted-new'
    for member in restore['members']:
        member['route']='target';member['expected_generation']=scopes.resolve_route(member['boundary'],who)['generation']
    restore['schedules'][0].update(from_owner='legacy',to_owner='dispatcher',expected_generation=owner_row()['generation'])
    with pytest.raises(plane.CutoverError,match='disabled'), rt.wake(context):cli.run_technical_review(write_report=False)
    with pytest.raises((plane.CutoverError, reads.RouteUnavailable)):dispatcher.dispatch(replace(first_plan,run_id='during-stop'))
    # Withdrawn current acceptance cannot restore; stopped authority stays intact.
    invalid=gates();invalid['report']=lambda i:{'identity':i.as_row(),'valid':False,'reference':'withdrawn'}
    with pytest.raises(plane.CutoverError):apply(restore,gates=invalid)
    assert all(scopes.resolve_route(m['boundary'],who)['route']=='disabled' for m in stop['members'])
    restored=apply(restore)
    assert restored['can_run'] and owner_row()['owner']=='dispatcher' and owner_row()['generation']==4
    # Actual Dispatcher resolves the same trigger without running the analysis again.
    replay=dispatcher.dispatch(replace(first_plan,run_id='after-restore'))
    if replay.complete:
        assert all(outcome.reused for outcome in replay.outcomes), replay.as_dict()
    else:
        assert 'reuse validation' in str(replay.as_dict()), replay.as_dict()
    assert [dict(r) for r in isolated.conn.execute('SELECT * FROM task_projection_envelopes')]==projections
    assert rt.snapshot()['claims']==claims
    with pytest.raises(rt.ScheduleAuthorityError), rt.wake(tick(datetime.now(UTC)+timedelta(minutes=1))):cli.run_technical_review(write_report=False)
    next_plan=plan(business_inputs,tick(datetime.now(UTC)+timedelta(minutes=2)),'next-new-tick')
    following=dispatcher.dispatch(next_plan)
    assert following.complete,following.as_dict()
    record_property('actual_new_schedule_recovery',json.dumps({'first':first.as_dict(),'restored':restored,
        'replay':replay.as_dict(),'ledger':rt.snapshot()}))


def test_missing_completed_record_cannot_open_new_generation(isolated):
    trigger=tick()
    rt.execute('technical-review',SCOPE,trigger,lambda:{'fixture':'recorded'},owner='dispatcher')
    token=rt.freeze('technical-review',SCOPE,actor='fixture',reason='safe stop')
    state=owner_row()
    # Deliberate storage corruption; product delete protection remains required.
    with sqlite3.connect(rt.state_path()) as conn:
        conn.execute('DROP TRIGGER schedule_runtime_claim_no_delete')
        conn.execute('DELETE FROM schedule_runtime_claims')
    with pytest.raises(rt.ScheduleAuthorityError,match='records missing'):
        rt.handover(token,to_owner='dispatcher',dispositions={},actor='fixture',reason='accepted target')
    assert owner_row()==state and owner_row()['frozen']
    with pytest.raises(rt.ScheduleAuthorityError):
        rt.execute('technical-review',SCOPE,trigger,lambda:pytest.fail('missing record reran'),owner='dispatcher')
