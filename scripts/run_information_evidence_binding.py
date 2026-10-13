"""Bounded evidence-first capture of the SAME reviewed eight source documents.

Prepare first, then execute an exact manifest digest. No acquisition, publication,
notifications or trading. Each recap receives only its located original excerpt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

from ats.agents.base import load_skill
from ats.agents.information import extract
from ats.agents.information.outputs import InformationEvidenceBatchView, InformationEvidenceRecapView
from ats.config import get_config
from ats.data.products.unstructured import admitted_documents
from ats.data.stores.unstructured import PlatformUnstructuredRepository
from review_information_selection import accepted
from run_information_capture import LIMITS, MODEL


def implementation_versions():
    from ats.agents.information import outputs, target_research, briefs
    from ats.schemas import research
    paths = [Path(extract.__file__), Path(outputs.__file__), Path(target_research.__file__),
             Path(research.__file__), Path(briefs.__file__), Path(__file__)]
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def articles(manifest, database):
    repo = PlatformUnstructuredRepository(database, writable=False)
    try:
        docs = admitted_documents(as_of=datetime.fromisoformat(manifest['as_of']), repository=repo, limit=1000)
    finally:
        repo.close()
    arts, _ = accepted(docs, datetime.fromisoformat(manifest['since']), datetime.fromisoformat(manifest['as_of']))
    versions = {art.document_version_id: art for art in arts}
    result = []
    for row in manifest['items']:
        art = versions[row['version_id']]
        if art.publication_id != row['publication_id'] or art.id != row['document_id']:
            raise ValueError('source identity differs from the reviewed source')
        result.append(art)
    return result


def prepare(source_manifest: Path, database: Path, output: Path):
    if output.exists():
        raise ValueError('fresh preparation directory required')
    original = json.loads(source_manifest.read_text())
    if len(original['items']) != 8:
        raise ValueError('closeout is restricted to the reviewed eight sources')
    output.mkdir(parents=True)
    manifest = {key: original[key] for key in ('as_of', 'since')}
    manifest.update(contract='evidence-first-v5', destination_host='openrouter.ai', model=MODEL,
        source_manifest=str(source_manifest), items=[], system=load_skill('research-insight'),
        recap_system=load_skill('information-evidence-recap'),
        recap_schema=InformationEvidenceRecapView.model_json_schema(), production_writes=0,
        implementation_versions=implementation_versions())
    for row, art in zip(original['items'], articles(original, database)):
        _, context, schema = extract.evidence_selection_input(art, row['target'], 40000)
        manifest['items'].append({**row, 'context': context, 'schema': schema.model_json_schema()})
    (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return preflight(output)[2]


def preflight(prepared: Path):
    raw = (prepared/'manifest.json').read_bytes()
    manifest = json.loads(raw)
    if manifest['contract'] != 'evidence-first-v5' or manifest['model'] != MODEL or manifest['destination_host'] != 'openrouter.ai':
        raise ValueError('unexpected experiment contract/destination')
    if len(manifest['items']) != 8 or manifest['system'] != load_skill('research-insight'):
        raise ValueError('source count/system prompt differs')
    if manifest.get('recap_system') != load_skill('information-evidence-recap'):
        raise ValueError('frozen recap prompt differs')
    if manifest.get('implementation_versions') != implementation_versions():
        raise ValueError('frozen extraction implementation differs')
    if manifest['recap_schema'] != InformationEvidenceRecapView.model_json_schema() or any(
            row['schema'] != InformationEvidenceBatchView.model_json_schema() for row in manifest['items']):
        raise ValueError('frozen evidence contract differs')
    # Budget includes four short recap calls per article, not just the selectors.
    request_cap = 40
    estimated_input = len(raw) + request_cap*4096 + 8*4*(extract.CORE_CHARS + extract.CONTEXT_COUNT*extract.CONTEXT_CHARS + extract.STRUCTURAL_CONTEXT_CHARS)*4
    reservation = (estimated_input*LIMITS['input_usd_per_million'] +
        request_cap*4096*LIMITS['output_usd_per_million'])/1e6
    if reservation > LIMITS['cost_reservation_usd']:
        raise ValueError('planning reservation exceeded')
    budget = dict(manifest_sha256=hashlib.sha256(raw).hexdigest(), requests=request_cap,
        sdk_retries=0, structured_retries=0, seconds_per_request=60, total_seconds=480,
        planned_cost_reservation_usd=reservation, cost_reservation_usd=.50,
        cost_limit_kind='planning reservation and unit-price ceiling, not billing guarantee')
    return manifest, manifest['items'], budget


def capture(prepared: Path, database: Path, output: Path, approval_digest: str, *, previous_file: Path | None = None):
    manifest, rows, budget = preflight(prepared)
    if approval_digest != budget['manifest_sha256'] or output.exists():
        raise ValueError('exact approved digest and fresh output directory required')
    arts = articles(manifest, database)
    for row, art in zip(rows, arts):
        if extract.evidence_selection_input(art, row['target'], 40000)[1] != row['context']:
            raise ValueError('frozen source input differs')
    cfg = get_config()
    if urlparse(cfg.secrets.openai_base_url or '').hostname != 'openrouter.ai':
        raise ValueError('configured destination differs')
    from openai import OpenAI
    output.mkdir(parents=True)
    report = dict(budget=budget, items=[], requests=[], production_writes=0,
        evidence_kind='bounded_evidence_first_content_capture_not_production_qualification')
    started = time.monotonic()
    deadline = started + budget['total_seconds']
    replay = {}
    prior_elapsed = 0
    if previous_file is not None:
        previous = json.loads(previous_file.read_text())
        if previous['budget']['manifest_sha256'] != approval_digest:
            raise ValueError('manual continuation belongs to a different manifest')
        if any(request['status'] != 'schema_valid' for request in previous['requests']):
            raise ValueError('uncertain or failed API requests cannot be retried by continuation')
        report['requests'] = previous['requests']
        report['previous_capture'] = str(previous_file)
        replay = {request['context']: request for request in previous['requests']}
        prior_elapsed = previous['elapsed_seconds'] + max(0, time.time()-previous_file.stat().st_mtime)
        deadline -= prior_elapsed
    def save():
        report['elapsed_seconds'] = round(prior_elapsed + time.monotonic()-started, 3)
        (output/'capture.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    with OpenAI(api_key=cfg.secrets.openai_api_key, base_url=cfg.secrets.openai_base_url,
                max_retries=0, timeout=60) as client:
        for row, art in zip(rows, arts):
            item = {key: row[key] for key in ('target','version_id','title','source','completeness','selection_reason')}
            report['items'].append(item)
            def transport(role, schema, context, **kwargs):
                if role != 'research_extract' or schema not in (InformationEvidenceBatchView, InformationEvidenceRecapView):
                    raise ValueError('unexpected extraction request')
                if context in replay:
                    old = replay.pop(context)
                    if old['schema'] != schema.__name__:
                        raise ValueError('captured response schema differs')
                    return schema.model_validate(old['result'])
                if len(report['requests']) >= budget['requests'] or time.monotonic() >= deadline:
                    raise TimeoutError('bounded capture exhausted')
                request = dict(target=row['target'], version_id=row['version_id'], schema=schema.__name__,
                    context=context, status='attempt_started', started_elapsed_seconds=round(time.monotonic()-started,3))
                report['requests'].append(request)
                save()
                response = client.with_options(timeout=min(60, deadline-time.monotonic()), max_retries=0).chat.completions.create(
                    model=MODEL, messages=[{'role':'system','content':manifest['recap_system'] if schema is InformationEvidenceRecapView else manifest['system']},{'role':'user','content':context}],
                    tools=[{'type':'function','function':{'name':schema.__name__,'parameters':schema.model_json_schema()}}],
                    tool_choice={'type':'function','function':{'name':schema.__name__}}, max_tokens=4096,
                    temperature=.2, extra_body={'reasoning':{'enabled':False},'provider':{'require_parameters':True,
                        'max_price':{'prompt':.10,'completion':.80}}})
                request['completed_elapsed_seconds'] = round(time.monotonic()-started,3)
                request['usage'] = response.usage.model_dump(mode='json') if response.usage else None
                calls = response.choices[0].message.tool_calls or []
                if len(calls) != 1 or calls[0].function.name != schema.__name__:
                    raise ValueError('missing schema-bound tool result')
                request['raw_tool_arguments'] = calls[0].function.arguments
                result = schema.model_validate_json(calls[0].function.arguments)
                request.update(status='schema_valid', result=result.model_dump(mode='json'))
                save()
                return result
            try:
                with patch.object(extract, 'run_structured', transport):
                    insights = extract._extract(art, f"- {row['target']} (target)", {row['target']}, 40000,
                        assess_materiality=True, target=row['target'], diagnostics=item, deadline=deadline)
                item.update(status='source_bound_pending_semantic_review', insights=[i.model_dump(mode='json') for i in insights])
            except Exception as exc:
                # Preserve the attempted request, including uncertain spend; never auto-retry.
                item.update(status='failed_no_retry', error_type=type(exc).__name__)
                report['stop_reason'] = 'model_or_contract_failure'
                save()
                break
            save()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-manifest', type=Path)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--prepared-dir', type=Path, required=True)
    parser.add_argument('--capture-output-dir', type=Path)
    parser.add_argument('--execute-approved-manifest')
    parser.add_argument('--continue-capture-file', type=Path)
    args = parser.parse_args()
    if args.execute_approved_manifest:
        if not args.capture_output_dir:
            parser.error('capture output directory required')
        result = capture(args.prepared_dir, args.database, args.capture_output_dir, args.execute_approved_manifest,
                         previous_file=args.continue_capture_file)
        print(json.dumps({'requests':len(result['requests']), 'stop_reason':result.get('stop_reason')}))
    elif args.source_manifest:
        print(json.dumps(prepare(args.source_manifest,args.database,args.prepared_dir)))
    else:
        print(json.dumps(preflight(args.prepared_dir)[2]))
