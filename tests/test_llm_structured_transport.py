"""Native structured transport preserves typed outputs and legacy cache identity."""
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_openai import ChatOpenAI
from ats.agents.fundamental.outputs import ClassificationView
from ats.config import LLMConfig, LLMRoleConfig
from ats.llm.gateway import StructuredOutputModel


def test_legacy_role_serialization_preserves_information_cache_identity():
    legacy = LLMRoleConfig(model='deepseek/example').model_dump()
    explicit = LLMRoleConfig(model='deepseek/example',
                             structured_output_method='function_calling').model_dump()
    assert explicit == legacy
    cfg = LLMConfig(default_structured_output_method='json_schema', routing={
        'research_extract': LLMRoleConfig(structured_output_method='function_calling')})
    assert cfg.for_role('research_extract').structured_output_method == 'function_calling'
    assert cfg.for_role('fundamental_analyst').structured_output_method == 'json_schema'
    with pytest.raises(ValueError):
        LLMRoleConfig(structured_output_method='json_mode')


def test_native_json_schema_uses_parse_and_returns_existing_output_type():
    requests = []
    def receive(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json={
            'id':'offline', 'object':'chat.completion', 'created':1, 'model':'offline',
            'choices':[{'index':0,'finish_reason':'stop','message':{
                'role':'assistant','content':json.dumps({'items':[
                    {'index':0,'classification':'待验证','reason':'Not yet reported.'}]})}}],
            'usage':{'prompt_tokens':20,'completion_tokens':20,'total_tokens':40}})
    client = httpx.Client(transport=httpx.MockTransport(receive))
    native = ChatOpenAI(model='offline',api_key='offline',http_client=client,max_retries=0)
    model = StructuredOutputModel(native,'json_schema')
    copied = model.model_copy(update={'max_tokens':4096})
    assert copied.max_tokens == 4096
    result = copied.with_structured_output(ClassificationView, method='function_calling').invoke('Classify.')
    assert isinstance(result,ClassificationView)
    assert result.items[0].classification == '待验证'
    assert len(requests) == 1
    schema = requests[0]['response_format']
    assert schema['type'] == 'json_schema'
    assert schema['json_schema']['strict'] is True
    assert 'tools' not in requests[0] and 'tool_choice' not in requests[0]
    assert schema['json_schema']['schema']['additionalProperties'] is False
    assert set(schema['json_schema']['schema']['required']) == {'items'}
    client.close()
