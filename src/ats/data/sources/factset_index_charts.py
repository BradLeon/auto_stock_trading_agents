"""Source-printed S&P 500 aggregate chart values, never bar-height estimates."""

from __future__ import annotations

import re
from io import BytesIO

from .factset_chart_grid import locate_printed_grids
from .factset_chart_values import read_horizontal_surprise, read_index_grid_candidates
from .factset_earnings_charts import FACTSET_CHARTS
from .factset_earnings_text import (
    EstimateState,
    FactSetCandidate,
    FactSetEvidenceAnchor,
    FactSetExtractionRun,
    MetricGroup,
    CandidateStatus,
    ReportPeriod,
)
from .factset_report_layout import (
    discover_charts, load_layout_policy, ocr_words, policy_hash, visual_lines,
)
from .factset_table_mapping import _column


_GROUPS = {
    "earnings_revenue_scorecard": MetricGroup.SCORECARD,
    "earnings_revenue_surprise": MetricGroup.SURPRISE,
    "earnings_revenue_growth": MetricGroup.GROWTH,
    "net_profit_margin": MetricGroup.MARGIN,
    "eps_guidance": MetricGroup.GUIDANCE,
}
_METRICS = {definition.chart_id: definition.expected_columns for definition in FACTSET_CHARTS}
_METRICS['eps_guidance'] = {
    **_METRICS['eps_guidance'],
    'positive_share': 'earnings.guidance.positive_share',
    'negative_share': 'earnings.guidance.negative_share',
}


def _aggregate_geography(chart, image, run, policy) -> list[FactSetCandidate]:
    """Read printed pie labels; neither wedge area nor colour supplies a value."""
    if not re.search(r'aggregate geographic revenue exposure', chart.title, re.I):
        return []
    from PIL import Image

    labels = {'international': 'revenue.geographic.international_share',
              'united': 'revenue.geographic.us_share'}
    output = []
    with Image.open(BytesIO(image.data)) as original:
        width, height = original.size
        for prefix, metric in labels.items():
            matches = [(text, box) for text, box in visual_lines(chart.words)
                       if text.casefold().startswith(prefix)]
            if len(matches) != 1:
                return []
            _, box = matches[0]
            region = (max(0, box[0] - .035), max(0, box[1] - .015),
                      min(1, box[2] + .055), min(1, box[3] + .065))
            crop = original.crop((round(region[0] * width), round(region[1] * height),
                                  round(region[2] * width), round(region[3] * height)))
            stream = BytesIO()
            crop.save(stream, format='PNG')
            words = ocr_words(stream.getvalue(), scale=4, psm=6)
            text = ' '.join(word.text for word in words)
            if prefix == 'united' and not re.search(r'United\s+States', text, re.I):
                return []
            if prefix == 'international' and 'international' not in text.casefold():
                return []
            values = [word for word in words if re.fullmatch(r'\d{1,3}%', word.text)]
            if len(values) != 1:
                return []
            word = values[0]
            value = int(word.text[:-1]) / 100
            numeric_region = (
                region[0] + word.box[0] * (region[2] - region[0]),
                region[1] + word.box[1] * (region[3] - region[1]),
                region[0] + word.box[2] * (region[2] - region[0]),
                region[1] + word.box[3] * (region[3] - region[1]),
            )
            output.append(FactSetCandidate(
                run_id=run.run_id, entity_id='SP500', provider_field=prefix + '_share',
                metric_id=metric, metric_group=MetricGroup.GEOGRAPHY,
                period=ReportPeriod(value=run.report_date.isoformat(), basis='snapshot'),
                estimate_state=EstimateState.ESTIMATED, unit='ratio',
                raw_token=word.text, raw_value=word.text[:-1], value=value,
                report_date=run.report_date, known_at=run.known_at,
                extractor_version=run.extractor_version,
                evidence=[FactSetEvidenceAnchor(
                    document_id=run.document_id, version_id=run.version_id,
                    anchor_kind='image_region', page_number=chart.page_number,
                    chart_id='geographic_revenue_exposure', region=numeric_region,
                    extraction_method=run.extractor_version)],
                dimensions={'source_label': text, 'source_estimate_wording': 'not_explicit',
                            'state_policy_version': policy['estimate_state_policy']['version'],
                            'extraction_policy_hash': policy_hash(policy),
                            'chart_image_hash': chart.image_hash},
            ))
    return output if len(output) == 2 and abs(sum(c.value for c in output) - 1) <= .01 else []


def _mapped_column(chart, row_label: str) -> str | None:
    group = chart.groups[0]
    if group == "earnings_revenue_growth":
        if not re.search(r"\btoday\b", row_label, re.IGNORECASE):
            return None
        if re.search(r"\bearnings\b", chart.title, re.IGNORECASE):
            return "eps_growth"
        if re.search(r"\brevenues?\b", chart.title, re.IGNORECASE):
            return "revenue_growth"
        return None
    return _column(group, chart.title, row_label)


def _bottom_up_eps(document, chart, image, run, policy) -> list[FactSetCandidate]:
    """Pair printed bar labels with their printed period ticks, not bar height.

    The source PDF's hatched estimate bars confuse full-image OCR, so the
    numeric label is read in a small crop immediately above the detected bar
    top. Bar geometry only locates the label; it never supplies the value.
    """
    from PIL import Image

    page_text = document.pages[chart.page_number - 1].text
    if 'Bottom-Up EPS Estimates' not in page_text:
        return []
    annual = bool(re.search(r'calendar\s+yea', chart.title, re.I))
    quarterly = bool(re.search(r'quarterly\s+bottom.up\s+eps', chart.title, re.I))
    if annual == quarterly:
        return []
    output = []
    with Image.open(BytesIO(image.data)) as original:
        rgb = original.convert('RGB')
        width, height = rgb.size
        footer = rgb.crop((0, round(.94 * height), width, height))
        stream = BytesIO()
        footer.save(stream, format='PNG')
        ticks = ocr_words(stream.getvalue(), scale=4, psm=6)
        quarter = (run.report_date.month - 1) // 3 + 1
        target = ({f'CY{run.report_date.year}', f'CY{run.report_date.year + 1}'}
                  if annual else {f'Q{quarter}{str(run.report_date.year)[-2:]}'})
        selected = [word for word in ticks if word.text.strip('.,=') in target]
        if {word.text.strip('.,=') for word in selected} != target:
            return []
        pixels = rgb.load()

        def blue_fraction(x: int, y: int) -> float:
            left, right = max(0, x - 13), min(width, x + 14)
            return sum(1 for col in range(left, right)
                       if (lambda r, g, b: b > r * 1.2 and b > g * 1.15 and b < 180)(
                           *pixels[col, y])) / (right - left)

        for tick in selected:
            x = round((tick.box[0] + tick.box[2]) * width / 2)
            tops = [y for y in range(round(.08 * height), round(.8 * height))
                    if blue_fraction(x, y) > .2
                    and blue_fraction(x, y + 1) > .2
                    and blue_fraction(x, y + 2) > .2]
            if not tops:
                return []
            top = tops[0]
            left, right = max(0, x - 36), min(width, x + 36)
            upper, lower = max(0, top - 36), top - 3
            crop = rgb.crop((left, upper, right, lower))
            stream = BytesIO()
            crop.save(stream, format='PNG')
            words = ocr_words(stream.getvalue(), scale=4, psm=7)
            numbers = [word for word in words
                       if re.fullmatch(r'\d{1,4}\.\d{2}', word.text)]
            if len(numbers) != 1:
                return []
            word = numbers[0]
            period = (tick.text.strip('.,=')[2:] if annual
                      else f'{run.report_date.year}Q{quarter}')
            basis = 'calendar_year' if annual else 'target_quarter'
            region = ((left + word.box[0] * (right - left)) / width,
                      (upper + word.box[1] * (lower - upper)) / height,
                      (left + word.box[2] * (right - left)) / width,
                      (upper + word.box[3] * (lower - upper)) / height)
            output.append(FactSetCandidate(
                run_id=run.run_id, entity_id='SP500', provider_field='bottom_up_eps',
                metric_id='earnings.bottom_up_eps', metric_group=MetricGroup.BOTTOM_UP_EPS,
                period=ReportPeriod(value=period, basis=basis),
                estimate_state=EstimateState.ESTIMATED, unit='currency_per_share',
                raw_token=word.text, raw_value=word.text, value=float(word.text),
                report_date=run.report_date, known_at=run.known_at,
                extractor_version=run.extractor_version,
                evidence=[FactSetEvidenceAnchor(
                    document_id=run.document_id, version_id=run.version_id,
                    anchor_kind='image_region', page_number=chart.page_number,
                    chart_id='bottom_up_eps', region=region,
                    extraction_method=run.extractor_version)],
                dimensions={'source_period_label': tick.text,
                            'source_estimate_wording': 'estimates',
                            'state_policy_version': policy['estimate_state_policy']['version'],
                            'extraction_policy_hash': policy_hash(policy),
                            'chart_image_hash': chart.image_hash},
            ))
    return output if len(output) == len(target) else []


def extract_index_chart_candidates(document, run: FactSetExtractionRun, *, policy=None,
                                   chart_inventory=None) -> list[FactSetCandidate]:
    """Extract supported printed-grid aggregate rows with local numeric evidence.

    A chart without a recognized title, period, aggregate header, printed
    numeric token or registered row is omitted and remains an inventory gap.
    The caller must compare against an independent applicability inventory.
    """
    policy = policy or load_layout_policy()
    inventory = chart_inventory if chart_inventory is not None else discover_charts(
        document, policy=policy
    )
    images = {(image.page_number, image.image_number): image for image in document.images}
    existing = {(candidate.metric_id, candidate.period.value, candidate.period.basis): candidate
                for candidate in run.candidates}
    result = []
    for chart in inventory:
        image = images.get((chart.page_number, chart.image_number))
        if image is None:
            continue
        if ('bottom_up_eps' in chart.groups
                or re.search(r'S&P\s*500\s+Calendar\s+Yea', chart.title, re.I)):
            result.extend(_bottom_up_eps(document, chart, image, run, policy))
            continue
        if (len(chart.groups) != 1
                or chart.groups[0] not in set(_GROUPS) | {'geographic_revenue_exposure'}
                or len(chart.periods) != 1 or chart.status != 'located'):
            continue
        if chart.groups[0] == 'geographic_revenue_exposure':
            result.extend(_aggregate_geography(chart, image, run, policy))
            continue
        if chart.groups[0] == 'earnings_revenue_surprise':
            rows = read_horizontal_surprise(chart, policy, scope_id='index')
            if rows.reasons or len(rows.values) != 1:
                continue
            row = rows.values[0]
            if row.status != 'pending_review' or row.value is None:
                continue
            eps = bool(re.search(r'\bearnings\b', chart.title, re.I))
            revenue = bool(re.search(r'\brevenues?\b', chart.title, re.I))
            if eps == revenue:
                continue
            metric = 'earnings.eps.surprise_pct' if eps else 'earnings.revenue.surprise_pct'
            basis = chart.period_basis
            prior = existing.get((metric, chart.periods[0], basis))
            state = prior.estimate_state if prior else EstimateState.ESTIMATED
            evidence = [FactSetEvidenceAnchor(
                document_id=run.document_id, version_id=run.version_id,
                anchor_kind='image_region', page_number=chart.page_number,
                chart_id=chart.groups[0], region=row.value_region,
                extraction_method=run.extractor_version)]
            if prior and state == EstimateState.ACTUAL:
                evidence.extend(prior.evidence)
            result.append(FactSetCandidate(
                run_id=run.run_id, entity_id='SP500',
                provider_field='eps_surprise_pct' if eps else 'revenue_surprise_pct',
                metric_id=metric, metric_group=MetricGroup.SURPRISE,
                period=ReportPeriod(value=chart.periods[0], basis=basis),
                estimate_state=state, unit='ratio', raw_token=row.raw_token,
                raw_value=row.value, value=float(row.value) / 100,
                report_date=run.report_date, known_at=run.known_at,
                extractor_version=run.extractor_version, evidence=evidence,
                dimensions={'source_label': row.source_label,
                            'source_estimate_wording': state.value if prior else 'not_explicit',
                            'state_policy_version': policy['estimate_state_policy']['version'],
                            'extraction_policy_hash': policy_hash(policy),
                            'chart_image_hash': chart.image_hash},
            ))
            continue
        for grid in locate_printed_grids(image.data, words=chart.words):
            rows = read_index_grid_candidates(image.data, grid, chart, policy)
            if rows.reasons:
                continue
            for row in rows.values:
                if row.entity_id != 'SP500':
                    continue
                column = _mapped_column(chart, row.row_label)
                metric = _METRICS[chart.groups[0]].get(column or '')
                if metric is None:
                    continue
                if chart.groups[0] == 'eps_guidance' and 'percentage' not in chart.title.lower():
                    continue
                unit = policy['groups'][chart.groups[0]]['column_units'].get(column, 'ratio')
                errors = list(row.reasons)
                if row.value is not None and unit in {'ratio', 'percent'} and row.source_unit != 'percent':
                    errors.append('printed_percent_unit_missing')
                if row.value is not None and unit == 'multiple' and row.source_unit != 'number':
                    errors.append('printed_multiple_unit_mismatch')
                value = float(row.value) if row.value is not None else None
                source_unit = unit
                # Industry chart contracts retain printed percentages; the
                # registered SP500 Index metrics use normalized ratios.
                if unit in {'ratio', 'percent'}:
                    if value is not None:
                        value /= 100
                    unit = 'ratio'
                basis = chart.period_basis
                prior = existing.get((metric, chart.periods[0], basis))
                state = prior.estimate_state if prior else EstimateState.ESTIMATED
                evidence = [FactSetEvidenceAnchor(
                    document_id=run.document_id, version_id=run.version_id,
                    anchor_kind='image_region', page_number=chart.page_number,
                    chart_id=chart.groups[0], region=row.value_region,
                    extraction_method=run.extractor_version,
                )]
                if prior and state == EstimateState.ACTUAL:
                    evidence.extend(prior.evidence)
                result.append(FactSetCandidate(
                    run_id=run.run_id, entity_id='SP500', provider_field=column,
                    metric_id=metric, metric_group=_GROUPS[chart.groups[0]],
                    period=ReportPeriod(value=chart.periods[0], basis=basis),
                    estimate_state=state, unit=unit, raw_token=row.raw_token,
                    raw_value=row.value or row.raw_token, value=value, report_date=run.report_date,
                    known_at=run.known_at, extractor_version=run.extractor_version,
                    evidence=evidence, dimensions={
                        'source_label': row.source_label,
                        'source_row_label': row.row_label,
                        'printed_unit': source_unit,
                        'source_estimate_wording': state.value if prior else 'not_explicit',
                        'state_policy_version': policy['estimate_state_policy']['version'],
                        'extraction_policy_hash': policy_hash(policy),
                        'chart_image_hash': chart.image_hash,
                    },
                    status=CandidateStatus.QUARANTINED if errors else CandidateStatus.PENDING,
                    reason_codes=errors,
                ))
    return result
