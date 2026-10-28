"""Real safety boundaries for the isolated content experiment; no network."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('information_capture',
    Path(__file__).parents[1]/'scripts/run_information_capture.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


def prepared(tmp_path, count=2):
    tmp_path.mkdir(exist_ok=True)
    payload = {'model': capture.MODEL, 'role': 'research_extract', 'system': 'Research',
        'user': 'Microsoft expanded capacity.',
        'structured_output_schema': capture.InformationInsightBatchView.model_json_schema()}
    raw = json.dumps(payload).encode()
    (tmp_path/'payload.json').write_bytes(raw)
    row = {'payload_file': 'payload.json', 'payload_sha256': hashlib.sha256(raw).hexdigest(),
        'target': 'MSFT', 'title': 'Capacity', 'source': 'semianalysis', 'completeness': 'partial',
        'version_id': 'exact@v1', 'publication_id': 'publication-v1'}
    (tmp_path/'manifest.json').write_text(json.dumps({'destination_host':'openrouter.ai',
        'model':capture.MODEL, 'items':[row]*count}))
    return tmp_path


def test_modified_payload_is_rejected_before_any_transport(tmp_path):
    directory = prepared(tmp_path/'input')
    (directory/'payload.json').write_text('{}')
    with pytest.raises(ValueError, match='hash'):
        capture.preflight(directory)


def test_experiment_failure_stops_without_retry_or_next_article(tmp_path):
    directory = prepared(tmp_path/'input')
    _, _, budget = capture.preflight(directory)
    attempts = []
    class Client:
        def with_options(self, **kwargs):
            assert kwargs['max_retries'] == 0 and kwargs['timeout'] <= 60
            return self
        def create(self, **kwargs):
            attempts.append(kwargs)
            raise TimeoutError('uncertain server result')
    client = Client()
    client.chat = SimpleNamespace(completions=client)
    result = capture.capture(directory, tmp_path/'output',
        approval_digest=budget['manifest_sha256'], client=client)
    assert len(attempts) == result['api_attempts'] == 1
    assert result['stop_reason'] == 'model_or_contract_failure'
    assert result['items'][0]['status'] == 'failed_no_retry'


def test_expired_run_never_starts_an_api_request(tmp_path):
    directory = prepared(tmp_path/'input')
    _, _, budget = capture.preflight(directory)
    times = iter([0, 481, 481])
    result = capture.capture(directory, tmp_path/'output',
        approval_digest=budget['manifest_sha256'], client=None, clock=lambda: next(times))
    assert result['api_attempts'] == 0 and result['stop_reason'] == 'run_deadline'


def test_schema_success_is_not_marked_content_signed(tmp_path):
    directory = prepared(tmp_path/'input', count=1)
    _, _, budget = capture.preflight(directory)
    class Client:
        def with_options(self, **kwargs):
            return self
        def create(self, **kwargs):
            assert kwargs['max_tokens'] == 4096
            assert 'parallel_tool_calls' not in kwargs
            assert kwargs['extra_body']['provider']['require_parameters']
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[
                SimpleNamespace(function=SimpleNamespace(name='InformationInsightBatchView', arguments='{"insights":[]}'))]))], usage=None)
    client=Client();client.chat=SimpleNamespace(completions=client)
    result=capture.capture(directory,tmp_path/'output',approval_digest=budget['manifest_sha256'],client=client)
    assert result['items'][0]['status']=='schema_valid_not_content_signed'
    assert result['production_writes']==0


def test_manual_continuation_skips_failed_payload_and_counts_it_in_total(tmp_path):
    directory=prepared(tmp_path/'input',count=2)
    manifest,_,budget=capture.preflight(directory)
    old=tmp_path/'old.json'
    old.write_text(json.dumps({'budget':budget,'api_attempts':1,'elapsed_seconds':1,
        'items':[{**manifest['items'][0],'status':'failed_no_retry'}]}))
    calls=[]
    class Client:
        def with_options(self,**kw): return self
        def create(self,**kw):
            calls.append(kw)
            raise TimeoutError()
    client=Client();client.chat=SimpleNamespace(completions=client)
    report=capture.capture(directory,tmp_path/'new',approval_digest=budget['manifest_sha256'],
        client=client,previous_file=old)
    assert len(calls)==1 and report['api_attempts']==2
    assert report['items'][0]['status']=='failed_no_retry'


def test_new_manifest_requires_new_approval_and_oversize_is_rejected(tmp_path):
    directory=prepared(tmp_path/'input', count=9)
    with pytest.raises(ValueError,match='request budget'):
        capture.preflight(directory)
    directory=prepared(directory,count=1)
    with pytest.raises(ValueError,match='approved manifest'):
        capture.capture(directory,tmp_path/'output',approval_digest='old',client=None)


def test_historical_schema_can_be_reviewed_but_execution_refuses_it(tmp_path):
    directory=prepared(tmp_path/'input',count=1)
    payload=json.loads((directory/'payload.json').read_text())
    payload['structured_output_schema']['title']='HistoricalContract'
    raw=json.dumps(payload).encode();(directory/'payload.json').write_bytes(raw)
    manifest=json.loads((directory/'manifest.json').read_text())
    manifest['items'][0]['payload_sha256']=hashlib.sha256(raw).hexdigest()
    (directory/'manifest.json').write_text(json.dumps(manifest))
    capture.preflight(directory,require_current_schema=False)
    with pytest.raises(ValueError,match='current extraction contract'):
        capture.preflight(directory)


def test_review_handles_source_separators_without_accepting_body_changes(monkeypatch):
    import sys
    script_dir = Path(__file__).parents[1] / 'scripts'
    monkeypatch.syspath_prepend(str(script_dir))
    from review_information_capture import matches_visible_input

    body = 'Marvell executive sold 1,100 shares.\n---\nTransaction disclosure follows.'
    header = 'Universe (targets and their signal-chain members):\nMRVL\n\nArticle\n---\n'
    current = header + body + '\n---\nCurrent extraction instructions'
    historical = header + body + '\n---\nEarlier extraction instructions'
    assert matches_visible_input(historical, current, body)
    assert not matches_visible_input(historical.replace('1,100', '2,100'), current, body)
    assert not matches_visible_input(historical.replace('MRVL', 'MSFT'), current, body)
