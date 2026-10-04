from dataclasses import replace
from types import SimpleNamespace
import pytest
from ats.data.sources.factset_chart_grid import GridCell, PrintedGrid
from ats.data.sources.factset_chart_values import parse_printed_number, read_grid_candidates
from ats.data.sources.factset_report_layout import load_layout_policy
from ats.data.sources.factset_chart_values import read_horizontal_surprise
from ats.data.sources.factset_report_layout import Word

@pytest.mark.parametrize('token,value,unit',[
    ('-1.6%','-1.6','percent'),('−6.4%','-6.4','percent'),('0%','0','percent'),
    ('22.9','22.9','number'),('',None,''),('5O%',None,''),
    ('12 3%',None,''),('1.3% 4.5%',None,''),('--2%',None,''),
])
def test_numbers_do_not_get_repaired(token,value,unit):
    assert parse_printed_number(token)==(value,unit)

def test_sector_order_and_header_position_are_not_fixed():
    policy=load_layout_policy()
    labels=[items[0] for items in policy['sector_aliases'].values()]
    for names in (labels,list(reversed(labels))):
        tokens={}
        cells=[]
        for row in range(2):
            for col in range(12):
                region=(col/12,row/2,(col+1)/12,(row+1)/2)
                token=('Today' if col==0 else f'{col}.5%') if row==0 else ('' if col==0 else names[col-1])
                tokens[region]=token
                cells.append(GridCell(row,col,region,token))
        grid=PrintedGrid((0,0,1,1),tuple(cells),2,12)
        result=read_grid_candidates(b'',grid,SimpleNamespace(),policy,reader=lambda data,region,**kw:tokens[region])
        assert not result.reasons
        assert len(result.values)==11
        assert result.values[0].source_label==names[0]
        assert result.values[0].raw_token=='1.5%'
        assert all(v.status=='pending_review' for v in result.values)

def test_alias_policy_conflicts_are_rejected(tmp_path):
    import yaml
    from ats.data.sources.factset_report_layout import load_layout_policy
    policy=load_layout_policy()
    policy['sector_aliases']['GICS_15'].append('Energy')
    p=tmp_path/'catalog.yaml'
    p.write_text(yaml.safe_dump({'datasets':{'sp500_earnings_insight':{'extraction_policy':policy}}}))
    with pytest.raises(ValueError,match='alias_conflict'):
        load_layout_policy(p)

def test_surprise_matches_negative_token_to_label_without_bar_geometry():
    chart=SimpleNamespace(groups=('earnings_revenue_surprise',),words=(
        Word('Utilities',(.05,.2,.2,.23),(1,1,1)),
        Word('-1.6%',(.4,.2,.47,.23),(2,1,1)),
        Word('Energy',(.05,.4,.2,.43),(3,1,1)),
        Word('11.7%',(.7,.4,.77,.43),(4,1,1))))
    result=read_horizontal_surprise(chart,load_layout_policy())
    assert [(v.entity_id,v.value) for v in result.values]==[('GICS_55','-1.6'),('GICS_10','11.7')]
    assert result.reasons==('sector_coverage_incomplete',)
    assert result.values[0].value_region==(.4,.2,.47,.23)

def test_ambiguous_surprise_numbers_remain_failed():
    chart=SimpleNamespace(groups=('earnings_revenue_surprise',),words=(
        Word('Energy',(.05,.2,.2,.23),(1,1,1)),
        Word('1.2%',(.4,.2,.47,.23),(2,1,1)),
        Word('3.4%',(.7,.2,.77,.23),(3,1,1))))
    result=read_horizontal_surprise(chart,load_layout_policy())
    assert result.values[0].status=='extraction_failed'
    assert result.values[0].value is None


def test_core_grid_does_not_inherit_unreadable_supplementary_sector():
    tokens = [['', 'Info. Technology', 'unreadable sector'], ['Today', '21.5%', 'bad']]
    cells = tuple(GridCell(row, col, (col/3, row/2, (col+1)/3, (row+1)/2), token)
                  for row, values in enumerate(tokens) for col, token in enumerate(values))
    grid = PrintedGrid((0, 0, 1, 1), cells, 2, 3)
    lookup = {cell.region: cell.text for cell in cells}
    result = read_grid_candidates(b'', grid, SimpleNamespace(), load_layout_policy(),
        reader=lambda data, region, **kw: lookup[region], scope_id='technology')
    assert result.reasons == ()
    assert len(result.values) == 1 and result.values[0].entity_id == 'GICS_45'
    assert result.values[0].reasons == ()
    full = read_grid_candidates(b'', grid, SimpleNamespace(), load_layout_policy(),
        reader=lambda data, region, **kw: lookup[region])
    assert full.reasons


def test_core_shared_legend_ambiguity_still_blocks():
    tokens = [['unknown1', 'Info. Technology', 'unknown2'], ['Today', '21.5%', 'bad']]
    cells = tuple(GridCell(row, col, (col/3, row/2, (col+1)/3, (row+1)/2), token)
                  for row, values in enumerate(tokens) for col, token in enumerate(values))
    lookup = {cell.region: cell.text for cell in cells}
    result = read_grid_candidates(b'', PrintedGrid((0, 0, 1, 1), cells, 2, 3),
        SimpleNamespace(), load_layout_policy(),
        reader=lambda data, region, **kw: lookup[region], scope_id='technology')
    assert result.reasons == ('row_legend_column_unresolved',)
    assert not result.values
