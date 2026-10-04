import pytest

from ats.data.sources.factset_contracts import GroupPackage, resolve_estimate_state, validate_group
from ats.data.sources.factset_report_layout import load_layout_policy
from test_factset_reviews_local import package


@pytest.mark.parametrize('token', ['', 'blended', 'estimated', 'not_applicable',
    'If this is the actual growth rate', '97% of companies reported actual earnings'])
def test_missing_or_nonactual_defaults_without_new_status(token):
    p = package()
    evidence = p.candidates[0].value_evidence[0].model_copy(update={'raw_token': token})
    assert resolve_estimate_state((evidence,), load_layout_policy()) == 'estimated'
    assert validate_group(p, load_layout_policy()) == []


def test_actual_requires_bound_source_evidence_and_changes_hash():
    p = package()
    p = p.model_copy(update={'group': p.group.model_copy(update={'estimate_state': 'actual'})})
    assert 'actual_source_evidence_required' in validate_group(p, load_layout_policy())
    evidence = p.candidates[0].value_evidence[0].model_copy(update={'raw_token': 'Actual Earnings'})
    changed = p.model_copy(update={'estimate_state_evidence': (evidence,)})
    assert validate_group(changed, load_layout_policy()) == []
    assert changed.package_hash != p.package_hash
    assert GroupPackage.model_validate_json(changed.model_dump_json()).package_hash == changed.package_hash


@pytest.mark.parametrize('state', ['blended', 'not_applicable', 'unresolved'])
def test_legacy_states_readable_but_not_newly_admissible(state):
    p = package()
    p = p.model_copy(update={'group': p.group.model_copy(update={'estimate_state': state})})
    assert GroupPackage.model_validate_json(p.model_dump_json()).package_hash == p.package_hash
    assert any('estimate_state' in reason for reason in validate_group(p, load_layout_policy()))


def test_default_state_does_not_bypass_other_errors():
    p = package().model_copy(update={'stage_errors': ('target_period_unresolved',)})
    assert validate_group(p, load_layout_policy()) == ['target_period_unresolved']
