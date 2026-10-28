"""Post-call independent comparison, separate from blind producer script."""
from pathlib import Path
from decimal import Decimal
import json,sys,yaml
from ats.data.sources.factset_report_layout import load_layout_policy
from ats.data.sources.factset_earnings_charts import normalize_chart_text

directory=Path(sys.argv[1])
policy=load_layout_policy()
aliases={normalize_chart_text(label):entity for entity,labels in policy['sector_aliases'].items() for label in labels}
observed={}; cost=0; versions=set(); labels=[]
for file in sorted(directory.glob('[0-9][0-9][0-9].json')):
    record=json.loads(file.read_text()); response=record['response']
    assert response['status']=='pending_review'
    versions.add(record['policy_hash'])
    cost+=float(response['usage'].get('cost',0) or 0)
    for cell in response['cells']:
        source=record['sources'][cell['id']]
        key=(source['chart'],source['entity'],source['kind'])
        assert key not in observed
        observed[key]=cell['token']
golden=yaml.safe_load(Path('tests/fixtures/factset_earnings_insight/acceptance/cross-report-visual-partial.yaml').read_text())
errors=[]; checked=0
for table in golden['tables']:
    if table.get('source_row')!='Today': continue
    prefix='aug' if table['report_date']=='2026-08-28' else 'sep'
    name=f"{prefix}-{table['page']}-{table['image']}"
    for label,expected in table['values'].items():
        checked+=1; entity=aliases[normalize_chart_text(label)]
        token=observed.get((name,entity,'value'))
        label_token=observed.get((name,entity,'label'))
        label_ok=aliases.get(normalize_chart_text(label_token or ''))==entity
        try: numeric_ok=token.endswith('%') and Decimal(token[:-1])==Decimal(str(expected))
        except Exception: numeric_ok=False
        if not label_ok or not numeric_ok:
            errors.append({'chart':name,'entity':entity,'expected':expected,'token':token,'label_token':label_token,'label_ok':label_ok})
result={'scope':'growth_current_values_and_labels_only','full_report_accepted':False,
        'checked_values':checked,'checked_labels':checked,'returned_crops':len(observed),
        'policy_hashes':sorted(versions),'errors':errors,'cost_usd':cost}
(directory/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False,indent=2))
