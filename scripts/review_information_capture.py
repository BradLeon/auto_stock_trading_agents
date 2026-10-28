"""Review isolated capture against exact admitted source versions; no model calls."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from ats.agents.information import assemble, extract
from ats.agents.information.briefs import assert_no_advice
from ats.agents.information.outputs import InformationInsightBatchView
from ats.data.products.unstructured import admitted_documents
from ats.data.stores.unstructured import PlatformUnstructuredRepository
from review_information_selection import accepted
from run_information_capture import preflight


def matches_visible_input(frozen: str, current: str, body: str) -> bool:
    """Compare exact input without interpreting separator lines in source text."""
    header = current.split('---\n', 1)[0] + '---\n'
    return frozen.startswith(header + body + '\n---')


def review(prepared: Path, capture_file: Path, database: Path, output: Path, *, write_markdown: bool = True):
    # Historical capture must retain its frozen schema and exact input even when
    # today's extraction contract changes. Execution preflight remains strict.
    manifest, payloads, budget = preflight(prepared, require_current_schema=False)
    captured = json.loads(capture_file.read_text())
    if captured['budget']['manifest_sha256'] != budget['manifest_sha256']:
        raise ValueError('capture belongs to a different reviewed manifest')
    point = datetime.fromisoformat(manifest['as_of'])
    from datetime import timedelta
    repo = PlatformUnstructuredRepository(database, writable=False)
    try:
        documents = admitted_documents(as_of=point, repository=repo, limit=1000)
    finally:
        repo.close()
    since = datetime.fromisoformat(manifest['since']) if manifest.get('since') else point-timedelta(days=7)
    arts, _ = accepted(documents, since, point)
    by_version = {art.document_version_id: art for art in arts}
    results = []
    index = {(r['target'],r['version_id'],r['payload_sha256']): r for r in captured['items']}
    for item, payload in zip(manifest['items'], payloads):
        key = (item['target'], item['version_id'], item['payload_sha256'])
        result = {**item, 'status': 'not_captured', 'insights': []}
        record = index.get(key)
        if record is not None:
            result['status'] = record['status']
            if record['status'] == 'schema_valid_not_content_signed':
                result['model_output_unaccepted'] = record['insights']
                result['empty_reason'] = record.get('empty_reason', '') or json.loads(record.get('raw_tool_arguments') or '{}').get('empty_reason', '')
                try:
                    art = by_version[item['version_id']]
                    if art.publication_id != item['publication_id']:
                        raise ValueError('capture publication differs from snapshot')
                    direct_card = f"- {item['target']} (target)"
                    if payload['user'].startswith('Universe (targets and their signal-chain members):\n' + direct_card + '\n\n'):
                        card, mapping = direct_card, {item['target']: [item['target']]}
                    else:
                        card, mapping = assemble.signal_chain_universe([item['target']])
                    span_chars = int(manifest.get('evidence_span_chars', 360))
                    visible, prompt, schema = extract.extraction_input(art, card, 40000, assess_materiality=True, span_chars=span_chars)
                    # Verify immutable input bytes and the frozen universe prefix;
                    # trailing instructions may have legitimately changed later.
                    # News bodies can themselves contain separator lines. Compare
                    # the complete known prefix instead of splitting inside text.
                    frozen_fields = payload['structured_output_schema'].get('$defs', {}).get('InformationInsightView', {}).get('properties', {})
                    frozen_body = visible.text[:40000]
                    if 'evidence_span_id' in frozen_fields:
                        frozen_body = '\n'.join(f'[{key}] {frozen_body[start:end]}'
                            for key, (start, end) in extract.evidence_spans(frozen_body, span_chars=span_chars).items())
                    if not matches_visible_input(payload['user'], prompt, frozen_body):
                        raise ValueError('captured input differs from snapshot or current universe')
                    if 'association_type' in frozen_fields:
                        batch = InformationInsightBatchView(insights=record['insights'])
                    else:
                        # Preserve historical evidence under its frozen response
                        # shape, never invent today's source facts or relation paths.
                        from types import SimpleNamespace
                        batch = SimpleNamespace(insights=[SimpleNamespace(**i) for i in record['insights']])
                    insights = extract.validate_extraction(art, visible, batch, set(mapping),
                                                          40000, assess_materiality=True, universe_card=card, span_chars=span_chars)
                    assert_no_advice([text for i in insights for text in (i.summary, i.source_fact)])
                    result['insights'] = [i.model_dump(mode='json') for i in insights]
                    result['non_universe_insights_dropped'] = len(batch.insights)-len(insights)
                    result['status'] = 'evidence_valid_pending_human_content_review'
                except Exception as exc:
                    result.update(status='content_contract_failed', error_type=type(exc).__name__,
                                  contract_error=str(exc))
        results.append(result)
    output.mkdir(parents=True, exist_ok=True)
    report = {'evidence_kind':'isolated_content_review_not_workflow_qualification',
        'manifest_sha256':budget['manifest_sha256'], 'model_calls':0, 'production_writes':0,
        'all_evidence_valid':all(r['status']=='evidence_valid_pending_human_content_review' for r in results),
        'human_content_signed':False, 'items':results}
    (output/'content_review.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    lines=['# Information 分标的内容审阅', '', '这是有限材料取证，不是 workflow 资格或人工核签完成。',
           '结论来自模型，证据定位由程序复查；原文引用保留来源语言。', '']
    for target in dict.fromkeys(r['target'] for r in results):
        lines += [f'## {target}', '']
        for row in (r for r in results if r['target']==target):
            lines += [f"### {row['title']}", '',
                f"来源：{row['source']}；完整性：{row['completeness']}；检查状态：{row['status']}。", '',
                f"文档版本：`{row['version_id']}`；publication：`{row['publication_id']}`。", '']
            if row['status']=='evidence_valid_pending_human_content_review' and not row['insights']:
                lines += ['本份可见材料未抽取到 universe 内的事实变化；不代表全市场没有重要消息。', '']
            if row.get('non_universe_insights_dropped'):
                lines += [f"模型返回 {row['non_universe_insights_dropped']} 条 universe 外结论，已排除，需人工评判抽取质量。", '']
            if row['status']=='content_contract_failed':
                lines += ['未通过内容契约：'+row.get('contract_error',row.get('error_type','')), '',
                          '以下仅保留未准入的模型结论供诊断，不构成简报事实：', '']
                for item in row.get('model_output_unaccepted',[]):
                    lines += [f"- {item['ticker']}：{item['summary'] or '（模型未提供事实摘要）'}；材料性 {item['materiality']}、置信度 {item['confidence']}。", '']
            for insight in row['insights']:
                lines += [f"- {insight['ticker']}：{insight['summary']}", '',
                    f"  来源陈述：{insight.get('source_fact', '')}；类型：{insight.get('source_kind', '')}；归属：{insight.get('source_actor', '')}。", '',
                    f"  模型关联上下文（非准入证明）：{' → '.join(insight.get('relation_path', [])) or '未提供'}。", '',
                    f"  材料性：{insight['materiality']}；置信度：{insight['confidence']}；影响路径：{insight['impact_path']}。", '',
                    f"  原文证据：{insight['evidence_quote']}", '',
                    f"  原文定位：`{json.dumps(insight['evidence_locator'],ensure_ascii=False)}`。", '']
            if row['completeness']!='full':
                lines += ['本份仅核读 partial 可见范围，未取得隐藏内容。', '']
    if write_markdown:
        (output/'content_review.md').write_text('\n'.join(lines))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('prepared-dir','capture-file','database','output-dir'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    result=review(args.prepared_dir,args.capture_file,args.database,args.output_dir)
    print(json.dumps({'all_evidence_valid':result['all_evidence_valid'],'items':len(result['items'])}))


if __name__=='__main__':
    main()
