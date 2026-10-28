from datetime import date, timedelta
from types import SimpleNamespace
from ats.data.products.base import DataProducts
from ats.data.pipelines.factset_groups import FactSetGroupPipeline
from ats.data.sources.factset_report_layout import load_layout_policy
from test_factset_reviews_local import store, T
from test_factset_group_pipeline_local import prepare


def test_current_gap_does_not_mix_historical_values(store, monkeypatch):
    monkeypatch.setattr('ats.data.pipelines.factset_groups.source_mode', lambda _: 'platform')
    p, artifact = prepare(store)
    requested=p.group
    p = p.model_copy(update={'document_version': 'aug', 'report_date': date(2026, 8, 28),
        'group':p.group.model_copy(update={'period':'2026-08-28'})})
    store.register(p, at=T)
    rid = store.decide(p.package_hash, decision='approve', reviewer='operator',
        evidence_refs=['pdf:p1'], note='review', policy=load_layout_policy(), at=T)
    pipeline = FactSetGroupPipeline(store.repository, clock=lambda:T)
    pipeline.run(p, artifact_id=artifact, document_id='doc', review_id=rid)
    products = DataProducts(structured_repository=store.repository)
    args = dict(version_id='sep', report_date=date(2026, 9, 18), expected_groups=[requested])
    result = products.earnings_insight_groups(**args, as_of=T)
    assert result['state'] == 'partial' and result['coverage'] == 0
    assert not result['current'][requested.key]['observations']
    assert len(result['latest_valid_history'][requested.key]['observations']) == 11
    assert result['latest_valid_history'][requested.key]['version_id'] == 'aug'
    assert result['latest_valid_history'][requested.key]['group']['period']=='2026-08-28'
    before = products.earnings_insight_groups(**args, as_of=T-timedelta(seconds=1))
    assert not before['latest_valid_history']


def test_empty_inventory_is_not_complete_and_shadow_not_exposed(store):
    p, artifact = prepare(store)
    FactSetGroupPipeline(store.repository, clock=lambda:T).run(p, artifact_id=artifact, document_id='doc')
    products = DataProducts(structured_repository=store.repository)
    result = products.earnings_insight_groups(version_id=p.document_version,
        report_date=p.report_date, expected_groups=[p.group], as_of=T)
    assert result['state'] == 'partial'
    assert not result['current'][p.group.key]['observations']
    empty = products.earnings_insight_groups(version_id=p.document_version,
        report_date=p.report_date, expected_groups=[], as_of=T)
    assert empty['state'] == 'partial' and empty['coverage'] is None


def test_current_summary_and_legacy_sector_are_separate(store):
    p, artifact = prepare(store)
    repo = store.repository
    repo.save_release_manifest(
        source_id='factset_earnings_insight_metrics',
        dataset_id='sp500_earnings_insight', partition='sector_core',
        report_date='2026-08-28', document_id='old-doc', version_id='aug',
        artifact_id=artifact, known_at=T - timedelta(days=1),
        extractor_version='legacy-v1', status='platform', passed=True,
        quality={'passed': True}, observation_ids=[])
    repo.save_release_manifest(
        source_id='factset_earnings_insight_metrics',
        dataset_id='sp500_earnings_insight', partition='sector_core',
        report_date='2026-09-18', document_id='new-doc', version_id=p.document_version,
        artifact_id=artifact, known_at=T,
        extractor_version='semantic-summary-v1', status='shadow', passed=False,
        quality={'core_acceptance': 'blocked', 'report_coverage': 'partial'},
        observation_ids=[])
    repo.save_release_manifest(
        source_id='factset_earnings_insight_metrics',
        dataset_id='sp500_earnings_insight', partition='index_core',
        report_date='2026-09-18', document_id='new-doc', version_id=p.document_version,
        artifact_id=artifact, known_at=T,
        extractor_version='new-index-v1', status='platform', passed=True,
        quality={'passed': True}, observation_ids=[])
    result = DataProducts(structured_repository=repo).earnings_insight_groups(
        version_id=p.document_version, report_date=p.report_date,
        expected_groups=[p.group], as_of=T)
    assert result['core_acceptance'] == 'blocked'
    assert result['report_coverage'] == 'partial'
    assert result['gaps'][0]['group_key'] == p.group.key
    assert result['historical_sector_core']['version_id'] == 'aug'
    assert result['historical_sector_core']['not_current_report']
    assert not result['current'][p.group.key]['observations']
    snapshot = DataProducts(
        structured_repository=repo,
        unstructured_repository=SimpleNamespace(documents_by_id=lambda _: {}),
    ).earnings_insight_snapshot(as_of=T)
    assert snapshot.report.version_id == p.document_version
    assert not snapshot.sectors
    assert snapshot.status.sector_release.state == 'registered_no_data'
