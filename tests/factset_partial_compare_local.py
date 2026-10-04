"""Compare independently transcribed cells; never an all-report acceptance."""
from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal
import json
import yaml
from ats.data.sources.factset_chart_grid import GridCell, PrintedGrid
from ats.data.sources.factset_chart_values import read_grid_candidates
from ats.data.sources.factset_report_layout import load_layout_policy
from ats.data.sources.factset_earnings_charts import normalize_chart_text

policy=load_layout_policy()
aliases={normalize_chart_text(label):entity for entity,labels in policy['sector_aliases'].items() for label in labels}
golden=yaml.safe_load(Path('tests/fixtures/factset_earnings_insight/acceptance/cross-report-visual-partial.yaml').read_text())
checked=0
errors=[]
for table in golden['tables']:
    if table.get('source_row')!='Today':
        continue
    prefix='aug' if table['report_date']=='2026-08-28' else 'sep'
    stem=f"{prefix}-{table['page']}-{table['image']}"
    raw=json.loads(Path(f'tests/fixtures/factset_earnings_insight/grid-candidates/{stem}.json').read_text())
    assert len(raw['grids'])==1
    data=raw['grids'][0]
    cells=tuple(GridCell(c['row'],c['column'],tuple(c['region']),c['text']) for c in data['cells'])
    grid=PrintedGrid(tuple(data['region']),cells,data['row_count'],data['column_count'])
    lookup={c.region:c.text for c in cells}
    candidates=read_grid_candidates(b'',grid,SimpleNamespace(),policy,reader=lambda data,region,**kw:lookup[region])
    values={v.entity_id:v for v in candidates.values if 'today' in v.row_label.casefold()}
    for label,expected in table['values'].items():
        checked+=1
        candidate=values.get(aliases[normalize_chart_text(label)])
        if candidate is None or candidate.value is None or Decimal(candidate.value)!=Decimal(str(expected)):
            errors.append({'chart':stem,'label':label,'expected':expected,'actual':candidate.value if candidate else None})
print(json.dumps({'checked':checked,'errors':errors,'scope':'partial_current_growth_only','not_full_acceptance':True},indent=2))
