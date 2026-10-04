"""Candidate evidence only. Never use this output to construct golden values."""
from pathlib import Path
from dataclasses import asdict
import json
from ats.data.sources.factset_chart_grid import locate_printed_grids, read_grid_cell
from ats.data.sources.factset_report_layout import load_layout_policy, policy_hash

root=Path('/private/tmp/ats-factset-review')
out=Path('tests/fixtures/factset_earnings_insight/grid-candidates')
out.mkdir(parents=True,exist_ok=True)
policy=load_layout_policy()
for name,date in [('aug','2026-08-28'),('sep','2026-09-18')]:
    inventory=json.loads(Path(f'tests/fixtures/factset_earnings_insight/discovery/{date}.json').read_text())
    for chart in inventory['images']:
        if not chart['groups']:
            continue
        stem=f"{name}-{chart['page_number']}-{chart['image_number']}"
        path=root/f'{stem}.png'
        if not path.exists():
            continue
        data=path.read_bytes()
        grids=locate_printed_grids(data)
        result=[]
        for grid in grids:
            value=asdict(grid)
            for cell in value['cells']:
                cell['text']=read_grid_cell(data,cell['region'],scale=policy['ocr']['cell_scale'])
            result.append(value)
        (out/f'{stem}.json').write_text(json.dumps({'candidate_only':True,'policy_hash':policy_hash(policy),'chart':chart,'grids':result},indent=2))
        print(stem,chart['groups'],[(g.row_count,g.column_count) for g in grids],flush=True)
