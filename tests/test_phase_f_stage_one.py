"""Information and probe behaviour first pinned during Phase F stage one (the stage-one preparation script and its
audits were retired with scoped_intake in r5 8.7)."""
import importlib.util
import json
import hashlib
import sqlite3
from pathlib import Path

import pytest

from ats.config import REPO_ROOT



def test_cached_probe_uses_invocation_time_and_never_window_end():
    from ats.workflow.scoped_routes import RouteIdentity
    from ats.workflow.cutover_routing import RouteUnavailable
    probe_spec = importlib.util.spec_from_file_location("phase_f_cached_probe", REPO_ROOT / "scripts/verify_dataflow_qualification.py")
    probe = importlib.util.module_from_spec(probe_spec)
    probe_spec.loader.exec_module(probe)
    who = RouteIdentity("runtime_market", "technical", "target-dataflow-v1", {
        "kind": "entity", "id": "COHR", "entities": ["COHR"], "time_policy": "per_invocation"})
    with pytest.raises(ValueError, match="independent"):
        probe.probe_time(who)
    assert probe.probe_time(who, "2026-10-10T03:00:00+00:00").isoformat() == "2026-10-10T03:00:00+00:00"
    bounded = RouteIdentity(who.domain_id, who.consumer_id, who.contract_version, {
        "kind": "entity", "id": "COHR", "entities": ["COHR"],
        "time_range": {"start": "2026-10-10T02:00:00+00:00", "end": "2026-10-10T04:00:00+00:00"}})
    assert probe.probe_time(bounded, "2026-10-10T03:00:00+00:00").hour == 3
    with pytest.raises(RouteUnavailable, match="outside"):
        probe.probe_time(bounded, "2026-10-10T05:00:00+00:00")


def test_readonly_probe_empty_or_bad_broker_cannot_prove_availability(monkeypatch):
    from ats.execution import broker_write_guard as guard
    probe_spec = importlib.util.spec_from_file_location("phase_f_tws_probe", REPO_ROOT / "scripts/probe_phase_f_tws_readonly.py")
    module = importlib.util.module_from_spec(probe_spec)
    probe_spec.loader.exec_module(module)
    calls = []
    class Broker:
        def get_portfolio(self):
            calls.append("portfolio")
            return None
        def completed_orders(self): return []
        def get_fills(self): return []
        def open_orders(self): return []
    monkeypatch.setattr(guard, "is_prohibited", lambda: False)
    with pytest.raises(ValueError, match="prohibition"):
        module.probe(Broker())
    assert calls == []
    monkeypatch.setattr(guard, "is_prohibited", lambda: True)
    receipt = module.probe(Broker())
    assert receipt["status"] == "pending" and receipt["broker_state"]["payload"] is None
    assert not receipt["qualification_granted"] and not receipt["permission_granted"]


def test_information_target_scope_is_configured_frozen_and_role_specific(tmp_path):
    import shutil
    from ats.workflow.planning import build_plan
    from ats.workflow.run_contracts import TriggerContext
    from ats.agent.task_projection import ProjectionScope
    from ats.workflow.runtime_reads import task_identity, consumer_entities, guard_input, ReadContext, _CURRENT
    from ats.workflow.cutover_routing import RouteUnavailable
    shutil.copytree(REPO_ROOT / 'config', tmp_path / 'config')
    root = tmp_path / 'config'
    plan = build_plan(requested_tasks=['fundamental-routine'], scope=ProjectionScope(kind='entity', id='MRVL'),
        trigger=TriggerContext(kind='manual', trigger_id='peer-scope'), as_of='2026-10-10T03:00:00+00:00',
        run_id='peer-scope', config_dir=root)
    info, funda = plan.tasks
    who = task_identity(plan, info)
    assert who.scope['entities'] == ['MRVL']
    assert task_identity(plan, funda).scope['entities'] == ['MRVL']
    assert 'config/pead/MRVL.yaml' in plan.source_config_hashes
    assert consumer_entities('information', 'entity', 'MRVL', config_dir=root) == who.scope['entities']
    token = _CURRENT.set(ReadContext(who, mode='isolated'))
    try:
        with pytest.raises(RouteUnavailable, match='entity escapes'):
            guard_input('information', {'entity': 'UNCONFIGURED'})
    finally:
        _CURRENT.reset(token)
    source = root / 'pead/MRVL.yaml'
    source.write_text(source.read_text() + '\n# changed relationship config\n')
    with pytest.raises(RouteUnavailable, match='configuration drifted'):
        task_identity(plan, info)


def test_information_uses_versioned_full_text_and_failed_model_publishes_nothing(monkeypatch):
    from ats.agents.information import documents
    from ats.schemas.news import ContextUpdate
    from datetime import UTC, datetime
    rows = [{'id':'doc', 'input_ref':'doc@version', 'publication_id':'publication', 'symbol':'NVDA',
             'headline':'Peer capacity changed', 'text':'Original admitted body with supporting facts',
             'published_at':'2026-10-10T01:00:00+00:00', 'source':'admitted-source'}]
    monkeypatch.setattr(documents.assemble, 'admitted_events', lambda *a, **kw: rows)
    seen = []
    def model(symbol, material, articles):
        assert material == rows
        assert articles[0][1] == rows[0]['text']
        return ContextUpdate(symbol=symbol, as_of=datetime.now(UTC), materiality=.5, confidence=.3,
                             event_summary='Capacity update', narrative_delta='Capacity changed')
    monkeypatch.setattr(documents, '_llm_brief', model)
    monkeypatch.setattr(documents, 'publish_information_brief', lambda store, payload, **kw: seen.append((payload, kw)))
    documents.run_document_pass(object(), 'MRVL', cfg={}, fresh=[])
    evidence = seen[0][0]['source_evidence'][0]
    assert evidence['document_ref'] == 'doc@version' and evidence['locator']['field'] == 'title'
    assert seen[0][0]['impact_candidates'] == ['supply_chain']
    assert seen[0][0]['confidence'] == .3 and evidence['confidence'] == .3
    assert seen[0][1]['input_refs'] == ['doc@version', 'publication']
    rows[0].update(symbol='DOWJONES', related_entities=['NVDA'])
    documents.run_document_pass(object(), 'NVDA', cfg={}, fresh=[])
    assert seen[1][0]['impact_candidates'] == ['direct']
    assert seen[1][0]['source_evidence'][0]['related_entities'] == ['NVDA']
    monkeypatch.undo()
    monkeypatch.setattr(documents.assemble, 'admitted_events', lambda *a, **kw: rows)
    monkeypatch.setattr(documents, 'run_structured', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('model unavailable')))
    monkeypatch.setattr(documents, 'publish_information_brief', lambda *a, **kw: pytest.fail('degraded brief published'))
    with pytest.raises(RuntimeError, match='no brief published'):
        documents.run_document_pass(object(), 'MRVL', cfg={}, fresh=[])


def test_blank_baseline_label_resolves_only_matching_known_calendar_without_rewriting(tmp_path, monkeypatch):
    from datetime import UTC, datetime
    from ats.memory.store import TradingMemory
    from ats.schemas.pead import PeadDossier, ExpectationSet
    from ats.agents.fundamental import routine
    store = TradingMemory(':memory:')
    stamp = datetime(2026, 10, 10, tzinfo=UTC)
    store.save_dossier(PeadDossier(symbol='MRVL', fiscal_label='', phase='prep', updated_at=stamp,
        earnings_date='2026-08-27', expectation_set=ExpectationSet(symbol='MRVL', fiscal_label='', as_of=stamp,
        narrative='Original expectation baseline', expectations=[])))
    monkeypatch.setattr(routine, 'evaluation_now', lambda tz: stamp)
    source = {'symbol':'MRVL', 'earnings_date':'2026-08-27', 'fiscal_label':'Q2 FY2027', 'year':2027,
              'quarter':2, 'detected_at':'2026-09-01T00:00:00+00:00'}
    monkeypatch.setattr(store, 'get_period', lambda symbol, day: source if str(day) == '2026-08-27' else None)
    before = store.get_dossier('MRVL', '').model_dump_json()
    baseline = routine.load_baseline(store, 'MRVL')
    assert baseline['fiscal_label'] == 'Q2 FY2027'
    assert any(ref.startswith('pead-period:MRVL:2026-08-27@') for ref in baseline['baseline_refs'])
    assert store.get_dossier('MRVL', '').model_dump_json() == before
    source['quarter'] = 3
    assert routine.load_baseline(store, 'MRVL')['fiscal_label'] == ''
    source['quarter'] = 2
    source['detected_at'] = '2026-10-11T00:00:00+00:00'
    assert routine.load_baseline(store, 'MRVL')['fiscal_label'] == ''
    assert routine._change_metric_number('[2026-10-10] gross_margin 52.1%') == 52.1
    assert routine._change_metric_number('[2026-10-10] no numeric reading') is None


def test_information_missing_confidence_or_opinion_update_is_not_a_success(monkeypatch):
    from ats.agents.information import documents
    from ats.agents.pead.outputs import ContextUpdateView
    row = {'input_ref':'document@version', 'publication_id':'publication', 'headline':'Admitted fact',
           'text':'Admitted original text', 'published_at':'2026-10-10', 'source':'admitted'}
    monkeypatch.setattr(documents.assemble, 'admitted_events', lambda *a, **kw: [row])
    def model(*args, **kwargs):
        assert kwargs['skill_slug'] == 'information-analyst'
        return ContextUpdateView(materiality=.9, event_summary='Admitted fact')
    monkeypatch.setattr(documents, 'run_structured', model)
    monkeypatch.setattr(documents, 'publish_information_brief', lambda *a, **kw: pytest.fail('missing confidence published'))
    with pytest.raises(RuntimeError, match='no brief published'):
        documents.run_document_pass(object(), 'MRVL', cfg={}, fresh=[])
    from ats.agents.pead.outputs import ExpectationChangeView
    view = ContextUpdateView(materiality=.9, confidence=.8, event_summary='Admitted fact',
        expectation_changes=[ExpectationChangeView(dim_key='revenue', change='raise expectations')])
    monkeypatch.setattr(documents, 'run_structured', lambda *a, **kw: view)
    with pytest.raises(RuntimeError, match='no brief published') as error:
        documents.run_document_pass(object(), 'MRVL', cfg={}, fresh=[])
    assert 'cannot emit Fundamental' in str(error.value.__cause__)


def test_additive_information_evidence_preserves_old_v1_hash_and_binds_new_evidence():
    from ats.agent.task_projection import InformationBriefPayload
    payload = {'entity':'MRVL','headline':'Original fact','summary':'Original summary',
               'relevance':'low','sources':['original']}
    old = InformationBriefPayload(**payload).model_dump(mode='json')
    old.pop('source_evidence')
    old_json = json.dumps(old,ensure_ascii=False,sort_keys=True)
    assert InformationBriefPayload.model_validate(old).payload_json() == old_json
    new = {**old,'source_evidence':[{'document_ref':'original@version','locator':{'field':'title'}}]}
    assert InformationBriefPayload.model_validate(new).payload_json() != old_json
