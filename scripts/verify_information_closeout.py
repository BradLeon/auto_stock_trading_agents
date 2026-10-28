"""Offline native Information entry/publication/cache/consumer verification.

Uses captured real responses, original admitted document rows and complete
isolation. Never calls a model/provider or certifies production qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from ats.agent.task_projection import ProjectionScope
from ats.agents.information import entry, extract, target_research
from ats.agents.information.outputs import InformationInsightBatchView
from ats.data.products.unstructured import admitted_documents
from ats.data.stores.unstructured import PlatformUnstructuredRepository
from ats.memory import get_store
from ats.workflow.consumer_reads import read_projection, trace_reads
from ats.workflow.evaluation_clock import at
from ats.workflow.isolation import isolated_run
from ats.workflow.runtime_reads import bind_read, business_identity
from review_information_selection import accepted
from run_information_capture import preflight


def snapshot(source: Path, target: Path, ids: list[str]):
    """Copy exact original rows for a declared sample, restoring all triggers."""
    q=lambda name:'"'+name.replace('"','""')+'"'
    marks=','.join('?' for _ in ids)
    with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(target) as dst:
        src.execute('BEGIN')
        versions=[r[0] for r in src.execute(f'SELECT version_id FROM data_document_versions WHERE document_id IN ({marks})',ids)]
        schema=src.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY rowid").fetchall()
        copied={}
        for kind,name,sql in schema:
            if kind!='table':continue
            dst.execute(sql)
            if not name.startswith('data_document'):
                copied[name]=0  # unrelated structured/query caches are not DOC_DATA inputs
                continue
            cols=[r[1] for r in src.execute('PRAGMA table_info('+q(name)+')')]
            field='document_id' if 'document_id' in cols else 'document_version_id' if 'document_version_id' in cols else None
            values=ids if field=='document_id' else versions
            where=f' WHERE {q(field)} IN ({",".join("?" for _ in values)})' if field else ''
            rows=src.execute('SELECT * FROM '+q(name)+where,values if field else []).fetchall()
            if rows:dst.executemany('INSERT INTO '+q(name)+' VALUES ('+','.join('?' for _ in cols)+')',rows)
            actual=dst.execute('SELECT * FROM '+q(name)).fetchall()
            canonical=lambda data:sorted(json.dumps(row,ensure_ascii=False,default=str) for row in data)
            if canonical(rows)!=canonical(actual):raise ValueError('snapshot rows differ: '+name)
            copied[name]=len(rows)
        for kind,name,sql in schema:
            if kind!='table':dst.execute(sql)
        dst.commit()
        assert dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    return copied


def verify(prepared: Path, capture_file: Path, database: Path, root: Path):
    evidence_first=json.loads((prepared/'manifest.json').read_text()).get('contract','').startswith('evidence-first-')
    if evidence_first:
        from run_information_evidence_binding import preflight as evidence_preflight
        manifest,rows,budget=evidence_preflight(prepared)
        payloads=[{'user':row['context']} for row in rows]
    else:
        manifest,payloads,budget=preflight(prepared)
    captured=json.loads(capture_file.read_text())
    if captured['budget']['manifest_sha256']!=budget['manifest_sha256']:raise ValueError('capture manifest mismatch')
    point=datetime.fromisoformat(manifest['as_of']);since=datetime.fromisoformat(manifest['since'])
    successful={(x['target'],x['version_id']):x for x in captured['items'] if x['status'] in
        ('schema_valid_not_content_signed','source_bound_pending_semantic_review')}
    if root.exists():raise ValueError('fresh verification root required')
    root.mkdir(parents=True)
    before=hashlib.sha256(database.read_bytes()).hexdigest()
    report={'evidence_kind':'isolated_native_entry_with_real_response_replay','model_calls':0,
        'production_writes':0,'production_qualification':False,'manifest_sha256':budget['manifest_sha256'],
        'targets':{},'historical_partial':[]}
    report['experimental_evidence_first']=evidence_first
    native_target_run=target_research.run
    def candidate_target_run(store,symbol,*,config,**kwargs):
        # Select the candidate implementation explicitly; never alter gates or
        # enable this unaccepted strategy in daily configuration.
        return native_target_run(store,symbol,config={**config,'evidence_first':evidence_first},**kwargs)
    for symbol in ['MRVL','MSFT','NVDA','TSM']:
        pairs=[(row,payload,successful[(symbol,row['version_id'])]) for row,payload in zip(manifest['items'],payloads)
            if row['target']==symbol and (symbol,row['version_id']) in successful]
        if not pairs:raise ValueError('no captured samples for '+symbol)
        contexts={payload['user']:record for row,payload,record in pairs}
        replay={request['context']:request for request in captured.get('requests',[]) if request['target']==symbol}
        consumed=[]
        def transport(role,schema,context,**kwargs):
            if evidence_first:
                request=replay.get(context)
                if role!='research_extract' or request is None or request['schema']!=schema.__name__ or request['status']!='schema_valid':
                    raise ValueError('unrecorded evidence-first request in offline verification')
                consumed.append(context)
                return schema.model_validate(request['result'])
            if role!='research_extract' or schema is not InformationInsightBatchView or context not in contexts:
                raise ValueError('unrecorded model request in offline verification')
            consumed.append(context)
            record=contexts[context]
            return schema(insights=record['insights'],empty_reason=record.get('empty_reason',''))
        with isolated_run('information-closeout-'+symbol,root=root/symbol) as env:
            counts=snapshot(database,env.path_for('ATS_DATA_DB_PATH'),[row['document_id'] for row,_,_ in pairs])
            store=get_store()
            neutral_before=store.data_store().conn.total_changes
            identity=business_identity('information',kind='entity',scope_id=symbol,entities=[symbol],as_of=point)
            with at(point),patch.object(extract,'run_structured',transport),patch.object(target_research,'run',candidate_target_run),trace_reads() as traces:
                with bind_read(identity,as_of=point,mode='isolated'):
                    first=entry.run_information_target(symbol,store=store,use_llm=True)
                    count=len(consumed)
                    second=entry.run_information_target(symbol,store=store,use_llm=True)
                    assert len(consumed)==count and second['selection']['model_calls']==0
                    assert second['selection']['cache_hits']==first['selection']['model_calls'] and count>0
                fundamental=business_identity('fundamental',kind='entity',scope_id=symbol,entities=[symbol],as_of=point)
                with bind_read(fundamental,as_of=point,mode='isolated'):
                    brief=read_projection(store,consumer='fundamental',role='information_brief',scope=ProjectionScope(kind='entity',id=symbol))
                    assert brief and brief['projection_id']==second['selection']['projection_id']
                    assert brief['input_refs'] and brief['payload']['source_evidence']
                    try:read_projection(store,consumer='fundamental',role='information_brief',scope=ProjectionScope(kind='entity',id='OTHER'))
                    except Exception as exc:
                        from ats.workflow.cutover_routing import RouteUnavailable
                        if not isinstance(exc,RouteUnavailable):raise
                    else:raise AssertionError('wrong target projection escaped scope')
                try:read_projection(store,consumer='sector',role='information_brief',scope=ProjectionScope(kind='entity',id=symbol))
                except PermissionError:pass
                else:raise AssertionError('undeclared role read allowed')
                report['targets'][symbol]={'first':first,'second':second,'transport_replayed':count,
                    'fundamental_read_projection_id':brief['projection_id'],'brief':brief['payload'],
                    'read_trace':list(traces),'snapshot_row_counts':counts}
                repo=PlatformUnstructuredRepository(env.path_for('ATS_DATA_DB_PATH'),writable=False)
                arts,_=accepted(admitted_documents(entities=[symbol],as_of=point,repository=repo,limit=120),since,point)
                repo.close()
                historical={row['version_id'] for row,_,_ in pairs if row['selection_reason']=='historical_partial_diagnostic'}
                for art in arts:
                    if art.document_version_id not in historical:continue
                    diagnostic={}
                    insights=extract._extract(art,f'- {symbol} (target)',{symbol},40000,assess_materiality=True,target=symbol,diagnostics=diagnostic)
                    assert art.completeness=='partial' and insights
                    report['historical_partial'].append({'target':symbol,'title':art.title,
                        'published_at':art.published_at.isoformat(),'version_id':art.document_version_id,
                        'excluded_from_current_seven_day_entry':all(x['version_id']!=art.document_version_id for x in first['selection']['items']),
                        'insights':[x.model_dump(mode='json') for x in insights]})
                report['targets'][symbol]['neutral_platform_writes']=store.data_store().conn.total_changes-neutral_before
                assert report['targets'][symbol]['neutral_platform_writes']==0
    report['source_unchanged']=hashlib.sha256(database.read_bytes()).hexdigest()==before
    assert report['source_unchanged'] and report['historical_partial']
    report['status']='passed'
    (root/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'status':report['status'],'targets':{t:{'replayed':r['transport_replayed'],'cache_hits':r['second']['selection']['cache_hits']} for t,r in report['targets'].items()},'partial':len(report['historical_partial']),'source_unchanged':True},ensure_ascii=False))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ['prepared-dir','capture-file','database','output-root']:parser.add_argument('--'+flag,type=Path,required=True)
    args=parser.parse_args()
    verify(args.prepared_dir,args.capture_file,args.database,args.output_root)
