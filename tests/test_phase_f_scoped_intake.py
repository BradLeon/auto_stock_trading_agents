"""Exact-scope original events, unchanged TTL and fail-closed missing production proof."""
import json
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from ats.data import assurance
from ats.workflow import scoped_intake as intake
from ats.workflow.isolation import isolated_run
from ats.workflow.scoped_routes import RouteIdentity


@pytest.fixture
def setup(tmp_path):
    with isolated_run('scoped-intake',root=tmp_path/'iso') as env:
        point=datetime.now(UTC)
        who=RouteIdentity('runtime_market','technical','target-dataflow-v1',
            {'kind':'entity','id':'COHR','entities':['COHR'],
             'time_range':{'start':point.isoformat(),'end':point.isoformat()}})
        source=env.root/'original.sqlite';target=env.root/'candidate.sqlite'
        yield who,source,target,intake.freeze_snapshot()


def record(who,path,at,*,kind='runtime_query',prerequisites=()):
    return assurance.record_evidence(domain_id=who.domain_id,consumer_id=who.consumer_id,
        scope=who.scope,evidence_type=kind,outcome='passed',as_of=at,
        prerequisite_event_ids=list(prerequisites),
        dependency_paths=intake.freeze_snapshot()['fingerprints'].keys(),
        command_summary='explicit isolated original event fixture',result_summary='mechanism only',db_path=path)


def test_actual_ledger_reuse_is_partial_preserves_original_time_and_routes(setup):
    who,source,target,freeze=setup;point=datetime.now(UTC)-timedelta(minutes=3)
    original=record(who,source,point)
    copied=intake.append_original_events([who.as_row()],source_db=source,target_db=target,freeze=freeze,actor='test')
    passed=[r for r in copied if r['registered']]
    assert len(passed)==1 and passed[0]['original_event_id']==original
    assert not passed[0]['production_qualification']
    history=assurance.evidence_history(domain_id=who.domain_id,consumer_id=who.consumer_id,scope=who.scope,db_path=target)
    assert len(history)==1 and history[0]['as_of']==point.isoformat()
    report=intake.qualification_report([who.as_row()],db_path=target)
    assert report['eligible']==0 and not report['routes_changed'] and not report['approval_created']


@pytest.mark.parametrize('bad',['expired','revoked','scope','drift'])
def test_original_bad_events_are_not_redated_or_widened(setup,bad):
    who,source,target,freeze=setup
    original=record(who,source,datetime.now(UTC)-timedelta(days=2 if bad=='expired' else 0))
    if bad=='revoked':assurance.revoke_evidence(original,reason='fixture withdrawal',db_path=source)
    if bad=='scope':
        who=RouteIdentity(who.domain_id,who.consumer_id,who.contract_version,{**who.scope,'id':'NVDA','entities':['NVDA']})
    if bad=='drift':
        freeze=json.loads(json.dumps(freeze));freeze['fingerprints']['src/ats/workflow/scoped_intake.py']='0'*64
        with pytest.raises(ValueError,match='drifted'):
            intake.append_original_events([who.as_row()],source_db=source,target_db=target,freeze=freeze,actor='test')
        return
    copied=intake.append_original_events([who.as_row()],source_db=source,target_db=target,freeze=freeze,actor='test')
    assert all(not r['registered'] for r in copied)
    assert not target.exists()


def test_product_scope_and_missing_ledger_cannot_become_business_qualification(setup):
    who,source,_,_=setup
    report=intake.qualification_report([who.as_row()],db_path=source)
    assert report['scopes'][0]['qualification']['reasons']==['assurance_ledger_missing']
    with pytest.raises(ValueError,match='kind/id'):
        intake.qualification_report([{**who.as_row(),'scope':{'products':['MARKET_DATA']}}],db_path=source)


def test_recovery_passed_metadata_cannot_replace_registered_raw_drill(setup):
    who,_,_,_=setup
    consumer,_,_=assurance._consumer('technical',assurance._COVERAGE)
    policy=assurance._manifest(assurance._COVERAGE)[0]['qualification_policy']
    assert assurance._rollback_failures(consumer,{'recovery':[{'reference':'summary','sha256':'0'*64}]},
        policy,now=datetime.now(UTC),scope=who.scope)==['new_recovery_unverified']


def test_valid_original_prerequisite_outside_bounded_history_is_copied_readonly(setup,monkeypatch):
    who,source,target,freeze=setup;point=datetime.now(UTC)-timedelta(minutes=3)
    prerequisite=record(who,source,point,kind='timestamp')
    original=record(who,source,point,prerequisites=[prerequisite])
    def source_rows():
        # WAL checkpointing can change database bytes without logical writes.
        # Compare the complete append-only source rows through a readonly URI.
        with closing(assurance._connect(source,writable=False)) as conn:
            return [tuple(row) for row in conn.execute('SELECT * FROM dataflow_assurance_events ORDER BY event_id')]
    before=source_rows()
    history=assurance.evidence_history
    monkeypatch.setattr(assurance,'evidence_history',lambda **kw:
        [row for row in history(**kw) if row['event_id']!=prerequisite])
    copied=intake.append_original_events([who.as_row()],source_db=source,target_db=target,freeze=freeze,actor='test')
    accepted={row['original_event_id']:row for row in copied if row['registered']}
    assert set(accepted)=={original,prerequisite}
    rows=history(domain_id=who.domain_id,consumer_id=who.consumer_id,scope=who.scope,db_path=target)
    child=next(row for row in rows if row['event_id']==accepted[original]['event_id'])
    assert json.loads(child['prerequisites_json'])==[accepted[prerequisite]['event_id']]
    assert all(row['as_of']==point.isoformat() for row in rows)
    assert source_rows()==before


def test_source_disappearing_after_qualification_is_reported_without_candidate_write(setup,monkeypatch):
    who,source,target,freeze=setup
    record(who,source,datetime.now(UTC))
    qualification=assurance.qualification
    def withdraw(**kwargs):
        result=qualification(**kwargs)
        source.rename(source.with_suffix('.unavailable'))
        return result
    monkeypatch.setattr(assurance,'qualification',withdraw)
    copied=intake.append_original_events([who.as_row()],source_db=source,target_db=target,freeze=freeze,actor='test')
    assert not any(row['registered'] for row in copied)
    assert any('original event chain unreadable' in reason for row in copied for reason in row.get('reasons',[]))
    assert not target.exists()


# --------------------------------------------------------------------------- #
# Freeze transitions: freeze-only changes are disposed of, not silently absorbed
# --------------------------------------------------------------------------- #

TOOL='tests/test_phase_f_scoped_intake.py'
RUNTIME='src/ats/workflow/consumer_reads.py'


def _previous(**changed):
    previous=json.loads(json.dumps(intake.freeze_snapshot()))
    previous['fingerprints'].update({path:'0'*64 for path in changed.values()})
    return previous


def test_freeze_only_change_without_disposition_is_refused():
    with pytest.raises(ValueError,match='need a disposition.*'+TOOL):
        intake.freeze_snapshot(previous=_previous(tool=TOOL))


def test_unaffected_disposition_is_recorded_and_taints_nothing():
    previous=_previous(tool=TOOL,runtime=RUNTIME)
    snapshot=intake.freeze_snapshot(previous=previous,dispositions=[
        {'path':TOOL,'decision':'unaffected','reason':'assertion wording only'}])
    transition=snapshot['transition']
    assert transition['previous_freeze']==intake.freeze_digest(previous)
    assert transition['qualification_changes']==[RUNTIME]
    assert transition['freeze_only_dispositions']==[
        {'path':TOOL,'decision':'unaffected','reason':'assertion wording only'}]
    assert transition['tainted_freezes']==[]
    intake.check_freeze(snapshot)


@pytest.mark.parametrize('row,message',[
    ({'path':RUNTIME,'decision':'unaffected','reason':'x'},'not a changed freeze-only file'),
    ({'path':TOOL,'decision':'ignore','reason':'x'},'decision in'),
    ({'path':TOOL,'decision':'unaffected','reason':' '},'reason'),
])
def test_dispositions_cannot_cover_runtime_code_or_skip_the_reason(row,message):
    with pytest.raises(ValueError,match=message):
        intake.freeze_snapshot(previous=_previous(tool=TOOL,runtime=RUNTIME),dispositions=[row])


def test_a_legacy_untiered_freeze_treats_every_change_as_retired():
    """Migration: the v1 freeze bound everything to qualification, so nothing in
    it needs a disposition — all evidence under it retires through the verifier."""
    previous=_previous(tool=TOOL)
    previous.pop('tiers');previous['version']='phase-f-final-freeze-v1'
    # The freeze-surface file itself did not exist under v1: added, still no disposition.
    previous['fingerprints'].pop('config/workflow/freeze_surface.yaml')
    snapshot=intake.freeze_snapshot(previous=previous)
    assert snapshot['transition']['qualification_changes']==sorted(
        [TOOL,'config/workflow/freeze_surface.yaml'])
    assert snapshot['transition']['freeze_only_dispositions']==[]


def test_a_freeze_only_file_added_since_a_tiered_freeze_needs_a_disposition():
    previous=_previous();previous['fingerprints'].pop(TOOL)
    previous['tiers']['freeze_only'].remove(TOOL)
    with pytest.raises(ValueError,match='need a disposition.*'+TOOL):
        intake.freeze_snapshot(previous=previous)


def test_evidence_dependencies_are_the_qualification_tier_only():
    snapshot=intake.freeze_snapshot()
    paths=intake.evidence_dependency_paths('information',snapshot)
    assert not set(paths)&set(snapshot['tiers']['freeze_only'])
    assert set(paths)<=set(snapshot['tiers']['qualification'])


def test_revoke_taints_events_registered_under_the_previous_freeze(setup):
    who,source,target,_=setup;point=datetime.now(UTC)-timedelta(minutes=3)
    previous=_previous(tool=TOOL)
    assurance.record_evidence(domain_id=who.domain_id,consumer_id=who.consumer_id,
        scope=who.scope,evidence_type='runtime_query',outcome='passed',as_of=point,
        dependency_paths=intake.evidence_dependency_paths(who.consumer_id,intake.freeze_snapshot()),
        command_summary='fixture probe; '+intake.provenance(previous),
        result_summary='mechanism only',db_path=source)
    revoked=intake.freeze_snapshot(previous=previous,dispositions=[
        {'path':TOOL,'decision':'revoke','reason':'capture bug produced these rows'}])
    assert revoked['transition']['tainted_freezes']==[intake.freeze_digest(previous)]
    report=intake.qualification_report([who.as_row()],db_path=source,freeze=revoked)
    assert len(report['scopes'][0]['tainted_accepted_events'])==1
    copied=intake.append_original_events([who.as_row()],source_db=source,target_db=target,
        freeze=revoked,actor='test')
    query=[row for row in copied if row['evidence_type']=='runtime_query']
    assert query and not query[0]['registered'] and 'revoke' in query[0]['reasons'][0]
