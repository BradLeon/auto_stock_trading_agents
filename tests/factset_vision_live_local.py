"""Authorized live diagnostic; no golden/expected values enter model requests."""
from pathlib import Path
import json
from datetime import datetime,timezone
from ats.data.sources.factset_report_layout import load_layout_policy
from ats.data.sources.factset_vision import FactSetVision,make_crop,compare_numeric

policy=load_layout_policy()
adapter=FactSetVision(policy['vision'])
crops=[]; tokens={}
for name,col in [('sep-24-2',11),('sep-25-2',2),('sep-22-3',9),('aug-26-3',9)]:
    raw=json.loads(Path(f'tests/fixtures/factset_earnings_insight/grid-candidates/{name}.json').read_text())
    cell=next(c for c in raw['grids'][0]['cells'] if c['row']==1 and c['column']==col)
    data=Path(f'/private/tmp/ats-factset-review/{name}.png').read_bytes()
    crops.append(make_crop(data,tuple(cell['region']),name))
    tokens[name]=cell['text']
response=adapter.transcribe(crops)
compared=[compare_numeric(c,tokens[c.crop_id],response) for c in crops]
out=Path('tests/fixtures/factset_earnings_insight/vision-runs')
out.mkdir(parents=True,exist_ok=True)
path=out/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')
path.write_text(json.dumps({'response':response,'comparisons':compared},ensure_ascii=False,indent=2))
print(json.dumps({'status':response['status'],'reason':response.get('reason'),'model':response.get('model'),
                  'usage':response.get('usage'),'evidence':str(path),'comparisons':compared},ensure_ascii=False),flush=True)
