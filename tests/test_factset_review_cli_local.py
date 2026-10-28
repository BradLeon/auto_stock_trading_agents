import json
import pytest
from ats.data.products.base import DataProducts
from ats.runtime.cli import run_data
from test_factset_reviews_local import store, package, T


def test_review_cli_requires_confirm_and_never_publishes(store, monkeypatch, capsys):
    products=DataProducts(structured_repository=store.repository)
    monkeypatch.setattr('ats.data.products.get_platform_data_products',lambda:products)
    p=package(); store.register(p,at=T)
    review=json.dumps(dict(decision='approve',reviewer='operator',evidence_refs=['pdf:p1'],note='reviewed'))
    with pytest.raises(ValueError,match='confirm'):
        run_data('factset-review',p.package_hash,query_scope=review)
    assert run_data('factset-review',p.package_hash,query_scope=review,confirm=True)==0
    result=json.loads(capsys.readouterr().out)
    assert result['review_id'] and not result['published']
    assert not store.repository.observations(dataset_id='sp500_earnings_insight')
    assert run_data('factset-review-packages')==0
    assert json.loads(capsys.readouterr().out)[0]['package_hash']==p.package_hash


def test_cli_correction_is_not_approval(store,monkeypatch,capsys):
    monkeypatch.setattr('ats.data.products.get_platform_data_products',
        lambda:DataProducts(structured_repository=store.repository))
    p=package(); store.register(p,at=T)
    cell=p.candidates[0]
    evidence=cell.value_evidence[0].model_dump(mode='json')
    evidence.update(raw_token='21',method='manual:operator')
    change=dict(corrections=[dict(entity_id=cell.entity_id,column=cell.column,value='21',evidence=evidence)],
        reviewer='operator',note='source review')
    run_data('factset-review-correct',p.package_hash,confirm=True,query_scope=json.dumps(change))
    result=json.loads(capsys.readouterr().out)
    assert result['package_hash']!=p.package_hash and not result['approved'] and not result['published']
