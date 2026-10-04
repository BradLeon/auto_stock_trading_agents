"""Fresh source-only replay; no golden input, approvals or production writes."""
from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile

from ats.data.sources.factset_earnings_insight import FactSetFetch, inspect_pdf, STABLE_URL
from ats.data.sources.factset_report_layout import discover_charts, load_layout_policy, policy_hash
from ats.data.sources.factset_chart_grid import locate_printed_grids
from ats.data.sources.factset_chart_values import read_grid_candidates
from ats.data.sources.factset_contracts import GroupIdentity, validate_group
from ats.data.sources.factset_growth import assemble_growth_packages

paths = [
    'var/data_artifacts/ae/ae262138891244678730deaaef8607466b919f058038578fbea3b5454d95f381.bin',
    'var/data_artifacts/f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin',
]
policy = load_layout_policy()
out = Path(tempfile.mkdtemp(prefix='factset-core-growth-', dir='/private/tmp'))
print('evidence_directory', out, 'policy_hash', policy_hash(policy), flush=True)
groups = [GroupIdentity(chart_id='earnings_revenue_growth', period=period,
    period_basis=basis, scope_id='technology', scope_version=policy['scope_policy']['version'],
    entity_ids=('GICS_45',)) for period, basis in (
        ('2026Q2','target_quarter'), ('2026Q3','target_quarter'),
        ('2026','calendar_year'), ('2027','calendar_year'))]
for path in paths:
    source = FactSetFetch(STABLE_URL, STABLE_URL, 200, '', '', 'application/pdf',
        Path(path).read_bytes(), datetime.now(timezone.utc))
    doc = inspect_pdf(source)
    print('discover', doc.report_date, len(doc.images), flush=True)
    charts = discover_charts(doc, policy=policy)
    image_by_id = {(image.page_number, image.image_number): image for image in doc.images}
    candidates = []
    for chart in charts:
        if chart.groups != ('earnings_revenue_growth',) or chart.period_basis not in {'target_quarter', 'calendar_year'}:
            continue
        data = image_by_id[chart.page_number, chart.image_number].data
        grids = locate_printed_grids(data)
        for grid in grids:
            candidates.append(read_grid_candidates(data, grid, chart, policy, scope_id='technology'))
        print('growth_chart', doc.report_date, chart.periods, chart.page_number,
              chart.image_number, 'grids', len(grids), flush=True)
    packages = assemble_growth_packages(tuple(candidates), expected_groups=groups,
        report_date=doc.report_date, document_version=f'isolated:{doc.pdf_hash}',
        pdf_hash=doc.pdf_hash, policy=policy)
    output = {'pdf_hash': doc.pdf_hash, 'policy_hash': policy_hash(policy),
        'packages': [p.model_dump(mode='json') for p in packages],
        'validation': [validate_group(p, policy) for p in packages],
        'source_only': True, 'production_approval': False}
    (out / f'{doc.report_date}.json').write_text(json.dumps(output, indent=2))
    print('result', doc.report_date, output['validation'],
        [(p.group.period, [(c.column, str(c.value)) for c in p.candidates]) for p in packages], flush=True)
