"""Phase D task group 1: payload extensions, role-based gating, projection reads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.agent.task_projection import (EnvelopeValidationError, ProjectionScope,
                                       available_projections_by_role,
                                       build_envelope, validate_payload)
from ats.decision.snapshot import build_research_snapshot
from ats.memory.store import TradingMemory
from ats.workflow.run_contracts import (DECISION_CATEGORY_ROLES,
                                        TaskRegistry, WorkflowTaskSpec,
                                        default_registry)

NOW = "2026-09-23T00:00:00+00:00"


def _expiry(days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(
        timespec="seconds")


# --- 1.1 / 1.7 InformationBriefPayload additive fields -------------------------- #

def test_information_brief_old_form_still_validates():
    payload = validate_payload("information_brief", {
        "entity": "NVDA", "headline": "h", "summary": "s",
        "relevance": "high", "sources": ["doc-1"],
    })
    assert payload.schema_version == "v1"


def test_information_brief_accepts_six_elements_and_three_clocks():
    payload = validate_payload("information_brief", {
        "entity": "NVDA", "headline": "h", "summary": "s",
        "relevance": "high", "sources": ["doc-1"],
        "fact_changes": ["guidance raised"], "impact_candidates": ["TSM"],
        "entities": ["NVDA", "TSM"], "confidence": 0.7,
        "freshness": "same_day", "unverified": ["rumour x"],
        "event_time": "2026-09-22T16:00:00+00:00",
        "published_at": "2026-09-22T16:05:00+00:00",
        "extracted_at": "2026-09-23T03:00:00+00:00",
        "cluster_key": "nvda-q3-2026", "source_count": 4,
        "independent_sources": 1,
    })
    assert payload.fact_changes == ["guidance raised"]
    assert payload.event_time < payload.extracted_at
    assert payload.source_count == 4 and payload.independent_sources == 1


# --- 1.2 FundamentalEventReviewPayload additive fields -------------------------- #

def test_event_review_old_form_still_validates():
    payload = validate_payload("fundamental_event_review", {
        "entity": "NVDA", "event": "Q3 earnings", "period": "FY26Q3",
        "direction": 1, "magnitude": 0.12, "notes": "beat",
    })
    assert payload.direction == 1 and payload.schema_version == "v1"


def test_event_review_accepts_view_fields():
    payload = validate_payload("fundamental_event_review", {
        "entity": "NVDA", "event": "Q3 earnings", "period": "FY26Q3",
        "direction": -1, "magnitude": 0.05,
        "scorecard": [{"metric": "eps", "vs_baseline": 0.08}],
        "guidance": "raised", "narrative": "data centre demand",
        "confidence": 0.8, "falsifiable_conditions": ["next Q orders fall"],
    })
    assert payload.confidence == 0.8
    assert payload.falsifiable_conditions == ["next Q orders fall"]


# --- 1.8 event review refuses action vocabulary and sizing fields --------------- #

@pytest.mark.parametrize("field", ["action", "qty", "quantity", "notional",
                                   "target_notional", "weight", "target_weight"])
def test_event_review_rejects_sizing_fields(field):
    with pytest.raises(EnvelopeValidationError):
        validate_payload("fundamental_event_review", {
            "entity": "NVDA", "event": "Q3 earnings", "period": "FY26Q3",
            "direction": 1, "magnitude": 0.1, field: 100,
        })


@pytest.mark.parametrize("action", ["buy", "add", "hold", "trim", "sell", "BUY"])
def test_event_review_rejects_action_vocabulary_values(action):
    with pytest.raises(EnvelopeValidationError):
        validate_payload("fundamental_event_review", {
            "entity": "NVDA", "event": "Q3 earnings", "period": "FY26Q3",
            "direction": 1, "magnitude": 0.1, "notes": action,
        })


def test_event_review_direction_values_still_pass():
    for direction in (-1, 0, 1):
        payload = validate_payload("fundamental_event_review", {
            "entity": "NVDA", "event": "e", "period": "p",
            "direction": direction, "magnitude": 0.1,
            "notes": f"expectation gap {direction}",
        })
        assert payload.direction == direction


# --- 1.4 / 1.5 role-based satisfaction and six required categories -------------- #

def _fundamental_registry():
    return TaskRegistry([
        WorkflowTaskSpec(task_id="fundamental_expectation_update",
                         agent_role="fundamental_expectation_update"),
        WorkflowTaskSpec(task_id="fundamental_event_review",
                         agent_role="fundamental_event_review"),
        WorkflowTaskSpec(task_id="macro", agent_role="macro_review"),
    ])


def test_fundamental_category_satisfied_by_either_mode():
    assert set(_fundamental_registry().satisfying_task_ids("fundamental_analysis")) == {
        "fundamental_expectation_update", "fundamental_event_review"}


def test_snapshot_fundamental_satisfied_by_event_review_alone():
    registry = TaskRegistry([
        WorkflowTaskSpec(task_id="fundamental_expectation_update",
                         agent_role="fundamental_expectation_update"),
        WorkflowTaskSpec(task_id="fundamental_event_review",
                         agent_role="fundamental_event_review"),
    ])
    envelope = build_envelope(
        role="fundamental_event_review",
        payload={"entity": "NVDA", "event": "Q3", "period": "FY26Q3",
                 "direction": 1, "magnitude": 0.1},
        scope=ProjectionScope(kind="entity", id="NVDA"), as_of=NOW,
        valid_until=_expiry(1))
    snapshot = build_research_snapshot(
        registry=registry,
        projections={"fundamental_event_review": envelope},
        scope=ProjectionScope(kind="entity", id="NVDA"))
    by_category = {item.category: item for item in snapshot.items}
    fundamental = by_category["fundamental_analysis"]
    # the satisfying task id is recorded on the item
    assert fundamental.task_id == "fundamental_event_review"
    assert fundamental.reusable and fundamental.freshness == "fresh"
    # and the other mode is NOT demanded on top of it
    assert snapshot.complete


def test_default_registry_has_exactly_six_required_categories():
    registry = default_registry()
    assert set(registry.required_categories()) == set(DECISION_CATEGORY_ROLES)
    assert len(DECISION_CATEGORY_ROLES) == 6


def test_technical_review_is_now_decision_required():
    spec = default_registry().spec("technical_review")
    assert spec.required_for_decision is True


# --- 1.6 per-role projection reads ---------------------------------------------- #

def test_available_projections_by_role_returns_latest_unexpired():
    store = TradingMemory(":memory:")
    old = build_envelope(
        role="layer_analysis",
        payload={"layer": "L3", "status": "expanding", "summary": "s",
                 "findings": ["f"], "confidence": 0.6},
        scope=ProjectionScope(kind="layer", id="L3"), as_of="2026-09-20T00:00:00+00:00",
        valid_until=_expiry(1))
    new = build_envelope(
        role="layer_analysis",
        payload={"layer": "L3", "status": "expanding", "summary": "s2",
                 "findings": ["f2"], "confidence": 0.7},
        scope=ProjectionScope(kind="layer", id="L3"), as_of="2026-09-22T00:00:00+00:00",
        valid_until=_expiry(1))
    brief = build_envelope(
        role="information_brief",
        payload={"entity": "NVDA", "headline": "h", "summary": "s",
                 "relevance": "high", "sources": ["doc-1"]},
        scope=ProjectionScope(kind="entity", id="NVDA"), as_of=NOW,
        valid_until=_expiry(1))
    for envelope in (old, new, brief):
        store.save_task_projection_envelope(envelope)

    got = available_projections_by_role(
        store, roles=("layer_analysis", "information_brief"),
        scope_by_role={"layer_analysis": ProjectionScope(kind="layer", id="L3"),
                       "information_brief": ProjectionScope(kind="entity", id="NVDA")})
    assert got["layer_analysis"].projection_id == new.projection_id
    assert got["layer_analysis"].payload["summary"] == "s2"
    assert got["information_brief"].projection_id == brief.projection_id


def test_available_projections_by_role_returns_none_for_missing_role():
    store = TradingMemory(":memory:")
    got = available_projections_by_role(
        store, roles=("macro_review",),
        scope_by_role={"macro_review": ProjectionScope(kind="portfolio")})
    assert got["macro_review"] is None
