"""Bounded, isolated model-content experiment. Dry-run unless explicitly executed.

Consumes reviewed payload files only; never acquires sources, publishes projections,
sends notifications or touches production databases. This is not workflow qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from pydantic import ValidationError

from ats.agents.information.outputs import InformationInsightBatchView

MODEL = 'deepseek/deepseek-v4-flash-0731'
LIMITS = {'requests': 8, 'output_tokens_per_request': 4096, 'seconds_per_request': 60,
          'total_seconds': 480, 'cost_reservation_usd': .50,
          'input_usd_per_million': .10, 'output_usd_per_million': .80,
          'total_user_chars': 160000}


def preflight(directory: Path, *, require_current_schema: bool = True):
    manifest_bytes = (directory/'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest['destination_host'] != 'openrouter.ai' or manifest['model'] != MODEL:
        raise ValueError('experiment destination/model differs from reviewed scope')
    items = manifest['items']
    if not items or len(items) > LIMITS['requests']:
        raise ValueError('request budget exceeded')
    payloads, estimated_input = [], 0
    for row in items:
        name = row['payload_file']
        if Path(name).name != name:
            raise ValueError('payload must be inside the reviewed directory')
        raw = (directory/name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['payload_sha256']:
            raise ValueError('reviewed payload hash differs')
        payload = json.loads(raw)
        if payload['model'] != MODEL or payload['role'] != 'research_extract':
            raise ValueError('payload model/role differs')
        if require_current_schema and payload['structured_output_schema'] != InformationInsightBatchView.model_json_schema():
            raise ValueError('reviewed schema differs from current extraction contract')
        # UTF-8 bytes plus framing reserve is a deliberately conservative planning
        # estimate, not an exact count for the provider's private tokenizer.
        estimated_input += len(raw) + 4096
        payloads.append(payload)
    if sum(len(p['user']) for p in payloads) > LIMITS['total_user_chars']:
        raise ValueError('total prompt character budget exceeded')
    estimate = (estimated_input * LIMITS['input_usd_per_million'] +
        len(items)*LIMITS['output_tokens_per_request']*LIMITS['output_usd_per_million'])/1e6
    if estimate > LIMITS['cost_reservation_usd']:
        raise ValueError('cost reservation exceeded')
    return manifest, payloads, {'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
        'requests': len(items), 'sdk_retries': 0, 'structured_retries': 0,
        'limits': LIMITS, 'planned_input_tokens_conservative_estimate': estimated_input,
        'planned_output_token_cap': len(items)*LIMITS['output_tokens_per_request'],
        'planned_cost_reservation_usd': round(estimate, 6),
        'cost_limit_kind': 'planning reservation plus provider unit-price ceiling; not billing guarantee',
        'wall_clock_kind': 'request deadline and remaining-run timeout; provider cancellation not guaranteed'}


def capture(directory: Path, output: Path, *, approval_digest: str, client, clock=time.monotonic,
            previous_file: Path | None = None):
    manifest, payloads, budget = preflight(directory)
    if approval_digest != budget['manifest_sha256']:
        raise ValueError('execution must identify the approved manifest digest')
    output.mkdir(parents=True, exist_ok=True)
    started = clock()
    report = {'evidence_kind': 'isolated_model_content_experiment_not_business_qualification',
        'budget': budget, 'production_writes': 0, 'items': [], 'api_attempts': 0}
    prior_elapsed = 0
    if previous_file is not None:
        previous = json.loads(previous_file.read_text())
        if previous['budget']['manifest_sha256'] != approval_digest:
            raise ValueError('continuation belongs to another approved manifest')
        prior = previous['items']
        if previous['api_attempts'] != len(prior) or not 0 < len(prior) < len(payloads):
            raise ValueError('invalid previous request ledger')
        for old, row in zip(prior, manifest['items']):
            if any(old[k] != row[k] for k in ('target','version_id','payload_sha256')):
                raise ValueError('previous attempts differ from approved manifest prefix')
        # Continuation only skips attempted payloads; failed/uncertain requests
        # are not retried. Charge intervening investigation to the run deadline.
        prior_elapsed = max(previous.get('elapsed_seconds', 0),
            time.time()-previous_file.stat().st_mtime+previous.get('elapsed_seconds', 0))
        report.update(items=prior, api_attempts=len(prior), previous_capture=str(previous_file))
    offset = len(report['items'])
    for row, payload in list(zip(manifest['items'], payloads))[offset:]:
        remaining = LIMITS['total_seconds'] - prior_elapsed - (clock()-started)
        if remaining <= 0:
            report['stop_reason'] = 'run_deadline'
            break
        record = {k: row[k] for k in ('target', 'title', 'source', 'completeness',
                                     'version_id', 'publication_id', 'payload_sha256')}
        report['api_attempts'] += 1
        report['items'].append(record)
        # Write intent before spending. An uncertain network outcome is never
        # retried automatically; output directories are single-use at the CLI.
        record['status'] = 'attempt_started'
        (output/'capture.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        try:
            response = client.with_options(timeout=min(remaining, LIMITS['seconds_per_request']),
                max_retries=0).chat.completions.create(model=MODEL,
                messages=[{'role': 'system', 'content': payload['system']},
                          {'role': 'user', 'content': payload['user']}],
                tools=[{'type': 'function', 'function': {'name': 'InformationInsightBatchView',
                       'parameters': payload['structured_output_schema']}}],
                tool_choice={'type': 'function', 'function': {'name': 'InformationInsightBatchView'}},
                max_tokens=LIMITS['output_tokens_per_request'],
                temperature=0.2, extra_body={'reasoning': {'enabled': False},
                    'provider': {'require_parameters': True, 'max_price': {
                        'prompt': LIMITS['input_usd_per_million'],
                        'completion': LIMITS['output_usd_per_million']}}})
            record['usage'] = response.usage.model_dump(mode='json') if response.usage else None
            record['finish_reason'] = getattr(response.choices[0], 'finish_reason', None)
            calls = response.choices[0].message.tool_calls or []
            record['tool_call_count'] = len(calls)
            if len(calls) != 1 or calls[0].function.name != 'InformationInsightBatchView':
                raise ValueError('expected one schema-bound extraction response')
            record['raw_tool_arguments'] = calls[0].function.arguments
            result = InformationInsightBatchView.model_validate_json(calls[0].function.arguments)
            record.update(status='schema_valid_not_content_signed', insights=result.model_dump(mode='json')['insights'],
                          empty_reason=result.empty_reason)
        except Exception as exc:
            # Do not log provider exceptions: they can include request text/keys.
            record.update(status='failed_no_retry', error_type=type(exc).__name__)
            if isinstance(getattr(exc, 'status_code', None), int):
                record['http_status'] = exc.status_code
            if isinstance(exc, ValidationError):
                record['schema_errors'] = [{'location':list(e['loc']), 'type':e['type']}
                                          for e in exc.errors(include_input=False)]
            report['stop_reason'] = 'model_or_contract_failure'
        (output/'capture.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        if report.get('stop_reason'):
            break
    report['elapsed_seconds'] = round(prior_elapsed+clock()-started, 3)
    (output/'capture.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-dir', type=Path, required=True)
    parser.add_argument('--budget-output', type=Path, required=True)
    parser.add_argument('--execute-approved-manifest', help='Exact approved manifest SHA256; absent means dry-run')
    parser.add_argument('--capture-output-dir', type=Path)
    parser.add_argument('--continue-unattempted-from', type=Path,
                        help='Manual continuation: preserve attempts, skip attempted payloads, retain total cap/deadline')
    args = parser.parse_args()
    _, _, budget = preflight(args.prepared_dir)
    args.budget_output.parent.mkdir(parents=True, exist_ok=True)
    args.budget_output.write_text(json.dumps(budget, ensure_ascii=False, indent=2))
    if args.execute_approved_manifest:
        if args.execute_approved_manifest != budget['manifest_sha256']:
            parser.error('approved manifest digest mismatch')
        if not args.capture_output_dir or args.capture_output_dir.exists():
            parser.error('capture requires a new output directory; never overwrite or auto-resume')
        from ats.config import get_config
        from openai import OpenAI
        from urllib.parse import urlparse
        cfg = get_config()
        if urlparse(cfg.secrets.openai_base_url or '').hostname != 'openrouter.ai':
            parser.error('configured destination differs from reviewed destination')
        with OpenAI(api_key=cfg.secrets.openai_api_key, base_url=cfg.secrets.openai_base_url,
                    max_retries=0, timeout=60) as client:
            result = capture(args.prepared_dir, args.capture_output_dir,
                             approval_digest=args.execute_approved_manifest, client=client,
                             previous_file=args.continue_unattempted_from)
        print(json.dumps({'api_attempts':result['api_attempts'], 'stop_reason':result.get('stop_reason')}))
    else:
        print(json.dumps({'status':'dry_run_no_model', **budget}))


if __name__ == '__main__':
    main()
