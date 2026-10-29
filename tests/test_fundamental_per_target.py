"""Fundamental is chosen per target; an event cycle is started by the targets with an event (D3-3).

The research-snapshot contract counts Fundamental as satisfied by EITHER the Routine update
or the event review. Each target takes the usable, newer of the two, so a name that just
reported is read through its event review while the others keep their Routine update; an
event cycle needs only its trigger entities' event reviews.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.graph.chief import assemble_context, route_after_assemble
from ats.graph.chief_state import ChiefDecisionState
from ats.memory.store import TradingMemory
from ats.workflow import decision_requirements as dr
from ats.workflow.cutover_routing import RouteUnavailable
from ats.workflow.planning import TASK_ROLE, build_plan, task_config_hashes
from ats.workflow.run_contracts import ContractError, TriggerContext
from phase_d_frozen_harness import freeze_config, producer_inputs

pytestmark = pytest.mark.usefixtures("open_decision_read_gate")

NOW = datetime.now(timezone.utc) - timedelta(seconds=2)
TARGETS = ("COHR", "LITE")


@pytest.fixture
def universe(tmp_path, monkeypatch):
    freeze_config(tmp_path, monkeypatch, targets=TARGETS)
    store = TradingMemory(tmp_path / "chief.sqlite")
    monkeypatch.setattr("ats.memory.get_store", lambda: store)
    monkeypatch.setattr("ats.graph.chief._now", lambda: NOW)
    monkeypatch.setattr("ats.config.load_pead_global", lambda: {
        "targets": list(TARGETS), "observe": [], "monitor": {},
        "sector_review": {"sectors": ["ai_hardware"]}, "macro_review": {"name": "macro"}})
    monkeypatch.setattr("ats.config.load_sector_config", lambda name="ai_hardware": SimpleNamespace(
        layers=[SimpleNamespace(key="L1_app"), SimpleNamespace(key="L3_optics")]))
    return tmp_path, store


def _state(**kw):
    base = dict(cycle_id="chief-per-target", as_of=NOW, source="chief", decide=True,
                fundamental_mode="routine", use_llm=False, use_broker=False)
    base.update(kw)
    return ChiefDecisionState(**base)


def _env(role, payload, scope, *, age=timedelta(0)):
    return build_envelope(role=role, payload=payload, scope=scope,
                          as_of=(NOW - age).isoformat(timespec="seconds"),
                          valid_until=(NOW + timedelta(hours=48)).isoformat(timespec="seconds"),
                          data_vintage_refs=["dataset@test"])


def _routine(sym, age=timedelta(hours=1)):
    return _env("fundamental_expectation_update",
                {"entity": sym, "metric": "gross_margin", "period": "Q3 FY2026",
                 "previous_value": 0.25, "new_value": 0.26, "driver": f"{sym} 例行读数"},
                ProjectionScope(kind="entity", id=sym), age=age)


def _event(sym, age=timedelta(minutes=10)):
    return _env("fundamental_event_review",
                {"entity": sym, "event": "earnings", "period": "Q3 FY2026", "direction": 1,
                 "magnitude": 0.05, "falsifiable_conditions": ["指引低于区间即证伪"],
                 "confidence": 0.7},
                ProjectionScope(kind="entity", id=sym), age=age)


def _others(brief_age=timedelta(0)):
    rows = [_env("layer_analysis", {"layer": key, "status": "expanding", "summary": f"{key} up",
                                    "findings": ["b2b 1.4"], "confidence": 0.8},
                 ProjectionScope(kind="layer", id=key)) for key in ("L1_app", "L3_optics")]
    for sym in TARGETS:
        rows.append(_env("information_brief",
                         {"entity": sym, "headline": "订单走强", "summary": "指引上修",
                          "relevance": "high", "sources": ["rss:test"], "fact_changes": ["+12%"],
                          "impact_candidates": ["demand"], "entities": [sym], "confidence": 0.7,
                          "freshness": "today", "unverified": []},
                         ProjectionScope(kind="entity", id=sym), age=brief_age))
        rows.append(_env("technical_review", {"entity": sym, "signal": "neutral", "summary": "震荡",
                                              "levels": {"support": 1.0}},
                         ProjectionScope(kind="entity", id=sym)))
    rows.append(_env("sector_allocation", {"sector": "ai_hardware", "stance": "overweight",
                                           "target_weight": 0.4, "rationale": "扩张",
                                           "drivers": ["L3_optics: expanding"]},
                     ProjectionScope(kind="sector", id="ai_hardware")))
    rows.append(_env("macro_review", {"regime": "risk_on", "summary": "利率见顶",
                                      "indicators": ["10Y 4.0"]}, ProjectionScope(kind="portfolio")))
    return rows


def _publish(tmp_path, store, fundamentals, state, *, brief_age=timedelta(0)):
    """Publish research with the lineage of the plan the cycle will freeze."""
    rows = _others(brief_age) + list(fundamentals)
    probe = TradingMemory(tmp_path / f"probe-{len(list(tmp_path.iterdir()))}.sqlite")
    for env in rows:
        probe.save_task_projection_envelope(env)
    plan = dr.restore(dr.standalone(state, probe))
    by_binding = {(e.agent_role, e.scope.key): e for e in rows}
    selected = {}
    for task in plan.tasks:
        env = by_binding.get((TASK_ROLE[task.task_id], task.scope.key))
        if env is None:
            continue
        env = build_envelope(role=env.agent_role, payload=env.payload, scope=env.scope,
            as_of=env.as_of, valid_until=env.valid_until,
            input_refs=producer_inputs(plan, task, {k: selected[k] for k in task.dependencies}),
            data_vintage_refs=env.data_vintage_refs)
        by_binding[(env.agent_role, env.scope.key)] = env
        selected[task.instance_key] = env
    for env in by_binding.values():
        store.save_task_projection_envelope(env)
    return plan


# --- the planner ------------------------------------------------------------------------------ #

def _plan(tmp_path, monkeypatch, **inputs):
    root = freeze_config(tmp_path, monkeypatch, targets=TARGETS)
    return build_plan(requested_tasks=("sector-review", "fundamental-routine", "fundamental-event",
                                       "macro-review", "technical-review"),
                      scope=ProjectionScope(kind="sector", id="ai_hardware"),
                      trigger=TriggerContext(kind="manual", trigger_id="t"), as_of=NOW.isoformat(),
                      run_id="t", profile_id="ai_hardware", enter_decision_cycle=True,
                      config_dir=root, task_inputs=inputs)


def test_a_plan_assigns_each_target_exactly_one_fundamental_mode(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch, fundamental_modes={"COHR": "fundamental-event",
                                                          "LITE": "fundamental-routine"})
    fundamental = sorted((t.task_id, t.scope.id) for t in plan.tasks if t.task_id.startswith("fundamental"))
    assert fundamental == [("fundamental-event", "COHR"), ("fundamental-routine", "LITE")]


@pytest.mark.parametrize("inputs", [
    {"fundamental_modes": {"COHR": "fundamental-event"}},                      # LITE unaccounted for
    {"fundamental_modes": {"COHR": "fundamental-event"}, "fundamental_uncovered": ["LITE"]},  # not an event cycle
    {"fundamental_modes": {"COHR": "fundamental-routine"}, "fundamental_uncovered": ["LITE"],
     "event_entities": ["COHR"]},                                               # trigger not on its event review
])
def test_a_plan_refuses_an_incomplete_or_inconsistent_fundamental_cover(tmp_path, monkeypatch, inputs):
    with pytest.raises(ContractError):
        _plan(tmp_path, monkeypatch, **inputs)


def test_an_entity_task_records_only_its_own_entity_config(tmp_path, monkeypatch):
    plan = _plan(tmp_path, monkeypatch, fundamental_modes={"COHR": "fundamental-routine",
                                                          "LITE": "fundamental-routine"})
    brief = next(t for t in plan.tasks if t.task_id == "information-brief" and t.scope.id == "COHR")
    paths = set(task_config_hashes(plan, brief.task_id, brief.scope))
    assert "config/pead/COHR.yaml" in paths and "config/pead/LITE.yaml" not in paths


# --- the cycle -------------------------------------------------------------------------------- #

def test_a_routine_cycle_reads_the_event_review_of_the_name_that_just_reported(universe):
    tmp_path, store = universe
    plan = _publish(tmp_path, store, [_routine("COHR"), _event("COHR"), _routine("LITE")], _state())
    assert plan.task_inputs["fundamental_modes"] == {"COHR": "fundamental-event",
                                                     "LITE": "fundamental-routine"}
    out = assemble_context(_state())
    assert not out.get("gap_report"), out.get("gap_report")
    text = out["context_text"]
    assert "fundamental_event_review [entity:COHR] fresh" in text
    assert "fundamental_expectation_update [entity:LITE] fresh" in text
    assert "fundamental_expectation_update [entity:COHR]" not in text


def test_an_event_cycle_runs_on_the_names_with_an_event_and_states_the_rest(universe):
    tmp_path, store = universe
    state = _state(fundamental_mode="event")
    plan = _publish(tmp_path, store, [_event("COHR"), _routine("LITE", age=timedelta(days=4))], state)
    assert plan.task_inputs["event_entities"] == ["COHR"]
    assert plan.task_inputs["fundamental_uncovered"] == ["LITE"]
    out = assemble_context(state)
    assert not out.get("gap_report"), out.get("gap_report")
    text = out["context_text"]
    assert "本轮事件标的（财报 / 公告后的事件评审）：COHR" in text
    assert "无新近基本面研究" in text and "LITE" in text.split("无新近基本面研究", 1)[1].split("\n", 1)[0]
    assert "fundamental_event_review [entity:COHR] fresh" in text


def test_an_event_cycle_whose_trigger_has_no_event_review_is_blocked(universe):
    tmp_path, store = universe
    state = _state(fundamental_mode="event", event_entities=["LITE"])
    _publish(tmp_path, store, [_event("COHR"), _routine("LITE")], state)
    out = assemble_context(state)
    assert out["gap_report"] and "LITE" in out["gap_report"] and out["context_text"] == ""
    assert route_after_assemble(_state(gap_report=out["gap_report"])) == "end"


def test_an_event_cycle_with_no_event_anywhere_is_blocked(universe):
    tmp_path, store = universe
    for env in _others() + [_routine("COHR"), _routine("LITE")]:
        store.save_task_projection_envelope(env)
    out = assemble_context(_state(fundamental_mode="event"))
    assert "事件周期没有可用的事件评审" in out["gap_report"] and out["context_text"] == ""


def test_a_routine_cycle_still_blocks_on_a_target_with_no_usable_fundamental(universe):
    tmp_path, store = universe
    _publish(tmp_path, store, [_routine("COHR"), _routine("LITE", age=timedelta(days=4))], _state())
    # past its 72-hour window the Routine update is refused by the frozen-input check, as before
    with pytest.raises(RouteUnavailable, match="fundamental-routine:entity:LITE.*freshness_unconfirmed"):
        assemble_context(_state())


def test_a_usable_event_review_wins_over_a_later_routine_note(universe):
    """Routine runs several times a day; a minor note after the earnings must not hide the review."""
    tmp_path, store = universe
    plan = _publish(tmp_path, store, [_event("COHR", age=timedelta(hours=6)),
                                      _routine("COHR", age=timedelta(minutes=5)), _routine("LITE")],
                    _state())
    assert plan.task_inputs["fundamental_modes"]["COHR"] == "fundamental-event"
    out = assemble_context(_state())
    assert not out.get("gap_report") and "fundamental_event_review [entity:COHR] fresh" in out["context_text"]


def test_an_expired_event_review_hands_back_to_routine(universe):
    tmp_path, store = universe
    plan = _publish(tmp_path, store, [_event("COHR", age=timedelta(days=5)), _routine("COHR"),
                                      _routine("LITE")], _state())
    assert plan.task_inputs["fundamental_modes"]["COHR"] == "fundamental-routine"


def test_the_brief_an_event_review_was_built_from_is_selected_not_the_newest(universe):
    """Briefs refresh more often than the event review that read them (GOOG, 2026-10-29)."""
    tmp_path, store = universe
    _publish(tmp_path, store, [_event("COHR", age=timedelta(hours=3)), _routine("LITE")],
             _state(fundamental_mode="event"), brief_age=timedelta(hours=4))
    newer = _env("information_brief", {"entity": "COHR", "headline": "又一条", "summary": "后续消息",
                                       "relevance": "low", "sources": ["rss:test"], "fact_changes": [],
                                       "impact_candidates": [], "entities": ["COHR"], "confidence": 0.5,
                                       "freshness": "today", "unverified": []},
                 ProjectionScope(kind="entity", id="COHR"), age=timedelta(minutes=1))
    store.save_task_projection_envelope(newer)
    out = assemble_context(_state(fundamental_mode="event"))
    assert not out.get("gap_report"), out.get("gap_report")
    briefs = [i for i in out["research_snapshot"]["items"]
              if i["agent_role"] == "information_brief" and i["scope_id"] == "COHR"]
    assert briefs and briefs[0]["projection_id"] != newer.projection_id


def test_an_event_review_whose_brief_has_expired_hands_back_to_routine(universe):
    tmp_path, store = universe
    _publish(tmp_path, store, [_event("COHR", age=timedelta(hours=1)), _routine("LITE")],
             _state(fundamental_mode="event"), brief_age=timedelta(hours=130))
    store.save_task_projection_envelope(_routine("COHR"))
    # the published event review names its brief, which is past its 120 hours
    plan = dr.restore(dr.standalone(_state(), store))
    assert plan.task_inputs["fundamental_modes"]["COHR"] == "fundamental-routine"


def test_a_brief_inside_its_120_hours_keeps_the_event_review_in_use(universe):
    """2026-10-30 ruling: briefs stay usable 120 hours, so a 4-day event review is not cut short."""
    tmp_path, store = universe
    _publish(tmp_path, store, [_event("COHR", age=timedelta(days=3)), _routine("LITE")],
             _state(fundamental_mode="event"), brief_age=timedelta(hours=100))
    store.save_task_projection_envelope(_routine("COHR"))
    plan = dr.restore(dr.standalone(_state(), store))
    assert plan.task_inputs["fundamental_modes"]["COHR"] == "fundamental-event"
