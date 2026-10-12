"""1.6/5.9/5.10 real CLI and scheduler admission, no production writes."""
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_phase_f_business_wiring import synthetic_target as _synthetic_target

from ats.runtime import cli, scheduler
from ats.workflow import cutover as plane
from ats.workflow import runtime_reads as reads
from ats.workflow.cutover_routing import RouteUnavailable

synthetic_target = _synthetic_target

@pytest.mark.parametrize('entry',['cli','scheduler','webhook'])
@pytest.mark.parametrize('fault',['missing','corrupt','missing_boundary','dual_active','incompatible'])
def test_actual_service_refuses_bad_authority_before_business(entry,fault,tmp_path,monkeypatch):
    original=Path(os.environ['ATS_CUTOVER_DB'])
    target=tmp_path/'unreadable.sqlite'
    if fault!='missing':
        if fault=='corrupt':target.write_bytes(b'not sqlite')
        else:
            with sqlite3.connect(original) as source,sqlite3.connect(target) as dest:source.backup(dest)
            with sqlite3.connect(target) as conn:
                if fault=='missing_boundary':conn.execute('DELETE FROM cutover_boundary_state WHERE boundary=?',(plane.PROJECTION_READ,))
                else:conn.execute('UPDATE cutover_boundary_state SET route=? WHERE boundary=?',
                    ('legacy,target' if fault=='dual_active' else 'target',plane.PROJECTION_READ))
    monkeypatch.setenv('ATS_CUTOVER_DB',str(target))
    monkeypatch.setattr(cli,'technical_show',lambda *a:pytest.fail('business entered'))
    monkeypatch.setattr(scheduler,'_daily',lambda **k:pytest.fail('scheduler entered'))
    with pytest.raises(plane.CutoverError):
        if entry=='cli':cli.main(['technical','show'])
        elif entry=='scheduler':scheduler.start(run_once=True)
        else:
            from fastapi.testclient import TestClient

            from ats.runtime.server import build_app
            with TestClient(build_app()):pytest.fail('unreadable webhook started')
    if fault=='missing':assert not target.exists()


def test_actual_cli_legacy_research_starts_with_trading_disabled(monkeypatch):
    plane.set_route(plane.LIVE_TRADER,'disabled',actor='fixture',reason='research independent')
    calls=[]
    monkeypatch.setattr('ats.agents.technical.review.run',lambda *a,**k:
        calls.append(reads.current_read_context().route) or SimpleNamespace(strategy='fixture',
            summary_line=lambda:'fixture',notes=[],readings=[]))
    assert cli.main(['technical','review','--offline','--no-report'])==0
    assert calls==['legacy']


def test_actual_main_target_admission_and_revocation(synthetic_target,monkeypatch):
    point=datetime.now(UTC)
    scope={'kind':'portfolio','id':'portfolio','entities':reads.configured_entities('portfolio','portfolio'),
        'time_range':{'start':(point-timedelta(hours=1)).isoformat(),'end':(point+timedelta(hours=1)).isoformat()}}
    who=reads.business_identity('technical',kind='portfolio',scope_id='portfolio',entities=scope['entities'],explicit=scope)
    synthetic_target(who)
    calls=[]
    monkeypatch.setattr('ats.agents.technical.review.run',lambda *a,**k:
        calls.append(reads.current_read_context().route) or SimpleNamespace(strategy='fixture',
            summary_line=lambda:'fixture',notes=[],readings=[]))
    monkeypatch.setattr('ats.data.assurance.qualification',lambda **k:{'status':'eligible'})
    args=['--read-scope-json',json.dumps({'technical':scope}),'technical','review','--offline','--no-report']
    assert cli.main(args)==0 and calls==['target']
    monkeypatch.setattr('ats.data.assurance.qualification',lambda **k:{'status':'ineligible'})
    with pytest.raises(RouteUnavailable):cli.main(args)
    assert calls==['target']


def test_global_target_and_wired_declaration_cannot_admit_unknown_scope(monkeypatch):
    plane.set_route(plane.PROJECTION_READ,'target',actor='fixture',reason='global declaration')
    plane.set_route(plane.DISPATCHER_SCHEDULE,'target',actor='fixture',reason='compatible global declaration')
    plane.declare_wiring(boundary=plane.PROJECTION_READ,call_site='ats.runtime.cli.run_technical_review',
        authority='fixture',semantics='declaration only')
    who=reads.business_identity('technical',kind='entity',scope_id='UNKNOWN',entities=['UNKNOWN'])
    monkeypatch.setattr('ats.data.assurance.qualification',lambda **k:{'status':'ineligible'})
    assert reads.gate_read(who).route=='legacy'
    from ats.workflow.boundary_evidence import assert_enforced
    with pytest.raises(plane.CutoverError, match='not enforced'):assert_enforced(plane.PROJECTION_READ,identity=who,mode='production')
