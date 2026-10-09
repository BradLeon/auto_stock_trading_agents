import ast
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] if 'docs/validation/' in str(Path(__file__).resolve()) else Path.cwd()
sys.path.insert(0, str(ROOT / 'src'))
out = {'date': '2026-10-07',
       'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
       'git_branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
       'production_reads': {}, 'isolated_probes': {}}

for name in ['var/phase_f_cutover.sqlite', 'var/phase_f_batches.sqlite',
             'var/phase_f_dispatch.sqlite', 'var/phase_f_routes.sqlite',
             'var/phase_f_schedule_switch.sqlite', 'var/phase_f_live_authorizations.sqlite',
             'var/shadow/orders.sqlite', 'var/shadow/reports.sqlite', 'var/data.sqlite',
             'var/data_platform.sqlite', 'var/ats.sqlite']:
    path = ROOT / name
    if not path.exists():
        out['production_reads'][name] = {'exists': False}
        continue
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    selected = [t for t in sorted(tables) if t.startswith(('cutover_', 'dispatch_', 'trade_route_',
                  'shadow_', 'dataflow_assurance', 'read_cutover_', 'schedule_', 'live_'))]
    counts = {t: conn.execute('SELECT COUNT(*) FROM "' + t.replace('"', '""') + '"').fetchone()[0]
              for t in selected}
    data = {'exists': True, 'table_count': len(tables), 'selected_counts': counts,
            'has_assurance_table': 'dataflow_assurance_events' in tables}
    if 'cutover_boundary_state' in tables:
        data['boundaries'] = [dict(r) for r in conn.execute('SELECT boundary,route,wired FROM cutover_boundary_state')]
    if 'cutover_batches' in tables:
        data['batches'] = [{k:r[k] for k in r.keys() if k in {
             'batch_id','batch_class','consumers_json','shadow_report_id','fallback_drill_ref'}}
             for r in conn.execute('SELECT * FROM cutover_batches')]
    if 'read_cutover_authorizations' in tables:
        data['deployment_authorizations'] = [dict(r) for r in conn.execute('SELECT reference,scope,valid_until FROM read_cutover_authorizations')]
    if 'live_authorizations' in tables:
        data['live_authorizations_redacted'] = [dict(r) for r in conn.execute('SELECT reference,issuer,scope,environment,valid_until,note FROM live_authorizations')]
    conn.close()
    out['production_reads'][name] = data

from ats.workflow.intake import consumer_reports
out['qualification_reports'] = [r.as_row() for r in consumer_reports()]

from ats.execution import broker_write_guard as guard
from ats.broker.ibkr import IBKRBroker

class SessionReached(Exception):
    pass

@contextmanager
def fake_session():
    raise SessionReached('mock broker session was reached; no network opened')
    yield None

guard.reset_for_tests()
broker = object.__new__(IBKRBroker)
broker.session = fake_session
try:
    broker.place_orders([(None, 1)], 'audit-no-grant')
    out['isolated_probes']['broker_no_grant'] = {'unexpected_return': True}
except SessionReached:
    out['isolated_probes']['broker_no_grant'] = {'session_reached': True, 'grant': None,
                                               'network_opened': False}
except Exception as exc:
    out['isolated_probes']['broker_no_grant'] = {'blocked': type(exc).__name__, 'message': str(exc)}

from ats.workflow import isolation
from ats.workflow import cutover as plane, cutover_wiring as wiring
from ats.memory import get_store
from ats.decision.repository import DecisionAuditRepository

with tempfile.TemporaryDirectory(prefix='phase-f-audit-') as temporary:
    with isolation.isolated_run('audit-20261007', root=Path(temporary)):
        wiring.bootstrap_wired(actor='audit')
        plane.set_route(plane.APPROVAL_LIFECYCLE, plane.ROUTE_DISABLED, actor='audit', reason='negative probe')
        repo = DecisionAuditRepository(get_store())
        repo.create_cycle(cycle_id='audit-cycle', trigger_source='isolated-audit')
        rev = repo.append_revision(cycle_id='audit-cycle', orders=[{'symbol':'NVDA','action':'buy','notional_usd':1}], rationale='audit')
        record = repo.record_approval(approval_id='audit-approval', cycle_id='audit-cycle',
                    revision_no=rev['revision_no'], decision_hash=rev['decision_hash'],
                    decision='approved', reviewer='isolated-audit', idempotency_key='audit')
        out['isolated_probes']['approval_disabled'] = {
            'boundary_route': plane.read_boundary(plane.APPROVAL_LIFECYCLE).route,
            'actual_repository_write_succeeded': record is not None}

        from ats.workflow import batch_manifest as bm, read_cutover as rc
        batch = bm.CutoverBatch(batch_id='audit', batch_class=bm.RESEARCH_READ,
                 owner='audit', old_route='legacy', new_route='target',
                 scope={'entity':'NVDA', 'consumers':['layer','sector']}, fallback_route='legacy',
                 fallback_proof='true', fallback_available='true', fallback_retired='false',
                 fallback_drill_ref='audit-isolated-drill', shadow_report_id='')
        auth = rc.DeploymentAuthorization(reference='AUDIT-ISOLATED', authorised_by='audit',
                 issued_by='audit', scope=('layer','sector'), valid_until='2099-01-01T00:00:00+00:00')
        result = rc.execute_batch(batch, qualification_reader=lambda c,s: {'status':'eligible'},
                                  authorisation=auth, apply=True)
        out['isolated_probes']['missing_shadow_and_projection_checks'] = {
            'shadow_report_id':'', 'projection_checker_supplied':False,
            'report_checker_supplied':False, 'changed':result.changed,
            'outcome':result.outcome,
            'routes':{b:s.route for b,s in plane.all_boundaries().items()}}

        # One eligible consumer still flips the entire global read/schedule pair.
        for b in (plane.PROJECTION_READ, plane.DISPATCHER_SCHEDULE):
            plane.set_route(b, plane.ROUTE_LEGACY, actor='audit', reason='reset isolated probe')
        partial = rc.execute_batch(batch, qualification_reader=lambda c,s: {
                'status':'eligible' if c == 'layer' else 'ineligible'}, authorisation=auth, apply=True)
        out['isolated_probes']['partial_global_switch'] = {
            'changed':partial.changed,'outcome':partial.outcome,
            'consumer_outcomes':{s.consumer_id:s.outcome for s in partial.scopes},
            'global_projection_route':plane.read_boundary(plane.PROJECTION_READ).route}

        for b in (plane.PROJECTION_READ, plane.DISPATCHER_SCHEDULE):
            plane.set_route(b, plane.ROUTE_LEGACY, actor='audit', reason='reset isolated probe')
        original_set_route = plane.set_route
        set_calls = []
        def failing_second_write(boundary, route, **kwargs):
            set_calls.append(boundary)
            if len(set_calls) == 2:
                raise RuntimeError('simulated crash between boundary writes')
            return original_set_route(boundary, route, **kwargs)
        plane.set_route = failing_second_write
        try:
            rc.execute_batch(batch, qualification_reader=lambda c,s: {'status':'eligible'},
                             authorisation=auth, apply=True)
        except RuntimeError as exc:
            out['isolated_probes']['paired_switch_crash'] = {
                'exception':str(exc), 'write_calls':set_calls,
                'routes':{b:plane.read_boundary(b).route for b in
                          (plane.PROJECTION_READ, plane.DISPATCHER_SCHEDULE)},
                'compatible_after_crash':plane.check_compatibility(plane.all_boundaries()).compatible}
        finally:
            plane.set_route = original_set_route

from ats.workflow import intake_verification as iv
records = [iv.scan_consumer_access(c).as_row() for c in iv.TEN_CONSUMERS]
out['source_scan'] = [{'consumer_id':r['consumer_id'], 'compliant':r['compliant'],
                      'product_ref_count':len(r['product_refs']), 'document_ref_count':len(r['document_refs']),
                      'vintage_ref_count':len(r['vintage_refs']), 'projection_hash':r['projection_hash'],
                      'violation_kinds':[v['kind'] for v in r['violations']]} for r in records]

names = {'guard_approval_write','guard_clerk_publication','guard_analyst_output','read_route','claim'}
calls = {n:[] for n in names}
for path in sorted((ROOT / 'src/ats').rglob('*.py')):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            n = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
            if n in names:
                calls[n].append(f'{path.relative_to(ROOT)}:{node.lineno}')
out['named_call_sites'] = calls
target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'tmp/phase-f-audit-20261007'
target.mkdir(parents=True, exist_ok=True)
(target / 'audit-evidence.json').write_text(json.dumps(out, ensure_ascii=False, indent=2))
print(json.dumps(out, ensure_ascii=False, indent=2))
