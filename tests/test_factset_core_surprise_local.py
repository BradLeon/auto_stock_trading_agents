from pathlib import Path
import json

from ats.data.sources.factset_chart_values import read_horizontal_surprise
from ats.data.sources.factset_report_layout import ChartLocation, load_layout_policy, ocr_words


def test_core_surprise_printed_labels():
    policy = load_layout_policy()
    expected = {'aug-18-2': '10.7', 'aug-18-3': '3.2',
                'sep-14-2': '10.5', 'sep-14-3': '3.2'}
    for stem, value in expected.items():
        data = (Path('/private/tmp/ats-factset-review') / (stem+'.png')).read_bytes()
        raw = json.loads((Path('tests/fixtures/factset_earnings_insight/grid-candidates') /
                          (stem+'.json')).read_text())['chart']
        chart = ChartLocation(**{**raw,'groups':tuple(raw['groups']),
            'periods':tuple(raw['periods']),'reasons':tuple(raw['reasons']),
            'words':ocr_words(data,scale=3)})
        result = read_horizontal_surprise(chart, policy, scope_id='technology')
        assert result.reasons == (), (stem,result.reasons)
        assert len(result.values) == 1 and result.values[0].value == value, (stem,result.values)
