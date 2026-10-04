"""Independent golden comparison after source-only extraction is finished."""
from decimal import Decimal
from pathlib import Path
import argparse
import json

import yaml

from ats.data.sources.factset_contracts import GroupPackage, validate_group
from ats.data.sources.factset_report_layout import load_layout_policy, policy_hash

parser = argparse.ArgumentParser()
parser.add_argument('evidence_directory', type=Path)
args = parser.parse_args()
policy = load_layout_policy()
golden_root = Path('tests/fixtures/factset_earnings_insight/acceptance')
expected = {}


def add(date, page, image, column, number):
    key = (str(date), int(page), int(image), column)
    if key in expected and expected[key] != number:
        raise ValueError(f'conflicting_independent_golden:{key}')
    expected[key] = number


for filename in ('scorecard-visual.yaml', 'surprise-visual.yaml', 'margin-visual.yaml',
                 'guidance-visual.yaml', 'valuation-ratings-visual.yaml'):
    raw = yaml.safe_load((golden_root/filename).read_text())
    for chart in raw['charts']:
        rows = [(label, values) for label, values in chart['rows'].items() if 'Tech' in label]
        assert len(rows) == 1
        for column, value in zip(chart['columns'], rows[0][1]):
            number = Decimal(str(value))
            if column.endswith('_share') or column in {'eps_above','eps_inline','eps_below',
                    'revenue_above','revenue_inline','revenue_below'}:
                number /= 100
            add(chart['report_date'],chart['page'],chart['image'],column,number)
growth = yaml.safe_load((golden_root/'cross-report-visual-partial.yaml').read_text())
for chart in growth['tables']:
    if chart.get('source_row') not in (None,'Today'):
        continue
    matches = [(label,value) for label,value in chart['values'].items() if 'Tech' in label]
    assert len(matches) == 1
    add(chart['report_date'],chart['page'],chart['image'],chart['column'],Decimal(str(matches[0][1])))

observed = {}
status = []
for path in sorted(args.evidence_directory.glob('[0-9]*.json')):
    record = json.loads(path.read_text())
    assert record['policy_hash'] == policy_hash(policy)
    packages = [GroupPackage.model_validate(p) for p in record['packages']]
    assert len(packages) == 12
    for package in packages:
        reasons = validate_group(package,policy)
        status.append((str(package.report_date),package.group.chart_id,package.group.period,reasons))
        for cell in package.candidates:
            source = cell.value_evidence[0]
            key = (str(package.report_date), source.page_number, source.image_number, cell.column)
            if key in observed:
                raise ValueError(f'duplicate_extracted_core_cell:{key}')
            observed[key] = cell.value
missing = sorted(set(expected)-set(observed))
extra = sorted(set(observed)-set(expected))
different = [(key,str(expected[key]),str(observed[key])) for key in expected.keys() & observed.keys()
             if expected[key] != observed[key]]
summary = {'policy_hash':policy_hash(policy),'expected_cells':len(expected),
    'extracted_cells':len(observed),'missing':missing,'extra':extra,
    'different':different,'blocked_groups':[row for row in status if row[3]],
    'production_approval':False,'production_cutover':False}
(args.evidence_directory/'golden-comparison.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
