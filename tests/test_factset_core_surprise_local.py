from pathlib import Path
import json
from datetime import datetime, timezone
from hashlib import sha256

import yaml

from ats.data.sources.factset_earnings_insight import FactSetFetch, inspect_pdf, STABLE_URL

from ats.data.sources.factset_chart_values import read_horizontal_surprise
from ats.data.sources.factset_report_layout import ChartLocation, load_layout_policy, ocr_words


def test_core_surprise_printed_labels():
    policy = load_layout_policy()
    expected = {'aug-18-2': '10.7', 'aug-18-3': '3.2',
                'sep-14-2': '10.5', 'sep-14-3': '3.2'}
    # Reopen the original content-addressed PDFs; no dependency on a deleted
    # investigator temp directory, and no fabricated chart image.
    manifest = yaml.safe_load(Path('tests/fixtures/factset_earnings_insight/acceptance/dual-report-fixtures.yaml').read_text())
    documents = {}
    for prefix, report_date in [('aug', '2026-08-28'), ('sep', '2026-09-18')]:
        report = manifest['reports'][report_date]
        body = Path(report['pdf_path']).read_bytes()
        assert sha256(body).hexdigest() == report['pdf_sha256']
        documents[prefix] = inspect_pdf(FactSetFetch(STABLE_URL, STABLE_URL, 200, '', '',
            'application/pdf', body, datetime.now(timezone.utc)))
    for stem, value in expected.items():
        raw = json.loads((Path('tests/fixtures/factset_earnings_insight/grid-candidates') /
                          (stem+'.json')).read_text())['chart']
        images = [image for image in documents[stem.split('-')[0]].images
                  if image.page_number == raw['page_number'] and image.image_number == raw['image_number']]
        assert len(images) == 1
        data = images[0].data
        assert sha256(data).hexdigest() == raw['image_hash']
        chart = ChartLocation(**{**raw,'groups':tuple(raw['groups']),
            'periods':tuple(raw['periods']),'reasons':tuple(raw['reasons']),
            'words':ocr_words(data,scale=3)})
        result = read_horizontal_surprise(chart, policy, scope_id='technology')
        assert result.reasons == (), (stem,result.reasons)
        assert len(result.values) == 1 and result.values[0].value == value, (stem,result.values)
