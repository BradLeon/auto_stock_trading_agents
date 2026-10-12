from pathlib import Path
import json
from datetime import datetime, timezone
from hashlib import sha256

import yaml

from ats.data.sources.factset_earnings_insight import FactSetFetch, inspect_pdf, STABLE_URL

from ats.data.sources.factset_chart_values import read_guidance_counts
from ats.data.sources.factset_report_layout import ChartLocation, load_layout_policy, ocr_words


def test_core_printed_guidance_counts_from_both_pdfs():
    policy = load_layout_policy()
    expected = {'aug-24-2': (6, 39), 'aug-27-2': (4, 29),
                'sep-20-2': (6, 44), 'sep-23-2': (4, 29)}
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
    for stem, values in expected.items():
        raw = json.loads((Path('tests/fixtures/factset_earnings_insight/grid-candidates') /
                          (stem+'.json')).read_text())['chart']
        images = [image for image in documents[stem.split('-')[0]].images
                  if image.page_number == raw['page_number'] and image.image_number == raw['image_number']]
        assert len(images) == 1
        data = images[0].data
        assert sha256(data).hexdigest() == raw['image_hash']
        chart = ChartLocation(**{**raw, 'groups':tuple(raw['groups']),
            'periods':tuple(raw['periods']), 'reasons':tuple(raw['reasons']),
            'words':ocr_words(data, scale=3)})
        cells, reasons = read_guidance_counts(data, chart, policy)
        assert not reasons, (stem,reasons)
        assert all(c.value is not None for c in cells), (stem,[(c.column,c.value_evidence[0].raw_token,c.value_evidence[0].region) for c in cells])
        assert tuple(int(c.value) for c in cells) == values, (stem,cells)
