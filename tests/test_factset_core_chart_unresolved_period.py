"""One chart with no resolvable period must not block the report's core (2026-10-30 report, page 25)."""
import pytest

from ats.data.sources.factset_report_layout import ChartLocation, discovered_core_groups, load_layout_policy
from datetime import date

REQUIRED = {'earnings_revenue_scorecard': 1, 'earnings_revenue_surprise': 1, 'earnings_revenue_growth': 4,
            'net_profit_margin': 1, 'eps_guidance': 2, 'geographic_revenue_exposure': 1, 'forward_pe': 1,
            'target_ratings': 1}


def chart(group, period, basis, *, status='located', reasons=()):
    return ChartLocation(1, 1, 'h', 't', (0, 0, 0, 0), (group,), (period,) if period else (), basis, '',
                         status, reasons, ())


def full_inventory():
    out = []
    for group, count in REQUIRED.items():
        for n in range(count):
            out.append(chart(group, f'2026Q{n + 1}', 'target_quarter'))
    return out


def test_a_chart_without_a_resolvable_period_is_skipped_and_the_rest_are_declared():
    unresolved = chart('eps_guidance', '', '', status='pending_review', reasons=('target_period_unresolved',))
    groups = discovered_core_groups(date(2026, 10, 30), full_inventory() + [unresolved],
                                    policy=load_layout_policy())
    assert {(g.chart_id, g.period) for g in groups} == {
        (c.groups[0], c.periods[0]) for c in full_inventory()}


def test_it_cannot_stand_in_for_a_group_that_is_otherwise_missing():
    inventory = [c for c in full_inventory() if not (c.groups[0] == 'eps_guidance' and c.periods[0] == '2026Q2')]
    unresolved = chart('eps_guidance', '', '', status='pending_review', reasons=('target_period_unresolved',))
    with pytest.raises(ValueError, match='factset_core_chart_inventory_changed'):
        discovered_core_groups(date(2026, 10, 30), inventory + [unresolved], policy=load_layout_policy())


@pytest.mark.parametrize('bad', [
    chart('eps_guidance', '2026Q4', 'target_quarter', status='extraction_failed', reasons=('TimeoutExpired',)),
    chart('eps_guidance', '', '', status='pending_review', reasons=('target_period_unresolved', 'title_unreadable')),
])
def test_other_failures_still_block(bad):
    with pytest.raises(ValueError, match='factset_core_chart_ambiguous'):
        discovered_core_groups(date(2026, 10, 30), full_inventory() + [bad], policy=load_layout_policy())


def test_the_same_inventory_can_be_declared_for_the_eleven_sector_scope_too():
    policy = load_layout_policy()
    groups = discovered_core_groups(date(2026, 10, 30), full_inventory(), policy=policy,
                                    scopes=('technology', 'all_sectors'))
    by_scope = {}
    for group in groups:
        by_scope.setdefault(group.scope_id, set()).add((group.chart_id, group.period))
    assert by_scope['technology'] == by_scope['all_sectors'] and len(by_scope) == 2
    assert {len(g.entity_ids) for g in groups if g.scope_id == 'all_sectors'} == {11}
    assert {len(g.entity_ids) for g in groups if g.scope_id == 'technology'} == {1}
