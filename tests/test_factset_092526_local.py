"""Local, licensed-PDF regression for the first unseen monthly report."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ats.data.sources.factset_contracts import validate_group
from ats.data.sources.factset_earnings_insight import FactSetFetch, STABLE_URL, inspect_pdf
from ats.data.sources.factset_earnings_text import (
    extract_index_text_semantic, validate_index_candidates,
)
from ats.data.sources.factset_report_layout import (
    ChartLocation, discover_charts, discovered_core_groups, load_layout_policy,
)
from ats.data.sources.factset_chart_grid import GridCell, PrintedGrid
from ats.data.sources.factset_chart_values import read_index_grid_candidates
from ats.data.sources.factset_semantic import extract_sector_packages


PDF = Path('var/data_artifacts/da/da6bfc99301d5a908705757612ee534018ab72504d14b79396903399b7534958.bin')


@pytest.fixture(scope='module')
def report():
    if not PDF.exists():
        pytest.skip('licensed FactSet PDF is only available in the local artifact store')
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    return inspect_pdf(FactSetFetch(
        STABLE_URL, STABLE_URL, 200, '', '', 'application/pdf', PDF.read_bytes(), now))


def test_index_ratings_use_complete_statement_not_split_leading_digit(report):
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    run = extract_index_text_semantic(
        report, document_id='doc', version_id='version', known_at=now)
    validated = validate_index_candidates(run)
    ratings = {candidate.metric_id: candidate for candidate in validated.candidates
               if candidate.metric_id.startswith('consensus.rating.')}
    assert {key: candidate.value for key, candidate in ratings.items()} == {
        'consensus.rating.buy_share': 0.599,
        'consensus.rating.hold_share': 0.355,
        'consensus.rating.sell_share': 0.047,
    }
    assert all(candidate.status.value == 'accepted' for candidate in ratings.values())


def test_index_unreadable_decimal_requires_two_scale_agreement():
    grid = PrintedGrid(
        region=(0, 0, 1, 1), row_count=2, column_count=2,
        cells=(
            GridCell(0, 0, (0, 0, .5, .5), ''),
            GridCell(0, 1, (.5, 0, 1, .5), 'S&P 500'),
            GridCell(1, 0, (0, .5, .5, 1), 'Today'),
            GridCell(1, 1, (.5, .5, 1, 1), '29. 1%'),
        ))
    chart = ChartLocation(
        page_number=1, image_number=1, image_hash='a' * 64,
        title='S&P 500 Earnings Growth (Y/Y): Q3 2026',
        title_region=(0, 0, 1, .1), groups=('earnings_revenue_growth',),
        periods=('2026Q3',), period_basis='target_quarter', period_evidence='',
        status='located', reasons=(), words=())

    def reader(_data, region, *, scale, timeout):
        if region[0] == 0:
            return 'Today'
        return '29.1%' if scale in (3, 6) else '29. 1%'

    result = read_index_grid_candidates(
        b'', grid, chart, load_layout_policy(), reader=reader)
    assert result.reasons == ()
    assert [(row.raw_token, row.value, row.source_unit) for row in result.values] == [
        ('29.1%', '29.1', 'percent')]

    def conflicting_reader(data, region, *, scale, timeout):
        if region[0] == 0:
            return 'Today'
        return '29.2%' if scale == 6 else reader(data, region, scale=scale, timeout=timeout)

    blocked = read_index_grid_candidates(
        b'', grid, chart, load_layout_policy(), reader=conflicting_reader)
    assert blocked.values[0].value is None
    assert 'numeric_token_unreadable' in blocked.values[0].reasons


def test_repeated_ratings_charts_are_one_technology_group(report):
    policy = load_layout_policy()
    inventory = discover_charts(report, policy=policy)
    group = next(group for group in discovered_core_groups(
        report.report_date, inventory, policy=policy) if group.chart_id == 'target_ratings')
    packages, _ = extract_sector_packages(
        report, document_version='version', expected_groups=[group], policy=policy,
        chart_inventory=inventory)
    package, = packages
    assert len(package.candidates) == 4
    assert not package.stage_errors
    assert not validate_group(package, policy)
