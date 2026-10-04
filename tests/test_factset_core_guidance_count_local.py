from pathlib import Path
import json

from ats.data.sources.factset_chart_values import read_guidance_counts
from ats.data.sources.factset_report_layout import ChartLocation, load_layout_policy, ocr_words


def test_core_printed_guidance_counts_from_both_pdfs():
    policy = load_layout_policy()
    expected = {'aug-24-2': (6, 39), 'aug-27-2': (4, 29),
                'sep-20-2': (6, 44), 'sep-23-2': (4, 29)}
    for stem, values in expected.items():
        data = (Path('/private/tmp/ats-factset-review') / (stem+'.png')).read_bytes()
        raw = json.loads((Path('tests/fixtures/factset_earnings_insight/grid-candidates') /
                          (stem+'.json')).read_text())['chart']
        chart = ChartLocation(**{**raw, 'groups':tuple(raw['groups']),
            'periods':tuple(raw['periods']), 'reasons':tuple(raw['reasons']),
            'words':ocr_words(data, scale=3)})
        cells, reasons = read_guidance_counts(data, chart, policy)
        assert not reasons, (stem,reasons)
        assert all(c.value is not None for c in cells), (stem,[(c.column,c.value_evidence[0].raw_token,c.value_evidence[0].region) for c in cells])
        assert tuple(int(c.value) for c in cells) == values, (stem,cells)
