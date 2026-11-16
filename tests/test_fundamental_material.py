"""Company risk register and material-event review (event-impact-routing 4.1–4.3, 2026-11-15)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.agent import task_projection as tp
from ats.agents.fundamental import material as mat
from ats.agents.fundamental.entry import material_request

NOW = datetime(2026, 11, 12, 14, 0, tzinfo=timezone.utc)
BASELINE = {"symbol": "NVDA", "fiscal_label": "Q3 FY2027", "narrative": "加速器订单与毛利率继续扩张",
            "dim_keys": ["data_center_revenue"], "baseline_refs": ["pead-baseline:NVDA:Q3 FY2027@abc"]}
BRIEF = {"projection_id": "brief-1", "payload": {
    "fact_changes": ["来源信息（WSJ）：董事会调查 CEO", "来源信息（IBD）：股价下跌"],
    "event_components": [{"component_id": "NVDA-1", "cluster_key": "nvda-ceo-probe", "content": "董事会调查 CEO",
                          "scope": "entity", "materiality": "major", "direction": "negative", "fact_indices": [0]}]}}


class Store:
    def __init__(self):
        self.saved = []

    def task_projection_envelopes(self, *, agent_role=None, scope_id=None, **_):
        rows = [{"projection_id": e.projection_id, "payload": e.payload, "as_of": e.as_of}
                for e in reversed(self.saved) if e.agent_role == agent_role and e.scope.id == scope_id]
        return rows

    def save_task_projection_envelope(self, envelope):
        self.saved.append(envelope)


@pytest.fixture
def env(monkeypatch):
    from ats.agents.fundamental import routine
    from ats.workflow import read_scope

    state = {"baseline": BASELINE}
    monkeypatch.setattr(routine, "load_baseline", lambda store, symbol: state["baseline"])
    monkeypatch.setattr(read_scope, "read_projection",
                        lambda store, **kw: BRIEF if kw.get("projection_id", "brief-1") in ("", "brief-1") else None)
    monkeypatch.setattr(mat, "_now", lambda: NOW)
    return state


def _register_view():
    return mat.RiskRegisterView(items=[mat.RiskItemView(key="key_person", label="创始人 CEO 稳定", kind="key_person",
                                                        falsifier="CEO 离任或被董事会调查")])


def _review_view(**kw):
    base = dict(thesis_status="pressured", hit_items=["key_person"], expectation_effect="risk_premium",
                direction=-1, magnitude=1.0, confidence=0.6, narrative="关键人风险上升，季度数字暂不改",
                materiality_review="agree：调查属实")
    return mat.MaterialReviewView(**{**base, **kw})


def test_register_is_published_and_refresh_keeps_the_previous_reference(env):
    store = Store()
    first = mat.build_register(store, "NVDA", rate=lambda ctx: _register_view())
    second = mat.build_register(store, "NVDA", rate=lambda ctx: _register_view())
    latest = mat.latest_register(store, "NVDA")
    assert latest["projection_id"] == second and latest["payload"]["previous_ref"] == first
    assert latest["payload"]["items"][0]["falsifier"]


def test_material_review_reads_the_components_and_judges_the_company_part(env):
    store = Store()
    mat.build_register(store, "NVDA", rate=lambda ctx: _register_view())
    seen = {}

    def rate(ctx):
        seen["ctx"] = ctx
        return _review_view()

    out = mat.run_material_pass(material_request("NVDA", component_refs=["brief-1#NVDA-1"]), store=store, rate=rate)
    env_row = [e for e in store.saved if e.agent_role == "fundamental_material_review"][0]
    assert out["projection_id"] == env_row.projection_id
    assert "董事会调查 CEO" in seen["ctx"] and "CEO 离任或被董事会调查" in seen["ctx"]
    assert env_row.payload["thesis_status"] == "pressured" and env_row.payload["expectation_effect"] == "risk_premium"
    assert env_row.valid_until == (NOW + timedelta(days=14)).isoformat()
    assert "brief-1" in env_row.input_refs


def test_without_a_baseline_confidence_is_capped(env):
    env["baseline"] = {"symbol": "NVDA", "fiscal_label": "", "narrative": "", "dim_keys": []}
    store = Store()
    mat.run_material_pass(material_request("NVDA", component_refs=["brief-1#NVDA-1"]), store=store,
                          rate=lambda ctx: _review_view(confidence=0.9))
    payload = store.saved[-1].payload
    assert payload["no_baseline"] is True and payload["confidence"] == 0.3


def test_a_component_not_in_the_readable_brief_is_refused(env):
    with pytest.raises(ValueError):
        mat.run_material_pass(material_request("NVDA", component_refs=["brief-9#NVDA-1"]), store=Store(),
                              rate=lambda ctx: _review_view())


def test_advice_in_the_narrative_is_refused(env):
    with pytest.raises(tp.EnvelopeValidationError):
        mat.run_material_pass(material_request("NVDA", component_refs=["brief-1#NVDA-1"]), store=Store(),
                              rate=lambda ctx: _review_view(narrative="建议减持英伟达"))


def test_init_registers_is_a_dry_run_by_default():
    store = Store()
    out = mat.init_registers(store, ["NVDA", "TSM"])
    assert out["requests"] == 2 and out["built"] == {} and store.saved == []


def test_an_expired_material_review_is_not_reused_and_a_new_development_republishes(env):
    """4.3: valid 14 days through the task's freshness; a later trigger publishes a new version."""
    from ats.workflow.planning import task_registry

    assert task_registry().spec("fundamental-material").freshness_seconds == 14 * 86_400
    store = Store()
    request = material_request("NVDA", component_refs=["brief-1#NVDA-1"])
    mat.run_material_pass(request, store=store, rate=lambda ctx: _review_view())
    mat.run_material_pass(request, store=store, rate=lambda ctx: _review_view(thesis_status="falsified"))
    reviews = [e for e in store.saved if e.agent_role == "fundamental_material_review"]
    assert len(reviews) == 2 and reviews[-1].payload["thesis_status"] == "falsified"


def test_register_reads_recent_brief_facts_so_new_company_risks_enter_it(env):
    """2026-11-15: built from the baseline alone, NVDA's register had no key-person item during the CEO probe."""
    from ats.agent.task_projection import ProjectionScope, build_envelope

    store = Store()
    brief = build_envelope(role="information_brief", scope=ProjectionScope(kind="entity", id="NVDA"),
                           as_of=(NOW - timedelta(days=2)).isoformat(),
                           payload={"entity": "NVDA", "headline": "h", "summary": "s", "relevance": "high",
                                    "sources": ["ibkr_news"], "fact_changes": ["来源信息（WSJ）：董事会调查 CEO 黄仁勋"]})
    old = build_envelope(role="information_brief", scope=ProjectionScope(kind="entity", id="NVDA"),
                         as_of=(NOW - timedelta(days=30)).isoformat(),
                         payload={"entity": "NVDA", "headline": "h", "summary": "s", "relevance": "low",
                                  "sources": ["ibkr_news"], "fact_changes": ["一个月前的旧事实"]})
    store.saved += [old, brief]
    seen = {}

    def rate(ctx):
        seen["ctx"] = ctx
        return _register_view()

    mat.build_register(store, "NVDA", rate=rate)
    assert "董事会调查 CEO 黄仁勋" in seen["ctx"] and "一个月前的旧事实" not in seen["ctx"]
    register = [e for e in store.saved if e.agent_role == "fundamental_risk_register"][-1]
    assert brief.projection_id in register.input_refs


def test_rebuild_covers_targets_that_already_have_a_register(env):
    store = Store()
    mat.build_register(store, "NVDA", rate=lambda ctx: _register_view())
    assert mat.init_registers(store, ["NVDA", "TSM"])["missing"] == ["TSM"]
    assert mat.init_registers(store, ["NVDA", "TSM"], rebuild=True)["missing"] == ["NVDA", "TSM"]


def test_a_newer_brief_does_not_break_a_review_of_the_cited_one(env, monkeypatch):
    """2026-11-16 replay: a brief published between the trigger and the run made the review fail outright."""
    from ats.workflow import read_scope

    newer = {"projection_id": "brief-2", "payload": {"fact_changes": ["新的一天"], "event_components": []}}
    seen = []

    def read(store, **kw):
        seen.append(kw.get("projection_id"))
        return {"brief-1": BRIEF, "": newer}.get(kw.get("projection_id", ""), None)

    monkeypatch.setattr(read_scope, "read_projection", read)
    store = Store()
    mat.run_material_pass(material_request("NVDA", component_refs=["brief-1#NVDA-1"]), store=store,
                          rate=lambda ctx: _review_view())
    assert seen == ["brief-1"] and "brief-1" in store.saved[-1].input_refs
