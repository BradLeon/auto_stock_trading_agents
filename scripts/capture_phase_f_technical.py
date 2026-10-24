"""Real market-input Technical capture and independent replay, physically isolated.

No model call, account lookup, collection, production write, order or signoff.
"""
import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from ats.agent.task_projection import ProjectionScope
from ats.workflow import acceptance_matrix as matrices
from ats.workflow import acceptance_reports as reports
from ats.workflow import acceptance_runner as runner
from ats.workflow.isolation import isolated_run
from ats.workflow.planning import build_plan
from ats.workflow.run_contracts import TriggerContext
from ats.workflow.scoped_routes import RouteIdentity

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root',type=Path,required=True)
parser.add_argument('--entity',required=True)
args=parser.parse_args()
entity=args.entity.upper()
point=datetime.now(UTC).isoformat()
identity=RouteIdentity('runtime_market','technical','target-dataflow-v1',
    {'kind':'entity','id':entity,'entities':[entity],'time_range':{'start':point,'end':point}})
root=args.root.resolve()
with isolated_run('actual-technical-candidate',root=root):
    inputs=root/'inputs.sqlite'
    def matrix(name):
        plan=build_plan(requested_tasks=['technical-review'],scope=ProjectionScope(kind='entity',id=entity),
            trigger=TriggerContext(kind='manual',trigger_id='phase-f-actual-technical'),as_of=point,run_id=name,
            task_inputs={'technical-review':{'use_llm':False}},enter_decision_cycle=False)
        return matrices.freeze(plan,workflow_id='technical-review',identity=identity,
            batch_class='research_read',model_execution=False)
    captured=runner.capture(matrix=matrix('capture'),input_store=inputs,root=root/'capture',capture_id='actual-inputs')
    for name in ('new','replay'):
        reports.run(matrix=matrix(name),input_store=inputs,input_hash=captured['input_hash'],
            root=root/name,replay_run_id=name)
    body=reports.record(report_id='actual-technical',input_hash=captured['input_hash'],actor='phase-f-candidate',
        proof={'input_store':str(inputs),'new_run_id':'new','replay_run_id':'replay'},path=root/'report.sqlite')
    result={'identity':identity.as_row(),'capture':captured,'report':body,
        'signoff':reports.read('actual-technical',path=root/'report.sqlite')['status'],
        'production_qualification':False,'production_mutated':False}
    with (root/'review.json').open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
print(json.dumps({'review':str(root/'review.json'),'signoff':result['signoff'],
    'assertions_passed':all(e['assertions']['passed'] for e in body['executions']),
    'production_mutated':False}))
