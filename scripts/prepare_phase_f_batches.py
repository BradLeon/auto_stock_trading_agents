"""Prepare five reviewable deployment classes; never modify production authority."""
import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ats.workflow import batch_manifest as bm
from ats.workflow import runtime_reads as reads
from ats.workflow.isolation import isolated_run

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root',type=Path,required=True)
parser.add_argument('--entity',required=True)
parser.add_argument('--actor',required=True)
args=parser.parse_args()
point=datetime.now(UTC)
entity=args.entity.upper()
if entity not in reads.configured_entities('portfolio','portfolio'):
    parser.error('entity must belong to the actual configured business universe')
window={'start':point.isoformat(),'end':(point+timedelta(days=1)).isoformat()}
entity_scope={'kind':'entity','id':entity,'entities':[entity],'time_range':window}
portfolio_scope={'kind':'portfolio','id':'portfolio','entities':reads.configured_entities('portfolio','portfolio'),
                 'time_range':window}
# These are existing module owners, not a production reassignment or human signoff.
owners={'collection_publish':'ats.data.persistent_queue.PersistentIngestionQueue',
        'research_read':'ats.agents.technical.review','schedule':'ats.workflow.ownership.run_owned_workflow',
        'internal_state_approval':'ats.decision.repository.DecisionAuditRepository','live_trader':'ats.trader.execute.place_orders'}
rows=[]
with isolated_run('phase-f-prepared-manifest',root=args.root):
    for kind in bm.BATCH_CLASSES:
        consumers={'collection_publish':[],'research_read':['technical'],'schedule':['technical'],
                   'internal_state_approval':['chief','risk','trader','clerk'],'live_trader':['trader']}[kind]
        scope=entity_scope if kind in {'research_read','schedule'} else portfolio_scope
        metadata={'consumers':consumers,'deployment_authorisation':'',
            'prepared_for_review':True,'scope_basis':'actual configured entity/portfolio',
            'action_kind':'verify_in_place' if kind=='collection_publish' else 'independent',
            'qualification_status':'not_yet_evaluated', 'report_status':'missing_current_signed_report',
            'authorization_status':'not_authorized',
            'required_before_deployment':['current exact-scope qualification','actual enforcement evidence',
                'current signed acceptance','current recovery/compatibility proofs','explicit human deployment authorization']}
        if kind in {'research_read','schedule'}:
            who=reads.business_identity('technical',kind='entity',scope_id=entity,entities=[entity],explicit=scope)
            boundary='projection_read' if kind=='research_read' else 'dispatcher_schedule'
            request={'batch_id':'phase-f-'+kind,'workflow':'technical-review','members':[
                {'boundary':boundary,'identity':who.as_row(),'route':'target','expected_generation':0}], 'schedules':[]}
            if kind=='schedule':
                # Exact production owner/generation must be read after approved installation.
                request['schedules']=[{'workflow':'technical-review','scope':{'kind':'entity','id':entity},
                    'from_owner':'legacy','to_owner':'dispatcher','expected_generation':1,'dispositions':{}}]
                metadata['owner_generation_basis']='proposed first installation; re-read production before authorize'
            metadata.update(read_request=request,projection_refs={},compatibility_proofs={})
        if kind=='live_trader':metadata['action_kind']='disabled_outside_change'
        row=bm.CutoverBatch(batch_id='phase-f-'+kind,batch_class=kind,owner=owners[kind],
            old_route='' if kind=='collection_publish' else 'disabled' if kind=='live_trader' else 'legacy',
            new_route='' if kind=='collection_publish' else 'disabled' if kind=='live_trader' else 'target',
            scope=metadata,qualification_scope=scope,
            observation_window='proposed: 2 trading sessions; actual start/end require deployment approval',
            success_criteria='all declared new requirements; unique logical trigger; hashes/history preserved',
            stop_conditions='missing input or expired/revoked qualification/report; partial authority; duplicate; unresolved history',
            fallback_route='disabled',fallback_drill_ref='pending: bind final exact-scope accepted recovery proof',
            direct_verification=kind=='collection_publish',declared_by=args.actor)
        bm.declare_batch(row)
        rows.append(row.as_row())
    if any(not row['consumers'] for row in rows if row['batch_class'] in {'research_read','schedule'}):
        raise AssertionError('empty qualification set')
    output=args.root.resolve()/'prepared-batches.json'
    with output.open('x') as stream:json.dump({'status':'prepared_not_authorized','batches':rows},stream,ensure_ascii=False,indent=2)
print(json.dumps({'output':str(output),'classes':len(rows),'production_mutated':False}))
