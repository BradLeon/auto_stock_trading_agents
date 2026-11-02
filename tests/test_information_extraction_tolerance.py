"""One unusable article must not take the whole target's brief with it (2026-11-02).

A model answer that is not usable content (missing field, evidence that does not resolve) skips that article and
the brief says so; a service that did not answer fails the target and is retried later; an article that keeps
failing is quarantined after three tries.
"""
from datetime import UTC, timedelta
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel
from test_information_target_selection import CONFIG, NOW, article, bound

from ats.agents.information import assemble, extract, target_research
from ats.agents.information.outputs import InformationEvidenceBatchView, InformationEvidenceRecapView

BAD = 'BADMARK '
SINCE = NOW - timedelta(days=7)


class _Needs(BaseModel):
    ticker: str


def _missing_field():
    try:
        _Needs.model_validate({})
    except Exception as exc:  # noqa: BLE001
        return exc


def _ingest(store, key, body):
    from ats.data import document_assets

    doc = document_assets.ingest(entity='SEMIANALYSIS', key=key, doc_type='research_article', text=body,
                                 source='semianalysis', title='Capacity', published_at=NOW.isoformat(),
                                 min_chars=1, now=NOW, store=store)
    version = store.latest_document_version(doc.document_id)['version_id']
    return article(doc.document_id, body=body).model_copy(update={'document_version_id': version})


def _world(monkeypatch, *, bad_only=False, fail_with=None):
    from ats.memory import get_store

    store = get_store()
    good = _ingest(store, 'tolerance-good', 'Microsoft increased capacity by 10%. Nvidia supplied GPUs. ' * 30)
    bad = _ingest(store, 'tolerance-bad', BAD + 'Microsoft raised capacity by 12%. Nvidia shipped GPUs. ' * 30)
    bound(monkeypatch)
    monkeypatch.setattr(assemble, 'admitted_articles', lambda store, **kw: [bad] if bad_only else [good, bad])
    monkeypatch.setattr(target_research, 'evaluation_now', lambda tz: NOW)
    monkeypatch.setattr(extract, 'evaluation_now', lambda tz: NOW)
    calls = []

    def model(role, schema, context, **kwargs):
        calls.append(BAD in context)
        if BAD in context:
            raise fail_with() if fail_with else _missing_field()
        if schema is InformationEvidenceBatchView:
            return schema(evidence=[dict(core={'first_chunk': 1, 'last_chunk': 1}, source_kind='reported_fact',
                source_actor='SemiAnalysis', target_role='subject', information_topic='capacity',
                information_quality='substantive', materiality=.2)])
        return InformationEvidenceRecapView(summary='来源报道产能上升10%。', source_fact='产能上升10%。',
            source_kind='reported_fact', source_actor='SemiAnalysis', association_type='direct_update',
            association_reason='微软产能更新', information_topic='capacity', information_quality='substantive',
            confidence=.95, materiality=.2)

    monkeypatch.setattr(extract, 'run_structured', model)
    return store, bad, calls


def run(store):
    return target_research.run(store, 'MSFT', config=CONFIG, since=SINCE, use_llm=True)


def test_an_unusable_article_is_skipped_and_the_rest_are_published_with_the_gap_disclosed(monkeypatch):
    store, bad, calls = _world(monkeypatch)
    report = run(store)
    assert report['status'] == 'published_with_gaps'
    payload = store.get_task_projection(report['projection_id'])['payload']
    assert payload['fact_changes'] == ['来源信息（SemiAnalysis）：来源报道产能上升10%。']
    assert payload['extraction_gaps'] == [{'document_ref': bad.document_version_id, 'article_id': bad.id,
                                           'status': 'retrying', 'attempts': 1,
                                           'reason': payload['extraction_gaps'][0]['reason']}]
    assert any('抽取失败，将在下次运行重试' in note for note in payload['unverified'])


def test_the_skipped_article_is_retried_and_quarantined_after_three_unusable_answers(monkeypatch):
    store, bad, calls = _world(monkeypatch)
    for attempt in (1, 2, 3):
        report = run(store)
        gap = store.get_task_projection(report['projection_id'])['payload']['extraction_gaps'][0]
        assert gap['attempts'] == attempt
        assert gap['status'] == ('quarantined' if attempt == 3 else 'retrying')
    seen = calls.count(True)
    assert seen == 3
    fourth = run(store)                         # quarantined: no model call for it, brief still says so
    assert calls.count(True) == seen
    payload = store.get_task_projection(fourth['projection_id'])['payload']
    assert payload['extraction_gaps'][0]['status'] == 'quarantined'
    assert any('已隔离、不再重试' in note for note in payload['unverified'])
    assert not any('将在下次运行重试' in note for note in payload['unverified'])


def test_when_nothing_could_be_read_the_run_fails_until_the_article_is_quarantined(monkeypatch):
    store, bad, calls = _world(monkeypatch, bad_only=True)
    for _ in range(2):
        with pytest.raises(extract.ArticleExtractionError):
            run(store)
    assert store.task_projection_envelopes(agent_role='information_brief') == []
    third = run(store)                           # third unusable answer: quarantined, so the brief states exactly that
    payload = store.get_task_projection(third['projection_id'])['payload']
    assert third['status'] == 'published_with_gaps'
    assert payload['extraction_gaps'][0]['status'] == 'quarantined'
    assert '不断言该标的没有重要消息' in payload['unverified'][0]


def test_a_service_that_does_not_answer_fails_the_target_and_never_counts_against_the_article(monkeypatch):
    def unreachable():
        return openai.APIConnectionError(request=httpx.Request('POST', 'https://openrouter.ai/api/v1'))

    store, bad, calls = _world(monkeypatch, fail_with=unreachable)
    for _ in range(4):
        with pytest.raises(extract.TransientExtractionError):
            run(store)
    assert store.task_projection_envelopes(agent_role='information_brief') == []
    # Still four real attempts: a connection error is never quarantined.
    assert calls.count(True) == 4


def test_failure_classes():
    assert extract.classify_extraction_failure(
        openai.APIConnectionError(request=httpx.Request('POST', 'https://x'))) == 'transient'
    assert extract.classify_extraction_failure(_missing_field()) == 'content'
    assert extract.classify_extraction_failure(RuntimeError('structured output empty for role')) == 'content'
    assert extract.classify_extraction_failure(RuntimeError('boom')) == 'unknown'
    wrapped = RuntimeError('outer')
    wrapped.__cause__ = TimeoutError('slow')
    assert extract.classify_extraction_failure(wrapped) == 'transient'


# --- the Dispatcher: a brief that still has a retryable gap is not a final answer for its data vintage -----------
class _Memory:
    def __init__(self, rows):
        self.rows = rows
        self.conn = SimpleNamespace(execute=lambda sql, args: SimpleNamespace(fetchone=lambda: {
            'status': 'succeeded', 'projection_refs_json': __import__('json').dumps(
                [r['projection_id'] for r in rows])}))

    def task_projection_envelopes(self, **kwargs):
        return self.rows


def _brief_row(gaps):
    from ats.agent.task_projection import ProjectionScope, build_envelope
    from ats.workflow.evaluation_clock import now

    stamp = now(UTC).isoformat(timespec='seconds')
    payload = {'entity': 'MSFT', 'headline': 'h', 'summary': 's', 'relevance': 'low', 'sources': ['x']}
    if gaps:
        payload['extraction_gaps'] = gaps
    env = build_envelope(role='information_brief', scope=ProjectionScope(kind='entity', id='MSFT'), as_of=stamp,
                         payload=payload, input_refs=['i'], data_vintage_refs=['documents:MSFT:2@a'],
                         agent_run_id='A', created_at=stamp)
    row = env.model_dump(mode='json')
    row.update(scope_kind='entity', scope_id='MSFT')
    return row, stamp


def _select(gaps):
    from ats.workflow.dispatcher import Dispatcher

    row, stamp = _brief_row(gaps)
    task = SimpleNamespace(task_id='information-brief', scope=SimpleNamespace(key='entity:MSFT', kind='entity', id='MSFT'))
    return Dispatcher()._select_reusable(_Memory([row]), task, ['i'], ['documents:MSFT:2@a'], stamp)[1]


def test_a_brief_with_a_retryable_gap_is_recomputed_but_a_quarantined_one_is_final():
    gap = {'document_ref': 'd@v', 'article_id': 'd', 'attempts': 1, 'reason': 'r'}
    assert _select([])['decision'] == 'hit'
    retry = _select([{**gap, 'status': 'retrying'}])
    assert retry['decision'] == 'recompute' and retry['reason'] == 'extraction_incomplete'
    assert _select([{**gap, 'status': 'quarantined'}])['decision'] == 'hit'


def test_the_information_task_retries_a_silent_model_service_after_minutes_not_one_second():
    from ats.workflow.planning import task_registry

    retry = task_registry().spec('information-brief').retry
    assert retry.backoff_seconds >= 60 and 'transient' in retry.retry_on
    assert task_registry().spec('technical-review').retry.backoff_seconds == 1.0


def test_the_adapter_turns_a_silent_service_into_a_retryable_error(monkeypatch):
    from ats.workflow import dispatcher as d

    def silent(*args, **kwargs):
        raise extract.TransientExtractionError('Information extraction failed for A')

    monkeypatch.setattr('ats.agents.information.entry.run_information_target', silent)
    context = SimpleNamespace(task=SimpleNamespace(scope=SimpleNamespace(id='MSFT')), store=None,
                              task_inputs={}, run_once=None)
    with pytest.raises(d.RetryableTaskError) as caught:
        d._information_adapter(context)
    assert caught.value.code == 'transient'
