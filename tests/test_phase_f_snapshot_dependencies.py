"""5.8 actual frozen six-role basis, repository callbacks and submission gate."""
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_phase_f_read_executor import policy
from test_phase_f_research_gate import business_inputs as _business_inputs
from test_phase_f_research_gate import decision_run
from test_phase_f_research_gate import research_isolation as _isolated

from ats.decision.repository import DecisionAuditRepository
from ats.execution.authorization import build_authorization, validate_authorization
from ats.workflow import cutover as plane
from ats.workflow import runtime_reads as reads
from ats.workflow import scoped_routes as scopes
from ats.workflow.cutover_routing import RouteUnavailable
from ats.workflow.isolation import verified_isolation_root

business_inputs=_business_inputs
isolated=_isolated


@pytest.fixture
def ready(business_inputs,isolated,monkeypatch):
    monkeypatch.setattr('ats.workflow.boundary_evidence.assert_enforced',lambda *a,**k:None)
    monkeypatch.setattr('ats.data.assurance.qualification',lambda **k:{'status':'eligible'})
    captured={}
    def before(plan):
        task=next(t for t in plan.tasks if t.task_id=='technical-review')
        who=reads.task_identity(plan,task)
        policy(who,verified_isolation_root())
        scopes.transition_route(plane.PROJECTION_READ,who,'target',expected_generation=0,mode='isolated',
            actor='fixture',reason='qualified dependency before actual role run',
            qualification=lambda i:{'identity':i.as_row(),'status':'eligible'},
            report=lambda i:{'identity':i.as_row(),'valid':True,'reference':'fixture-report'},
            fallback=lambda i:{'identity':i.as_row(),'valid':True,'reference':'fixture-stop'})
        captured['who']=who
    def runner(snapshot,manifest):
        captured['basis']=snapshot.to_payload();return {}
    result=decision_run(business_inputs,isolated,runner=runner,before_dispatch=before)
    assert result.complete and result.decision_cycle_ready,result.as_dict()
    repo=DecisionAuditRepository(isolated)
    repo.create_cycle(cycle_id='dependent',trigger_source='actual-six-role',research_snapshot=captured['basis'])
    rev=repo.append_revision(cycle_id='dependent',orders=[{'symbol':'COHR','action':'buy','qty':1,'order_type':'market'}])
    stamp=datetime.now(UTC).isoformat()
    repo.record_review(review_id='review',cycle_id='dependent',revision_no=rev['revision_no'],
        decision_hash=rev['decision_hash'],ruleset_version='fixture-rules',portfolio_snapshot_id='fixture-pf',
        market_as_of=stamp,verdict='approved')
    repo.record_approval(approval_id='approved',cycle_id='dependent',revision_no=rev['revision_no'],
        decision_hash=rev['decision_hash'],decision='approved',reviewer='fixture-human',idempotency_key='approved')
    auth=build_authorization(repo,'dependent').model_copy(update={'route_id':'sim','route_generation':1})
    return SimpleNamespace(repo=repo,store=isolated,revision=rev,auth=auth,**captured)


@pytest.mark.parametrize('failure',['revoked','manifest_drift','disabled','generation','policy'])
def test_actual_unbound_callback_and_authorization_recheck_frozen_dependencies(ready,monkeypatch,failure,record_property):
    s=ready
    before={t:[dict(r) for r in s.store.conn.execute('SELECT * FROM '+t)] for t in
            ('decision_revisions','decision_risk_reviews','boss_approvals')}
    if failure in {'revoked','manifest_drift'}:
        monkeypatch.setattr('ats.data.assurance.qualification',lambda **kw:{'status':'ineligible'
            if kw['consumer_id']=='technical' else 'eligible','reasons':[failure]})
    elif failure in {'disabled','generation'}:
        scopes.transition_route(plane.PROJECTION_READ,s.who,'disabled' if failure=='disabled' else 'legacy',
            expected_generation=1,actor='fixture',reason='dependency changed',mode='isolated',
            fallback=lambda i:{'identity':i.as_row(),'valid':True,'reference':'fixture-selected-route'})
    else: policy(s.who,verified_isolation_root())
    with pytest.raises(RouteUnavailable):
        s.repo.record_approval(approval_id='late',cycle_id='dependent',revision_no=s.revision['revision_no'],
            decision_hash=s.revision['decision_hash'],decision='approved',reviewer='fixture',idempotency_key='late')
    reasons=validate_authorization(s.repo,s.auth,snapshot_as_of=datetime.now(UTC),
        route_state=SimpleNamespace(route_id='sim',generation=1))
    assert any(r.startswith('research_dependencies_invalid') for r in reasons),reasons
    # The real Trader entry must refuse before quotes or transport acceptance.
    from ats.execution.authorization import bind_to_active_route
    from ats.execution.route_registry import install_route
    from ats.execution.simulation import simulated_execution
    from ats.schemas.decision import TradeDecision
    from ats.trader.execute import place_orders
    install_route('sim', environment='paper', account='DU1')
    auth=bind_to_active_route(build_authorization(s.repo,'dependent'))
    orders=[TradeDecision.model_validate(row) for row in json.loads(s.revision['orders_json'])]
    with simulated_execution(store=s.store,account='DU1') as transport:
        entries,fills=place_orders([(order,order.qty) for order in orders],'dependent',
            revision_no=s.revision['revision_no'],authorization=auth.model_dump(mode='json'))
        assert not transport.accepted and not fills
        assert entries and all('research_dependencies_invalid' in row.error for row in entries)
    assert {t:[dict(r) for r in s.store.conn.execute('SELECT * FROM '+t)] for t in before}==before
    assert s.repo.read_chain('dependent')['has_execution_approval']
    # No consumer-wide/global stop: unrelated business scope remains readable.
    unrelated=scopes.RouteIdentity(s.who.domain_id,s.who.consumer_id,s.who.contract_version,
        {**s.who.scope,'id':'NVDA','entities':['NVDA']})
    assert reads.gate_read(unrelated).route=='legacy'
    record_property('actual_snapshot_dependency_rejection',json.dumps({'root':str(verified_isolation_root()),
        'failure':failure,'basis':s.basis,'reasons':reasons,'approval_chain':s.repo.read_chain('dependent')}))


def test_current_dependency_allows_idempotent_actual_approval_and_current_authorization(ready):
    s=ready
    approval=s.repo.record_approval(approval_id='same',cycle_id='dependent',revision_no=s.revision['revision_no'],
        decision_hash=s.revision['decision_hash'],decision='approved',reviewer='fixture',idempotency_key='approved')
    assert approval['approval_id']=='approved'
    assert not validate_authorization(s.repo,s.auth,snapshot_as_of=datetime.now(UTC),
        route_state=SimpleNamespace(route_id='sim',generation=1))


@pytest.fixture
def multi_routine_basis(business_inputs, isolated):
    """Real role compute/publish and Chief binding; external transports are fixtures."""
    from ats.data import document_assets
    from ats.data.stores.unstructured import get_platform_unstructured_store
    from ats.workflow import acceptance_matrix as matrix
    repository = get_platform_unstructured_store()
    point = datetime.now(UTC)
    try:
        for key, title in [('capacity', 'Factory capacity outlook changed'),
                           ('customer', 'Customer qualification milestone confirmed')]:
            assert document_assets.ingest(entity='COHR', key='multi-'+key, doc_type='article',
                text=(title+'. Source uncertainty remains explicit. ')*30,
                source='ibkr_news', source_url='fixture:multi-'+key, title=title,
                published_at=(point.replace(microsecond=0)).isoformat(), min_chars=1, store=repository)
    finally:
        repository.close()
    captured = {}
    def before(plan):
        task = next(t for t in plan.tasks if t.task_id == 'fundamental-routine')
        captured['plan'] = plan
        captured['matrix'] = matrix.freeze(plan, workflow_id='multi-routine',
            identity=reads.task_identity(plan, task), batch_class='decision', model_execution=True)
    def runner(snapshot, manifest):
        captured['basis'] = snapshot.to_payload()
        captured['manifest'] = manifest
        return {}
    result = decision_run(business_inputs, isolated, runner=runner, before_dispatch=before)
    assert result.complete and result.decision_cycle_ready and result.decision_cycle_entered, result.as_dict()
    outcome = next(o for o in result.outcomes if o.task_id == 'fundamental-routine')
    assert len(outcome.projection_refs) == 3
    return SimpleNamespace(store=isolated, result=result, outcome=outcome, **captured)


def test_actual_multi_routine_preserves_all_updates_in_checker_chief_and_reuse(multi_routine_basis, record_property):
    from ats.agents.chief import assemble
    from ats.workflow import acceptance_assertions as checks, decision_requirements as dr
    from ats.workflow.dispatcher import Dispatcher
    from ats.workflow.store import WorkflowStore
    s = multi_routine_basis
    assertion = next(a for a in s.matrix.body['assertions']
                     if a['id'] == 'analysis.'+s.outcome.instance_key)
    actual, refs = checks._analysis(s.matrix.body, assertion,
                                  {'store':s.store, 'dispatch':s.result.as_dict()})
    assert len(actual['outputs']) == 3 and set(s.outcome.projection_refs) <= set(refs)
    items = [i for i in s.basis['items'] if i['agent_role'] == 'fundamental_expectation_update']
    assert {i['projection_id'] for i in items} == set(s.outcome.projection_refs)
    dr.validate_chief(s.store, s.basis, at=datetime.fromisoformat(s.plan.as_of))
    from ats.decision.snapshot import frozen_snapshot_stale_reasons
    assert not frozen_snapshot_stale_reasons(s.store, s.basis,
        at=datetime.fromisoformat(s.plan.as_of))
    repository = DecisionAuditRepository(s.store)
    repository.create_cycle(cycle_id='multi-routine-approved', trigger_source='fixture', research_snapshot=s.basis)
    revision = repository.append_revision(cycle_id='multi-routine-approved',
        orders=[{'symbol':'COHR','action':'buy','qty':1,'order_type':'market'}])
    repository.record_review(review_id='multi-review',cycle_id='multi-routine-approved',
        revision_no=revision['revision_no'],decision_hash=revision['decision_hash'],
        ruleset_version='fixture',portfolio_snapshot_id='fixture',market_as_of=s.plan.as_of,verdict='approved')
    approval = repository.record_approval(approval_id='multi-approved',cycle_id='multi-routine-approved',
        revision_no=revision['revision_no'],decision_hash=revision['decision_hash'],decision='approved',
        reviewer='fixture-human',idempotency_key='multi-approved')
    assert approval['approval_id'] == 'multi-approved'
    _, detail = assemble.build_chief_snapshot(s.store, at=datetime.fromisoformat(s.plan.as_of),
        projection_manifest=s.manifest, query_plan=dr.query_plan(s.plan))
    text = assemble.projection_context_block(detail)
    assert all(ref in text for ref in s.outcome.projection_refs)
    standalone, standalone_detail = assemble.build_chief_snapshot(s.store,
        at=datetime.fromisoformat(s.plan.as_of), query_plan=dr.query_plan(s.plan))
    dr.attach(standalone, dr.freeze(s.plan), standalone_detail)
    dr.validate_chief(s.store, standalone.to_payload(), at=datetime.fromisoformat(s.plan.as_of))
    assert {i.projection_id for i in standalone.items
        if i.agent_role == 'fundamental_expectation_update'} == set(s.outcome.projection_refs)
    task = next(t for t in s.plan.tasks if t.task_id == 'fundamental-routine')
    env = Dispatcher._envelope(s.store.get_task_projection(s.outcome.projection_refs[0]))
    hit, reason = Dispatcher(workflow_store=WorkflowStore(str(s.store.path)))._select_reusable(
        s.store, task, env.input_refs, env.data_vintage_refs, s.plan.as_of)
    assert hit is None and reason['reason'] == 'multi_projection_recompute_required'
    record_property('actual_multi_routine_basis', json.dumps({'scope':task.scope.model_dump(mode='json'),
        'projection_refs':list(s.outcome.projection_refs), 'basis':s.basis,
        'model_transport':'controlled fixture; not production evidence'}))


@pytest.mark.parametrize('change', ['producer_damage', 'later_publication'])
def test_multi_routine_new_producer_still_supersedes_complete_frozen_bundle(multi_routine_basis, change):
    from ats.decision.snapshot import frozen_snapshot_stale_reasons
    s = multi_routine_basis
    latest = s.store.task_projection_envelopes(agent_role='fundamental_expectation_update',
        scope_kind='entity', scope_id='COHR', limit=1)[0]
    ref = latest['projection_id']
    if change == 'producer_damage':
        # Producer damage must not be excused as a same-producer sibling.
        s.store.conn.execute('UPDATE task_projection_envelopes SET agent_run_id=? WHERE projection_id=?',
            ('later-producer', ref))
        s.store.conn.commit()
    else:
        from ats.agent.task_projection import build_envelope
        from ats.workflow.dispatcher import Dispatcher
        env = Dispatcher._envelope(latest)
        newer = build_envelope(role=env.agent_role, payload=env.payload, scope=env.scope,
            as_of=(datetime.fromisoformat(env.as_of)+timedelta(seconds=1)).isoformat(),
            valid_until=env.valid_until, input_refs=env.input_refs, data_vintage_refs=env.data_vintage_refs,
            workflow_run_id='later-fixture-run', agent_run_id='later-fixture-producer')
        s.store.save_task_projection_envelope(newer)
    assert frozen_snapshot_stale_reasons(s.store, s.basis, at=datetime.fromisoformat(s.plan.as_of))


@pytest.mark.parametrize('damage', ['missing_member', 'failed_producer', 'duplicate_refs', 'missing_producer', 'foreign_member'])
def test_standalone_chief_refuses_incomplete_routine_producer_bundle(multi_routine_basis, damage):
    from ats.agents.chief import assemble
    from ats.workflow import decision_requirements as dr
    s = multi_routine_basis
    latest = s.store.task_projection_envelopes(agent_role='fundamental_expectation_update',
        scope_kind='entity', scope_id='COHR', limit=1)[0]
    if damage == 'missing_member':
        ref = next(ref for ref in s.outcome.projection_refs if ref != latest['projection_id'])
        s.store.conn.execute('DELETE FROM task_projection_envelopes WHERE projection_id=?', (ref,))
    elif damage == 'missing_producer':
        s.store.conn.execute('UPDATE task_projection_envelopes SET agent_run_id=? WHERE projection_id=?',
            ('missing-producer', latest['projection_id']))
    elif damage == 'foreign_member':
        ref = next(ref for ref in s.outcome.projection_refs if ref != latest['projection_id'])
        s.store.conn.execute('UPDATE task_projection_envelopes SET agent_run_id=? WHERE projection_id=?',
            ('foreign-producer', ref))
    elif damage == 'failed_producer':
        s.store.conn.execute("UPDATE agent_runs SET status='failed' WHERE agent_run_id=?", (latest['agent_run_id'],))
    else:
        refs = [*s.outcome.projection_refs, s.outcome.projection_refs[0]]
        s.store.conn.execute('UPDATE agent_runs SET projection_refs_json=? WHERE agent_run_id=?',
            (json.dumps(refs), latest['agent_run_id']))
    s.store.conn.commit()
    snapshot, detail = assemble.build_chief_snapshot(s.store,
        at=datetime.fromisoformat(s.plan.as_of), query_plan=dr.query_plan(s.plan))
    assert not snapshot.complete
    assert any(d['status'] in {'missing','producer_unresolved','producer_mismatch'} for d in detail)


@pytest.mark.parametrize('damage', ['omit', 'duplicate', 'hash', 'lineage', 'vintage', 'scope', 'schema', 'expired', 'producer', 'narrative_number'])
def test_multi_routine_member_damage_refuses_actual_checker_and_chief(multi_routine_basis, damage):
    import copy
    from ats.workflow import acceptance_assertions as checks, decision_requirements as dr
    s = multi_routine_basis
    actual = copy.deepcopy(s.result.as_dict())
    basis = copy.deepcopy(s.basis)
    outcome = next(o for o in actual['outcomes'] if o['task_id'] == 'fundamental-routine')
    ref = outcome['projection_refs'][1]
    if damage == 'omit':
        outcome['projection_refs'].remove(ref)
        basis['items'] = [i for i in basis['items'] if i['projection_id'] != ref]
        req = basis['requirements']
        req['manifest'] = [m for m in req['manifest'] if m['projection_id'] != ref]
        next(o for o in req['outcomes'] if o['task_id'] == 'fundamental-routine')['projection_refs'].remove(ref)
        req['requirements_hash'] = dr._hash({k:v for k,v in req.items() if k!='requirements_hash'})
    elif damage == 'duplicate':
        outcome['projection_refs'].append(ref)
        basis['items'].append(copy.deepcopy(next(i for i in basis['items'] if i['projection_id']==ref)))
    elif damage == 'narrative_number':
        from ats.agent.task_projection import content_hash, validate_payload
        from ats.workflow.dispatcher import Dispatcher
        env = Dispatcher._envelope(s.store.get_task_projection(ref))
        payload = {**env.payload, 'metric':'narrative','previous_value':None,'new_value':40.0}
        digest = content_hash(role=env.agent_role, scope=env.scope, as_of=env.as_of,
            schema_name=env.schema_name, schema_version=env.schema_version,
            payload=validate_payload(env.agent_role, payload),
            input_refs=env.input_refs, data_vintage_refs=env.data_vintage_refs)
        s.store.conn.execute('UPDATE task_projection_envelopes SET payload=?,content_hash=? WHERE projection_id=?',
            (json.dumps(payload), digest, ref))
        s.store.conn.commit()
        next(i for i in basis['items'] if i['projection_id']==ref)['content_hash'] = digest
        req = basis['requirements']
        next(m for m in req['manifest'] if m['projection_id']==ref)['content_hash'] = digest
        req['requirements_hash'] = dr._hash({k:v for k,v in req.items() if k!='requirements_hash'})
        from ats.agents.chief import assemble
        snapshot, detail = assemble.build_chief_snapshot(s.store, at=datetime.fromisoformat(s.plan.as_of),
            projection_manifest=req['manifest'], query_plan=dr.query_plan(s.plan))
        assert not snapshot.complete and any(d['status']=='value_unconfirmed' for d in detail)
        task = next(t for t in s.plan.tasks if t.task_id=='fundamental-routine')
        bad = Dispatcher._envelope(s.store.get_task_projection(ref))
        ok, reason = Dispatcher._projection_reuse_check(bad, task, bad.input_refs,
            bad.data_vintage_refs, s.plan.as_of, 86400, task.output_schema)
        assert not ok and reason=='routine_value_contract_invalid'
    else:
        columns = {'hash':('content_hash','bad'), 'lineage':('input_refs','[]'),
            'vintage':('data_vintage_refs','[]'), 'scope':('scope_id','NVDA'),
            'schema':('schema_version','v0'), 'expired':('valid_until','2000-01-01T00:00:00+00:00'),
            'producer':('agent_run_id','missing-producer')}
        column, value = columns[damage]
        s.store.conn.execute('UPDATE task_projection_envelopes SET '+column+'=? WHERE projection_id=?',(value,ref))
        s.store.conn.commit()
    assertion = next(a for a in s.matrix.body['assertions'] if a['id']=='analysis.'+s.outcome.instance_key)
    with pytest.raises((ValueError, RouteUnavailable)):
        checks._analysis(s.matrix.body, assertion, {'store':s.store,'dispatch':actual})
    with pytest.raises((RouteUnavailable, ValueError)):
        dr.validate_chief(s.store, basis, at=datetime.fromisoformat(s.plan.as_of))


@pytest.mark.parametrize('damage', ['failed_producer', 'incomplete_refs', 'missing_producer'])
def test_standalone_frozen_routine_rechecks_terminal_producer_before_use(multi_routine_basis, damage):
    from ats.agents.chief import assemble
    from ats.workflow import decision_requirements as dr
    s = multi_routine_basis
    at = datetime.fromisoformat(s.plan.as_of)
    snapshot, detail = assemble.build_chief_snapshot(s.store, at=at, query_plan=dr.query_plan(s.plan))
    dr.attach(snapshot, dr.freeze(s.plan), detail)
    basis = snapshot.to_payload()
    dr.validate_chief(s.store, basis, at=at)
    repository = DecisionAuditRepository(s.store)
    repository.create_cycle(cycle_id='late-producer', trigger_source='fixture', research_snapshot=basis)
    revision = repository.append_revision(cycle_id='late-producer',
        orders=[{'symbol':'COHR','action':'buy','qty':1,'order_type':'market'}])
    repository.record_review(review_id='late-review', cycle_id='late-producer',
        revision_no=revision['revision_no'], decision_hash=revision['decision_hash'],
        ruleset_version='fixture', portfolio_snapshot_id='fixture', market_as_of=s.plan.as_of,
        verdict='approved')
    row = s.store.get_task_projection(s.outcome.projection_refs[0])
    if damage == 'failed_producer':
        s.store.conn.execute("UPDATE agent_runs SET status='failed' WHERE agent_run_id=?", (row['agent_run_id'],))
    elif damage == 'incomplete_refs':
        s.store.conn.execute('UPDATE agent_runs SET projection_refs_json=? WHERE agent_run_id=?',
            (json.dumps(list(s.outcome.projection_refs)[:-1]), row['agent_run_id']))
    else:
        s.store.conn.execute('UPDATE task_projection_envelopes SET agent_run_id=? WHERE projection_id=?',
            ('missing-producer', row['projection_id']))
    s.store.conn.commit()
    with pytest.raises(RouteUnavailable):
        dr.validate_chief(s.store, basis, at=at)
    with pytest.raises((RouteUnavailable, ValueError)):
        repository.record_approval(approval_id='late-refused', cycle_id='late-producer',
            revision_no=revision['revision_no'], decision_hash=revision['decision_hash'], decision='approved',
            reviewer='fixture-human', idempotency_key='late-refused')
    assert not s.store.conn.execute("SELECT 1 FROM boss_approvals WHERE approval_id='late-refused'").fetchone()
