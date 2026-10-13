"""Preparation cannot turn a successful subset or a failure label into admission."""
import importlib.util
import json
import hashlib
import sqlite3
from pathlib import Path

import pytest

from ats.config import REPO_ROOT

spec = importlib.util.spec_from_file_location("phase_f_stage_one", REPO_ROOT / "scripts/prepare_phase_f_stage_one.py")
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


def test_inventory_selects_one_fundamental_mode_and_keeps_downstream_conditional():
    result = stage.inventory("2026-10-10T03:00:00+00:00")
    assert not result["production_claim"]
    assert {r["consumer"] for g in result["groups"] for r in g["required_uses"]} | {
        r["consumer"] for r in result["downstream_uses"]} == {
            "layer", "sector", "information", "fundamental", "macro", "technical", "chief", "risk", "trader", "clerk"}
    for group in result["groups"]:
        if not group["profile"]:
            continue
        tasks = {r["task_id"] for r in group["required_uses"]}
        mode = group["selected_fundamental_mode"]
        assert "fundamental-" + mode in tasks
        assert "fundamental-" + ({"routine", "event"} - {mode}).pop() not in tasks
        assert {"layer-review", "information-brief", "sector-review", "macro-review", "technical-review"} <= tasks
        assert all(r["report_status"] == "not_bound" for r in group["required_uses"])
    routine = next(g for g in result["groups"] if g["workflow"] == "fundamental-routine")
    assert {r["task_id"] for r in routine["required_uses"]} == {"information-brief", "fundamental-routine"}


def test_triage_preserves_unknown_shared_failure_and_refuses_dropped_original(tmp_path):
    junit = tmp_path / "discovery.xml"
    junit.write_text('<testsuites><testsuite><testcase classname="tests.test_unknown_shared" name="test_shared"><failure message="unsafe"/></testcase></testsuite></testsuites>')
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps({"failures": [{"test": "tests/test_unknown_shared.py::test_shared", "classification": "requires attribution"}]}))
    report = stage.failures(junit, previous)
    row = report["failures"][0]
    assert row["disposition"] == "unattributed"
    assert not row["scope_exclusion_proven"] and not row["gate_pass_claim"]
    junit.write_text('<testsuites><testsuite/></testsuites>')
    with pytest.raises(ValueError, match="missing from discovery"):
        stage.failures(junit, previous)


def test_preflight_missing_report_cannot_pass_eligible_qualification(monkeypatch):
    from ats.workflow import scoped_intake
    from ats.workflow.runtime_reads import business_identity
    who = business_identity("technical", kind="entity", scope_id="COHR", entities=["COHR"], as_of="2026-10-10T03:00:00+00:00")
    checks = []
    monkeypatch.setattr(scoped_intake, "check_freeze", lambda snapshot: checks.append(snapshot))
    monkeypatch.setattr(scoped_intake, "qualification_report", lambda *a, **k: {
        "scopes": [{"identity": who.as_row(), "qualification": {"status": "eligible"}}]})
    result = stage.preflight([who.as_row()], {"fixture": True}, {}, source_ledger="fixture")
    assert result["status"] == "pending" and len(checks) == 2
    assert not result["production_claim"] and not result["permission_checked"]
    with pytest.raises(ValueError, match="escape"):
        stage.preflight([who.as_row()], {}, {"other": {}}, source_ledger="fixture")


def test_inventory_rejects_ambiguous_time():
    with pytest.raises(ValueError, match="timezone"):
        stage.inventory("2026-10-10T03:00:00")


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


def test_real_preflight_missing_ledger_is_readonly_and_pending(tmp_path):
    from ats.workflow.scoped_intake import freeze_snapshot
    from ats.workflow.runtime_reads import business_identity
    who = business_identity("technical", kind="entity", scope_id="COHR", entities=["COHR"], as_of="2026-10-10T03:00:00+00:00")
    source = tmp_path / "missing.sqlite"
    result = stage.preflight([who.as_row()], freeze_snapshot(), {}, source_ledger=source)
    assert result["status"] == "pending" and not source.exists()
    assert result["scopes"][0]["qualification"]["status"] == "ineligible"


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


def test_cache_audit_keeps_referenced_missing_history_pending_without_store_writes(tmp_path):
    from ats.data.stores.structured.repository import SQLiteStructuredRepository
    from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository
    data = tmp_path / 'data.sqlite'
    SQLiteStructuredRepository(data).close()
    repository = PlatformUnstructuredRepository(data, writable=True)
    repository.conn.execute('INSERT INTO data_document_versions VALUES (?,?,?,?,?,?,?,?)',
        ('doc@version', 'doc', 'expected-hash', str(tmp_path / 'missing.txt'), 50,
         'fixture:source', '2026-10-10T01:00:00+00:00', '2026-10-10T01:00:00+00:00'))
    repository.conn.commit()
    repository.close()
    internal = tmp_path / 'internal.sqlite'
    with sqlite3.connect(internal) as connection:
        connection.execute('CREATE TABLE historical_refs (input_ref TEXT)')
        connection.execute('INSERT INTO historical_refs VALUES (?)', ('doc@version',))
    manifest = tmp_path / 'inventory.json'
    manifest.write_text(json.dumps({'groups': [], 'downstream_uses': []}))
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (data, internal)}
    result = stage.cache_audit(manifest, data, internal)
    assert result['status'] == 'pending' and not result['qualification_granted']
    row = result['historical_document_versions'][0]
    assert row['status'] == 'unresolved'
    assert row['internal_reference_hits'] == [{'table': 'historical_refs', 'rows': 1}]
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before} == before


def test_cache_audit_missing_store_refuses_without_creating_it(tmp_path):
    manifest = tmp_path / 'inventory.json'
    manifest.write_text('{}')
    missing = tmp_path / 'missing.sqlite'
    with pytest.raises(ValueError, match='existing'):
        stage.cache_audit(manifest, missing, missing)
    assert not missing.exists()


@pytest.mark.parametrize('fault', [None, 'missing_entity', 'period', 'unit', 'missing_history',
                                  'corrupt_history', 'migration_label_without_proof', 'conflict'])
def test_selected_financial_uses_reopen_history_and_refuse_incomplete_inputs(tmp_path, fault):
    from datetime import datetime, UTC
    from ats.data.core.structured_models import ArtifactDescriptor, ObservationInput, SeriesIdentity, QualityStatus
    from ats.data.stores.structured.repository import SQLiteStructuredRepository
    from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository
    point = datetime(2026, 10, 10, tzinfo=UTC)
    data, artifacts = tmp_path / 'source.sqlite', tmp_path / 'raw'
    repo = SQLiteStructuredRepository(data, artifact_root=artifacts)
    repo.bootstrap_catalog()
    artifact = repo.put_artifact({'revenue': 100}, ArtifactDescriptor(source_id='yfinance_financials',
        dataset_id='company_financials', fetched_at=point, query_scope={'entities': ['NVDA']},
        source_url='https://example.test/financials', retention='query_slice'))
    observation = repo.save_observation(ObservationInput(series=SeriesIdentity(source_id='yfinance_financials',
        dataset_id='company_financials', entity_id='NVDA', metric_id='financial.revenue.gaap',
        unit='USD', currency='USD', period_basis='quarter'), period='2026-07-31', value=100.,
        known_at=point, fetched_at=point, artifact_id=artifact.id,
        quality_status=QualityStatus.CONFLICT if fault == 'conflict' else QualityStatus.ACCEPTED))
    if fault == 'migration_label_without_proof':
        repo.conn.execute('UPDATE structured_observations SET value=101, quality_json=? WHERE observation_id=?',
            (json.dumps({'reason_codes': ['semantic_reclassification']}), observation.id))
        repo.conn.commit()
    raw_path = artifacts / repo.conn.execute('SELECT relative_path FROM structured_artifact_blobs').fetchone()[0]
    repo.close()
    PlatformUnstructuredRepository(data, writable=True).close()
    if fault == 'missing_history': raw_path.unlink()
    if fault == 'corrupt_history': raw_path.write_text('different raw data')
    use = {'use': 'financial:NVDA', 'entity': 'NVDA', 'source_id': 'yfinance_financials',
        'period': '2026-07-31', 'currency': 'USD',
        'metrics': [{'metric_id': 'financial.revenue.gaap', 'period_basis': 'quarter', 'unit': 'USD'}]}
    if fault == 'missing_entity': use['entity'] = 'COHR'
    if fault == 'period': use['period'] = '2026-04-30'
    if fault == 'unit': use['metrics'][0]['unit'] = 'TWD'
    manifest = tmp_path / 'required.json'
    manifest.write_text(json.dumps({'as_of': point.isoformat(), 'uses': [use]}))
    before = hashlib.sha256(data.read_bytes()).hexdigest()
    result = stage.financial_use_audit(manifest, data, artifacts)
    assert result['status'] == ('passed' if fault is None else 'failed')
    assert not result['qualification_granted'] and not result['production_claim']
    assert hashlib.sha256(data.read_bytes()).hexdigest() == before
    if fault is None:
        assert result['uses'][0]['input_refs'] == [observation.id]
        assert result['uses'][0]['historical_refs'] == [observation.id]
    else:
        assert result['uses'][0]['problems']
        if fault == 'conflict':
            assert 'not accepted' in result['uses'][0]['problems'][0]


def test_completed_data_audit_keeps_missing_dependency_blocked():
    required = [{'use': 'information:MRVL'}, {'use': 'fundamental:MRVL'}]
    checks = [{'use': required[0]['use'], 'status': 'passed', 'problems': []},
              {'use': required[1]['use'], 'status': 'rejected', 'problems': ['missing input lineage']}]
    result = stage.summarize_selected_audit(required, checks)
    assert result['audit_status'] == 'completed'
    assert result['coverage_status'] == 'pending' and result['gaps'] == [checks[1]]
    assert not result['retirement_allowed'] and not result['qualification_granted']
    assert not result['production_claim']


@pytest.mark.parametrize('fault', ['omit', 'extra', 'duplicate', 'unsupported_status', 'unexplained_rejection'])
def test_data_audit_cannot_complete_by_dropping_required_uses_or_hiding_errors(fault):
    required = [{'use': 'information:MRVL'}, {'use': 'fundamental:MRVL'}]
    checks = [{'use': row['use'], 'status': 'passed', 'problems': []} for row in required]
    if fault == 'omit': checks.pop()
    if fault == 'extra': checks.append({'use': 'unexpected', 'status': 'passed'})
    if fault == 'duplicate': checks.append(checks[0])
    if fault == 'unsupported_status': checks[0]['status'] = 'error'
    if fault == 'unexplained_rejection': checks[0]['status'] = 'rejected'
    with pytest.raises(ValueError):
        stage.summarize_selected_audit(required, checks)


def test_selected_use_audit_missing_internal_store_refuses_without_creation(tmp_path):
    missing = tmp_path / 'absent.sqlite'
    with pytest.raises(ValueError, match='existing read-only internal'):
        stage.selected_use_audit(missing, missing, tmp_path, missing)
    assert not missing.exists()


def test_information_target_scope_is_configured_frozen_and_role_specific(tmp_path):
    import shutil
    from ats.workflow.phase_e import build_plan
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


def test_selected_research_snapshot_preserves_original_rows_and_append_only_guards(tmp_path):
    from ats.workflow.isolation import isolated_run
    module_spec = importlib.util.spec_from_file_location('research_capture', REPO_ROOT / 'scripts/capture_phase_f_research.py')
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    source = tmp_path / 'original.sqlite'
    with sqlite3.connect(source) as conn:
        conn.executescript('''CREATE TABLE structured_series(series_id TEXT PRIMARY KEY, entity_id TEXT);
        CREATE TABLE structured_observations(observation_id TEXT PRIMARY KEY, series_id TEXT, value REAL, content_hash TEXT);
        CREATE TABLE data_documents(document_id TEXT PRIMARY KEY, entity TEXT, payload TEXT);
        CREATE TABLE structured_snapshot_items(snapshot_id TEXT, observation_id TEXT);
        INSERT INTO structured_series VALUES ('a','MRVL'),('b','OTHER');
        INSERT INTO structured_observations VALUES ('one','a',12.5,'original-hash'),('two','b',20,'other-hash');
        INSERT INTO data_documents VALUES ('peer-doc','NVDA','original body');
        CREATE TRIGGER protect_observations BEFORE UPDATE ON structured_observations BEGIN SELECT RAISE(ABORT,'immutable'); END;''')
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    with isolated_run('selected-snapshot', root=tmp_path/'iso') as env:
        target = env.path_for('ATS_DATA_DB_PATH')
        result = module.selected_snapshot(source, target, ['MRVL'])
        with sqlite3.connect(target) as conn:
            assert conn.execute('SELECT * FROM structured_observations').fetchall() == [('one','a',12.5,'original-hash')]
            assert conn.execute('SELECT * FROM data_documents').fetchall() == [('peer-doc','NVDA','original body')]
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                conn.execute('UPDATE structured_observations SET value=0')
        assert result['tables']['structured_snapshot_items']['copied'] == 0
        with pytest.raises(ValueError, match='destination'):
            module.selected_snapshot(source, source, ['MRVL'])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_information_deployment_accepts_configured_read_universe_but_requires_target_output(monkeypatch):
    from ats.workflow.runtime_reads import consumer_entities, business_identity
    from ats.workflow.read_deployment import projection_check
    from ats.workflow.cutover import CutoverError
    entities = consumer_entities('information', 'entity', 'MRVL')
    who = business_identity('information', kind='entity', scope_id='MRVL', entities=entities,
                            as_of='2026-10-10T03:00:00+00:00')
    monkeypatch.setattr('ats.memory.get_store', lambda: object())
    monkeypatch.setattr('ats.workflow.consumer_reads.read_projection', lambda *a, **kw: {'input_refs':['doc@version']})
    def reference(entity):
        return {'projection_id':'unit-projection', 'content_hash':'unit-hash', 'role':'information_brief',
                'scope_kind':'entity', 'scope_id':entity}
    assert projection_check(who, refs=[reference('MRVL')])['available']
    with pytest.raises(CutoverError, match='escapes business entities'):
        projection_check(who, refs=[reference('NVDA')])
    with pytest.raises(CutoverError, match='escapes'):
        projection_check(who, refs=[reference('UNCONFIGURED')])


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


def test_research_capture_children_install_isolation_without_parent_path_alias(tmp_path, monkeypatch):
    """Orchestration regression with real child isolation; no business/model proof."""
    import os
    import shutil
    import subprocess
    import sys
    from types import SimpleNamespace
    from ats.workflow.scoped_intake import freeze_snapshot
    from ats.workflow.isolation import verified_isolation_root

    module_spec = importlib.util.spec_from_file_location('research_child_environment', REPO_ROOT / 'scripts/capture_phase_f_research.py')
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    data, memory = tmp_path / 'source.sqlite', tmp_path / 'memory.sqlite'
    for path in (data, memory):
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE original(value TEXT)')
    freeze = tmp_path / 'freeze.json'
    freeze.write_text(json.dumps(freeze_snapshot()))
    root = tmp_path / 'candidate'
    original_environment = os.environ.copy()
    real_run = subprocess.run
    observed = []

    def snapshot(source, target, entities):
        assert verified_isolation_root() == root.resolve()
        shutil.copyfile(source, target)
        return {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}

    def child(command, **options):
        # Check the actual orchestrator's boundary, then exercise the child guard.
        assert options['env'] == original_environment
        assert os.environ['ATS_DB_PATH'] != options['env'].get('ATS_DB_PATH')
        program = ('import sys; from pathlib import Path; '
                   'from ats.workflow.isolation import isolated_run, verified_isolation_root; '\
                   '\nwith isolated_run("child-environment-regression", root=Path(sys.argv[1])) as env:\n'
                   ' assert verified_isolation_root() == Path(sys.argv[1]).resolve()\n'
                   ' print("child-isolation-verified")\n')
        result = real_run([sys.executable, '-c', program, str(root)], **options)
        observed.append(command[-1])
        return result

    monkeypatch.setattr(module, 'selected_snapshot', snapshot)
    monkeypatch.setattr('ats.data.consumer_api.read_input', lambda *a, **k: SimpleNamespace(input_refs=['original-doc@version']))
    preview = tmp_path / 'approved-preview.json'
    preview.write_text(json.dumps({'entity':'MRVL', 'as_of':'2026-10-10T14:40:00+00:00',
        'prompts':{'information':{'context':'1.44 million chips; $40B financing'}}}))
    def capture(**kwargs):
        criteria = next(a['criteria'] for a in kwargs['matrix'].body['assertions'] if a['dimension']=='analysis')
        assert criteria == [{'path':'quantity_values','op':'subset','value':['1440000','40000000000']}]
        return {'input_hash':'orchestration-fixture-only'}
    monkeypatch.setattr(module.runner, 'capture', capture)
    monkeypatch.setattr(module.reports, 'record', lambda **k: {'fixture_only': True})
    monkeypatch.setattr(module.reports, 'read', lambda *a, **k: {'status': 'fixture_only'})
    monkeypatch.setattr(module.subprocess, 'run', child)
    module.execute(SimpleNamespace(root=root, freeze=freeze, replay_matrix=None, entity='MRVL',
        with_routine=False, as_of='2026-10-10T14:40:00+00:00', data_source=data, memory_source=memory,
        quantity_source_preview=preview))
    assert observed == ['new', 'replay']
    assert all('child-isolation-verified' in (root / (name + '.log')).read_text() for name in observed)
    assert json.loads((root / 'review.json').read_text())['source_unchanged']


@pytest.mark.parametrize('wrong', ['entity', 'as_of'])
def test_research_quantity_preview_requires_exact_approved_scope(tmp_path, wrong):
    from types import SimpleNamespace
    from ats.workflow.scoped_intake import freeze_snapshot
    module_spec = importlib.util.spec_from_file_location('research_quantity_scope', REPO_ROOT / 'scripts/capture_phase_f_research.py')
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    freeze = tmp_path / 'freeze.json'
    freeze.write_text(json.dumps(freeze_snapshot()))
    preview = tmp_path / 'preview.json'
    body = {'entity':'MRVL','as_of':'2026-10-10T14:40:00+00:00',
            'prompts':{'information':{'context':'1.44 million chips'}}}
    body[wrong] = 'MSFT' if wrong=='entity' else '2026-10-11T14:40:00+00:00'
    preview.write_text(json.dumps(body))
    root = tmp_path / 'not_started'
    with pytest.raises(ValueError, match='entity/as_of differs'):
        module.execute(SimpleNamespace(root=root, freeze=freeze, replay_matrix=None,
            entity='MRVL', as_of='2026-10-10T14:40:00+00:00', quantity_source_preview=preview))
    assert not root.exists()
