"""material_development routing, dedupe and ledger (event-impact-routing 7.1–7.2, 2026-11-16)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ats.workflow import material_events as me

NOW = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)
CHAIN = {"layers": {"L5_chip_design", "L7_foundry_pkg"}, "member_of": {"NVDA": ["L5_chip_design"],
                                                                       "TSM": ["L7_foundry_pkg"]}}


class Store:
    def __init__(self, briefs):
        self.briefs = briefs

    def task_projection_envelopes(self, **_):
        return self.briefs


def _brief(pid, entity, comps, *, minutes_ago=30):
    return {"projection_id": pid, "scope_id": entity, "created_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
            "payload": {"event_components": comps}}


def _comp(cid, scope, materiality="major", *, layers=(), novelty="first", cluster="nvda-ceo", content=None):
    return {"component_id": cid, "cluster_key": cluster, "scope": scope, "layers": list(layers),
            "materiality": materiality, "direction": "negative", "novelty": novelty, "content": content or cid}


class Runner:
    def __init__(self):
        self.calls, self.seen = [], set()

    def __call__(self, workflow_id, *, scope, trigger, request, now, config_dir):
        self.calls.append((workflow_id, scope.key, request["task_inputs"], request["enter_decision_cycle"]))
        ident = (trigger.event_id, trigger.event_version, workflow_id)
        dup = ident in self.seen
        self.seen.add(ident)
        return {"status": "complete", "duplicate": dup, "trigger_key": f"t-{len(self.calls)}", "run_id": "r"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(me, "settings", lambda config_dir=None: {"enabled": True, "min_materiality": "major",
                                                                  "lookback_hours": 6})
    monkeypatch.setattr(me, "chain_index", lambda sector=me.SECTOR: CHAIN)
    monkeypatch.setattr(me, "_covered", lambda entity: entity in {"NVDA", "TSM", "AAPL"})
    return str(tmp_path / "ledger.sqlite")


def _run(store, ledger, runner, now=NOW):
    return me.dispatch_material_developments(store=store, now=now, ledger_path=ledger, runner=runner)


def test_routes_load_from_the_allow_list_and_refuse_a_decision_cycle(tmp_path):
    routes = me.load_routes()
    assert {r["scope_resolver"] for r in routes} == me.RESOLVERS
    bad = tmp_path / "workflow"
    bad.mkdir()
    (bad / "event_routes.yaml").write_text(
        "material_development:\n  - {scope_resolver: component_entity, workflow_ids: [fundamental-material],"
        " enter_decision_cycle: true}\n", encoding="utf-8")
    with pytest.raises(me.RouteError):
        me.load_routes(tmp_path)


def test_a_company_part_and_a_cross_layer_part_route_independently(env):
    store = Store([_brief("b1", "NVDA", [_comp("c-own", "entity"),
                                         _comp("c-x", "cross_layer", layers=["L5_chip_design", "L7_foundry_pkg"],
                                               cluster="export-push")])])
    runner = Runner()
    _run(store, env, runner)
    called = [(wf, scope) for wf, scope, _inputs, _cycle in runner.calls]
    # the company part → its material review AND (chain member, D2b) its layer; the cross-layer part → both layers
    assert called[:2] == [("fundamental-material", "entity:NVDA"), ("layer-review", "layer:L5_chip_design")]
    assert ("layer-review", "layer:L7_foundry_pkg") in called
    assert called[-1] == ("sector-review", "sector:ai_hardware")          # once, after the layer reviews
    assert sum(1 for wf, _ in called if wf == "sector-review") == 1
    assert all(cycle is False for *_x, cycle in runner.calls)              # never a decision cycle
    material_inputs = runner.calls[0][2]
    assert material_inputs["fundamental-material"]["component_refs"] == ["b1#c-own"]
    assert runner.calls[-1][2]["sector-review"]["trigger_source"] == "material_development"


def test_moderate_components_trigger_nothing(env):
    runner = Runner()
    _run(Store([_brief("b1", "NVDA", [_comp("c", "entity", "moderate")])]), env, runner)
    assert runner.calls == [] and me.ledger_rows(env) == []


def test_a_follow_up_report_is_deduplicated_and_ledgered(env):
    runner = Runner()
    _run(Store([_brief("b1", "NVDA", [_comp("c1", "entity")])]), env, runner)
    first = len(runner.calls)
    _run(Store([_brief("b2", "NVDA", [_comp("c2", "entity", novelty="follow_up", content="same story again")])]),
         env, runner)
    assert len(runner.calls) == first
    rows = me.ledger_rows(env, component_id="c2")
    assert rows and {r["outcome"] for r in rows} == {"deduplicated"} and rows[0]["brief_projection_id"] == "b2"


def test_an_escalation_in_the_same_cluster_triggers_again_and_a_rescan_does_not(env):
    runner = Runner()
    store = Store([_brief("b1", "NVDA", [_comp("c1", "entity")])])
    _run(store, env, runner)
    n = len(runner.calls)
    _run(store, env, runner, now=NOW + timedelta(minutes=30))           # same brief seen on the next tick
    assert len(runner.calls) == n
    _run(Store([_brief("b3", "NVDA", [_comp("c3", "entity", "critical", novelty="escalation",
                                            content="board removes the CEO")])]), env, runner)
    assert len(runner.calls) > n
    triggered = [r for r in me.ledger_rows(env, cluster_key="nvda-ceo") if r["outcome"] == "triggered"]
    assert {r["component_id"] for r in triggered} == {"c1", "c3"}


def test_an_unregistered_layer_is_refused_with_a_reason(env):
    runner = Runner()
    _run(Store([_brief("b1", "AAPL", [_comp("c", "layer", layers=["L9_quantum"], cluster="q")])]), env, runner)
    assert runner.calls == []
    row = me.ledger_rows(env, component_id="c")[0]
    assert row["outcome"] == "rejected" and "L9_quantum" in row["reason"]


def test_off_by_default():
    assert me.settings()["enabled"] is False
    assert me.dispatch_material_developments(store=Store([]), runner=Runner()) == []


def test_one_tick_runs_each_task_and_scope_once_with_all_its_components(env):
    """2026-11-16 replay: 11-09 13:05 ran L5 four times and the NVDA review twice."""
    store = Store([_brief("b1", "NVDA", [_comp("c1", "entity"), _comp("c2", "entity", content="second part"),
                                         _comp("c3", "cross_layer", layers=["L5_chip_design"], cluster="other")])])
    runner = Runner()
    _run(store, env, runner)
    called = [(wf, scope) for wf, scope, _i, _c in runner.calls]
    assert called.count(("fundamental-material", "entity:NVDA")) == 1
    assert called.count(("layer-review", "layer:L5_chip_design")) == 1
    refs = runner.calls[0][2]["fundamental-material"]["component_refs"]
    assert refs == ["b1#c1", "b1#c2"]
    l5 = [r for r in me.ledger_rows(env) if r["workflow_id"] == "layer-review"]
    assert {r["component_id"] for r in l5} == {"c1", "c2", "c3"} and len({r["trigger_key"] for r in l5}) == 1
