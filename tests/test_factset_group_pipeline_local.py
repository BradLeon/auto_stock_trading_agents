from datetime import timedelta
import hashlib
from ats.data.core.structured_models import ArtifactDescriptor
from ats.data.pipelines.factset_groups import FactSetGroupPipeline
from ats.data.sources.factset_report_layout import load_layout_policy
from test_factset_reviews_local import package,store,T,approve

def prepare(store):
    data=b'%PDF synthetic fixture only'
    p=package().model_copy(update={'pdf_hash':hashlib.sha256(data).hexdigest()})
    artifact=store.repository.put_artifact(data,ArtifactDescriptor(source_id='factset_earnings_insight_metrics',
        dataset_id='sp500_earnings_insight',fetched_at=T,media_type='application/pdf'))
    return p,artifact.id

def test_shadow_then_bound_approval_preserves_history_and_replays(store):
    p,artifact=prepare(store)
    pipeline=FactSetGroupPipeline(store.repository,clock=lambda:T)
    first=pipeline.run(p,artifact_id=artifact,document_id='doc')
    assert not first['passed'] and not first['observation_ids']
    assert len(store.repository.candidates(dataset_id='sp500_earnings_insight'))==11
    rid=store.decide(p.package_hash,decision='approve',reviewer='operator',evidence_refs=['source:pdf:p1'],
        note='independent review',policy=load_layout_policy(),at=T+timedelta(seconds=1))
    pipeline.clock=lambda:T+timedelta(seconds=2)
    second=pipeline.run(p,artifact_id=artifact,document_id='doc',review_id=rid)
    assert second['passed'] and len(second['observation_ids'])==11
    assert first['release_id']!=second['release_id']
    repeat=pipeline.run(p,artifact_id=artifact,document_id='doc',review_id=rid)
    assert repeat['status']=='no_change' and repeat['release_id']==second['release_id']
    old=store.repository.release_manifests(dataset_id='sp500_earnings_insight',as_of=T)
    assert len(old)==1 and not old[0]['passed']
    assert len(store.repository.observations(dataset_id='sp500_earnings_insight',as_of=T))==0

def test_unapproved_or_failed_group_cannot_leak_observations(store):
    p,artifact=prepare(store)
    p=p.model_copy(update={'stage_errors':('missing_cell',)})
    result=FactSetGroupPipeline(store.repository,clock=lambda:T).run(p,artifact_id=artifact,document_id='doc')
    assert not result['passed']
    assert len(store.repository.candidates(dataset_id='sp500_earnings_insight'))==11
    assert not store.repository.observations(dataset_id='sp500_earnings_insight')

def test_bool_approval_and_wrong_pdf_are_rejected(store):
    import pytest
    p,artifact=prepare(store)
    pipeline=FactSetGroupPipeline(store.repository,clock=lambda:T)
    with pytest.raises(ValueError,match='bound_review'):
        pipeline.run(p,artifact_id=artifact,document_id='doc',review_id=True)
    with pytest.raises(ValueError,match='artifact_mismatch'):
        pipeline.run(p,artifact_id='missing',document_id='doc')

def test_manifest_crash_retry_preserves_observation_ids_and_visibility(store, monkeypatch):
    import pytest
    from ats.data.products.base import DataProducts
    monkeypatch.setattr('ats.data.pipelines.factset_groups.source_mode',lambda _: 'platform')
    p,artifact=prepare(store)
    rid=approve(store,p)
    pipeline=FactSetGroupPipeline(store.repository,clock=lambda:T)
    original=store.repository.save_release_manifest
    def crash(**kwargs):
        raise RuntimeError('simulated manifest crash')
    monkeypatch.setattr(store.repository,'save_release_manifest',crash)
    with pytest.raises(RuntimeError,match='manifest crash'):
        pipeline.run(p,artifact_id=artifact,document_id='doc',review_id=rid)
    ids={row['observation_id'] for row in store.repository.observations(dataset_id='sp500_earnings_insight',accepted_only=False)}
    assert len(ids)==11
    assert not store.repository.observations(dataset_id='sp500_earnings_insight')
    products=DataProducts(structured_repository=store.repository)
    args=dict(version_id=p.document_version,report_date=p.report_date,expected_groups=[p.group])
    assert products.earnings_insight_groups(**args,as_of=T)['published_groups']==0
    monkeypatch.setattr(store.repository,'save_release_manifest',original)
    pipeline.clock=lambda:T+timedelta(seconds=1)
    recovered=pipeline.run(p,artifact_id=artifact,document_id='doc',review_id=rid)
    assert set(recovered['observation_ids'])==ids
    assert not store.repository.observations(dataset_id='sp500_earnings_insight',as_of=T)
    assert len(store.repository.observations(dataset_id='sp500_earnings_insight',as_of=T+timedelta(seconds=1)))==11
    assert products.earnings_insight_groups(**args,as_of=T)['published_groups']==0
    assert products.earnings_insight_groups(**args,as_of=T+timedelta(seconds=1))['published_groups']==1
    store.decide(p.package_hash,decision='reject',reviewer='operator',evidence_refs=['pdf:p1'],
        note='later discrepancy',policy=load_layout_policy(),at=T+timedelta(seconds=2))
    assert products.earnings_insight_groups(**args,as_of=T+timedelta(seconds=2))['published_groups']==0
    assert products.earnings_insight_groups(**args,as_of=T+timedelta(seconds=1))['published_groups']==1
