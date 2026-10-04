"""Independent annotation integrity, not automatic extraction acceptance."""
from pathlib import Path
from collections import Counter
import yaml
from ats.data.sources.factset_report_layout import load_layout_policy
from ats.data.sources.factset_earnings_charts import normalize_chart_text

ROOT=Path(__file__).parent/'fixtures/factset_earnings_insight/acceptance'

def test_independently_read_inventory_has_385_cells_per_report():
    policy=load_layout_policy()
    aliases={normalize_chart_text(label):entity for entity,labels in policy['sector_aliases'].items() for label in labels}
    totals=Counter()
    paths=['valuation-ratings-visual.yaml','scorecard-visual.yaml','margin-visual.yaml',
        'surprise-visual.yaml','guidance-visual.yaml']
    for name in paths:
        fixture=yaml.safe_load((ROOT/name).read_text())
        assert fixture['production_review_approval'] is False
        for chart in fixture['charts']:
            assert len(chart['rows'])==11
            assert {aliases[normalize_chart_text(label)] for label in chart['rows']}==set(policy['sector_aliases'])
            for values in chart['rows'].values():
                assert len(values)==len(chart['columns'])
                totals[chart['report_date']]+=len(values)
    original=yaml.safe_load((ROOT/'cross-report-visual-partial.yaml').read_text())
    for table in original['tables']:
        assert len(table['values'])==11
        totals[table['report_date']]+=len(table['values'])
    assert totals=={'2026-08-28':385,'2026-09-18':385}
