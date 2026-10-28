"""Source-driven FactSet chart discovery; no dated layouts or reference values.

Discovery emits evidence, never published numeric observations. Period and
estimate-state ambiguities remain visible for later admission/review.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from io import BytesIO, StringIO
from pathlib import Path
import csv
import hashlib
import json
import os
import re
import subprocess

import yaml

from .factset_earnings_charts import _tesseract_command, normalize_chart_text

GROUPS = frozenset({
    "earnings_revenue_scorecard", "earnings_revenue_surprise",
    "earnings_revenue_growth", "net_profit_margin", "eps_guidance",
    "geographic_revenue_exposure", "forward_pe", "target_ratings",
    "bottom_up_eps",
})


def load_layout_policy(path: Path | None = None) -> dict:
    from ...config import _config_dir

    path = path or _config_dir() / 'data' / 'structured.yaml'
    raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    policy = raw['datasets']['sp500_earnings_insight']['extraction_policy']
    if set(policy['groups']) != GROUPS:
        raise ValueError('factset_metric_groups_incomplete')
    if policy['target_period_scope'] != 'all_disclosed':
        raise ValueError('factset_period_scope_must_be_all_disclosed')
    if policy['comparison_date_policy'] != 'latest_occurrence_on_or_before_report':
        raise ValueError('factset_comparison_date_policy_invalid')
    state = policy['estimate_state_policy']
    if (state['version'] != 'explicit-actual-else-estimated-v1'
            or state['default'] != 'estimated' or not state['actual_labels']
            or state['preserve_source_wording'] is not True):
        raise ValueError('factset_estimate_state_policy_invalid')
    scope = policy['scope_policy']
    auto = policy.get('auto_admission') or {}
    registered_metrics = set(raw['datasets']['sp500_earnings_insight']['core_metrics'])
    if (set(auto.get('optional_index_metrics', ())) - registered_metrics
            or set(auto.get('min_index_period_counts', {})) - registered_metrics
            or any(not isinstance(count, int) or count < 1
                   for count in auto.get('min_index_period_counts', {}).values())):
        raise ValueError('factset_auto_admission_policy_invalid')
    if not scope['version'] or scope['core_index']['entity'] != 'SP500':
        raise ValueError('factset_core_scope_invalid')
    if set(scope['effort']) != {'P0','P1','P2'}:
        raise ValueError('factset_priority_contract_invalid')
    for name, rule in scope['scopes'].items():
        entities = rule['entities']
        if (not entities or len(entities) != len(set(entities))
                or not set(entities) <= set(policy['sector_aliases'])
                or rule['priority'] not in scope['effort']):
            raise ValueError('factset_entity_scope_invalid:'+name)
    if (scope['scopes']['technology']['entities'] != ['GICS_45']
            or scope['scopes']['technology']['priority'] != 'P0'
            or scope['scopes']['technology']['required_for_core'] is not True
            or set(scope['scopes']['all_sectors']['entities']) != set(policy['sector_aliases'])):
        raise ValueError('factset_core_scope_invalid')
    if (scope['effort']['P2']['dedicated_vision'] is not False
            or scope['effort']['P2']['stop_after_generic_failure'] is not True):
        raise ValueError('factset_supplementary_effort_invalid')
    inventory = policy.get('report_inventories') or {}
    if not inventory.get('version') or not isinstance(inventory.get('reports'), dict):
        raise ValueError('factset_report_inventory_missing')
    for report_day, scopes in inventory['reports'].items():
        date.fromisoformat(report_day)
        if set(scopes) - set(scope['scopes']):
            raise ValueError('factset_report_inventory_scope_unknown')
        for scope_id, charts in scopes.items():
            if scope_id == 'technology' and set(charts) != GROUPS - {'bottom_up_eps'}:
                raise ValueError('factset_technology_inventory_incomplete')
            for chart_id, periods in charts.items():
                if chart_id not in GROUPS - {'bottom_up_eps'} or not periods:
                    raise ValueError('factset_report_inventory_chart_invalid')
                if len(periods) != len({(item['period'], item['basis']) for item in periods}):
                    raise ValueError('factset_report_inventory_duplicate_period')
    aliases = {}
    for entity, labels in policy['sector_aliases'].items():
        for label in labels:
            key = normalize_chart_text(label)
            if key in aliases and aliases[key] != entity:
                raise ValueError('factset_sector_alias_conflict')
            aliases[key] = entity
    if len(policy['sector_aliases']) != int(policy['admission']['expected_sector_count']):
        raise ValueError('factset_sector_aliases_incomplete')
    for group in policy['groups'].values():
        if not group['title_patterns'] or not group['columns'] or not group['units']:
            raise ValueError('factset_group_contract_incomplete')
        if len(group['columns']) != len(set(group['columns'])):
            raise ValueError('factset_duplicate_columns')
        if set(group['estimate_states']) != {'estimated','actual'}:
            raise ValueError('factset_invalid_estimate_states')
        if set(group['column_units']) != set(group['columns']) or not set(group['column_units'].values()) <= set(group['units']):
            raise ValueError('factset_column_unit_contract_invalid')
        if any(not set(columns)<=set(group['columns']) for columns in group.get('sum_to_one', [])):
            raise ValueError('factset_composition_contract_invalid')
        for pattern in group['title_patterns'] + group.get('title_exclusions', []):
            re.compile(pattern)
    ocr = policy['ocr']
    if not (1 <= int(ocr['scale']) <= 4 and
            2 <= int(ocr['cell_scale']) <= 12 and
            0 < int(ocr['timeout_seconds']) <= 120 and
            0 < int(ocr['max_images']) <= 200):
        raise ValueError('factset_ocr_budget_invalid')
    return policy


def policy_hash(policy: dict) -> str:
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()


def declared_report_groups(report_date: date, *, policy: dict | None = None):
    """Read independently registered identities, never successful parser output."""
    from .factset_contracts import GroupIdentity

    policy = policy or load_layout_policy()
    reports = policy['report_inventories']['reports']
    declared = reports.get(report_date.isoformat())
    if not declared:
        raise ValueError('factset_report_inventory_not_registered')
    groups = []
    for scope_id, charts in declared.items():
        entities = tuple(policy['scope_policy']['scopes'][scope_id]['entities'])
        for chart_id, periods in charts.items():
            for item in periods:
                groups.append(GroupIdentity(
                    chart_id=chart_id, period=item['period'], period_basis=item['basis'],
                    scope_id=scope_id,
                    scope_version=policy['scope_policy']['version'],
                    entity_ids=entities))
    return groups


def discovered_core_groups(report_date: date, charts, *, policy: dict | None = None):
    """Auto-declare a new month's IT inventory from located chart headings.

    This inventory is independent of extracted numeric cells. A changed or
    ambiguous report layout remains an exception requiring manual review.
    """
    from .factset_contracts import GroupIdentity

    policy = policy or load_layout_policy()
    required = {'earnings_revenue_scorecard': 1,
                'earnings_revenue_surprise': 1,
                'earnings_revenue_growth': 4,
                'net_profit_margin': 1,
                'eps_guidance': 2,
                'geographic_revenue_exposure': 1,
                'forward_pe': 1,
                'target_ratings': 1}
    found = {}
    for chart in charts:
        if not chart.groups or chart.groups[0] not in required:
            continue
        if (chart.status != 'located' or len(chart.groups) != 1
                or len(chart.periods) != 1 or not chart.period_basis):
            raise ValueError('factset_core_chart_ambiguous')
        found.setdefault(chart.groups[0], set()).add(
            (chart.periods[0], chart.period_basis))
    if {name: len(found.get(name, ())) for name in required} != required:
        raise ValueError('factset_core_chart_inventory_changed')
    entities = tuple(policy['scope_policy']['scopes']['technology']['entities'])
    return [GroupIdentity(chart_id=name, period=period, period_basis=basis,
                          scope_id='technology',
                          scope_version=policy['scope_policy']['version'],
                          entity_ids=entities)
            for name in required for period, basis in sorted(found[name])]


@dataclass(frozen=True)
class Word:
    text: str
    box: tuple[float, float, float, float]
    line: tuple[int, int, int]


@dataclass(frozen=True)
class ChartLocation:
    page_number: int
    image_number: int
    image_hash: str
    title: str
    title_region: tuple[float, float, float, float]
    groups: tuple[str, ...]
    periods: tuple[str, ...]
    period_basis: str
    period_evidence: str
    status: str
    reasons: tuple[str, ...]
    words: tuple[Word, ...]

    def summary(self) -> dict:
        value = asdict(self)
        value.pop('words')
        return value


def ocr_words(data: bytes, *, scale: int = 3, timeout: int = 30, psm: int = 11) -> tuple[Word, ...]:
    """Keep source-space word boxes; confidence never confers admission."""
    from PIL import Image

    executable = _tesseract_command()
    if not executable:
        raise RuntimeError('factset_ocr_dependency_missing')
    with Image.open(BytesIO(data)) as original:
        width, height = original.size
        image = original.convert('RGB').resize((width * scale, height * scale))
        stream = BytesIO()
        image.save(stream, format='PNG')
    proc = subprocess.run(
        [executable, 'stdin', 'stdout', '--psm', str(psm), 'tsv'],
        input=stream.getvalue(), capture_output=True, timeout=timeout, check=True,
        env={**os.environ, 'OMP_THREAD_LIMIT': '1'})
    words = []
    for row in csv.DictReader(StringIO(proc.stdout.decode('utf-8')), delimiter='\t'):
        if row['level'] != '5' or not row['text'].strip():
            continue
        x, y, w, h = (int(row[k]) for k in ('left', 'top', 'width', 'height'))
        words.append(Word(row['text'], (x / (width * scale), y / (height * scale),
                                        (x + w) / (width * scale), (y + h) / (height * scale)),
                          tuple(int(row[k]) for k in ('block_num', 'par_num', 'line_num'))))
    return tuple(words)


def text_lines(words: tuple[Word, ...]) -> list[tuple[str, tuple[float, float, float, float]]]:
    grouped: dict[tuple, list[Word]] = {}
    for word in words:
        grouped.setdefault(word.line, []).append(word)
    lines = []
    for row in grouped.values():
        row.sort(key=lambda w: w.box[0])
        lines.append((' '.join(w.text for w in row),
                      (min(w.box[0] for w in row), min(w.box[1] for w in row),
                       max(w.box[2] for w in row), max(w.box[3] for w in row))))
    return sorted(lines, key=lambda line: (line[1][1], line[1][0]))


def target_periods(title: str, page_text: str) -> tuple[tuple[str, ...], str, str]:
    """Only use explicit source periods; do not invent years from report date."""
    quarter_range = re.search(r'\bQ([1-4])\s*(20\d{2})\s*[-–]\s*Q([1-4])\s*(20\d{2})\b', title, re.I)
    if quarter_range:
        start = int(quarter_range[2]) * 4 + int(quarter_range[1]) - 1
        end = int(quarter_range[4]) * 4 + int(quarter_range[3]) - 1
        if 0 <= end - start <= 16:
            return tuple(f'{n // 4}Q{n % 4 + 1}' for n in range(start, end + 1)), 'quarter_range', quarter_range[0]
        return (), '', quarter_range[0]
    quarter = re.search(r'\bQ([1-4])\s*(20\d{2})\b', title, re.I)
    if quarter:
        return (f'{quarter[2]}Q{quarter[1]}',), 'target_quarter', quarter[0]
    # Margin charts use Q226 vs. Q225: first quarter is current; second is comparison.
    compact = re.search(r'\bQ([1-4])(\d{2})\b', title, re.I)
    if compact:
        return (f'20{compact[2]}Q{compact[1]}',), 'target_quarter', compact[0]
    calendar_pair = re.search(
        r'\bCY\s*(20\d{2})\s*(?:&|and)\s*CY\s*(20\d{2})\b', title, re.I
    )
    if calendar_pair:
        return (calendar_pair[1], calendar_pair[2]), 'calendar_year_range', calendar_pair[0]
    annual = re.search(r'\b(CY|FY)\s*(20\d{2})(?:\s*/\s*(20\d{2}))?', title, re.I)
    if annual:
        years = tuple(y for y in (annual[2], annual[3]) if y)
        basis = 'calendar_year' if annual[1].upper() == 'CY' else 'fiscal_year'
        multi_basis = 'calendar_year_range' if basis == 'calendar_year' else 'mixed_fiscal_years'
        if len(years) > 1 and basis == 'fiscal_year':
            return ('/'.join(years),), 'mixed_fiscal_years', annual[0]
        return years, basis if len(years) == 1 else multi_basis, annual[0]
    if re.search(r'\bFY\b', title, re.I):
        annual = re.search(r'\bFY\s*(20\d{2})\s*/\s*(20\d{2})', page_text, re.I)
        if annual:
            # Aggregate across issuers' fiscal years is NOT two separate observations.
            return (f'{annual[1]}/{annual[2]}',), 'mixed_fiscal_years', annual[0]
    short_quarter = re.search(r'\bQ([1-4])\b', title, re.I)
    if short_quarter:
        # Local page evidence may supply a year, but conflicting years must
        # remain unresolved rather than adopting the first occurrence.
        matches = re.findall(rf'\bQ{short_quarter[1]}\s*(20\d{{2}})\b', page_text, re.I)
        if len(set(matches)) == 1:
            return (f'{matches[0]}Q{short_quarter[1]}',), 'target_quarter', f'Q{short_quarter[1]} {matches[0]}'
    return (), '', ''


def visual_lines(words: tuple[Word, ...]) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Join same-baseline OCR fragments without depending on block numbers.

    Sparse-text OCR often emits a separate block for the second half of a
    heading. Only vertically overlapping fragments are joined; separate rows
    are not flattened into a fabricated title.
    """
    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.box[1], w.box[0])):
        for row in rows:
            top = max(w.box[1] for w in row)
            bottom = min(w.box[3] for w in row)
            overlap = min(bottom, word.box[3]) - max(top, word.box[1])
            height = min(bottom - top, word.box[3] - word.box[1])
            if height > 0 and overlap / height >= .6:
                row.append(word)
                break
        else:
            rows.append([word])
    return [(' '.join(w.text for w in sorted(row, key=lambda w: w.box[0])),
             (min(w.box[0] for w in row), min(w.box[1] for w in row),
              max(w.box[2] for w in row), max(w.box[3] for w in row))) for row in rows]


def locate_chart(image, page_text: str, policy: dict, *, reader=ocr_words,
                 report_date: date | None = None) -> ChartLocation:
    words = reader(image.data, scale=int(policy['ocr']['scale']),
                   timeout=int(policy['ocr']['timeout_seconds']))
    lines = visual_lines(words)
    matches = []
    for text, box in lines:
        normalized = normalize_chart_text(text)
        groups = tuple(name for name, rule in policy['groups'].items()
                       if any(re.search(p, normalized) for p in rule['title_patterns'])
                       and not any(re.search(p, normalized) for p in rule.get('title_exclusions', [])))
        if groups:
            matches.append((text, box, groups))
    reasons = []
    if not matches:
        title, box, groups = (lines[0][0], lines[0][1], ()) if lines else ('', (0, 0, 0, 0), ())
        status = 'unclassified'
        reasons.append('title_not_matched')
    else:
        title, box, groups = matches[0]
        status = 'located'
        if len(matches) > 1 or len(groups) != 1:
            status = 'pending_review'
            reasons.append('ambiguous_chart_title')
    period_title = title
    if groups and not target_periods(title, page_text)[0]:
        # Printed title continuation directly beneath the heading; do not
        # borrow a quarter from unrelated page prose or a different chart.
        continuation = [line for line, region in lines
                        if region[1] >= box[3] and region[1] - box[3] < .06
                        and region[0] < box[2] and region[2] > box[0]
                        and re.fullmatch(r'Q[1-4]\s*\d{2,4}\s+vs\.?\s*Q[1-4]\s*\d{2,4}', line.strip(), re.I)]
        if len(continuation) == 1:
            period_title += ' ' + continuation[0]
    periods, basis, evidence = target_periods(period_title, page_text)
    if not periods and report_date and groups and groups[0] in {
            'geographic_revenue_exposure', 'forward_pe', 'target_ratings'}:
        periods, basis, evidence = (report_date.isoformat(),), 'snapshot', 'report_date'
    if groups and not periods:
        status = 'pending_review'
        reasons.append('target_period_unresolved')
    return ChartLocation(image.page_number, image.image_number,
                         hashlib.sha256(image.data).hexdigest(), title, box, groups,
                         periods, basis, evidence, status, tuple(reasons), words)


def discover_charts(document, *, policy: dict | None = None, reader=ocr_words) -> tuple[ChartLocation, ...]:
    policy = policy or load_layout_policy()
    if len(document.images) > int(policy['ocr']['max_images']):
        raise ValueError('factset_image_budget_exceeded')
    pages = {page.page_number: page.text for page in document.pages}
    # Inventory all embedded images; never use a dated page/image allowlist.
    result = []
    word_cache: dict[str, tuple[Word, ...]] = {}

    def cached_reader(data, **kwargs):
        digest = hashlib.sha256(data).hexdigest()
        if digest not in word_cache:
            word_cache[digest] = reader(data, **kwargs)
        return word_cache[digest]

    for image in document.images:
        try:
            result.append(locate_chart(image, pages.get(image.page_number, ''), policy,
                                       reader=cached_reader, report_date=document.report_date))
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as exc:
            result.append(ChartLocation(image.page_number, image.image_number,
                                        hashlib.sha256(image.data).hexdigest(), '', (0, 0, 0, 0),
                                        (), (), '', '', 'extraction_failed',
                                        (type(exc).__name__,), ()))
    return tuple(result)
