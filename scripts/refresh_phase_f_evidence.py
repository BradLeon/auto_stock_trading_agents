"""Minimal frozen-scope evidence reuse/query; no collection or production writes."""
import argparse
import json
from pathlib import Path

from ats.workflow.isolation import isolated_run
from ats.workflow.scoped_intake import (
    append_original_events,
    check_freeze,
    freeze_snapshot,
    qualification_report,
)

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--scopes', type=Path, required=True, help='JSON array of complete RouteIdentity objects')
parser.add_argument('--source-ledger', type=Path, required=True)
parser.add_argument('--root', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--freeze', type=Path, required=True)
parser.add_argument('--capture-freeze', action='store_true')
parser.add_argument('--previous-freeze', type=Path,
    help='freeze the new one supersedes; every changed freeze-only file needs a disposition')
parser.add_argument('--dispositions', type=Path,
    help='JSON array of {path, decision: unaffected|revoke, reason} for changed freeze-only files')
parser.add_argument('--initial-freeze', action='store_true',
    help='capture without a predecessor (only when no earlier freeze backs registered evidence)')
parser.add_argument('--reuse-current', action='store_true')
parser.add_argument('--actor', default='phase-f-intake')
args=parser.parse_args()
rows=json.loads(args.scopes.read_text())
if args.capture_freeze:
    if args.previous_freeze is None and not args.initial_freeze:
        parser.error('--capture-freeze needs --previous-freeze (or an explicit --initial-freeze)')
    previous=json.loads(args.previous_freeze.read_text()) if args.previous_freeze else None
    dispositions=json.loads(args.dispositions.read_text()) if args.dispositions else None
    snapshot=freeze_snapshot(previous=previous,dispositions=dispositions)
    with args.freeze.open('x') as stream:json.dump(snapshot,stream,ensure_ascii=False,indent=2)
else:
    snapshot=json.loads(args.freeze.read_text())
check_freeze(snapshot)
source=qualification_report(rows, db_path=args.source_ledger, freeze=snapshot)
with isolated_run('phase-f-minimal-evidence-refresh',root=args.root) as env:
    copied=append_original_events(rows,source_db=args.source_ledger,
        target_db=env.path_for('ATS_DATA_DB_PATH'),freeze=snapshot,actor=args.actor) if args.reuse_current else []
    candidate=qualification_report(rows,db_path=env.path_for('ATS_DATA_DB_PATH'),freeze=snapshot)
result={'source':source,'candidate':candidate,'registration':copied,
    'production_qualification_granted':False,'network_calls':0,
    'refresh_rule':'reuse only currently valid raw events; expired evidence requires a new actual probe, never redate'}
with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
print(json.dumps({'output':str(args.output),'eligible':source['eligible'],'total':source['total'],
    'registered':sum(r['registered'] for r in copied),'production_mutated':False}))
