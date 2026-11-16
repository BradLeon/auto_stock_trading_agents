"""Down-only transmission of layer risk into the sector allocation (event-impact-routing 6.1, 2026-11-16)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from ats.agent import task_projection as tp
from ats.agents.sector import cross_section
from ats.agents.sector import review as rv
from ats.schemas.sector import LayerVerdict

NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)
TWO = {"direction": "down", "notches": 2, "reasons": ["l5_export_market_access_risk"], "rationale": "出口许可收紧"}


def test_an_ad_hoc_review_lowers_one_notch_and_records_the_clamp():
    lowered, reasons = rv.apply_downgrade("L5", "超配", TWO, ad_hoc=True, max_notches=1)
    assert lowered == "标配"
    assert "超配→标配" in reasons[0] and "l5_export_market_access_risk" in reasons[0]
    assert "临时复审单次最多 1 档" in reasons[1]


def test_the_routine_review_may_lower_two_notches():
    lowered, reasons = rv.apply_downgrade("L5", "超配", TWO, ad_hoc=False)
    assert lowered == "低配" and len(reasons) == 1


def test_nothing_without_cited_reasons_and_never_upward():
    assert rv.apply_downgrade("L5", "标配", {"direction": "down", "notches": 1, "reasons": []}, ad_hoc=False) == (
        "标配", [])
    assert rv.apply_downgrade("L5", "低配", {"direction": "up", "notches": 1, "reasons": ["x"]}, ad_hoc=False) == (
        "低配", [])
    assert rv.apply_downgrade("L5", "低配", {"direction": "none"}, ad_hoc=False) == ("低配", [])
    assert rv.apply_downgrade("L5", "清仓", TWO, ad_hoc=False)[0] == "清仓"


class _Store:
    def __init__(self, payload):
        self.payload = payload

    def reusable_task_projection(self, **_):
        return SimpleNamespace(schema_version="v1", payload=self.payload, projection_id="p5")


def _cfg():
    return SimpleNamespace(layers=[SimpleNamespace(key="L5_chip_design", weight_cap=0.25, weight_cap_hard=None)],
                           layer_groups=[])


def test_allocations_follow_the_advice_and_the_weight_cap_stays(monkeypatch):
    monkeypatch.setattr(rv, "_ad_hoc_max_notches", lambda: 1)
    cfg = _cfg()
    store = _Store({"status": "expanding", "utilization_advice": TWO})
    downgrades = {}
    allocations, refs, missing = rv._sector_allocations_from_projections(store, cfg, True, ad_hoc=True,
                                                                         downgrades=downgrades)
    assert allocations == {"L5_chip_design": "标配"} and refs == {"L5_chip_design": "p5"} and not missing
    assert len(downgrades["L5_chip_design"]) == 2
    routine = {}
    allocations, _, _ = rv._sector_allocations_from_projections(store, cfg, True, downgrades=routine)
    assert allocations == {"L5_chip_design": "低配"}
    layer = cfg.layers[0]
    assert cross_section.budget_for(layer, "低配") < cross_section.budget_for(layer, "超配")
    assert layer.weight_cap == 0.25                                   # the cap the risk check reads is untouched


def test_a_resolved_risk_restores_nothing_on_an_ad_hoc_review(monkeypatch):
    """No advice (risk resolved) -> the tier is the status default; nothing above it is ever produced here."""
    monkeypatch.setattr(rv, "_ad_hoc_max_notches", lambda: 1)
    store = _Store({"status": "steady", "utilization_advice": {"direction": "none", "notches": 0, "reasons": []}})
    downgrades = {}
    allocations, _, _ = rv._sector_allocations_from_projections(store, _cfg(), True, ad_hoc=True,
                                                                 downgrades=downgrades)
    assert allocations == {"L5_chip_design": "标配"} and downgrades == {}


def test_the_projection_carries_the_lowered_tier_and_its_reasons():
    verdicts = [LayerVerdict(layer_key="L5", as_of=NOW, layer_status="expanding"),
                LayerVerdict(layer_key="L6", as_of=NOW, layer_status="expanding")]
    review = SimpleNamespace(layer_verdicts=verdicts, baskets=[], rotation_advice="")
    reasons = rv.apply_downgrade("L5", "超配", TWO, ad_hoc=True, max_notches=1)[1]
    out = rv.allocation_extras(review, {}, {}, allocations={"L5": "标配", "L6": "超配"}, downgrades={"L5": reasons})
    l5, l6 = out["layers"]
    assert l5["allocation"] == "标配" and l5["downgrade_reasons"] == reasons
    assert l6["allocation"] == "超配" and l6["downgrade_reasons"] == []
    payload = tp.SectorAllocationPayload(sector="x", stance="overweight", target_weight=0.2, rationale="r", **out)
    assert "downgrade_reasons" not in __import__("json").loads(payload.payload_json())["layers"][1]
