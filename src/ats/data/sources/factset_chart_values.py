"""Typed, unapproved candidates from dynamically located FactSet table cells."""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import re

from .factset_chart_grid import PrintedGrid, read_grid_cell
from .factset_earnings_charts import normalize_chart_text
from .factset_report_layout import ChartLocation, visual_lines
from .factset_contracts import CellEvidence, SectorCandidate


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, char in enumerate(left, 1):
        current = [i]
        for j, other in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (char != other)))
        previous = current
    return previous[-1]


def _unique_sector_label(text: str, aliases: dict[str, str]) -> str | None:
    """Accept a one-character OCR error only when the entity is unambiguous."""
    normalized = normalize_chart_text(text)
    exact = {entity for alias, entity in aliases.items() if normalized == alias}
    if len(exact) == 1:
        return next(iter(exact))
    near = {entity for alias, entity in aliases.items()
            if len(alias) >= 5 and _edit_distance(normalized, alias) == 1}
    return next(iter(near)) if len(near) == 1 else None


@dataclass(frozen=True)
class GridValue:
    entity_id: str
    source_label: str
    row_label: str
    raw_token: str
    value: str | None
    source_unit: str
    label_region: tuple[float, float, float, float]
    value_region: tuple[float, float, float, float]
    status: str = 'pending_review'
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class GridCandidates:
    chart: ChartLocation
    values: tuple[GridValue, ...]
    reasons: tuple[str, ...]


def parse_printed_number(token: str) -> tuple[str | None, str]:
    """Parse only an entire numeric token; do not repair OCR punctuation."""
    match = re.fullmatch(r'([+\-−]?\d+(?:\.\d+)?)\s*(%)?', token.strip())
    if not match:
        return None, ''
    try:
        value = Decimal(match[1].replace('−', '-'))
    except InvalidOperation:
        return None, ''
    return str(value), 'percent' if match[2] else 'number'


def read_grid_candidates(data: bytes, grid: PrintedGrid, chart: ChartLocation,
                         policy: dict, *, reader=read_grid_cell,
                         scope_id: str = 'all_sectors') -> GridCandidates:
    """Detect the label row by registered entities, not by a row position.

    Keep every numeric row (including comparison evidence). Metric mapping,
    period/estimate state validation and independent review happen downstream.
    """
    aliases = {normalize_chart_text(label): entity
               for entity, labels in policy['sector_aliases'].items() for label in labels}
    aggregates = {normalize_chart_text(label) for label in policy['aggregate_aliases']}
    if scope_id == 'index':
        expected = {policy['scope_policy']['core_index']['entity']}
        aliases.update({label: next(iter(expected)) for label in aggregates})
    else:
        expected = set(policy['scope_policy']['scopes'][scope_id]['entities'])
    cells = []
    for cell in grid.cells:
        raw = reader(data, cell.region, scale=int(policy['ocr']['cell_scale']),
                     timeout=int(policy['ocr']['timeout_seconds']))
        if raw and parse_printed_number(raw)[0] is None and re.search(r'\d', raw):
            # Re-read an unreadable printed cell at two independent scales.
            # Agreement is required; never manufacture a number by editing OCR.
            retries = [reader(data, cell.region, scale=scale,
                              timeout=int(policy['ocr']['timeout_seconds']))
                       for scale in (3, 6)]
            parsed = [parse_printed_number(token) for token in retries]
            if parsed[0][0] is not None and parsed[0] == parsed[1]:
                raw = retries[0]
        cells.append(replace(cell, text=raw))
    rows = {row: [cell for cell in cells if cell.row == row] for row in range(grid.row_count)}
    headers = [row for row in rows.values()
               if sum(aliases.get(normalize_chart_text(cell.text)) in expected
                      for cell in row) >= min(3, len(expected))]
    if len(headers) != 1:
        return GridCandidates(chart, (), ('entity_header_ambiguous_or_missing',))
    header = headers[0]
    header_index = header[0].row
    labels = {cell.column: cell for cell in header
              if aliases.get(normalize_chart_text(cell.text)) in expected}
    entities = [aliases[normalize_chart_text(cell.text)] for cell in labels.values()]
    reasons = []
    if len(entities) != len(set(entities)):
        reasons.append('duplicate_entity_columns')
    if set(entities) != expected:
        reasons.append('sector_coverage_incomplete')
    unknown = [cell for cell in header if normalize_chart_text(cell.text) not in aliases
               and normalize_chart_text(cell.text) not in aggregates]
    # A single non-entity column must identify each data row. Unknown sector
    # columns cannot silently become numeric rows' legend column.
    blank_headers = [cell for cell in unknown if not cell.text.strip()]
    # An explicit empty header identifies the legend column even if another
    # (out-of-scope) sector label was unreadable. Never guess its position.
    legend_headers = blank_headers if len(blank_headers) == 1 else unknown
    if len(legend_headers) != 1:
        return GridCandidates(chart, (), tuple(reasons + ['row_legend_column_unresolved']))
    legend_column = legend_headers[0].column
    result = []
    for index, row in rows.items():
        if index == header_index:
            continue
        legend = next(cell.text for cell in row if cell.column == legend_column)
        for cell in row:
            label = labels.get(cell.column)
            if label is None:
                continue
            value, unit = parse_printed_number(cell.text)
            cell_reasons = tuple(reasons + ([] if value is not None else ['numeric_token_unreadable']))
            result.append(GridValue(aliases[normalize_chart_text(label.text)], label.text, legend,
                                    cell.text, value, unit, label.region, cell.region,
                                    'pending_review' if value is not None else 'extraction_failed', cell_reasons))
    return GridCandidates(chart, tuple(result), tuple(reasons))


def read_index_grid_candidates(data: bytes, grid: PrintedGrid, chart: ChartLocation,
                               policy: dict, *, reader=read_grid_cell) -> GridCandidates:
    """OCR only the aggregate column and semantic row labels, not all sectors.

    The SP500 header and legend column are discovered from printed text on
    every chart. No fixed sector column or page/image ordinal is assumed.
    """
    from .factset_table_mapping import _column

    scale = int(policy['ocr']['cell_scale'])
    timeout = int(policy['ocr']['timeout_seconds'])
    by_row = {row: [cell for cell in grid.cells if cell.row == row]
              for row in range(grid.row_count)}
    cached = {}

    def read(cell, *, force=False):
        key = (cell, force)
        if key not in cached:
            cached[key] = ('' if force else cell.text) or reader(
                data, cell.region, scale=scale, timeout=timeout
            )
        return cached[key]

    aggregate_labels = {normalize_chart_text(label) for label in policy['aggregate_aliases']}
    header_matches = []
    for row in by_row.values():
        header_matches = [(cell.row, cell.column, cell, read(cell))
                          for cell in row if normalize_chart_text(read(cell)) in aggregate_labels]
        if header_matches:
            break
    if len(header_matches) != 1:
        return GridCandidates(chart, (), ('aggregate_header_ambiguous_or_missing',))
    header_row, value_column, label_cell, source_label = header_matches[0]
    data_rows = [row for number, row in by_row.items() if number != header_row]
    if not data_rows:
        return GridCandidates(chart, (), ('aggregate_numeric_rows_missing',))
    def is_legend(value):
        if chart.groups == ('earnings_revenue_growth',):
            return bool(re.search(r'\btoday\b', value, re.I))
        return _column(chart.groups[0], chart.title, value) is not None

    legend_columns = [cell.column for cell in data_rows[0]
                      if is_legend(read(cell, force=True))]
    if len(legend_columns) != 1:
        return GridCandidates(chart, (), ('aggregate_row_legend_ambiguous',))
    legend_column = legend_columns[0]
    result = []
    for row in data_rows:
        legend_cell = next(cell for cell in row if cell.column == legend_column)
        value_cell = next(cell for cell in row if cell.column == value_column)
        legend = read(legend_cell, force=True)
        raw = read(value_cell)
        value, source_unit = parse_printed_number(raw)
        if value is None and value_cell.text:
            raw = read(value_cell, force=True)
            value, source_unit = parse_printed_number(raw)
        if value is None:
            # A single OCR scale can insert whitespace inside a printed
            # decimal (e.g. "29. 1%"). Accept only two independent scales
            # that agree on the complete token; never edit its punctuation.
            retries = [reader(data, value_cell.region, scale=retry_scale,
                              timeout=timeout)
                       for retry_scale in (3, 6)]
            parsed = [parse_printed_number(token) for token in retries]
            if parsed[0][0] is not None and parsed[0] == parsed[1]:
                raw = retries[0]
                value, source_unit = parsed[0]
        reasons = () if value is not None else ('numeric_token_unreadable',)
        result.append(GridValue('SP500', source_label, legend, raw, value, source_unit,
                                label_cell.region, value_cell.region,
                                'pending_review' if value is not None else 'extraction_failed',
                                reasons))
    return GridCandidates(chart, tuple(result), ())


def read_horizontal_surprise(chart: ChartLocation, policy: dict, *,
                             scope_id: str = 'all_sectors') -> GridCandidates:
    """Match printed sector labels and percentages on the same visual row.

    Negative value labels may sit left of the zero axis. No assumptions about
    bar length, axis range, colour, sector order or sign are made.
    """
    if chart.groups != ('earnings_revenue_surprise',):
        return GridCandidates(chart, (), ('not_horizontal_surprise',))
    aliases = {normalize_chart_text(label): entity
               for entity, labels in policy['sector_aliases'].items() for label in labels}
    if scope_id == 'index':
        expected = {policy['scope_policy']['core_index']['entity']}
        aliases.update({normalize_chart_text(label): next(iter(expected))
                        for label in policy['aggregate_aliases']})
    else:
        expected = set(policy['scope_policy']['scopes'][scope_id]['entities'])
    result = []
    for text, region in visual_lines(chart.words):
        normalized = normalize_chart_text(text)
        if scope_id == 'index' and 'surprise' in normalized:
            continue
        matches = [(alias, entity) for alias, entity in aliases.items()
                   if re.match(rf'^{re.escape(alias)}(?:\s|$)', normalized)]
        if not matches:
            percent_words = [word for word in chart.words
                             if region[0] <= word.box[0] and word.box[2] <= region[2]
                             and region[1] <= word.box[1] and word.box[3] <= region[3]
                             and parse_printed_number(word.text)[1] == 'percent']
            if len(percent_words) == 1:
                label = text.replace(percent_words[0].text, '').strip()
                entity = _unique_sector_label(label, aliases)
                if entity is not None:
                    matches = [(normalize_chart_text(label), entity)]
        if not matches:
            continue
        entities = {entity for _, entity in matches}
        if len(entities) != 1:
            continue
        if not entities <= expected:
            continue
        row_words = [word for word in chart.words
                     if region[0] <= word.box[0] and word.box[2] <= region[2]
                     and region[1] <= word.box[1] and word.box[3] <= region[3]]
        tokens = [(word, parse_printed_number(word.text)) for word in row_words]
        numbers = [(word, value) for word, (value, unit) in tokens if unit == 'percent']
        label_words = [word for word, (_, unit) in tokens if unit != 'percent']
        if not label_words:
            continue
        label_region = (min(w.box[0] for w in label_words), min(w.box[1] for w in label_words),
                        max(w.box[2] for w in label_words), max(w.box[3] for w in label_words))
        reasons = () if len(numbers) == 1 else ('numeric_token_ambiguous_or_missing',)
        word, value = numbers[0] if len(numbers) == 1 else (None, None)
        result.append(GridValue(next(iter(entities)), ' '.join(w.text for w in label_words),
                                'Surprise %', word.text if word else text, value, 'percent',
                                label_region, word.box if word else region,
                                'pending_review' if word else 'extraction_failed', reasons))
    reasons = []
    entities = [cell.entity_id for cell in result]
    if len(entities) != len(set(entities)):
        reasons.append('duplicate_entity_rows')
    if set(entities) != expected:
        reasons.append('sector_coverage_incomplete')
    return GridCandidates(chart, tuple(result), tuple(reasons))


def read_guidance_counts(data: bytes, chart: ChartLocation, policy: dict,
                         *, scope_id: str = 'technology') -> tuple[tuple[SectorCandidate, ...], tuple[str, ...]]:
    """Locate printed count labels above bars; bar height is never a value.

    Bars are used only to locate the two source-printed labels. Colour meaning
    comes from the registered source legend and must be visible in the chart.
    The first implementation supports the P0 technology entity only; a missing
    colour, label or source integer remains a candidate failure, never zero.
    """
    from io import BytesIO
    from PIL import Image, ImageOps
    from .factset_report_layout import ocr_words

    if chart.groups != ('eps_guidance',) or 'number' not in chart.title.lower():
        return (), ('not_guidance_count_chart',)
    if scope_id == 'all_sectors':
        return _read_all_sector_guidance_counts(data, chart, policy)
    if scope_id != 'technology' or policy['scope_policy']['scopes'][scope_id]['entities'] != ['GICS_45']:
        return (), ('count_entity_scope_unsupported',)
    words = chart.words
    footer = ' '.join(w.text.lower() for w in words if w.box[1] > .94)
    # OCR may drop the i in "Positive"; the printed two-colour legend itself
    # must still be identified. Do not infer red/green semantics from bars.
    if 'negative' not in footer or not re.search(r'positiv|positv', footer):
        return (), ('guidance_count_legend_unresolved',)
    tech = [w for w in words if w.text.lower().strip('.,') in {'tech', 'technology'} and w.box[1] > .86]
    labels = [w for w in words if w.text.lower().startswith('info') and w.box[1] > .86]
    pairs = [(left, right) for left in labels for right in tech
             if abs((left.box[0]+left.box[2])/2 - (right.box[0]+right.box[2])/2) < .045
             and 0 <= right.box[1] - left.box[1] < .04]
    if len(pairs) != 1:
        return (), ('guidance_technology_label_unresolved',)
    left, right = pairs[0]
    label_region = (min(left.box[0], right.box[0]), min(left.box[1], right.box[1]),
                    max(left.box[2], right.box[2]), max(left.box[3], right.box[3]))
    centre = (label_region[0]+label_region[2])/2
    with Image.open(BytesIO(data)) as original:
        rgb = original.convert('RGB')
        width, height = rgb.size
        pixels = rgb.load()
        columns: dict[str, list[int]] = {'red': [], 'green': []}
        ymin, ymax = int(.08*height), int(.89*height)
        for x in range(max(0, int((centre-.048)*width)), min(width, int((centre+.048)*width))):
            counts = {'red': 0, 'green': 0}
            for y in range(ymin, ymax):
                r,g,b = pixels[x,y]
                if r > 100 and r > g*1.5 and r > b*1.5:
                    counts['red'] += 1
                elif g > 100 and g > r*1.3 and g > b*1.3:
                    counts['green'] += 1
            for colour, count in counts.items():
                if count >= 8:
                    columns[colour].append(x)
        results = []
        label_evidence = CellEvidence(page_number=chart.page_number,
            image_number=chart.image_number, image_hash=chart.image_hash,
            region=label_region, raw_token=f'{left.text} {right.text}', method='local_chart_label_ocr')
        for meaning, colour in policy['groups']['eps_guidance']['count_legend'].items():
            xs = columns[colour]
            if not xs:
                return (), (f'guidance_{meaning}_bar_not_located',)
            centre_x = round(sum(xs)/len(xs))
            if abs(centre_x/width-centre) > .04:
                return (), (f'guidance_{meaning}_bar_ambiguous',)
            matching = [y for y in range(ymin, ymax) if (
                (lambda r,g,b: r > 100 and r > g*1.5 and r > b*1.5)(*pixels[centre_x,y])
                if colour == 'red' else
                (lambda r,g,b: g > 100 and g > r*1.3 and g > b*1.3)(*pixels[centre_x,y]))]
            if not matching:
                return (), (f'guidance_{meaning}_bar_top_unresolved',)
            top = min(matching)
            for radius, above, below in ((14,40,7), (22,40,7), (24,39,4)):
                top_px, bottom_px = max(0,top-above), min(height,top+below)
                left_px, right_px = max(0,centre_x-radius), min(width,centre_x+radius)
                crop = ImageOps.expand(rgb.crop((left_px,top_px,right_px,bottom_px)),
                                       border=4, fill='white')
                stream = BytesIO()
                crop.save(stream, format='PNG')
                source_words = ocr_words(stream.getvalue(),scale=int(policy['ocr']['cell_scale']),
                                         timeout=int(policy['ocr']['timeout_seconds']),psm=6)
                numeric = [(w, parsed) for w in source_words
                           if (parsed := parse_printed_number(w.text))[0] is not None]
                if numeric:
                    break
            # A vertical bar may be OCR noise from the bar edge, never a
            # second source number. Multiple numeric labels are ambiguous.
            word, (value, unit) = numeric[0] if len(numeric) == 1 else (None,(None,''))
            if word is not None:
                cropped_width, cropped_height = crop.size
                region = ((left_px-4+word.box[0]*cropped_width)/width,
                          (top_px-4+word.box[1]*cropped_height)/height,
                          (left_px-4+word.box[2]*cropped_width)/width,
                          (top_px-4+word.box[3]*cropped_height)/height)
            else:
                region = (left_px/width, top_px/height, right_px/width, bottom_px/height)
            raw = word.text if word else ' '.join(w.text for w in source_words)
            evidence = CellEvidence(page_number=chart.page_number,
                image_number=chart.image_number, image_hash=chart.image_hash,
                region=region, raw_token=raw, method='local_count_label_ocr')
            results.append(SectorCandidate(entity_id='GICS_45', column=f'{meaning}_count',
                value=Decimal(value) if value is not None and unit == 'number' else None,
                unit='count', source_label='Info. Technology', label_evidence=label_evidence,
                value_evidence=(evidence,),
                status='pending_review' if value is not None and unit == 'number' else 'extraction_failed',
                reasons=() if value is not None and unit == 'number' else ('guidance_count_token_unreadable',)))
    return tuple(results), ()


def _read_all_sector_guidance_counts(data: bytes, chart: ChartLocation, policy: dict
                                     ) -> tuple[tuple[SectorCandidate, ...], tuple[str, ...]]:
    """Pair each printed sector label with the two labelled bars above it.

    The colour legend identifies meaning; geometry only locates OCR crops.
    A missing or ambiguous printed integer is never replaced by bar height.
    """
    from io import BytesIO
    from PIL import Image, ImageOps
    from .factset_report_layout import ocr_words

    words = chart.words
    footer = ' '.join(w.text.lower() for w in words if w.box[1] > .94)
    if 'negative' not in footer or not re.search(r'positiv|positv', footer):
        return (), ('guidance_count_legend_unresolved',)
    aliases = {normalize_chart_text(label): entity
               for entity, labels in policy['sector_aliases'].items() for label in labels}
    label_words = sorted((w for w in words if .89 <= w.box[1] < .945
                          and re.search(r'[A-Za-z]', w.text)),
                         key=lambda w: (w.box[0] + w.box[2]) / 2)
    clusters: list[list] = []
    for word in label_words:
        centre = (word.box[0] + word.box[2]) / 2
        if clusters and centre - max((w.box[0] + w.box[2]) / 2 for w in clusters[-1]) < .055:
            clusters[-1].append(word)
        else:
            clusters.append([word])
    labels = []
    for cluster in clusters:
        label = ' '.join(w.text for w in sorted(cluster, key=lambda w: (w.box[1], w.box[0])))
        entity = _unique_sector_label(label, aliases)
        if entity is None:
            return (), ('guidance_sector_label_unresolved:' + label,)
        region = (min(w.box[0] for w in cluster), min(w.box[1] for w in cluster),
                  max(w.box[2] for w in cluster), max(w.box[3] for w in cluster))
        labels.append((entity, label, region, (region[0] + region[2]) / 2))
    expected = set(policy['scope_policy']['scopes']['all_sectors']['entities'])
    if {item[0] for item in labels} != expected or len(labels) != len(expected):
        return (), ('guidance_sector_labels_incomplete_or_duplicate',)

    result = []
    with Image.open(BytesIO(data)) as original:
        rgb = original.convert('RGB')
        width, height = rgb.size
        pixels = rgb.load()
        ymin, ymax = int(.08 * height), int(.89 * height)
        for index, (entity, label, label_region, centre) in enumerate(labels):
            left_bound = (labels[index - 1][3] + centre) / 2 if index else max(0, centre - .05)
            right_bound = (centre + labels[index + 1][3]) / 2 if index + 1 < len(labels) else min(1, centre + .05)
            evidence_label = CellEvidence(page_number=chart.page_number,
                image_number=chart.image_number, image_hash=chart.image_hash,
                region=label_region, raw_token=label, method='local_chart_label_ocr')
            for meaning, colour in policy['groups']['eps_guidance']['count_legend'].items():
                coloured = []
                for x in range(int(left_bound * width), int(right_bound * width)):
                    hits = 0
                    for y in range(ymin, ymax):
                        r, g, b = pixels[x, y]
                        hits += (r > 100 and r > g * 1.5 and r > b * 1.5) if colour == 'red' else (
                            g > 100 and g > r * 1.3 and g > b * 1.3)
                    if hits >= 8:
                        coloured.append(x)
                if coloured:
                    centre_x = round(sum(coloured) / len(coloured))
                    matching = []
                    for y in range(ymin, ymax):
                        r, g, b = pixels[centre_x, y]
                        if ((r > 100 and r > g * 1.5 and r > b * 1.5) if colour == 'red'
                                else (g > 100 and g > r * 1.3 and g > b * 1.3)):
                            matching.append(y)
                    top = min(matching) if matching else ymax
                else:
                    centre_x = round((centre + (-.015 if colour == 'red' else .015)) * width)
                    top = ymax
                numeric = []
                crop_region = (max(0, centre_x - 22), max(0, top - 40),
                               min(width, centre_x + 22), min(height, top + 7))
                if coloured:
                    for radius, scale in ((14, 4), (22, 3), (24, 6)):
                        x0, y0 = max(0, centre_x - radius), max(0, top - 40)
                        x1, y1 = min(width, centre_x + radius), min(height, top + 7)
                        crop = rgb.crop((x0, y0, x1, y1))
                        # Coloured bar edges can OCR as a black "4". Remove
                        # only red/green pixels; the black printed count stays.
                        crop_pixels = crop.load()
                        for cy in range(crop.height):
                            for cx in range(crop.width):
                                r, g, b = crop_pixels[cx, cy]
                                if (r > 100 and r > g * 1.5 and r > b * 1.5) or (
                                        g > 100 and g > r * 1.3 and g > b * 1.3):
                                    crop_pixels[cx, cy] = (255, 255, 255)
                        crop = ImageOps.expand(crop, border=4, fill='white')
                        stream = BytesIO()
                        crop.save(stream, format='PNG')
                        numeric = [w for w in ocr_words(stream.getvalue(), scale=scale,
                                    timeout=int(policy['ocr']['timeout_seconds']), psm=6)
                                   if parse_printed_number(w.text)[1] == 'number']
                        crop_region = (x0, y0, x1, y1)
                        if len(numeric) == 1:
                            break
                else:
                    # A zero bar has no colour pixels; accept only a source-printed
                    # zero adjacent to its expected left/right label location.
                    numeric = [w for w in words if w.text == '0' and .80 <= w.box[1] < .90
                               and abs((w.box[0] + w.box[2]) / 2 - centre_x / width) < .012]
                if len(numeric) == 1:
                    word = numeric[0]
                    value, unit = parse_printed_number(word.text)
                    if coloured:
                        x0, y0, x1, y1 = crop_region
                        crop_width, crop_height = x1 - x0 + 8, y1 - y0 + 8
                        region = ((x0 - 4 + word.box[0] * crop_width) / width,
                                  (y0 - 4 + word.box[1] * crop_height) / height,
                                  (x0 - 4 + word.box[2] * crop_width) / width,
                                  (y0 - 4 + word.box[3] * crop_height) / height)
                    else:
                        region = word.box
                else:
                    word, value, unit = None, None, ''
                    region = tuple(v / (width if i % 2 == 0 else height)
                                   for i, v in enumerate(crop_region))
                value_evidence = CellEvidence(page_number=chart.page_number,
                    image_number=chart.image_number, image_hash=chart.image_hash,
                    region=region, raw_token=word.text if word else '',
                    method='local_count_label_ocr')
                ok = value is not None and unit == 'number' and Decimal(value) >= 0
                result.append(SectorCandidate(entity_id=entity, column=f'{meaning}_count',
                    value=Decimal(value) if ok else None, unit='count', source_label=label,
                    label_evidence=evidence_label, value_evidence=(value_evidence,),
                    status='pending_review' if ok else 'extraction_failed',
                    reasons=() if ok else ('guidance_count_token_unreadable',)))
    return tuple(result), ()
