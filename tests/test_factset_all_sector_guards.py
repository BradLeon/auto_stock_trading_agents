"""A clean-looking but wrong OCR number must not be published for the eleven-sector scope.

2026-11-03: the consensus-ratings chart printed 7.4% for Consumer Staples; one OCR scale returned "74%", the
others "TA%", "TA", "14%". The token parsed, so it was accepted and published as 74."""
from decimal import Decimal
from types import SimpleNamespace

from ats.data.sources.factset_chart_grid import GridCell, PrintedGrid
from ats.data.sources.factset_chart_values import read_grid_candidates
from ats.data.sources.factset_contracts import validate_group
from ats.data.sources.factset_report_layout import load_layout_policy
from test_factset_reviews_local import package


def _grid(policy, row_reads):
    """Two rows: the sector header, then one value per sector. row_reads maps column -> {scale: token}."""
    names = [items[0] for items in policy['sector_aliases'].values()]
    reads, cells = {}, []
    for row in range(2):
        for col in range(12):
            region = (col / 12, row / 2, (col + 1) / 12, (row + 1) / 2)
            if row == 0:
                token = 'Today' if col == 0 else names[col - 1]
                reads[region] = {'default': token}
            else:
                reads[region] = row_reads.get(col, {'default': '' if col == 0 else f'{col}.5%'})
            cells.append(GridCell(row, col, region, ''))
    return PrintedGrid((0, 0, 1, 1), tuple(cells), 2, 12), reads


def _read(row_reads, scope_id='all_sectors'):
    policy = load_layout_policy()
    grid, reads = _grid(policy, row_reads)
    calls = []

    def reader(data, region, scale, timeout):
        calls.append(scale)
        return reads[region].get(scale, reads[region]['default'])

    return read_grid_candidates(b'', grid, SimpleNamespace(), policy, reader=reader, scope_id=scope_id), calls


def test_a_number_that_only_one_scale_reads_is_not_published():
    result, _ = _read({3: {'default': '74%', 3: 'TA', 6: 'TA%'}})
    cell = next(v for v in result.values if v.raw_token.endswith('74%'))
    assert cell.value is None and cell.status == 'extraction_failed'
    assert cell.reasons == ('numeric_token_unreadable',)
    assert all(v.status == 'pending_review' for v in result.values if v is not cell)


def test_a_number_confirmed_by_a_second_scale_is_kept():
    result, _ = _read({3: {'default': '7.4%', 3: '7.4%', 6: 'TA%'}})
    cell = next(v for v in result.values if v.raw_token == '7.4%')
    assert cell.value == '7.4' and cell.status == 'pending_review'


def test_two_scales_that_disagree_do_not_confirm_each_other():
    result, _ = _read({3: {'default': '74%', 3: '14%', 6: '7.4%'}})
    cell = next(v for v in result.values if v.raw_token.endswith('74%'))
    assert cell.status == 'extraction_failed'


def test_the_technology_scope_keeps_its_single_read():
    # IT is released through its own independent review, so it is not read three times.
    result, calls = _read({}, scope_id='technology')
    assert result.values and sorted(set(calls)) == [4]


def test_a_dropped_decimal_point_breaks_the_column_format():
    policy = load_layout_policy()
    p = package()
    odd = p.candidates[0].model_copy(update={
        'value': Decimal('20'),
        'value_evidence': (p.candidates[0].value_evidence[0].model_copy(update={'raw_token': '20'}),)})
    changed = p.model_copy(update={'candidates': (odd,) + p.candidates[1:]})
    assert validate_group(p, policy) == []
    assert validate_group(changed, policy) == ['numeric_format_inconsistent']


def test_a_column_of_whole_numbers_is_not_a_format_problem():
    policy = load_layout_policy()
    p = package()
    whole = tuple(c.model_copy(update={
        'value': Decimal('20'),
        'value_evidence': (c.value_evidence[0].model_copy(update={'raw_token': '20'}),)}) for c in p.candidates)
    assert validate_group(p.model_copy(update={'candidates': whole}), policy) == []
