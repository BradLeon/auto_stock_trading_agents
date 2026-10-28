"""Sector consumer cutover (Phase F 10.5, 2026-10-17): health check, data-vintage reference and the Layer dependency.

Products, projections and configuration are explicit fakes. They prove what the check treats as a failure and a
warning, that it never goes through a consumer read gate, the memory store, a provider or the network, that the
vintage changes exactly when an input of the review changes, and that a Sector plan binds exactly the eight Layer
instances the Dispatcher must complete or reuse first.
"""
import json
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ats.workflow import consumer_health as health
from ats.workflow import layer_sector_inputs as inp

NOW = datetime(2026, 10, 17, 1, 0, tzinfo=UTC)          # a Saturday
LAYERS = ["L1", "L2"]


def layer(key, symbols):
    return SimpleNamespace(key=key, tickers=[SimpleNamespace(symbol=s) for s in symbols], cohort_extra=[], claims=[])


def stored(key, *, usable=True, problem="", days=0):
    created = (NOW - timedelta(days=days)).isoformat()
    return {"projection_id": f"p-{key}", "as_of": created, "created_at": created, "usable": usable, "problem": problem}


class Structured:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class Unstructured(Structured):
    pass


class Products:
    def __init__(self, consensus):
        self.structured, self.unstructured, self._consensus = Structured(), Unstructured(), consensus

    def consensus_snapshot(self, *, entity, as_of):
        value = self._consensus[entity]
        if isinstance(value, Exception):
            raise value
        return {"known_at": value}


@pytest.fixture
def world(monkeypatch):
    fresh = (NOW - timedelta(days=1)).isoformat()
    state = SimpleNamespace(
        layers=[layer("L1", ["AAA", "BBB"]), layer("L2", ["CCC"])],
        projections={key: stored(key) for key in LAYERS},
        covered={"AAA": True, "BBB": True, "CCC": True},
        consensus={"AAA": fresh, "BBB": fresh, "CCC": fresh},
        factset={"text": "packet", "state": "published", "version_id": "fs-1", "report_date": "2026-10-09",
                 "freshness": "latest_released", "reason": ""})
    state.products = Products(state.consensus)
    cfg = lambda: SimpleNamespace(layers=state.layers)
    monkeypatch.setattr(inp, "configured_sectors", lambda: ["demo"])
    monkeypatch.setattr(inp, "load_sector", lambda name: cfg())
    monkeypatch.setattr(inp, "cohort", lambda layer_: [t.symbol for t in layer_.tickers])
    monkeypatch.setattr(inp, "latest_layer_projections", lambda: dict(state.projections))
    monkeypatch.setattr(inp, "financial_state", lambda products, symbol, *, as_of: {
        "covered": state.covered[symbol], "source": "src" if state.covered[symbol] else "", "period": "2026-06-30",
        "observations": [f"{symbol}-obs"] if state.covered[symbol] else []})
    monkeypatch.setattr("ats.data.products.get_platform_data_products", lambda readonly=False: state.products)
    monkeypatch.setattr("ats.data.factset.fetch_sector_material", lambda products=None: dict(state.factset))
    monkeypatch.setattr("ats.memory.get_store", lambda: pytest.fail("health check must not open the memory store"))
    for name in ("ats.workflow.runtime_reads.gate_read", "ats.workflow.cutover_routing.read_route",
                 "ats.data.consumer_api.read_input"):
        monkeypatch.setattr(name, lambda *a, **k: pytest.fail("health check went through a consumer read gate"))
    monkeypatch.setattr("socket.socket.connect", lambda *a, **k: pytest.fail("health check used the network"))
    return state


def test_sector_is_healthy_with_all_layer_projections_and_full_cohort_coverage(world):
    result = health.check("sector", now=NOW)
    assert result["status"] == "ok" and result["problems"] == [] and result["warnings"] == []
    assert set(result["detail"]["layer_projections"]) == set(LAYERS)
    assert world.products.structured.closed and world.products.unstructured.closed
    assert "runtime prices" in result["detail"]["not_probed"]


def test_a_missing_or_unusable_layer_projection_is_a_failure_because_the_entry_needs_all_of_them(world):
    del world.projections["L2"]
    result = health.check("sector", now=NOW)
    assert result["status"] == "failed" and "missing: L2" in result["problems"][0] and "Layer review first" in result["problems"][0]
    world.projections["L2"] = stored("L2", usable=False, problem="content_hash_mismatch")
    result = health.check("sector", now=NOW)
    assert result["status"] == "failed" and "unusable: L2 (content_hash_mismatch)" in result["problems"][0]


def test_no_layer_projection_at_all_names_every_layer(world):
    world.projections.clear()
    result = health.check("sector", now=NOW)
    assert result["status"] == "failed" and "not usable for 2 of 2 layers" in result["problems"][0]


def test_an_old_layer_projection_is_only_a_warning_because_the_dispatcher_rebuilds_it(world):
    world.projections["L1"] = stored("L1", days=health.LAYER_PROJECTION_STALE_DAYS + 2)
    result = health.check("sector", now=NOW)
    assert result["status"] == "ok" and any("older than 7 days for L1" in w for w in result["warnings"])


def test_thin_cohort_coverage_is_a_warning_and_none_at_all_is_a_failure(world):
    world.covered["BBB"] = False
    result = health.check("sector", now=NOW)
    assert result["status"] == "ok" and any("L1: no governed financials for 1 of 2 names (BBB)" in w
                                            for w in result["warnings"])
    assert result["detail"]["financials"]["uncovered"] == ["BBB"]
    world.covered.update(AAA=False, CCC=False)
    result = health.check("sector", now=NOW)
    assert result["status"] == "failed" and any("no governed financials for any of the 3 cohort names" in p
                                                for p in result["problems"])


def test_stale_or_absent_consensus_is_a_warning_and_none_at_all_is_a_failure(world):
    world.consensus["AAA"] = (NOW - timedelta(days=health.SECTOR_CONSENSUS_STALE_DAYS + 5)).isoformat()
    world.consensus["BBB"] = None
    result = health.check("sector", now=NOW)
    assert result["status"] == "ok"
    assert any("consensus older than 14 days for 1 names (AAA)" in w for w in result["warnings"])
    assert any("no platform consensus for 1 names (BBB)" in w for w in result["warnings"])
    world.consensus.update(AAA=None, CCC=None)
    result = health.check("sector", now=NOW)
    assert result["status"] == "failed" and any("no platform consensus for any" in p for p in result["problems"])


def test_an_unreadable_product_is_a_failure(world):
    world.consensus["CCC"] = RuntimeError("database is locked")
    result = health.check("sector", now=NOW)
    assert result["status"] == "failed" and any("CCC: consensus snapshot unreadable" in p for p in result["problems"])


def test_an_unavailable_factset_packet_is_a_warning_the_rotation_prompt_already_says_so(world):
    world.factset.update(text="", state="registered_no_data", reason="所选报告尚未发布可用的十一行业分区。")
    result = health.check("sector", now=NOW)
    assert result["status"] == "ok" and any("FactSet sector packet unavailable" in w for w in result["warnings"])
    assert result["detail"]["factset"]["version_id"] == "fs-1"


def test_a_sector_config_without_layers_is_a_failure(world, monkeypatch):
    monkeypatch.setattr(inp, "load_sector", lambda name: SimpleNamespace(layers=[]))
    assert any("no layers configured" in p for p in health.check("sector", now=NOW)["problems"])


# --- data vintage -------------------------------------------------------------------------------------------
def vintage(world):
    return inp.sector_vintage("demo", NOW)


def test_the_vintage_names_financials_consensus_factset_session_and_the_budget_switch(world, monkeypatch):
    monkeypatch.setattr("ats.config.load_pead_global", lambda: {"sector_review": {"bind_layer_budget": True}})
    refs = vintage(world)
    assert [r.split(":")[0] for r in refs] == ["financials", "consensus", "factset", "market-session", "layer-budget-bound"]
    assert refs[3] == "market-session:demo:2026-10-16:after-close"        # Saturday -> Friday's close
    assert refs[2] == "factset:fs-1:published" and refs[4] == "layer-budget-bound:1"
    assert world.products.structured.closed and world.products.unstructured.closed


def test_the_vintage_is_stable_and_changes_exactly_when_an_input_changes(world, monkeypatch):
    monkeypatch.setattr("ats.config.load_pead_global", lambda: {"sector_review": {}})
    first = vintage(world)
    assert vintage(world) == first
    world.covered["BBB"] = False                                       # a financial package appears or goes
    assert vintage(world) != first
    world.covered["BBB"] = True
    world.consensus["AAA"] = (NOW - timedelta(hours=3)).isoformat()    # a new consensus snapshot
    assert vintage(world) != first
    world.consensus["AAA"] = (NOW - timedelta(days=1)).isoformat()
    world.factset["version_id"] = "fs-2"                               # a new FactSet report
    assert vintage(world) != first
    world.factset["version_id"] = "fs-1"
    assert vintage(world) == first
    monkeypatch.setattr("ats.config.load_pead_global", lambda: {"sector_review": {"bind_layer_budget": False}})
    assert vintage(world) != first                                     # the budget switch changes the output


@pytest.mark.parametrize("utc,expected", [
    ("2026-10-16T14:00:00+00:00", "market-session:s:2026-10-16:before-close"),   # Friday 10:00 ET
    ("2026-10-16T21:00:00+00:00", "market-session:s:2026-10-16:after-close"),    # Friday 17:00 ET
    ("2026-10-17T01:00:00+00:00", "market-session:s:2026-10-16:after-close"),    # Friday 21:00 ET
    ("2026-10-17T15:00:00+00:00", "market-session:s:2026-10-16:after-close"),    # Saturday: nothing traded
    ("2026-10-18T22:00:00+00:00", "market-session:s:2026-10-16:after-close"),    # Sunday
    ("2026-10-19T14:00:00+00:00", "market-session:s:2026-10-19:before-close"),   # Monday morning: new session
])
def test_runtime_prices_are_reused_within_a_session_and_a_weekend_maps_to_fridays_close(utc, expected):
    assert inp.session_ref(datetime.fromisoformat(utc), "s") == expected


def test_default_data_vintage_resolves_a_sector_task_and_needs_a_named_sector(world):
    from ats.workflow.dispatcher import default_data_vintage

    plan = SimpleNamespace(request_scope=SimpleNamespace(kind="sector", id="demo"), as_of=NOW.isoformat())
    task = SimpleNamespace(task_id="sector-review", scope=SimpleNamespace(kind="sector", id="demo"))
    assert default_data_vintage(plan, task, {})[0].startswith("financials:demo:3@")
    portfolio = SimpleNamespace(request_scope=SimpleNamespace(kind="portfolio", id="x"), as_of=NOW.isoformat())
    assert default_data_vintage(portfolio, task, {}) == []


# --- the plan binds exactly the eight Layer instances ---------------------------------------------------------
def real_plan():
    from ats.agent.task_projection import ProjectionScope
    from ats.workflow.planning import build_plan
    from ats.workflow.run_contracts import TriggerContext

    return build_plan(requested_tasks=("sector-review",), scope=ProjectionScope(kind="sector", id="ai_hardware"),
                      trigger=TriggerContext(kind="schedule", workflow_id="sector-review", schedule_id="sector-review",
                                             scheduled_for="2026-10-17T01:00:00+00:00"),
                      as_of="2026-10-17T01:00:00+00:00", run_id="plan-test")


def test_a_sector_plan_completes_the_layer_dependency_and_binds_exactly_the_configured_layers():
    from ats.config import load_sector_config

    plan = real_plan()
    keys = [layer_.key for layer_ in load_sector_config("ai_hardware").layers]
    layer_tasks = [t for t in plan.tasks if t.task_id == "layer-review"]
    sector_task = next(t for t in plan.tasks if t.task_id == "sector-review")
    assert plan.requested_tasks == ("sector-review",) and len(keys) == 8
    assert sorted(t.scope.id for t in layer_tasks) == sorted(keys)
    assert sorted(sector_task.dependencies) == sorted(t.instance_key for t in layer_tasks)       # and nothing else
    assert sorted(plan.edges) == sorted((t.instance_key, sector_task.instance_key) for t in layer_tasks)
    assert all(not t.dependencies for t in layer_tasks)


def test_the_sector_task_inputs_carry_every_layer_projection_so_a_rebuilt_layer_forces_a_rebuild():
    from ats.agent.task_projection import ProjectionScope, build_envelope
    from ats.workflow.dispatcher import Dispatcher

    plan = real_plan()
    sector_task = next(t for t in plan.tasks if t.task_id == "sector-review")
    deps = {}
    for t in plan.tasks:
        if t.task_id == "layer-review":
            deps[t.instance_key] = build_envelope(
                role="layer_analysis", scope=ProjectionScope(kind="layer", id=t.scope.id), as_of=plan.as_of,
                payload={"layer": t.scope.id, "status": "steady", "summary": "s", "findings": ["f"], "confidence": 0.3})
    input_refs, data_refs, _ = Dispatcher(vintage_resolver=lambda p, t, i: ["v"])._task_inputs(plan, sector_task, deps)
    for env in deps.values():
        assert f"projection:{env.projection_id}:{env.content_hash}" in input_refs
    assert data_refs == ["v"]


def test_reusing_the_sector_needs_the_same_layer_projections_and_the_same_vintage():
    from ats.agent.task_projection import ProjectionScope, build_envelope
    from ats.workflow.dispatcher import Dispatcher
    from ats.workflow.evaluation_clock import now

    task = SimpleNamespace(task_id="sector-review", scope=SimpleNamespace(key="sector:ai_hardware", kind="sector", id="ai_hardware"))
    stamp = now(UTC).isoformat(timespec="seconds")
    env = build_envelope(role="sector_allocation", scope=ProjectionScope(kind="sector", id="ai_hardware"), as_of=stamp,
                         payload={"sector": "ai_hardware", "stance": "neutral", "target_weight": 0.5, "rationale": "r",
                                  "drivers": ["d"]},
                         input_refs=["projection:aaa:h", "aaa", "config:risk.yaml@1"], data_vintage_refs=["v1"],
                         agent_run_id="A")
    row = env.model_dump(mode="json")
    row.update(scope_kind="sector", scope_id="ai_hardware")
    memory = SimpleNamespace(
        task_projection_envelopes=lambda **kw: [row],
        conn=SimpleNamespace(execute=lambda sql, args: SimpleNamespace(fetchone=lambda: {
            "status": "succeeded", "projection_refs_json": json.dumps([row["projection_id"]])})))

    def select(input_refs, vintage_refs):
        return Dispatcher()._select_reusable(memory, task, input_refs, vintage_refs, stamp)

    reused, detail = select(["projection:aaa:h", "aaa", "config:risk.yaml@1"], ["v1"])
    assert detail["decision"] == "hit" and reused.projection_id == row["projection_id"]
    assert select(["projection:bbb:h", "bbb", "config:risk.yaml@1"], ["v1"])[1]["decision"] == "recompute"   # layer rebuilt
    assert select(["projection:aaa:h", "aaa", "config:risk.yaml@1"], ["v2"])[1]["decision"] == "recompute"   # data moved


# --- the Layer dependency has to be owned by the Dispatcher too ------------------------------------------------
OK = {"status": "ok", "problems": [], "warnings": []}


def test_a_sector_plan_is_refused_unless_the_layer_dependency_is_dispatcher_owned_as_well(tmp_path):
    from ats.workflow import consumer_cutover as cc
    from ats.workflow import cutover_wiring as cw
    from ats.workflow import schedule_runtime as rt
    from ats.workflow.isolation import isolated_run

    layer_scope = {"kind": "layer", "id": "L1_app"}
    sector_scope = {"kind": "sector", "id": "ai_hardware"}
    with isolated_run("sector-dependency", root=tmp_path / "iso") as environment:
        path = environment.path_for("ATS_CUTOVER_DB")
        cw.bootstrap_wired(path=path)
        for consumer in ("layer", "sector"):
            cc.switch(consumer, actor="op", approval="chat", stop_conditions="health fails", health=OK, path=path,
                      observe_until=(datetime.now(UTC) + timedelta(hours=72)).isoformat())
        with closing(rt._connect(write=True)) as conn:
            conn.execute("INSERT INTO schedule_scope_owners VALUES(?,?,?,1,0,'')",
                         ("layer-review", rt.canonical(layer_scope), "legacy"))
            conn.execute("INSERT INTO schedule_scope_owners VALUES(?,?,?,1,0,'')",
                         ("sector-review", rt.canonical(sector_scope), "legacy"))
        go = lambda workflow: rt.owner_switch(workflow, to_owner="dispatcher", actor="op", reason="drill",
                                              approval="user approved", health_check=lambda c: OK)
        go("sector-review")
        rt.check_owner("sector-review", sector_scope, "dispatcher")                  # the Sector row is switched
        with pytest.raises(rt.ScheduleAuthorityError, match="fenced"):               # but its dependency is not
            rt.check_owner("layer-review", layer_scope, "dispatcher")
        go("layer-review")
        rt.check_owner("layer-review", layer_scope, "dispatcher")
