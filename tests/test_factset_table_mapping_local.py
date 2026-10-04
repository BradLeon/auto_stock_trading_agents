from dataclasses import replace
from decimal import Decimal
import pytest
from ats.data.sources.factset_chart_values import GridCandidates,GridValue
from ats.data.sources.factset_report_layout import ChartLocation,load_layout_policy
from ats.data.sources.factset_table_mapping import map_table

@pytest.mark.parametrize('group,title,row,column,token,unit,value',[
    ('forward_pe','Sector Forward 12-Month P/E','Fwd P/E','forward_pe','21.3','number','21.3'),
    ('target_ratings','Ratings','Buy','buy_share','69%','percent','.69'),
    ('target_ratings','Targets','Target/Close','target_upside','26.0%','percent','26'),
    ('geographic_revenue_exposure','Sector Geographic','United States','us_share','98%','percent','.98'),
    ('eps_guidance','Percentage of Guidance','Positive','positive_share','0%','percent','0'),
    ('earnings_revenue_scorecard','Earnings Above, In-Line, Below','Above','eps_above','96%','percent','.96'),
    ('earnings_revenue_scorecard','Revenues Above, In-Line, Below','In-Line','revenue_inline','0%','percent','0'),
    ('net_profit_margin','Margins Q226 vs. Q225','Q226','net_margin','34.1%','percent','34.1'),
    ('net_profit_margin','Increase or Decrease','Down %','decrease_share','19%','percent','.19'),
])
def test_semantic_rows(group,title,row,column,token,unit,value):
    chart=ChartLocation(5,7,'a'*64,title,(.1,.1,.8,.2),(group,),(),'', '', 'located',(),())
    cell=GridValue('GICS_10','Energy',row,token,token.rstrip('%'),unit,(.1,.3,.2,.4),(.2,.3,.3,.4))
    values,reasons=map_table(GridCandidates(chart,(cell,),()),load_layout_policy())
    assert not reasons and len(values)==1
    assert values[0].column==column and values[0].value==Decimal(value)
    bad=replace(cell,row_label='unrelated metric')
    values,reasons=map_table(GridCandidates(chart,(bad,),()),load_layout_policy())
    assert not values and reasons
