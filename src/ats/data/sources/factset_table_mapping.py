"""Map visible table legends to registered columns, not row positions."""
from decimal import Decimal
import re

from .factset_chart_values import GridCandidates
from .factset_contracts import CellEvidence, SectorCandidate


def _legend(text: str) -> str:
    # Tesseract can attach a colour swatch to the first printed legend word.
    # Match against a closed semantic vocabulary; never change numeric tokens.
    value = re.sub(r'^[|=■▪●\s]+', '', text).strip().lower()
    known = {'below', 'in-line', 'above', 'down %', 'none %', 'up %',
             'international', 'united states', 'positive', 'negative',
             'fwd p/e', '5-yr avg.', '10-yr avg.', 'buy', 'hold', 'sell',
             'target/close', 'today'}
    separated = re.fullmatch(r'[a-z]\s+(.+)', value)
    if separated and separated[1] in known:
        return separated[1]
    if value in known or re.fullmatch(r'q[1-4]\d{2}', value):
        return value
    if value[:1] in {'a', 'e', 'm', 's', 'w'}:
        suffix = value[1:]
        if suffix in known or re.fullmatch(r'q[1-4]\d{2}', suffix):
            return suffix
    if value in {'ln-line', 'eln-line'}:
        return 'in-line'
    if value in {'lnternational', 'alnternational'}:
        return 'international'
    return value


def _column(group: str, title: str, legend: str) -> str | None:
    row = _legend(legend)
    title = title.lower()
    if group == 'forward_pe':
        return 'forward_pe' if re.fullmatch(r'fwd\s+p/e', row) else None
    if group == 'target_ratings':
        return {'buy':'buy_share','hold':'hold_share','sell':'sell_share',
                'target/close':'target_upside'}.get(row)
    if group == 'geographic_revenue_exposure':
        return {'international':'international_share','united states':'us_share'}.get(row)
    if group == 'eps_guidance' and 'percentage' in title:
        return {'positive':'positive_share','negative':'negative_share'}.get(row)
    if group == 'earnings_revenue_scorecard':
        eps = bool(re.search(r'\bearnings\b', title))
        revenue = bool(re.search(r'\brevenues?\b', title))
        if eps == revenue:
            return None
        suffix = {'above':'above','in-line':'inline','below':'below'}.get(row)
        return ('eps_' if eps else 'revenue_')+suffix if suffix else None
    if group == 'net_profit_margin':
        breadth = {'up %':'increase_share','none %':'unchanged_share','down %':'decrease_share'}
        if row in breadth:
            return breadth[row]
        current = re.search(r'\bq([1-4])(\d{2})\b', title)
        if current and row == current[0]:
            return 'net_margin'
    return None


def map_table(grid: GridCandidates, policy: dict) -> tuple[tuple[SectorCandidate, ...], tuple[str, ...]]:
    """Produce unapproved cells. Unknown rows stay visible as stage failures."""
    chart = grid.chart
    if len(chart.groups) != 1:
        return (), ('metric_group_unresolved',)
    group = chart.groups[0]
    rule = policy['groups'][group]
    reasons = list(grid.reasons)
    cells = []
    for cell in grid.values:
        column = _column(group, chart.title, cell.row_label)
        if column is None:
            # Explicit P/E historical averages and margin previous-quarter
            # columns are comparison data, not separate current observations.
            row = _legend(cell.row_label)
            if group == 'forward_pe' and re.fullmatch(r'(5|10)-yr avg\.', row):
                continue
            previous = re.search(r'\bvs\.\s*(q[1-4]\d{2})\b', chart.title, re.I)
            if group == 'net_profit_margin' and previous and row == previous[1].lower():
                continue
            reasons.append('unmapped_source_row:'+cell.row_label)
            continue
        unit = rule['column_units'][column]
        errors = list(cell.reasons)
        value = Decimal(cell.value) if cell.value is not None else None
        if unit in {'percent', 'ratio'} and cell.source_unit != 'percent':
            errors.append('printed_percent_unit_missing')
        if unit == 'ratio' and value is not None:
            value /= 100
        if unit == 'multiple' and cell.source_unit != 'number':
            errors.append('printed_multiple_unit_mismatch')

        def evidence(token, region):
            return CellEvidence(page_number=chart.page_number,image_number=chart.image_number,
                image_hash=chart.image_hash,region=region,raw_token=token,method='local_grid_ocr')

        cells.append(SectorCandidate(entity_id=cell.entity_id,column=column,value=value,unit=unit,
            source_label=cell.source_label,label_evidence=evidence(cell.source_label,cell.label_region),
            value_evidence=(evidence(cell.raw_token,cell.value_region),),
            status='extraction_failed' if errors else cell.status,reasons=tuple(errors)))
    return tuple(cells), tuple(sorted(set(reasons)))
