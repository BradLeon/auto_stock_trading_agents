"""Projection contracts for event-impact-routing group 1 (2026-11-13)."""

from __future__ import annotations

import json

import pytest

from ats.agent import task_projection as tp
from ats.workflow.planning import FUNDAMENTAL_TASKS, TASK_ROLE, task_registry

COMPONENT = {"component_id": "c1", "cluster_key": "nvda-ceo-probe", "content": "董事会调查 CEO",
             "scope": "entity", "entities": ["NVDA"], "materiality": "major", "novelty": "escalation",
             "citations": ["DOWJONES:x@v1#p2"], "rationale": "治理 × 本公司"}

MATERIAL = {"entity": "NVDA", "event": "nvda-ceo-probe", "component_refs": ["brief-1#c1"],
            "thesis_status": "pressured", "hit_items": ["key_person"], "expectation_effect": "risk_premium",
            "direction": -1, "magnitude": 0.5, "confidence": 0.4, "valid_until": "2026-11-27T00:00:00+00:00"}


def _brief(**extra):
    return {"entity": "NVDA", "headline": "h", "summary": "s", "relevance": "high", "sources": ["ibkr_news"],
            **extra}


def test_brief_event_components_validate_and_stay_out_of_old_hashes():
    with_components = tp.validate_payload("information_brief", _brief(event_components=[COMPONENT]))
    assert with_components.event_components[0].materiality == "major"
    plain = tp.validate_payload("information_brief", _brief())
    assert "event_components" not in json.loads(plain.payload_json())
    assert "event_components" in json.loads(with_components.payload_json())


def test_component_scope_and_materiality_are_enumerations():
    with pytest.raises(tp.EnvelopeValidationError):
        tp.validate_payload("information_brief", _brief(event_components=[{**COMPONENT, "materiality": "huge"}]))
    with pytest.raises(tp.EnvelopeValidationError):
        tp.validate_payload("information_brief", _brief(event_components=[{**COMPONENT, "scope": "planet"}]))


def test_material_review_validates_and_refuses_sizing_and_actions():
    review = tp.validate_payload("fundamental_material_review", MATERIAL)
    assert review.thesis_status == "pressured" and review.expectation_effect == "risk_premium"
    with pytest.raises(tp.EnvelopeValidationError):
        tp.validate_payload("fundamental_material_review", {**MATERIAL, "target_weight": 0.1})
    with pytest.raises(tp.EnvelopeValidationError):
        tp.validate_payload("fundamental_material_review", {**MATERIAL, "narrative": "sell"})


def test_material_review_without_baseline_is_capped():
    with pytest.raises(tp.EnvelopeValidationError):
        tp.validate_payload("fundamental_material_review", {**MATERIAL, "no_baseline": True, "confidence": 0.5})
    assert tp.validate_payload("fundamental_material_review",
                               {**MATERIAL, "no_baseline": True, "confidence": 0.3}).no_baseline


def test_risk_register_requires_falsifiers():
    item = {"key": "key_person", "label": "创始人 CEO 稳定", "falsifier": "CEO 离任或被调查"}
    assert tp.validate_payload("fundamental_risk_register", {"entity": "NVDA", "items": [item]}).items
    with pytest.raises(tp.EnvelopeValidationError):
        tp.validate_payload("fundamental_risk_register",
                            {"entity": "NVDA", "items": [{"key": "k", "label": "l"}]})


def test_layer_fields_validate_and_are_omitted_when_empty():
    base = {"layer": "L5_chip_design", "status": "steady", "summary": "s", "findings": ["f"], "confidence": 0.5}
    plain = json.loads(tp.validate_payload("layer_analysis", base).payload_json())
    assert not {"risk_claims", "off_thesis_shocks", "cross_scope_claims", "utilization_advice"} & set(plain)
    full = tp.validate_payload("layer_analysis", {
        **base,
        "risk_claims": [{"claim_id": "xpu_export_access_stable", "status": "pressured", "independent_sources": 3,
                         "authoritative_sources": 1}],
        "off_thesis_shocks": [{"shock_id": "s1", "component_refs": ["b#c2"], "summary": "x", "materiality": "major",
                               "expires_at": "2026-11-27"}],
        "cross_scope_claims": [{"component_ref": "b#c1", "from_entity": "NVDA", "transmission": "采购推迟"}],
        "utilization_advice": {"direction": "down", "notches": 1, "reasons": ["xpu_export_access_stable"]}})
    assert full.utilization_advice.notches == 1
    with pytest.raises(tp.EnvelopeValidationError):
        tp.validate_payload("layer_analysis", {**base, "utilization_advice": {"direction": "up", "notches": 1}})


def test_sector_downgrade_reasons_are_nested_and_omitted_when_empty():
    base = {"sector": "ai_hardware", "stance": "neutral", "target_weight": 0.5, "rationale": "r",
            "layers": [{"layer": "L5_chip_design", "allocation": "标配"}]}
    plain = json.loads(tp.validate_payload("sector_allocation", base).payload_json())
    assert "downgrade_reasons" not in plain["layers"][0]
    cut = {**base, "layers": [{"layer": "L5_chip_design", "allocation": "标配",
                               "downgrade_reasons": ["risk:xpu_export_access_stable 超配→标配（建议 2 档，截为 1 档）"]}]}
    assert json.loads(tp.validate_payload("sector_allocation", cut).payload_json())["layers"][0]["downgrade_reasons"]


def test_fundamental_material_task_is_registered_outside_the_per_target_choice():
    spec = task_registry().spec("fundamental-material")
    assert TASK_ROLE["fundamental-material"] == "fundamental_material_review"
    assert spec.depends_on == ("information-brief",) and spec.freshness_seconds == 14 * 86_400
    assert "fundamental-material" not in FUNDAMENTAL_TASKS
