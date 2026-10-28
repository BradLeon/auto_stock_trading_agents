"""Report-generic FactSet sector candidate assembly; no approval or golden input."""

from __future__ import annotations

from .factset_chart_grid import locate_printed_grids
from .factset_chart_values import (
    read_grid_candidates, read_guidance_counts, read_horizontal_surprise,
)
from decimal import Decimal
import re

from .factset_contracts import CellEvidence, GroupPackage, SectorCandidate
from .factset_report_layout import discover_charts, load_layout_policy, policy_hash
from .factset_growth import map_growth
from .factset_table_mapping import map_table

EXTRACTOR_VERSION = 'factset-semantic-sector-v1'


def _surprise_candidates(chart, grid):
    eps = bool(re.search(r'\bearnings\b', chart.title, re.I))
    revenue = bool(re.search(r'\brevenues?\b', chart.title, re.I))
    if eps == revenue:
        return (), ('surprise_metric_ambiguous',)
    column = 'eps_surprise' if eps else 'revenue_surprise'
    result = []
    for cell in grid.values:
        label = CellEvidence(page_number=chart.page_number,image_number=chart.image_number,
            image_hash=chart.image_hash,region=cell.label_region,
            raw_token=cell.source_label,method='local_row_ocr')
        value = CellEvidence(page_number=chart.page_number,image_number=chart.image_number,
            image_hash=chart.image_hash,region=cell.value_region,
            raw_token=cell.raw_token,method='local_row_ocr')
        result.append(SectorCandidate(entity_id=cell.entity_id,column=column,
            value=Decimal(cell.value) if cell.value is not None else None,
            unit='percent',source_label=cell.source_label,label_evidence=label,
            value_evidence=(value,),status=cell.status,reasons=cell.reasons))
    return tuple(result), grid.reasons


def extract_sector_packages(document, *, document_version: str, expected_groups,
                            policy: dict | None = None, scope_id: str = 'technology',
                            chart_inventory=None):
    """Read source PDF once, and assemble only a separately declared inventory.

    The caller freezes applicability before extraction. All output remains
    pending review; missing groups, cells, or ambiguous shared headers are
    retained as errors, not turned into not_disclosed or invented values.
    """
    policy = policy or load_layout_policy()
    expected = {group.key: group for group in expected_groups}
    if len(expected) != len(expected_groups):
        raise ValueError('duplicate_expected_sector_group')
    scope = policy['scope_policy']['scopes'][scope_id]
    for group in expected.values():
        if (group.scope_id != scope_id or group.scope_version != policy['scope_policy']['version']
                or set(group.entity_ids) != set(scope['entities'])):
            raise ValueError('expected_group_scope_mismatch')
    buckets = {key: [] for key in expected}
    errors = {key: [] for key in expected}
    empty_chart_errors = {key: [] for key in expected}
    seen_images = set()
    images = {(image.page_number, image.image_number): image for image in document.images}
    chart_inventory = (chart_inventory if chart_inventory is not None
                       else discover_charts(document, policy=policy))
    for chart in chart_inventory:
        if len(chart.groups) != 1 or len(chart.periods) != 1:
            continue
        matching = [(key, group) for key, group in expected.items()
                    if group.chart_id == chart.groups[0]
                    and group.period == chart.periods[0]
                    and group.period_basis == chart.period_basis]
        if not matching:
            continue
        image_key = (chart.groups, chart.periods, chart.period_basis, chart.image_hash)
        if image_key in seen_images:
            # The report may repeat the identical embedded chart in an
            # overview and an appendix. Its cells are one observation, not
            # two independent measurements.
            continue
        seen_images.add(image_key)
        image = images[chart.page_number, chart.image_number]
        if chart.groups == ('earnings_revenue_surprise',):
            mapped = read_horizontal_surprise(chart, policy, scope_id=scope_id)
            cells, reasons = _surprise_candidates(chart, mapped)
        elif chart.groups == ('eps_guidance',) and 'number' in chart.title.lower():
            cells, reasons = read_guidance_counts(image.data, chart, policy, scope_id=scope_id)
        else:
            grids = locate_printed_grids(image.data)
            # Aggregate-only and unrelated figures may share a title family.
            # Their failure is not an industry-group failure when a proper
            # industry chart for the same group is available elsewhere.
            if not grids:
                continue
            cells, reasons = (), ()
            for grid in grids:
                raw = read_grid_candidates(image.data, grid, chart, policy, scope_id=scope_id)
                part, part_reasons = (
                    map_growth(raw, report_date=document.report_date, policy=policy)
                    if chart.groups == ('earnings_revenue_growth',)
                    else map_table(raw, policy)
                )
                cells += part
                reasons += part_reasons
        if not cells:
            # Preserve failure when this is a located sector figure, but do
            # not let an aggregate-only figure contaminate a valid sector one.
            if 'sector' in chart.title.lower():
                for key, _ in matching:
                    empty_chart_errors[key].extend(reasons or ('sector_cells_not_located',))
            continue
        for key, _ in matching:
            buckets[key].extend(cells)
            errors[key].extend(chart.reasons)
            errors[key].extend(reasons)
    packages = []
    for key, group in expected.items():
        reasons = errors[key]
        if not buckets[key]:
            reasons.extend(empty_chart_errors[key])
            reasons.append('sector_group_not_located')
        packages.append(GroupPackage(
            pdf_hash=document.pdf_hash, document_version=document_version,
            report_date=document.report_date, extractor_version=EXTRACTOR_VERSION,
            policy_hash=policy_hash(policy), group=group,
            candidates=tuple(buckets[key]), stage_errors=tuple(sorted(set(reasons))),
        ))
    return tuple(packages), chart_inventory
