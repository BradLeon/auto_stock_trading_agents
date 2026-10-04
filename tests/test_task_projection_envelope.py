"""The unified TaskProjection envelope: validation, hashing, reuse and storage.

These tests pin the two invariants downstream code relies on — a payload is stored
only after its own role schema accepted it, and the content hash moves when the inputs
move — plus the separation from the pre-existing `task_projections` table.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.agent.task_projection import (
    EnvelopeValidationError,
    ProjectionScope,
    UnknownRoleError,
    build_envelope,
    content_hash,
    is_reusable,
    reuse_decision,
    scope_covers,
    validate_payload,
)
from ats.memory.store import TradingMemory

NOW = "2026-09-22T00:00:00+00:00"


def _expiry(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")


def _layer_payload(**overrides):
    body = {
        "layer": "L3", "status": "expanding", "summary": "orders accelerating",
        "findings": ["book-to-bill 1.4", "lead times extending"], "confidence": 0.8,
    }
    body.update(overrides)
    return body


def _envelope(**kwargs):
    params = dict(
        role="layer_analysis", payload=_layer_payload(),
        scope=ProjectionScope(kind="layer", id="L3"), as_of=NOW,
        input_refs=["obs-1"], data_vintage_refs=["dataset@2026-09-21"],
        valid_until=_expiry(1))
    params.update(kwargs)
    return build_envelope(**params)


# --- validation ------------------------------------------------------------ #

def test_a_valid_payload_is_normalized_into_the_role_schema() -> None:
    validated = validate_payload("layer_analysis", _layer_payload())
    assert validated.schema_name == "LayerAnalysis"
    assert validated.findings == ["book-to-bill 1.4", "lead times extending"]


def test_a_payload_missing_a_required_field_is_rejected() -> None:
    with pytest.raises(EnvelopeValidationError) as caught:
        validate_payload("layer_analysis", {"layer": "L3", "status": "expanding"})
    assert caught.value.role == "layer_analysis"
    assert any("summary" in err for err in caught.value.errors)


def test_a_payload_with_an_out_of_range_value_is_rejected() -> None:
    with pytest.raises(EnvelopeValidationError) as caught:
        validate_payload("layer_analysis", _layer_payload(confidence=1.7))
    assert any("confidence" in err for err in caught.value.errors)


def test_a_rejected_payload_is_not_stored_as_free_text(tmp_path) -> None:
    store = TradingMemory(tmp_path / "memory.sqlite")
    with pytest.raises(EnvelopeValidationError):
        build_envelope(role="layer_analysis", payload={"summary": "just prose"},
                       scope=ProjectionScope(kind="layer", id="L3"), as_of=NOW)
    assert store.task_projection_envelopes() == []
    store.conn.close()


def test_a_role_outside_the_vocabulary_is_rejected() -> None:
    with pytest.raises(UnknownRoleError):
        validate_payload("chief_decision", {"any": "thing"})


def test_a_structurally_wrong_but_recoverable_payload_is_normalized() -> None:
    """A list rendered as a string is fixed; unrecoverable junk still fails."""
    validated = validate_payload("layer_analysis", _layer_payload(findings="a, b"))
    assert validated.findings == ["a", "b"]

    with pytest.raises(EnvelopeValidationError):
        validate_payload("sector_allocation", {
            "sector": "semis", "stance": "overweight", "target_weight": "not-a-number",
            "rationale": "x"})


def test_an_unknown_field_is_rejected_rather_than_carried_along() -> None:
    with pytest.raises(EnvelopeValidationError):
        validate_payload("macro_review", {
            "regime": "risk_on", "summary": "liquidity ample",
            "indicators": ["credit"], "unexpected_extra": 1})


# --- content hash ---------------------------------------------------------- #

def test_identical_semantics_and_inputs_hash_the_same() -> None:
    first = _envelope()
    second = _envelope()
    assert first.content_hash == second.content_hash
    assert first.projection_id == second.projection_id


def test_serialization_noise_does_not_change_the_hash() -> None:
    base = _envelope()
    reordered = _envelope(payload={
        "confidence": 0.8, "summary": "orders accelerating", "layer": "L3",
        "status": "expanding", "findings": ["book-to-bill 1.4", "lead times extending"]})
    assert reordered.content_hash == base.content_hash


def test_changing_an_input_reference_changes_the_hash() -> None:
    base = _envelope()
    moved = _envelope(input_refs=["obs-2"])
    assert moved.content_hash != base.content_hash


def test_changing_the_data_vintage_changes_the_hash() -> None:
    base = _envelope()
    moved = _envelope(data_vintage_refs=["dataset@2026-09-22"])
    assert moved.content_hash != base.content_hash


def test_reference_collections_are_order_insensitive() -> None:
    scope = ProjectionScope(kind="layer", id="L3")
    left = content_hash(role="layer_analysis", scope=scope, as_of=NOW,
                        schema_name="LayerAnalysis", schema_version="v1",
                        payload=_layer_payload(), input_refs=["a", "b"],
                        data_vintage_refs=["v1"])
    right = content_hash(role="layer_analysis", scope=scope, as_of=NOW,
                         schema_name="LayerAnalysis", schema_version="v1",
                         payload=_layer_payload(), input_refs=["b", "a"],
                         data_vintage_refs=["v1"])
    assert left == right


# --- scope ----------------------------------------------------------------- #

def test_a_portfolio_projection_covers_a_narrower_query() -> None:
    assert scope_covers(ProjectionScope(kind="portfolio"),
                        ProjectionScope(kind="entity", id="NVDA"))


def test_a_sector_projection_does_not_cover_a_single_name() -> None:
    assert not scope_covers(ProjectionScope(kind="sector", id="semis"),
                            ProjectionScope(kind="entity", id="NVDA"))


def test_an_exact_scope_covers_itself() -> None:
    scope = ProjectionScope(kind="entity", id="NVDA")
    assert scope_covers(scope, scope)


# --- reuse ----------------------------------------------------------------- #

def test_a_fresh_compatible_projection_is_reusable() -> None:
    envelope = _envelope()
    reusable, reason = reuse_decision(
        envelope, scope=ProjectionScope(kind="layer", id="L3"),
        input_refs=["obs-1"], data_vintage_refs=["dataset@2026-09-21"])
    assert (reusable, reason) == (True, "reusable")
    assert is_reusable(envelope, scope=ProjectionScope(kind="layer", id="L3"))


def test_an_expired_projection_is_not_reusable() -> None:
    envelope = _envelope(valid_until=_expiry(-1))
    reusable, reason = reuse_decision(
        envelope, scope=ProjectionScope(kind="layer", id="L3"))
    assert (reusable, reason) == (False, "expired")


def test_a_projection_for_another_scope_is_not_reusable() -> None:
    envelope = _envelope()
    reusable, reason = reuse_decision(
        envelope, scope=ProjectionScope(kind="layer", id="L5"))
    assert (reusable, reason) == (False, "scope_mismatch")


def test_an_incompatible_schema_is_not_reusable() -> None:
    envelope = _envelope()
    reusable, reason = reuse_decision(
        envelope, scope=ProjectionScope(kind="layer", id="L3"), schema_name="OtherThing")
    assert (reusable, reason) == (False, "schema_mismatch")


def test_a_changed_data_vintage_is_not_reusable() -> None:
    envelope = _envelope()
    reusable, reason = reuse_decision(
        envelope, scope=ProjectionScope(kind="layer", id="L3"),
        data_vintage_refs=["dataset@2026-09-22"])
    assert (reusable, reason) == (False, "data_vintage_changed")


def test_a_newly_required_input_ref_blocks_reuse() -> None:
    envelope = _envelope()
    reusable, reason = reuse_decision(
        envelope, scope=ProjectionScope(kind="layer", id="L3"),
        input_refs=["obs-1", "obs-9"])
    assert (reusable, reason) == (False, "input_refs_changed")


def test_a_failed_projection_is_never_reused() -> None:
    envelope = _envelope()
    envelope = envelope.model_copy(update={"status": "failed"})
    assert reuse_decision(envelope, scope=ProjectionScope(kind="layer", id="L3"))[0] is False


# --- storage --------------------------------------------------------------- #

def test_an_envelope_round_trips_through_workflow_memory(tmp_path) -> None:
    store = TradingMemory(tmp_path / "memory.sqlite")
    envelope = _envelope(workflow_run_id="run-1", agent_run_id="agent-1",
                         model_version="gpt-x", prompt_version="p3")
    store.save_task_projection_envelope(envelope)

    rows = store.task_projection_envelopes(agent_role="layer_analysis")
    assert len(rows) == 1
    row = rows[0]
    assert row["projection_id"] == envelope.projection_id
    assert row["workflow_run_id"] == "run-1"
    assert row["scope_kind"] == "layer" and row["scope_id"] == "L3"
    assert row["as_of"] == NOW
    assert row["input_refs"] == ["obs-1"]
    assert row["data_vintage_refs"] == ["dataset@2026-09-21"]
    assert row["model_version"] == "gpt-x" and row["prompt_version"] == "p3"
    assert row["schema_name"] == "LayerAnalysis"
    assert row["payload"]["layer"] == "L3"
    assert row["content_hash"] == envelope.content_hash
    store.conn.close()


def test_republishing_the_same_content_does_not_duplicate_the_row(tmp_path) -> None:
    store = TradingMemory(tmp_path / "memory.sqlite")
    first = _envelope()
    store.save_task_projection_envelope(first)
    # Same inputs, different run: the run columns move, the row identity does not.
    store.save_task_projection_envelope(_envelope(workflow_run_id="run-2"))
    assert len(store.task_projection_envelopes(agent_role="layer_analysis")) == 1
    store.conn.close()


def test_a_changed_input_is_a_new_row_not_an_overwrite(tmp_path) -> None:
    store = TradingMemory(tmp_path / "memory.sqlite")
    store.save_task_projection_envelope(_envelope())
    store.save_task_projection_envelope(_envelope(input_refs=["obs-2"]))
    assert len(store.task_projection_envelopes(agent_role="layer_analysis")) == 2
    store.conn.close()


def test_the_envelope_query_finds_the_latest_usable_projection(tmp_path) -> None:
    store = TradingMemory(tmp_path / "memory.sqlite")
    store.save_task_projection_envelope(_envelope(valid_until=_expiry(-1)))
    fresh = _envelope(data_vintage_refs=["dataset@2026-09-22"])
    store.save_task_projection_envelope(fresh)

    found = store.reusable_task_projection(
        agent_role="layer_analysis", scope=ProjectionScope(kind="layer", id="L3"),
        data_vintage_refs=["dataset@2026-09-22"])
    assert found is not None
    assert found.content_hash == fresh.content_hash
    store.conn.close()


def test_no_usable_projection_returns_none(tmp_path) -> None:
    store = TradingMemory(tmp_path / "memory.sqlite")
    store.save_task_projection_envelope(_envelope())
    assert store.reusable_task_projection(
        agent_role="layer_analysis", scope=ProjectionScope(kind="layer", id="L3"),
        data_vintage_refs=["dataset@2026-09-30"]) is None
    store.conn.close()


def test_the_legacy_projection_table_is_untouched_by_envelope_writes(tmp_path) -> None:
    """The new write path must leave `task_projections` at zero writes."""
    store = TradingMemory(tmp_path / "memory.sqlite")
    before = store.conn.execute("SELECT count(*) AS n FROM task_projections").fetchone()["n"]
    store.save_task_projection_envelope(_envelope())
    after = store.conn.execute("SELECT count(*) AS n FROM task_projections").fetchone()["n"]
    assert (before, after) == (0, 0)
    assert len(store.task_projection_envelopes()) == 1
    store.conn.close()


def test_the_legacy_projection_table_keeps_its_own_columns(tmp_path) -> None:
    store = TradingMemory(tmp_path / "memory.sqlite")
    columns = {row["name"] for row in
               store.conn.execute("PRAGMA table_info(task_projections)").fetchall()}
    assert columns == {"projection_id", "profile", "profile_version", "input_kind",
                       "input_ref", "target_type", "target_id", "payload",
                       "created_at", "expires_at"}
    store.conn.close()
