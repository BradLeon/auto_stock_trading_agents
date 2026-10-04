import pytest
from ats.data.sources.factset_contracts import digest,validate_group
from ats.data.sources.factset_report_layout import load_layout_policy
from ats.data.sources.factset_vision import FactSetVision
from ats.data.pipelines.factset_groups import FactSetGroupPipeline
from test_factset_reviews_local import package,store,approve,T
from test_factset_group_pipeline_local import prepare

def core(p):
    policy=load_layout_policy()
    group=p.group.model_copy(update={'scope_id':'technology','scope_version':policy['scope_policy']['version'],
        'entity_ids':('GICS_45',)})
    return p.model_copy(update={'group':group,'candidates':tuple(c for c in p.candidates if c.entity_id=='GICS_45')})

def test_core_passes_without_other_sectors_but_full_scope_requires_all():
    p=core(package()); policy=load_layout_policy()
    assert validate_group(p,policy)==[]
    assert 'group_coverage_mismatch' in validate_group(p.model_copy(update={'group':package().group}),policy)
    assert 'entity_scope_mismatch' in validate_group(p.model_copy(update={'group':p.group.model_copy(update={'entity_ids':('GICS_10',)})}),policy)
    assert validate_group(p.model_copy(update={'stage_errors':('ambiguous_header',)}),policy)==['ambiguous_header']

def test_scope_hash_and_approval_binding(store):
    p=package(); rid=approve(store,p)
    scoped=core(p)
    assert p.group.key!=scoped.group.key and p.package_hash!=scoped.package_hash
    with pytest.raises(ValueError):store.require_approval(scoped,rid,policy=load_layout_policy(),as_of=T)
    own=approve(store,scoped)
    assert store.require_approval(scoped,own,policy=load_layout_policy(),as_of=T)
    altered=scoped.model_copy(update={'group':scoped.group.model_copy(update={'scope_version':'changed'})})
    with pytest.raises(ValueError):store.require_approval(altered,own,policy=load_layout_policy(),as_of=T)

def test_legacy_hash_preserved():
    p=package(); legacy=p.model_dump(mode='json')
    legacy.pop('estimate_state_evidence')
    for cell in legacy['candidates']:cell.pop('comparison_label')
    for field in ('scope_id','scope_version','entity_ids'):legacy['group'].pop(field)
    legacy['candidates']=sorted(legacy['candidates'],key=lambda c:(c['entity_id'],c['column'],digest(c)))
    assert p.package_hash==digest(legacy)

def test_scoped_publication_retains_scope(store,monkeypatch):
    monkeypatch.setattr('ats.data.pipelines.factset_groups.source_mode',lambda _:'platform')
    p,artifact=prepare(store); p=core(p); rid=approve(store,p)
    result=FactSetGroupPipeline(store.repository,clock=lambda:T).run(p,artifact_id=artifact,document_id='doc',review_id=rid)
    assert result['passed'] and len(result['observation_ids'])==1
    assert result['quality']['priority']=='P0'
    assert result['quality']['group']['entity_ids']==['GICS_45']
    from ats.data.products.base import DataProducts
    products=DataProducts(structured_repository=store.repository)
    args=dict(version_id=p.document_version,report_date=p.report_date,as_of=T)
    assert products.earnings_insight_groups(**args,expected_groups=[p.group])['published_groups']==1
    assert products.earnings_insight_groups(**args,expected_groups=[package().group])['published_groups']==0

def test_registry_rejects_core_scope_downgrade(tmp_path):
    import yaml
    from ats.config import _config_dir
    raw=yaml.safe_load((_config_dir()/'data/structured.yaml').read_text())
    raw['datasets']['sp500_earnings_insight']['extraction_policy']['scope_policy']['scopes']['technology']['priority']='P2'
    path=tmp_path/'invalid.yaml'; path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError,match='core_scope_invalid'):load_layout_policy(path)

@pytest.mark.parametrize('priority',['P1','P2','invalid'])
def test_noncore_dedicated_model_call_stops_before_network(priority):
    with pytest.raises(ValueError,match='deferred_for_priority'):
        FactSetVision(load_layout_policy()['vision'],api_key='unused',priority=priority)
