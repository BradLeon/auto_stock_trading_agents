from datetime import date
from dataclasses import replace
from ats.data.sources.factset_chart_values import GridCandidates, GridValue
from ats.data.sources.factset_report_layout import ChartLocation
from ats.data.sources.factset_growth import map_growth, comparison_date

def fixture():
    chart=ChartLocation(22,3,'a'*64,'Q3 2026 Earnings Growth Y/Y',(.1,.1,.8,.2),
        ('earnings_revenue_growth',),('2026Q3',),'target_quarter','Q3 2026','located',(),())
    cell=GridValue('GICS_10','Energy','Today','-7.4%','-7.4','percent',(.1,.4,.2,.5),(.2,.4,.3,.5))
    return GridCandidates(chart,(cell,replace(cell,row_label='6/30/2026',raw_token='-9.1%',value='-9.1')),())

def test_maps_current_only_and_keeps_comparison():
    result,reasons=map_growth(fixture(),report_date=date(2026,9,18))
    assert not reasons and len(result)==1
    assert str(result[0].value)=='-7.4'
    assert result[0].comparison_date==date(2026,6,30)
    assert result[0].comparison_token=='-9.1%'
    assert result[0].column=='eps_growth'

def test_no_order_or_page_dependence():
    grid=fixture()
    changed=replace(grid,chart=replace(grid.chart,page_number=9,image_number=7,
        title='CY 2027 Revenue Growth Y/Y'),values=tuple(reversed(grid.values)))
    result,reasons=map_growth(changed,report_date=date(2026,9,18))
    assert not reasons and result[0].column=='revenue_growth'
    assert result[0].value_evidence[0].page_number==9

def test_ambiguous_current_and_dates_fail_closed():
    grid=fixture()
    result,reasons=map_growth(replace(grid,values=(grid.values[1],)),report_date=date(2026,9,18))
    assert not result and 'current_column_not_located' in reasons
    for token in ['6/30','2/30/2026','12/31/2026']:
        assert comparison_date(token,date(2026,9,18)) is None


def test_separated_legend_swatch_does_not_change_numeric_tokens():
    grid = fixture()
    grid = replace(grid, values=(replace(grid.values[0], row_label='| m Today'), grid.values[1]))
    cells, reasons = map_growth(grid, report_date=date(2026,9,18))
    assert not reasons and cells[0].value_evidence[0].raw_token == '-7.4%'


def test_short_comparison_date_requires_policy_and_handles_rollover():
    from ats.data.sources.factset_report_layout import load_layout_policy
    policy = load_layout_policy()
    rule = policy['comparison_date_policy']
    assert comparison_date('30-Jun', date(2026,9,18)) is None
    assert comparison_date('30-Jun', date(2026,9,18), short_date_policy=rule) == date(2026,6,30)
    assert comparison_date('31-Dec', date(2027,1,8), short_date_policy=rule) == date(2026,12,31)
    assert comparison_date('31-Feb', date(2026,9,18), short_date_policy=rule) is None
    grid = fixture()
    grid = replace(grid, values=(grid.values[0], replace(grid.values[1], row_label='| 30-Jun')))
    cells, reasons = map_growth(grid, report_date=date(2026,9,18), policy=policy)
    assert not reasons and cells[0].comparison_date == date(2026,6,30)
