import json
from io import BytesIO
import httpx
import pytest
from PIL import Image
from ats.data.sources.factset_report_layout import load_layout_policy
from ats.data.sources.factset_vision import FactSetVision,make_crop,compare_numeric

def crop():
    out=BytesIO(); Image.new('RGB',(100,40),'white').save(out,format='PNG')
    return make_crop(out.getvalue(),(.1,.1,.9,.9),'cell-1')

def client(content,finish='stop'):
    def handle(request):
        body=json.loads(request.content)
        assert body['provider']=={'data_collection':'deny','zdr':True}
        assert 'golden' not in str(body['messages'])
        return httpx.Response(200,json={'id':'test','model':'test-model','usage':{},
            'choices':[{'finish_reason':finish,'message':{'content':json.dumps(content)}}]})
    return httpx.Client(transport=httpx.MockTransport(handle))

def test_result_is_bound_and_never_an_approval():
    c=crop(); p=load_layout_policy()['vision']
    result=FactSetVision(p,api_key='test',client=client({'cells':[{'id':'cell-1','token':'7.4%'}]})).transcribe([c])
    assert result['status']=='pending_review' and result['approved'] is False
    compared=compare_numeric(c,'74%',result)
    assert compared['reason']=='ocr_vision_conflict_or_unreadable'
    assert not compared['approved']
    result['refs'][0]['crop_hash']='wrong'
    with pytest.raises(ValueError,match='binding'):
        compare_numeric(c,'7.4%',result)

@pytest.mark.parametrize('content',[
    {'cells':[]},{'cells':[{'id':'other','token':'1%'}]},
    {'cells':[{'id':'cell-1','token':1}]},
    {'cells':[{'id':'cell-1','token':'1%','confidence':1}]},
    {'cells':[{'id':'cell-1','token':'1%'}]*2},
])
def test_bad_responses_are_not_candidates(content):
    result=FactSetVision(load_layout_policy()['vision'],api_key='test',client=client(content)).transcribe([crop()])
    assert result['status']=='extraction_failed'
    assert not result['approved']

def test_budget_and_disabled_policy_fail_closed():
    p=load_layout_policy()['vision']; p['max_requests']=1
    adapter=FactSetVision(p,api_key='test',client=client({'cells':[]},finish='length'))
    assert adapter.transcribe([crop()])['status']=='extraction_failed'
    with pytest.raises(ValueError,match='budget'):
        adapter.transcribe([crop()])
    p['enabled']=False
    with pytest.raises(ValueError,match='authorized'):
        FactSetVision(p,api_key='test')

@pytest.mark.parametrize('key,value',[
    ('max_requests',0),('max_requests',101),('max_tokens',99999),
    ('max_crops_per_request',13),('zero_data_retention',False),
    ('provider_data_collection','allow'),('automatic_publication',True),
    ('prompt_version','unknown'),('model',''),
])
def test_invalid_policy_fails_before_network(key,value):
    policy=load_layout_policy()['vision']; policy[key]=value
    with pytest.raises(ValueError):
        FactSetVision(policy,api_key='test')

def test_network_failure_does_not_leak_secret():
    def handle(request):
        raise httpx.ConnectError('sensitive-test-key')
    adapter=FactSetVision(load_layout_policy()['vision'],api_key='sensitive-test-key',
        client=httpx.Client(transport=httpx.MockTransport(handle)))
    result=adapter.transcribe([crop()])
    assert result['status']=='extraction_failed'
    assert 'sensitive-test-key' not in json.dumps(result)

def test_request_budget_is_shared_between_threads():
    from concurrent.futures import ThreadPoolExecutor
    p=load_layout_policy()['vision']; p['max_requests']=1
    adapter=FactSetVision(p,api_key='test',client=client({'cells':[{'id':'cell-1','token':'1%'}]}))
    def call():
        try: return adapter.transcribe([crop()])['status']
        except ValueError: return 'budget_exhausted'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _:call(),range(2)))==['budget_exhausted','pending_review']
