from types import SimpleNamespace
import pytest
from ats.data.sources.factset_report_layout import (
    Word, target_periods, locate_chart, discover_charts, load_layout_policy,
    declared_report_groups, policy_hash,
)
from datetime import date
from pathlib import Path
import yaml

@pytest.mark.parametrize('title,page,expected,basis', [
    ('S&P 500 Earnings Growth (Y/Y): Q3 2026', '', ('2026Q3',), 'target_quarter'),
    ('Net Profit Margins: Q226 vs. Q225', '', ('2026Q2',), 'target_quarter'),
    ('Revenue Growth: CY 2027', '', ('2027',), 'calendar_year'),
    ('Revenue Growth: CY 2026/2027', '', ('2026','2027'), 'calendar_year_range'),
    ('Positive & Negative FY Guidance', 'FY 2026 / 2027: EPS Guidance', ('2026/2027',), 'mixed_fiscal_years'),
    ('Forward 12-Month P/E Ratio', '', (), ''),
    ('Earnings Growth: Q1 2026 - Q4 2026', '', ('2026Q1','2026Q2','2026Q3','2026Q4'), 'quarter_range'),
    ('Positive & Negative Q3 Guidance', 'Q3 2026: EPS Guidance', ('2026Q3',), 'target_quarter'),
    ('Positive & Negative Q3 Guidance', 'Q3 2026 vs Q3 2025', (), ''),
])
def test_periods_are_not_invented(title,page,expected,basis):
    result=target_periods(title,page)
    assert result[:2] == (expected,basis)

def reader(data,**kwargs):
    return (Word(data.decode(), (.1,.02,.9,.08), (1,1,1)),)

def test_discovery_independent_of_page_and_image_order():
    policy=load_layout_policy()
    data=b'S&P 500 Earnings Growth (Y/Y): Q3 2026'
    for page,image in [(1,8),(45,2)]:
        result=locate_chart(SimpleNamespace(data=data,page_number=page,image_number=image),'',policy,reader=reader)
        assert result.groups == ('earnings_revenue_growth',)
        assert result.periods == ('2026Q3',)
        assert result.page_number == page

def test_budget_rejected_before_ocr():
    document=SimpleNamespace(images=[None]*161)
    with pytest.raises(ValueError,match='budget'):
        discover_charts(document,reader=reader)

def test_unknown_chart_is_not_absent_data():
    result=locate_chart(SimpleNamespace(data=b'Unknown topic',page_number=3,image_number=2),'',load_layout_policy(),reader=reader)
    assert result.status == 'unclassified'
    assert result.reasons == ('title_not_matched',)

def test_heading_fragments_are_joined_spatially():
    def fragmented(data, **kwargs):
        return (Word('S&P 500 Revenues Above, In-Line,', (.1,.02,.55,.06), (1,1,1)),
                Word('and Below Estimates: Q2 2026', (.56,.021,.9,.061), (2,1,1)))
    result=locate_chart(SimpleNamespace(data=b'x',page_number=1,image_number=1),'',load_layout_policy(),reader=fragmented)
    assert result.groups == ('earnings_revenue_scorecard',)
    assert result.periods == ('2026Q2',)


def test_company_contributor_chart_is_not_sector_growth():
    result = locate_chart(SimpleNamespace(
        data=b'S&P 500: Top 5 Contributors to Q2 2026 Earnings Growth (Y/Y)',
        page_number=5, image_number=3), '', load_layout_policy(), reader=reader)
    assert result.groups == ()
    assert 'Contributors' in result.title


def test_registered_report_inventory_is_independent_and_same_policy_for_both_reports():
    inventory = yaml.safe_load(Path(
        'tests/fixtures/factset_earnings_insight/acceptance/core-inventory.yaml').read_text())
    policy = load_layout_policy()
    assert policy_hash(policy)
    for report_date in ('2026-08-28', '2026-09-18'):
        declared = declared_report_groups(date.fromisoformat(report_date), policy=policy)
        expected = {(chart, row['period'], row['basis'])
                    for chart, periods in inventory['reports'][report_date]['groups'].items()
                    for row in periods}
        assert {(group.chart_id, group.period, group.period_basis)
                for group in declared} == expected
        assert all(group.scope_id == 'technology' and group.entity_ids == ('GICS_45',)
                   for group in declared)
    with pytest.raises(ValueError, match='inventory_not_registered'):
        declared_report_groups(date(2026, 10, 2), policy=policy)
