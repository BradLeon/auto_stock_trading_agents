"""Existing Information extraction, own-role cache, and a target-scoped brief."""
from __future__ import annotations

import time
from bisect import bisect_left
from datetime import timezone

from ...schemas.research import Insight
from ...data.document_quality import visible_analysis_view
from ...workflow.evaluation_clock import now as evaluation_now
from ...agent.task_projection import ProjectionScope
from . import extract, selection, triage
from .briefs import assert_no_advice, clocks_for, publish_information_brief


def run(store, symbol: str, *, config: dict, since, use_llm: bool) -> dict:
    started = time.monotonic()
    rows, report = selection.candidates(store, symbol, since=since, config=config)
    card, universe = report['universe_card'], set(report['universe'])
    processors, caches = {}, {}
    for row in rows:
        art = row.article
        processor = extract.processing_version(art, {**config, 'universe_card': card}) + ':target-materiality-v1'
        processors[art.document_version_id] = processor
        cache = store.information_extraction_cache(input_ref=art.document_version_id,
            processor=processor, target=symbol, as_of=evaluation_now(timezone.utc))
        if cache is not None:
            caches[art.document_version_id] = cache
    count_cap = config['max_articles_per_run']
    selected = selection.choose(rows, max_articles=None if count_cap is None else int(count_cap),
        exploration_articles=0, cached_versions=set(caches))
    total_cap = int(config.get('total_article_chars', (count_cap or config.get('candidate_limit', 120)) * config['article_chars']))
    dispatch_seconds = float(config.get('dispatch_seconds', 180))
    if total_cap < 1 or dispatch_seconds <= 0 or int(config['article_chars']) < 1:
        raise ValueError('Information character/time budget must be positive')
    report.update(model_calls=0, model_invocations=0, input_chars=0, cache_hits=0, items=[],
                  deferred=len(rows)-len(selected), projection_id='', materiality=None)
    extracted, inspected = [], []
    for row in selected:
        art = row.article
        version = art.document_version_id
        processor = processors[version]
        item = {'document_id': art.id, 'version_id': version, 'publication_id': art.publication_id,
                'title': art.title, 'source': art.source, 'completeness': art.completeness,
                'selection_reason': row.reason}
        report['items'].append(item)
        view = visible_analysis_view(art.body, title=art.title, source=art.source)
        max_chars = min(int(config['article_chars']), len(view.text))
        if not use_llm:
            item['status'] = 'selected_no_model'
            continue
        cache = caches.get(version)
        if cache is not None:
            item['empty_reason'] = cache['payload'].get('empty_reason', '')
            insights = [Insight.model_validate(data) for data in cache['payload']['insights']]
            # Re-admission and fingerprint are necessary but not sufficient: ensure
            # even stored conclusions still resolve to this exact visible input.
            for insight in insights:
                if insight.article_id != art.id or insight.ticker not in universe:
                    raise ValueError('Information cache identity no longer resolves')
                for part in [{'quote':insight.evidence_quote,'locator':insight.evidence_locator}, *insight.evidence_context]:
                    locator, quote = part.get('locator', {}), part.get('quote', '')
                    position = (bisect_left(view.offsets, locator['start'])
                                if isinstance(locator.get('start'), int) else -1)
                    if not quote or position < 0 or view.text[:int(config['article_chars'])][position:position+len(quote)] != quote or view.locator(position, position+len(quote)) != locator:
                        raise ValueError('Information cache evidence no longer resolves')
            report['cache_hits'] += 1
            item['status'] = 'cache_hit'
        else:
            if report['input_chars'] + max_chars > total_cap or time.monotonic()-started >= dispatch_seconds:
                item['status'] = 'deferred_budget'
                report['deferred'] += 1
                continue
            claim = store.begin_information_processing(art.id, symbol, processor,
                version_id=version, retry_failed=True)
            if not claim:
                item['status'] = 'processing_busy_or_cache_missing'
                report['deferred'] += 1
                continue
            report['model_calls'] += 1
            report['input_chars'] += max_chars
            try:
                insights = extract._extract(art, card, universe, int(config['article_chars']),
                    assess_materiality=True, target=symbol, diagnostics=item,
                    deadline=started + dispatch_seconds,
                    evidence_first=bool(config.get('evidence_first', False)))
                report['model_invocations'] += item.get('model_invocations', 0)
                assert_no_advice([text for insight in insights for text in (insight.summary, insight.source_fact)],
                                 source_statement=True)
                cache_id = store.save_task_projection(profile='information_extraction',
                    profile_version=processor, input_kind='document_version', input_ref=version,
                    target_type='entity', target_id=symbol,
                    payload={'insights': [insight.model_dump(mode='json') for insight in insights],
                             'empty_reason': item.get('empty_reason', '')},
                    created_at=evaluation_now(timezone.utc).isoformat())
                cache = {'projection_id': cache_id}
            except Exception:
                store.finish_information_processing(version, symbol, processor, ok=False,
                    note='target extraction/cache publication failed')
                raise
            store.finish_information_processing(version, symbol, processor, ok=True, outputs=len(insights))
            item['status'] = 'extracted' if insights else 'no_relevant_insight'
        # An explicitly empty result is cached too; unrelated articles never
        # become target facts merely because their publisher was authorized.
        item['insights'] = len(insights)
        inspected.append((art, cache['projection_id'], max_chars))
        item['reading_buckets'] = [triage.reading_bucket(insight, symbol) for insight in insights]
        adopted = [insight for insight in insights if triage.reading_bucket(insight, symbol) == 'adopted']
        item['adopted_insights'] = len(adopted)
        extracted.extend((art, insight, cache['projection_id']) for insight in adopted)
    if extracted:
        report['materiality'] = max((i.materiality or 0) for _, i, _ in extracted)
        report['projection_id'] = _publish(store, symbol, extracted, deferred=report['deferred'])
    elif inspected:
        report['materiality'] = 0.0 if not report['deferred'] else None
        report['projection_id'] = _publish_empty(store, symbol, inspected, deferred=report['deferred'])
    report['elapsed_seconds'] = round(time.monotonic()-started, 3)
    report['status'] = ('selected_no_model' if not use_llm else
                        'published_with_deferred' if extracted and report['deferred'] else 'published' if extracted else
                        'pending' if report['deferred'] else 'no_relevant_change')
    report.pop('universe_card')
    return report


def _publish_empty(store, symbol, inspected, *, deferred):
    """A bounded no-change observation, never invented issuer facts or coverage."""
    now = evaluation_now(timezone.utc)
    evidence = []
    for art, cache_id, chars in inspected:
        view = visible_analysis_view(art.body, title=art.title, source=art.source)
        evidence.append({'fact_index': 0, 'document_ref': art.document_version_id,
            'publication_id': art.publication_id, 'locator': view.locator(0, chars),
            'statement_type': 'bounded_inspection_result', 'completeness': art.completeness,
            'extraction_projection_id': cache_id, 'confidence': 0.0,
            **clocks_for({'published_at': art.published_at}, extracted_at=now),
            'event_time_basis': 'publication_proxy_not_independently_verified'})
    observation = f'本次已核读 {len(inspected)} 篇选定材料的可见范围，未选出进入标的主简报的信息。'
    payload = {'entity': symbol, 'headline': f'{symbol} 本次材料无可提取变化', 'summary': observation,
        'fact_changes': [observation], 'source_evidence': evidence, 'relevance': 'low',
        'sources': sorted({art.source for art, _, _ in inspected}), 'entities': [symbol],
        'impact_candidates': [], 'confidence': 0.0,
        'freshness': f'published {max(art.published_at for art, _, _ in inspected).date()}',
        'unverified': ['仅表达主简报筛选结果；背景/薄弱信息保留在抽取缓存，不断言市场没有重要消息；投资影响留待下游'] +
            ([f'{deferred} 篇候选延期，未覆盖其内容'] if deferred else []) +
            (['包含 partial 预览，未取得隐藏内容'] if any(a.completeness != 'full' for a, _, _ in inspected) else []),
        'event_time': evidence[0]['event_time'], 'published_at': evidence[0]['published_at'],
        'extracted_at': now.isoformat(), 'source_count': len(inspected)}
    from ..base import execution_versions
    return publish_information_brief(store, payload, scope=ProjectionScope(kind='entity', id=symbol),
        as_of=now.isoformat(), input_refs=sorted({ref for art, _, _ in inspected
            for ref in (art.document_version_id, art.publication_id) if ref}),
        **execution_versions('research_extract', 'research-insight', template_source=__file__))


def _publish(store, symbol, extracted, *, deferred=0):
    # Equal facts/quotes from syndicated materials do not become extra facts.
    unique, update_versions = {}, {}
    for art, insight, cache_id in extracted:
        update_key = selection.target_update_fingerprint(art, symbol)
        if update_key is not None:
            representative = update_versions.setdefault(update_key, art.document_version_id or art.id)
            if representative != (art.document_version_id or art.id):
                continue
        key = (insight.ticker, ' '.join(insight.evidence_quote.casefold().split()),
               ' '.join(insight.summary.casefold().split()))
        unique.setdefault(key, (art, insight, cache_id))
    rows = list(unique.values())
    now = evaluation_now(timezone.utc)
    changes, evidence, fact_indices = [], [], {}
    for art, insight, cache_id in rows:
        clocks = clocks_for({'published_at': art.published_at}, extracted_at=now)
        fact = insight.source_fact or insight.summary
        # The model's descriptive category is not a truth/admission verdict.
        attribution = '来源信息'
        statement = (f'{attribution}（{insight.source_actor}）：{fact}'
                     if insight.source_fact else f'{insight.ticker}: {fact}')
        fact_key = (statement, ' '.join(insight.evidence_quote.casefold().split()))
        if fact_key not in fact_indices:
            fact_indices[fact_key] = len(changes)
            changes.append(statement)
        index = fact_indices[fact_key]
        evidence.append({'fact_index': index, 'document_ref': art.document_version_id,
            'entity': insight.ticker,
            'publication_id': art.publication_id, 'locator': insight.evidence_locator,
            'event_time': clocks['event_time'], 'published_at': clocks['published_at'],
            'event_time_basis': 'publication_proxy_not_independently_verified',
            'extracted_at': clocks['extracted_at'], 'confidence': insight.confidence,
            'statement_type': insight.source_kind,
            'source_actor': insight.source_actor,
            'source_summary': fact,
            'evidence_quote': insight.evidence_quote,
            'target_role': insight.target_role,
            'context': insight.evidence_context,
            'input_truncated': insight.input_truncated,
            'visible_input_chars': insight.visible_input_chars,
            'completeness': art.completeness, 'extraction_projection_id': cache_id})
    materiality = max((insight.materiality or 0) for _, insight, _ in rows)
    confidence = min(insight.confidence for _, insight, _ in rows)
    sources = sorted({art.source for art, _, _ in rows})
    # Source count here is attribution, not independent corroboration of one event.
    payload = {'entity': symbol, 'headline': f'{symbol} 信息材料更新', 'summary': '；'.join(changes)[:400],
        'relevance': 'high' if materiality >= .7 else 'medium' if materiality >= .4 else 'low',
        'sources': sources, 'fact_changes': changes, 'source_evidence': evidence,
        'impact_candidates': [], 'entities': [symbol],
        'confidence': confidence, 'freshness': f'published {max(a.published_at for a, _, _ in rows).date()}',
        'unverified': ['来源陈述及标的影响尚需独立核验；多篇材料不等于同事件独立佐证',
                       '事件发生时点未独立核验，event_time 使用原始发布时间代理'] +
                      ([f'{deferred} 篇候选因预算或处理中状态延期，当前简报不声称覆盖全部候选'] if deferred else []) +
                      (['包含 partial 可见预览，未取得隐藏内容'] if any(a.completeness != 'full' for a, _, _ in rows) else []),
        'event_time': evidence[0]['event_time'], 'published_at': evidence[0]['published_at'],
        'extracted_at': now.isoformat(), 'source_count': len({a.id for a, _, _ in rows})}
    from ..base import execution_versions
    return publish_information_brief(store, payload, scope=ProjectionScope(kind='entity', id=symbol),
        as_of=now.isoformat(), input_refs=sorted({ref for art, _, _ in rows
            for ref in (art.document_version_id, art.publication_id) if ref}),
        **execution_versions('research_extract', 'research-insight', template_source=__file__))
