"""Rollout and compatibility feature-flag tests."""

from ats.data.rollout import READ_MODES, load_release_overlay, reconcile_rows


def test_rollout_entrypoint_exposes_independent_modes_and_audit_overlay(tmp_path):
    path = tmp_path / "releases.yaml"
    overlay = load_release_overlay(path)
    assert READ_MODES == {"legacy", "shadow", "platform", "fallback"}
    assert overlay["sources"] == {}
    assert overlay["consumers"] == {}


def test_rollout_reconciliation_can_gate_a_consumer_switch():
    result = reconcile_rows(
        [{"id": "MSFT", "value": 1}],
        [{"id": "MSFT", "value": 1}],
        key_fields=("id",), value_fields=("value",),
    )
    assert result.matched is True
