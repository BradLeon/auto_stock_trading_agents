from datetime import date,datetime,timezone,timedelta
from decimal import Decimal
import pytest
from ats.data.sources.factset_contracts import CellEvidence,GroupIdentity,GroupPackage,SectorCandidate,validate_group
from ats.data.sources.factset_report_layout import load_layout_policy,policy_hash
from ats.data.stores.structured.factset_reviews import FactSetReviews
from ats.data.stores.structured.repository import SQLiteStructuredRepository

T=datetime(2026,9,27,tzinfo=timezone.utc)

def package():
    policy=load_layout_policy()
    evidence=CellEvidence(page_number=1,image_number=2,image_hash='a'*64,region=(.1,.2,.3,.4),raw_token='20.0',method='test')
    cells=tuple(SectorCandidate(entity_id=entity,column='forward_pe',value=Decimal('20.0'),unit='multiple',
                               source_label=labels[0],label_evidence=evidence.model_copy(update={'raw_token':labels[0]}),value_evidence=(evidence,))
                for entity,labels in policy['sector_aliases'].items())
    return GroupPackage(pdf_hash='b'*64,document_version='document-v1',report_date=date(2026,9,18),
                        extractor_version='semantic-v1',policy_hash=policy_hash(policy),
                        group=GroupIdentity(chart_id='forward_pe',period='2026-09-18',period_basis='snapshot',
                                            estimate_state='estimated'),candidates=cells)

@pytest.fixture
def store(tmp_path):
    repository=SQLiteStructuredRepository(tmp_path/'factset-isolated.sqlite',artifact_root=tmp_path/'artifacts')
    yield FactSetReviews(repository)
    repository.close()

def approve(store,p,at=T):
    store.register(p,at=at)
    return store.decide(p.package_hash,decision='approve',reviewer='operator',evidence_refs=['source:pdf:p1'],
                        note='independent source review',policy=load_layout_policy(),at=at)

def test_hash_is_order_independent_but_evidence_sensitive():
    p=package()
    assert p.package_hash==p.model_copy(update={'candidates':tuple(reversed(p.candidates))}).package_hash
    changed=p.candidates[0].model_copy(update={'value':Decimal('21')})
    assert p.package_hash!=p.model_copy(update={'candidates':(changed,)+p.candidates[1:]}).package_hash

def test_approval_bound_to_exact_revision_and_time(store):
    p=package(); rid=approve(store,p)
    assert store.require_approval(p,rid,policy=load_layout_policy(),as_of=T)['review_id']==rid
    for changed in [p.model_copy(update={'pdf_hash':'c'*64}),p.model_copy(update={'extractor_version':'v2'}),
                    p.model_copy(update={'document_version':'new'}),p.model_copy(update={'policy_hash':'d'*64})]:
        with pytest.raises(ValueError): store.require_approval(changed,rid,policy=load_layout_policy(),as_of=T)
    with pytest.raises(ValueError): store.require_approval(p,rid,policy=load_layout_policy(),as_of=T-timedelta(seconds=1))
    with pytest.raises(ValueError,match='bound_review'): store.require_approval(p,True,policy=load_layout_policy())

def test_rejection_revokes_current_but_keeps_asof(store):
    p=package(); rid=approve(store,p)
    store.decide(p.package_hash,decision='reject',reviewer='operator',evidence_refs=['source:pdf:p1'],
                 note='later discrepancy',policy=load_layout_policy(),at=T+timedelta(seconds=1))
    with pytest.raises(ValueError): store.require_approval(p,rid,policy=load_layout_policy(),as_of=T+timedelta(seconds=2))
    assert store.require_approval(p,rid,policy=load_layout_policy(),as_of=T)
    assert store.conn.execute('SELECT COUNT(*) FROM factset_group_reviews').fetchone()[0]==2

def test_idempotent_register_and_review(store):
    p=package(); a=approve(store,p); b=approve(store,p)
    assert a==b
    assert len(store.list_packages())==1
    assert store.conn.execute('SELECT COUNT(*) FROM factset_group_reviews').fetchone()[0]==1

def test_incomplete_or_conflicted_group_cannot_be_approved(store):
    p=package(); p=p.model_copy(update={'candidates':p.candidates[:-1]})
    with pytest.raises(ValueError,match='coverage'): approve(store,p)
    p=package(); p=p.model_copy(update={'stage_errors':('unresolved_decimal',)})
    with pytest.raises(ValueError,match='unresolved_decimal'): approve(store,p)

@pytest.mark.parametrize('basis,value',[('target_quarter','2026'),('calendar_year','2026Q3'),('snapshot','bad')])
def test_period_basis_is_not_interchangeable(basis,value):
    with pytest.raises(ValueError): GroupIdentity(chart_id='x',period=value,period_basis=basis,estimate_state='actual')

def test_no_sp500_no_unknown_unit_and_no_nan():
    p=package(); data=p.candidates[0].model_dump()
    for key,value in [('entity_id','SP500'),('unit','unknown'),('value','NaN')]:
        with pytest.raises(ValueError): SectorCandidate.model_validate({**data,key:value})

def test_review_requires_actor_source_and_note(store):
    p=package(); store.register(p,at=T)
    with pytest.raises(ValueError): store.decide(p.package_hash,decision='approve',reviewer='',evidence_refs=[],
                                               note='',policy=load_layout_policy(),at=T)

def test_review_persists_after_reopen(tmp_path):
    path=tmp_path/'audit.sqlite'
    repo=SQLiteStructuredRepository(path,artifact_root=tmp_path/'assets'); s=FactSetReviews(repo)
    p=package(); rid=approve(s,p); repo.close()
    repo=SQLiteStructuredRepository(path,artifact_root=tmp_path/'assets')
    assert FactSetReviews(repo).require_approval(p,rid,policy=load_layout_policy(),as_of=T)
    repo.close()

def test_correction_appends_and_requires_new_approval(store):
    p=package(); rid=approve(store,p)
    evidence=p.candidates[0].value_evidence[0].model_dump(mode='json')
    evidence.update(raw_token='21.0',method='manual:operator')
    new_hash=store.correct(p.package_hash,corrections=[{'entity_id':p.candidates[0].entity_id,
        'column':'forward_pe','value':'21.0','evidence':evidence}],reviewer='operator',note='source correction',at=T)
    changed=store.package(new_hash)
    assert changed.candidates[0].status=='manual_reviewed'
    assert changed.candidates[0].corrected_from
    assert store.package(p.package_hash).candidates[0].value==Decimal('20.0')
    with pytest.raises(ValueError): store.require_approval(changed,rid,policy=load_layout_policy(),as_of=T)
    assert store.conn.execute('SELECT COUNT(*) FROM factset_group_corrections').fetchone()[0]==1
