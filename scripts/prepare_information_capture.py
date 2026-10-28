"""Stage exact content for review; never fetch or send anything to a model."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from ats.config import get_config
from ats.agents.base import load_skill
from ats.agents.information import assemble, extract
from ats.data.products.unstructured import admitted_documents
from ats.data.stores.unstructured import PlatformUnstructuredRepository
from review_information_selection import accepted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection-review', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--max-per-target', type=int, default=2)
    parser.add_argument('--all-selected', action='store_true')
    parser.add_argument('--no-review-markdown', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.max_per_target <= 500:
        parser.error('max-per-target must be 1..500')
    review = json.loads(args.selection_review.read_text())
    point = datetime.fromisoformat(review['as_of'])
    since = datetime.fromisoformat(review['since'])
    repo = PlatformUnstructuredRepository(args.database, writable=False)
    try:
        docs = admitted_documents(as_of=point, repository=repo, limit=1000)
    finally:
        repo.close()
    arts, _ = accepted(docs, since, point)
    by_version = {art.document_version_id: art for art in arts}
    config = get_config()
    role = config.app.llm.for_role('research_extract')
    endpoint = urlparse(config.secrets.openai_base_url or '').hostname if role.provider == 'openai' else None
    system = load_skill('research-insight')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {'status': 'prepared_not_authorized_not_sent', 'destination_host': endpoint,
        'model': role.model, 'role': 'research_extract', 'as_of': point.isoformat(), 'since': since.isoformat(), 'items': [],
        'model_calls': 0, 'production_writes': 0, 'source': str(args.selection_review),
        'max_per_target': None if args.all_selected else args.max_per_target, 'output_tokens_per_invoke': role.max_tokens,
        'evidence_span_chars': extract.EVIDENCE_SPAN_CHARS,
        'sdk_max_retries': config.app.llm.max_retries, 'structured_output_attempts': 4,
        'timeout_per_sdk_attempt_seconds': config.app.llm.timeout_seconds}
    for target_index, (symbol, target) in enumerate(review['targets'].items()):
        selected = target['after_selected']
        direct = [row for row in selected if row['selection_reason'] != 'publisher_exploration']
        exploration = [row for row in selected if row['selection_reason'] == 'publisher_exploration']
        # Alternate the reviewed exploration slots so a bounded multi-target
        # experiment covers both publishers rather than repeating the first one.
        exploratory = exploration[target_index % len(exploration)] if exploration else None
        subset = ([direct[0]] if direct else []) + ([exploratory] if exploratory else [])
        subset += [row for row in selected if row not in subset]
        card = f'- {symbol} (target)'
        for index, row in enumerate(subset if args.all_selected else subset[:args.max_per_target], 1):
            art = by_version[row['version_id']]
            if art.publication_id != row['publication_id']:
                raise ValueError('prepared publication differs from reviewed selection')
            view, prompt, schema = extract.extraction_input(art, card, 40000, assess_materiality=True, target=symbol)
            payload = {'model': role.model, 'role': 'research_extract', 'system': system, 'user': prompt,
                       'structured_output_schema': schema.model_json_schema()}
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
            name = f'{symbol.lower()}_{index:02d}_payload.json'
            (args.output_dir/name).write_text(encoded)
            manifest['items'].append({'target': symbol, 'document_id': art.id,
                'version_id': art.document_version_id, 'publication_id': art.publication_id,
                'title': art.title, 'source': art.source, 'completeness': art.completeness,
                'selection_reason': row['selection_reason'], 'payload_file': name,
                'payload_sha256': hashlib.sha256(encoded.encode()).hexdigest(),
                'body_chars': min(len(view.text),40000), 'input_truncated':len(view.text)>40000,
                'system_chars': len(system), 'user_chars':len(prompt)})
    manifest['logical_calls_if_all_items_run'] = len(manifest['items'])
    manifest['raw_api_attempts_theoretical_upper_bound'] = len(manifest['items'])*4*(config.app.llm.max_retries+1)
    manifest['input_token_count'] = None
    manifest['cost_budget'] = None
    manifest['budget_gaps'] = ['provider token accounting and overhead', 'maximum approved cost',
                              'total wall-clock and raw API attempt cap before capture']
    (args.output_dir/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    lines = ['# Information 新材料外发准备清单', '', '**尚未外发，也未取得本轮外发授权。**', '',
        f"拟目的地：{endpoint}；当前 research_extract 模型：{role.model}。", '',
        '拟首轮仅核验下列有限材料，不是全部标的内容验收；JSON payload 含精确 system／user／输出 schema。',
        '发送内容为选定文档可见正文、标题、版本及完整性、对应静态 signal_chain，不含账户、持仓、订单或其他分析师叙事。', '',
        f"逻辑调用 {len(manifest['items'])} 次；原网关与 structured-output 重试合计理论最多 {manifest['raw_api_attempts_theoretical_upper_bound']} 次 API 尝试。",
        '总 token、成本与整体时限尚未登记，不得仅凭此清单启动 capture。原有两模型授权不能自动覆盖这些新增正文。', '']
    for row in manifest['items']:
        lines += [f"- {row['target']}：{row['title']}（{row['source']}，{row['completeness']}，{row['body_chars']} 字符）", '',
                  f"  精确内容：[payload]({row['payload_file']})；SHA256：`{row['payload_sha256']}`。", '']
    if not args.no_review_markdown:
        (args.output_dir/'review.md').write_text('\n'.join(lines))
    print(json.dumps({'prepared_items':len(manifest['items']), 'destination_host':endpoint,
                      'model':role.model, 'model_calls':0, 'output':str(args.output_dir)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
