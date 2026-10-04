"""Cached source OCR replay; independent golden is comparison-only, never input."""
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import yaml

from ats.data.sources.factset_chart_grid import GridCell, PrintedGrid
from ats.data.sources.factset_chart_values import read_grid_candidates
from ats.data.sources.factset_growth import map_growth, assemble_growth_packages
from ats.data.sources.factset_contracts import GroupIdentity, validate_group
from ats.data.sources.factset_report_layout import ChartLocation, load_layout_policy


def test_both_reports_core_growth_cached_source_replay():
    root = Path('tests/fixtures/factset_earnings_insight')
    policy = load_layout_policy()
    extracted = {}
    by_report = {}
    # Inventory is source discovery, not the success subset or golden table.
    for path in sorted((root / 'grid-candidates').glob('*.json')):
        raw = json.loads(path.read_text())
        chart = raw['chart']
        if chart['groups'] != ['earnings_revenue_growth'] or chart['period_basis'] not in {'target_quarter', 'calendar_year'}:
            continue
        chart = ChartLocation(**{**chart, 'groups': tuple(chart['groups']),
            'periods': tuple(chart['periods']), 'reasons': tuple(chart['reasons']), 'words': ()})
        report_date = date(2026, 8, 28) if path.stem.startswith('aug-') else date(2026, 9, 18)
        if not raw['grids']:
            continue  # Non-sector plots have no grid; golden comparison still checks all core targets.
        assert len(raw['grids']) == 1
        data = raw['grids'][0]
        cells = tuple(GridCell(c['row'], c['column'], tuple(c['region']), c['text']) for c in data['cells'])
        grid = PrintedGrid(tuple(data['region']), cells, data['row_count'], data['column_count'])
        lookup = {c.region: c.text for c in cells}
        candidates = read_grid_candidates(b'', grid, chart, policy, scope_id='technology',
            reader=lambda data, region, **kw: lookup[region])
        result, reasons = map_growth(candidates, report_date=report_date, policy=policy)
        by_report.setdefault(report_date, []).append(candidates)
        assert not reasons, (path.name, reasons)
        assert len(result) == 1, path.name
        cell = result[0]
        assert not cell.reasons and cell.value is not None, (path.name, cell)
        extracted[(report_date.isoformat(), chart.page_number, chart.image_number)] = cell
    golden = yaml.safe_load((root/'acceptance/cross-report-visual-partial.yaml').read_text())
    expected = {}
    for table in golden['tables']:
        if table.get('source_row') != 'Today':
            continue
        labels = policy['sector_aliases']['GICS_45']
        value = next(v for label, v in table['values'].items() if label in labels)
        expected[(table['report_date'], table['page'], table['image'])] = Decimal(str(value))
    assert set(extracted) == set(expected)
    assert len(expected) == 16
    for key, value in expected.items():
        assert extracted[key].value == value, key
    # Independently fixed disclosed growth periods, never inferred from success.
    groups = [GroupIdentity(chart_id='earnings_revenue_growth', period=period,
        period_basis=basis, scope_id='technology', scope_version=policy['scope_policy']['version'],
        entity_ids=('GICS_45',)) for period, basis in (
            ('2026Q2','target_quarter'), ('2026Q3','target_quarter'),
            ('2026','calendar_year'), ('2027','calendar_year'))]
    for stamp, grids in by_report.items():
        packages = assemble_growth_packages(tuple(grids), expected_groups=groups,
            report_date=stamp, document_version=stamp.isoformat(), pdf_hash='b'*64, policy=policy)
        assert len(packages) == 4
        assert all(validate_group(p, policy) == [] for p in packages)
        assert all(c.comparison_label for p in packages for c in p.candidates)
        missing = assemble_growth_packages(tuple(grids[:-1]), expected_groups=groups,
            report_date=stamp, document_version=stamp.isoformat(), pdf_hash='b'*64, policy=policy)
        assert len(missing) == 4
        assert any(validate_group(p, policy) for p in missing)
