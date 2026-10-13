"""Read-only, same-as_of selection rehearsal; no models, providers or qualification bypass.

This compares public Data Products from two database snapshots. It does not run a
production business invocation or grant publisher scope/deployment authorization.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from ats.config import load_pead_global
from ats.agents.information import assemble, selection
from ats.data.articles.material_quality import completeness_assessment, interpretation_fingerprint, visible_analysis_view
from ats.data.products.unstructured import admitted_documents
from ats.data.stores.unstructured import PlatformUnstructuredRepository
from ats.schemas.research import Article


def accepted(documents, since, point):
    out, gaps = [], []
    for doc in documents:
        visibility = completeness_assessment(doc.text, stored=doc.completeness) if doc.source == 'semianalysis' else {}
        completeness = visibility.get('completeness', doc.completeness)
        try:
            stamp = datetime.fromisoformat(doc.published_at.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=point.tzinfo)
        except ValueError:
            gaps.append({'document_id': doc.document_id, 'reason': 'unknown_publication_time'})
            continue
        if not since <= stamp <= point:
            continue
        if completeness not in {'full', 'partial'} or (completeness == 'partial' and doc.source != 'semianalysis'):
            gaps.append({'document_id': doc.document_id, 'reason': 'unusable_completeness', 'completeness': completeness})
            continue
        out.append(Article(id=doc.document_id, document_version_id=doc.version_id,
            publication_id=doc.publication_id, title=doc.title or doc.document_id, source=doc.source,
            body=doc.text, published_at=stamp, completeness=completeness,
            related_entities=list(doc.related_entities), quality=doc.quality,
            source_fields={key: str(value) for key, value in (doc.quality.get('provenance') or {}).items()},
            interpretation_key=interpretation_fingerprint(version_id=doc.version_id, title=doc.title,
                published_at=doc.published_at, completeness=completeness,
                quality={**doc.quality, 'visibility': visibility})))
    return out, gaps


def record(art, *, reason):
    view = visible_analysis_view(art.body, title=art.title, source=art.source)
    return {'document_id': art.id, 'version_id': art.document_version_id, 'publication_id': art.publication_id,
        'title': art.title, 'source': art.source, 'published_at': art.published_at.isoformat(),
        'completeness': art.completeness, 'selection_reason': reason,
        'visible_chars': len(view.text), 'excerpt': view.text[:350], 'excerpt_kind': 'original_visible_prefix_not_model_summary'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-database', type=Path, required=True)
    parser.add_argument('--after-database', type=Path, required=True)
    parser.add_argument('--as-of', type=datetime.fromisoformat, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--symbols', nargs='+', default=['MRVL', 'MSFT', 'NVDA', 'TSM'])
    args = parser.parse_args()
    if args.as_of.tzinfo is None:
        parser.error('as_of must be timezone-aware')
    before = PlatformUnstructuredRepository(args.before_database, writable=False)
    after = PlatformUnstructuredRepository(args.after_database, writable=False)
    g = load_pead_global()
    config = g['research']
    publishers = config['publisher_entities']
    since = args.as_of - timedelta(days=int(g.get('monitor', {}).get('lookback_days', 7)))
    report = {'as_of': args.as_of.isoformat(), 'since': since.isoformat(), 'model_calls': 0,
              'production_writes': 0, 'evidence_kind': 'offline_readonly_selection_rehearsal_not_business_qualification', 'targets': {}}
    try:
        for symbol in args.symbols:
            symbol = symbol.upper()
            card, mapping = f'- {symbol} (target)', {symbol: [symbol]}
            universe = set(mapping)
            old_raw = admitted_documents(entities=sorted(universe), as_of=args.as_of, repository=before, limit=60)
            # Baseline selected the first 25 current-window admitted documents,
            # without new literal ranking or publisher exploration.
            old = []
            for doc in old_raw:
                try:
                    stamp = datetime.fromisoformat(doc.published_at.replace('Z', '+00:00'))
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=args.as_of.tzinfo)
                except ValueError:
                    continue
                if since <= stamp <= args.as_of:
                    old.append({'document_id': doc.document_id, 'version_id': doc.version_id,
                        'title': doc.title, 'source': doc.source, 'published_at': doc.published_at,
                        'stored_completeness': doc.completeness})
            old = old[:25]
            channels = {'target_and_peers': admitted_documents(entities=sorted(universe),
                as_of=args.as_of, repository=after, limit=config['candidate_limit'])}
            for publisher in publishers:
                channels[publisher] = admitted_documents(entities=[publisher], as_of=args.as_of,
                    repository=after, limit=config['candidate_limit'],
                    document_types=('article','research','research_article','news','newsletter'))
            materials, gaps = [], []
            for name, docs in channels.items():
                arts, rejected = accepted(docs, since, args.as_of)
                materials.extend(art for art in arts if name == 'target_and_peers' or art.source == selection.PUBLISHERS[name])
                gaps.extend(rejected)
            candidates, empty = selection.rank_articles(materials, symbol=symbol, universe=universe, publishers=publishers)
            chosen = selection.choose(candidates, max_articles=config['max_articles_per_run'],
                exploration_articles=config['exploration_articles'], cached_versions=set())
            report['targets'][symbol] = {'universe': sorted(universe), 'before_selected': old,
                'after_candidates': len(candidates), 'after_selected': [record(row.article, reason=row.reason) for row in chosen],
                'deferred': len(candidates)-len(chosen), 'empty_visible_views': empty,
                'gaps': list({(gap['document_id'], gap['reason']): gap for gap in gaps}.values()),
                'raw_channel_counts': {name: len(docs) for name, docs in channels.items()}}
    finally:
        before.close()
        after.close()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir/'selection_review.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    lines = ['# Information 选择规则离线对照', '', f"统一 as_of：{report['as_of']}；窗口起点：{report['since']}。",
        '', '这是无模型、无采集、无生产写入的规则复演；不是 Information 最终采纳清单或内容签核。',
        'after 使用隔离元数据修订，before 使用生产快照的旧元数据。以下摘录为原文可见开头，并非中文模型摘要。', '']
    for symbol, result in report['targets'].items():
        lines += [f'## {symbol}', '', f"旧规则选 {len(result['before_selected'])} 篇；新规则候选 {result['after_candidates']} 篇，选 {len(result['after_selected'])} 篇，延期 {result['deferred']} 篇。", '']
        for index, row in enumerate(result['after_selected'], 1):
            lines += [f"{index}. **{row['title']}**", '', f"来源：{row['source']}；发布日期：{row['published_at']}；完整性：{row['completeness']}；选择依据：{row['selection_reason']}。",
                '', '原文摘录：'+row['excerpt'].replace('\n', ' '), '', f"版本：`{row['version_id']}`。", '']
        lines += [f"缺口：{len(result['gaps'])} 项（详见 JSON；未知发布日期仍排除）。", '']
    (args.output_dir/'selection_review.md').write_text('\n'.join(lines))
    print(json.dumps({symbol: {'before': len(row['before_selected']), 'candidates': row['after_candidates'],
        'selected': len(row['after_selected']), 'deferred': row['deferred']} for symbol, row in report['targets'].items()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
