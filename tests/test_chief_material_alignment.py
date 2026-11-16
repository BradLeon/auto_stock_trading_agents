"""Optional material-review category and event-cluster alignment for the Chief (event-impact-routing 8.1–8.2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ats.agents.chief import assemble as asm
from ats.decision.snapshot import ResearchSnapshot

NOW = datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc)

BRIEF = {"projection_id": "brief-1", "agent_role": "information_brief", "status": "published",
         "payload": {"event_components": [
             {"component_id": "NVDA-1", "cluster_key": "nvda-ceo-probe", "content": "董事会调查 CEO"},
             {"component_id": "NVDA-2", "cluster_key": "nvda-ceo-probe", "content": "出口限制立法加速"}]}}


class Store:
    def __init__(self, rows):
        self.rows = {r["projection_id"]: r for r in rows}

    def get_task_projection(self, projection_id):
        return self.rows.get(projection_id)


def _review_row(pid="rev-1", *, days_ago=1, refs=("brief-1#NVDA-1",), valid_days=14):
    as_of = NOW - timedelta(days=days_ago)
    return {"projection_id": pid, "agent_role": "fundamental_material_review", "scope_kind": "entity",
            "scope_id": "NVDA", "as_of": as_of.isoformat(), "valid_until": (as_of + timedelta(days=valid_days)).isoformat(),
            "schema_name": "FundamentalMaterialReview", "schema_version": "v1", "status": "published",
            "content_hash": "h-" + pid, "input_refs": ["brief-1"],
            "payload": {"component_refs": list(refs), "thesis_status": "pressured", "expectation_effect": "risk_premium",
                        "direction": -1, "narrative": "关键人风险上升"}}


def _patch(monkeypatch, review):
    monkeypatch.setattr(asm, "_latest_row", lambda store, role, scope, **kw: review if scope.id == "NVDA" else None)
    monkeypatch.setattr("ats.agent.task_projection.reuse_decision",
                        lambda env, scope, at=None, **kw: (datetime.fromisoformat(env.valid_until) > at,
                                                           "reusable" if datetime.fromisoformat(env.valid_until) > at
                                                           else "expired"))


def test_no_review_means_nothing_optional_and_no_gap():
    from ats.agent.task_projection import ProjectionScope

    snapshot = ResearchSnapshot(scope=ProjectionScope(kind="portfolio"), built_at="x")
    assert snapshot.optional_items == [] and snapshot.gaps() == []
    payload = snapshot.to_payload()                 # the stored shape is unchanged when nothing is optional
    assert "optional_items" not in payload and "optional_excluded" not in payload


def test_a_valid_review_is_included(monkeypatch):
    _patch(monkeypatch, _review_row())
    included, excluded = asm.material_reviews(Store([BRIEF]), at=NOW, entities=["NVDA", "TSM"])
    assert [d["projection_id"] for d in included] == ["rev-1"] and excluded == []


def test_an_expired_review_is_left_out_with_its_reason(monkeypatch):
    _patch(monkeypatch, _review_row(days_ago=15, valid_days=30))       # inside valid_until, past 14-day freshness
    included, excluded = asm.material_reviews(Store([BRIEF]), at=NOW, entities=["NVDA"])
    assert included == [] and excluded[0]["reason"] == "expired"


def test_a_review_whose_brief_is_gone_is_dropped_without_blocking(monkeypatch):
    _patch(monkeypatch, _review_row())
    included, excluded = asm.material_reviews(Store([]), at=NOW, entities=["NVDA"])
    assert included == [] and excluded[0]["reason"].startswith("lineage:")


def test_one_cluster_heading_holds_the_review_the_layer_risk_and_the_downgrade():
    material = [{"role": "fundamental_material_review", "scope": "entity:NVDA", "projection_id": "rev-1",
                 "content_hash": "h", "payload": _review_row()["payload"]}]
    detail = [
        {"role": "layer_analysis", "scope": "layer:L5_chip_design", "status": "fresh",
         "payload": {"layer": "L5_chip_design",
                     "risk_claims": [{"claim_id": "l5_export_market_access_risk", "status": "pressured",
                                      "independent_sources": 2, "authoritative_sources": 1,
                                      "component_refs": ["brief-1#NVDA-2"]}]}},
        {"role": "sector_allocation", "scope": "sector:ai_hardware", "status": "fresh",
         "payload": {"layers": [{"layer": "L5_chip_design", "downgrade_reasons": [
             "L5_chip_design: 超配→标配（下调 1 档，依据 l5_export_market_access_risk）"]}]}},
    ]
    text = asm.event_alignment_block(Store([BRIEF]), detail, material)
    assert text.count("### 事件簇") == 1 and "nvda-ceo-probe" in text
    block = text.split("### 事件簇 nvda-ceo-probe")[1]
    assert "基本面重大事件评审 entity:NVDA" in block and "风险命题 l5_export_market_access_risk" in block
    assert "行业配置下调 L5_chip_design: 超配→标配" in block
    assert "不得重复计算" in text


def test_no_components_no_alignment_block():
    assert asm.event_alignment_block(Store([]), [], []) == ""


def test_the_profile_declares_the_optional_category():
    assert asm.optional_categories() == {"fundamental_material": "fundamental_material_review"}


def test_a_downgrade_lands_only_under_the_cluster_it_rests_on():
    detail = [
        {"role": "layer_analysis", "scope": "layer:L5_chip_design", "status": "fresh",
         "payload": {"layer": "L5_chip_design",
                     "risk_claims": [{"claim_id": "l5_export_market_access_risk", "status": "pressured",
                                      "component_refs": ["brief-1#NVDA-2"]}],
                     "off_thesis_shocks": [{"shock_id": "L5-shock-1", "materiality": "moderate", "direction": "negative",
                                            "summary": "选举抛售", "component_refs": ["brief-2#X-1"]}]}},
        {"role": "sector_allocation", "scope": "sector:ai_hardware", "status": "fresh",
         "payload": {"layers": [{"layer": "L5_chip_design", "downgrade_reasons": [
             "L5_chip_design: 超配→标配（下调 1 档，依据 l5_export_market_access_risk）；L5-shock-1 不作为下调依据"]}]}},
    ]
    brief2 = {"projection_id": "brief-2", "agent_role": "information_brief", "status": "published",
              "payload": {"event_components": [{"component_id": "X-1", "cluster_key": "election", "content": "选举"}]}}
    text = asm.event_alignment_block(Store([BRIEF, brief2]), detail, [])
    election = text.split("### 事件簇 election")[1].split("### 事件簇")[0]
    assert "行业配置下调" not in election
    assert "行业配置下调" in text.split("### 事件簇 nvda-ceo-probe")[1]
