"""Freeze scoped verification and compare read-only production inventories."""
import hashlib
import json
import runpy
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).parent


def main():
    before = json.loads((OUTPUT / "before.json").read_text())
    current = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in before['source_sha256']}
    changed = sorted(p for p in current if current[p] != before['source_sha256'][p])
    approved = {'src/ats/graph/chief.py', 'src/ats/trader/execute.py', 'src/ats/memory/store.py',
                'src/ats/schemas/memory.py', 'src/ats/execution/simulation.py', 'src/ats/execution/clerk.py',
                'src/ats/workflow/isolation.py', 'src/ats/workflow/intake_verification.py',
                'src/ats/workflow/shadow_ledger.py'}
    assert set(changed) <= approved, changed
    production = runpy.run_path(str(ROOT / 'docs/validation/phase_f_recovery_20261007/preflight.py'))['read_databases']()
    assert production == before['production']
    suites = {}
    green = set()
    for filename in ('related-final.junit.xml', 'origin-final.junit.xml', 'docs-final.junit.xml',
                     'intake-final.junit.xml', 'intake-baseline.junit.xml'):
        cases = ET.parse(OUTPUT / filename).findall('.//testcase')
        failed = [c.attrib['name'] for c in cases if c.find('failure') is not None or c.find('error') is not None]
        suites[filename] = {'tests':len(cases), 'passed':len(cases)-len(failed), 'failed':failed}
        if filename not in {'intake-final.junit.xml', 'intake-baseline.junit.xml'}:
            assert not failed, filename
            green.update((c.attrib['classname'], c.attrib['name']) for c in cases)
    assert suites['intake-final.junit.xml']['failed'] == suites['intake-baseline.junit.xml']['failed']
    from ats.workflow.shadow_reports import code_fingerprint
    from ats.trader.execute import AUTO_EXECUTION_ENABLED
    assert AUTO_EXECUTION_ENABLED is False
    additional = ('src/ats/execution/clerk.py', 'src/ats/workflow/isolated_entry.py', 'src/ats/execution/shadow_execution.py',
                  'src/ats/workflow/shadow_reports.py', 'tests/test_phase_f_shadow_business.py',
                  'tests/test_intake_verification.py', 'tests/test_isolation.py', 'tests/test_intake_cli.py')
    current.update({p:hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in additional})
    evidence = json.loads((OUTPUT/'business-evidence.json').read_text())
    assert evidence['fresh_process_attribution']['submitted_count'] == 0
    assert evidence['original_rows_unchanged_by_rebuild'] and not evidence['tradable']
    assert all(not a['production_side_effects'] for a in evidence['attestations'])
    result = {'scope':['7.1','3.7'], 'progress':{'completed':56,'total':116,'remaining':60},
              'tests':suites, 'unique_passing_gate_and_related_tests':len(green),
              'known_intake_failures_reproduced_with_pre_session_intake_module':True,
              'source_sha256':current, 'approved_changed_preexisting_sources':changed,
              'other_captured_sources_unchanged':True,
              'modified_existing_sources_not_captured_in_before':['src/ats/execution/clerk.py','src/ats/workflow/shadow_reports.py'], 'production_inventory_before':before['production'],
              'production_inventory_after':production, 'production_inventory_unchanged':True,
              'report_code_fingerprint':code_fingerprint(), 'c3_enabled':AUTO_EXECUTION_ENABLED,
              'artifacts':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
                  (OUTPUT/'business-evidence.json',OUTPUT/'shadow-ledger.sql',OUTPUT/'current-static-index.json')},
              'limits':['Single chain with synthetic external inputs; 7.5/3.9/3.10 not complete.',
                        'No full repository or full pre-Phase-F baseline regression.',
                        'Production inventory comparison uses counts/authorization inventory; runtime attestations additionally compare row digests.'],
              'actions':{'real_broker_network':False,'production_route_change':False,
                         'production_qualification_write':False,'production_authorization_change':False}}
    (OUTPUT/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passing_related_tests':len(green),'intake_passed':suites['intake-final.junit.xml']['passed'],
                      'known_intake_failed':len(suites['intake-final.junit.xml']['failed']),
                      'production_inventory_unchanged':True, 'changed_sources':changed},ensure_ascii=False))


if __name__ == '__main__':
    main()
