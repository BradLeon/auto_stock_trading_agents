"""Read-only current exit inventory and explicit new requirement receipts."""
import argparse
import json
from pathlib import Path

from ats.workflow.exit_requirements import production_inventory, verify_new_coverage

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--requirements',type=Path)
parser.add_argument('--out',type=Path,required=True)
args=parser.parse_args()
result=production_inventory()
if args.requirements:
    result['new_requirement_coverage']=verify_new_coverage(json.loads(args.requirements.read_text()))
args.out.parent.mkdir(parents=True,exist_ok=True)
with args.out.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
print(json.dumps({'output':str(args.out),'status':result['status']}))
