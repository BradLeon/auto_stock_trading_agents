"""Blind source-crop replay: no acceptance fixtures imported or read here."""
from pathlib import Path
from datetime import datetime,timezone
from dataclasses import asdict
import hashlib,json
from ats.data.sources.factset_report_layout import load_layout_policy,policy_hash
from ats.data.sources.factset_earnings_charts import normalize_chart_text
from ats.data.sources.factset_vision import FactSetVision,make_crop

policy=load_layout_policy()
adapter=FactSetVision(policy['vision'])
aliases={normalize_chart_text(label):entity for entity,labels in policy['sector_aliases'].items() for label in labels}
items=[]
for path in sorted(Path('tests/fixtures/factset_earnings_insight/grid-candidates').glob('*.json')):
    doc=json.loads(path.read_text())
    if doc['chart']['groups']!=['earnings_revenue_growth'] or len(doc['grids'])!=1:
        continue
    grid=doc['grids'][0]
    rows={i:[c for c in grid['cells'] if c['row']==i] for i in range(grid['row_count'])}
    headers=[row for row in rows.values() if sum(normalize_chart_text(c['text']) in aliases for c in row)>=3]
    today=[row for row in rows.values() if any('today' in c['text'].lower() for c in row)]
    if len(headers)!=1 or len(today)!=1:
        raise RuntimeError(f'header/current row missing:{path.stem}')
    data=Path(f'/private/tmp/ats-factset-review/{path.stem}.png').read_bytes()
    for header in headers[0]:
        entity=aliases.get(normalize_chart_text(header['text']))
        if not entity: continue
        value=next(c for c in today[0] if c['column']==header['column'])
        for kind,cell in [('label',header),('value',value)]:
            crop=make_crop(data,tuple(cell['region']),f'{path.stem}-{cell["row"]}-{cell["column"]}')
            items.append((crop,{'chart':path.stem,'kind':kind,'entity':entity,'ocr_token':cell['text']}))
out=Path('tests/fixtures/factset_earnings_insight/vision-growth')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
out.mkdir(parents=True,exist_ok=False)
print(json.dumps({'directory':str(out),'crop_count':len(items),'policy_hash':policy_hash(policy)}),flush=True)
total_cost=0
for index,start in enumerate(range(0,len(items),policy['vision']['max_crops_per_request'])):
    batch=items[start:start+policy['vision']['max_crops_per_request']]
    response=adapter.transcribe([crop for crop,_ in batch])
    record={'response':response,'sources':{crop.crop_id:source for crop,source in batch},'policy_hash':policy_hash(policy)}
    (out/f'{index:03d}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
    total_cost+=float(response.get('usage',{}).get('cost',0) or 0)
    print(json.dumps({'batch':index,'status':response['status'],'cost_total':total_cost,'reason':response.get('reason')}),flush=True)
    if response['status']!='pending_review':
        raise SystemExit('Stopped on failed response; preserved all prior responses, no auto retries.')
