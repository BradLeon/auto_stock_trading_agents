"""Semantic growth mapping from source grid candidates, without dated layouts."""

from datetime import date
from decimal import Decimal
import re

from .factset_chart_values import GridCandidates
from .factset_contracts import CellEvidence, SectorCandidate
from .factset_table_mapping import _legend


def comparison_date(token: str, report_date: date, *, short_date_policy: str = '') -> date | None:
    """Full dates are literal; short dates need an explicit registered policy.

    The normalized year belongs to the comparison snapshot, never the target
    fiscal/calendar period. Raw comparison tokens remain attached to the cell.
    """
    token = token.strip()
    for pattern in (r"(\d{1,2})/(\d{1,2})/(20\d{2})", r"(\d{1,2})/(\d{1,2})/(\d{2})"):
        match = re.fullmatch(pattern, token)
        if match:
            month, day, year = map(int, match.groups())
            year = year + 2000 if year < 100 else year
            try:
                result = date(year, month, day)
            except ValueError:
                return None
            return result if result <= report_date else None
    if short_date_policy == 'latest_occurrence_on_or_before_report':
        short = re.fullmatch(r'(\d{1,2})-([A-Za-z]{3})', token)
        months = dict(zip(('jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'), range(1, 13)))
        if short and short[2].lower() in months:
            for year in (report_date.year, report_date.year - 1):
                try:
                    result = date(year, months[short[2].lower()], int(short[1]))
                except ValueError:
                    continue
                if result <= report_date and (report_date - result).days <= 366:
                    return result
    return None


def map_growth(
    grid: GridCandidates, *, report_date: date, policy: dict | None = None
) -> tuple[tuple[SectorCandidate, ...], tuple[str, ...]]:
    """Map Today rows only; retain prior dates as evidence, not new observations.

    Caller groups these cells by the ChartLocation's independently discovered
    target period. This mapper does not approve or infer estimate state.
    """
    chart = grid.chart
    if chart.groups != ("earnings_revenue_growth",):
        return (), ("not_growth_chart",)
    words = chart.title.lower()
    eps, revenue = bool(re.search(r"\bearnings\b", words)), bool(re.search(r"\brevenue\b", words))
    if eps == revenue:
        return (), ("growth_metric_ambiguous",)
    column = "eps_growth" if eps else "revenue_growth"
    reasons = list(grid.reasons)
    current, prior = {}, {}
    for cell in grid.values:
        # OCR legend bullets are allowed, arbitrary prefix text is not.
        label = _legend(cell.row_label)
        if label.lower() == "today":
            if cell.entity_id in current:
                reasons.append("duplicate_current_row")
            current[cell.entity_id] = cell
        else:
            when = comparison_date(label, report_date, short_date_policy=(policy or {}).get('comparison_date_policy', ''))
            if when is None:
                reasons.append("comparison_date_unresolved")
                continue
            if cell.entity_id in prior:
                reasons.append("multiple_comparison_rows")
            prior[cell.entity_id] = (when, cell.raw_token, cell.row_label)
    if not current:
        reasons.append("current_column_not_located")
    result = []
    for entity, cell in current.items():

        def evidence(token, region):
            return CellEvidence(
                page_number=chart.page_number,
                image_number=chart.image_number,
                image_hash=chart.image_hash,
                region=region,
                raw_token=token,
                method="local_grid_ocr",
            )

        cell_reasons = list(cell.reasons)
        if cell.source_unit != "percent":
            cell_reasons.append("growth_percent_unit_missing")
        previous = prior.get(entity)
        result.append(
            SectorCandidate(
                entity_id=entity,
                column=column,
                value=Decimal(cell.value) if cell.value is not None else None,
                unit="percent",
                source_label=cell.source_label,
                label_evidence=evidence(cell.source_label, cell.label_region),
                value_evidence=(evidence(cell.raw_token, cell.value_region),),
                status="extraction_failed" if cell_reasons else cell.status,
                reasons=tuple(cell_reasons),
                comparison_date=previous[0] if previous else None,
                comparison_token=previous[1] if previous else None,
                comparison_label=previous[2] if previous else None,
            )
        )
    return tuple(result), tuple(sorted(set(reasons)))


def assemble_growth_packages(
    grids: tuple[GridCandidates, ...], *, expected_groups, report_date: date,
    document_version: str, pdf_hash: str, policy: dict,
    extractor_version: str = 'factset-semantic-growth-v2',
):
    """Assemble against an independent inventory; missing charts stay missing.

    Input grids must already be read in the declared entity scope. This stage
    never reads a golden or confers approval. Different periods stay separate.
    """
    from .factset_contracts import GroupPackage
    from .factset_report_layout import policy_hash

    if len({group.key for group in expected_groups}) != len(expected_groups):
        raise ValueError('duplicate_expected_growth_group')
    packages = []
    for group in expected_groups:
        if group.chart_id != 'earnings_revenue_growth':
            raise ValueError('not_growth_inventory')
        candidates, errors = [], []
        for grid in grids:
            chart = grid.chart
            if (chart.groups != ('earnings_revenue_growth',)
                    or chart.period_basis != group.period_basis
                    or chart.periods != (group.period,)):
                continue
            cells, reasons = map_growth(grid, report_date=report_date, policy=policy)
            # Do not hide supplemental rows from a wrongly scoped caller.
            candidates.extend(cells)
            errors.extend(chart.reasons)
            errors.extend(reasons)
        if not candidates:
            errors.append('growth_charts_not_located')
        packages.append(GroupPackage(
            pdf_hash=pdf_hash, document_version=document_version, report_date=report_date,
            extractor_version=extractor_version, policy_hash=policy_hash(policy),
            group=group, candidates=tuple(candidates), stage_errors=tuple(sorted(set(errors))),
        ))
    return tuple(packages)
