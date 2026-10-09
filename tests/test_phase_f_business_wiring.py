"""Real business gates; synthetic qualification never proves production readiness."""
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.data.consumer_api import read_input
from ats.decision.repository import DecisionAuditRepository
from ats.execution import broker_write_guard as broker_guard
from ats.execution.clerk import clerk_run
from ats.memory.store import TradingMemory
from ats.workflow import cutover as co
from ats.workflow import runtime_reads as rr
from ats.workflow.cutover_routing import RouteUnavailable
from ats.workflow.cutover_wiring import BoundaryWriteRefused
from ats.workflow.dispatcher import Dispatcher, TaskAdapterResult
from ats.workflow.isolation import isolated_run
from ats.workflow.phase_e import build_plan
from ats.workflow.run_contracts import TriggerContext
from ats.workflow.scoped_routes import transition_route
from ats.workflow.store import WorkflowStore

POINT = "2026-10-08T00:00:00+00:00"


@pytest.fixture(autouse=True)
def clean_guard():
    broker_guard.reset_for_tests()
    yield
    broker_guard.reset_for_tests()


def write(store, entry):
    if entry == "review":
        return DecisionAuditRepository(store).record_review(
            review_id="review", cycle_id="cycle", revision_no=1, decision_hash="hash",
            ruleset_version="rules", portfolio_snapshot_id="portfolio", market_as_of=POINT,
            verdict="approved")
    if entry == "approval":
        return DecisionAuditRepository(store).record_approval(
            approval_id="approval", cycle_id="cycle", revision_no=1, decision_hash="hash",
            decision="approved", reviewer="test", idempotency_key="approval")
    if entry == "legacy":
        return store.save_task_projection(profile="test", profile_version="v1", input_kind="entity",
                                          input_ref="ref", target_type="entity", target_id="AMD", payload={})
    if entry == "envelope":
        return store.save_task_projection_envelope(build_envelope(
            role="information_brief", scope=ProjectionScope(kind="entity", id="AMD"), as_of=POINT,
            payload={"entity": "AMD", "headline": "h", "summary": "s", "relevance": "high",
                     "sources": ["ref"]}))
    return clerk_run(store=store, broker=object(), steps=())


WRITES = [("review", co.APPROVAL_LIFECYCLE, "decision_risk_reviews"),
          ("approval", co.APPROVAL_LIFECYCLE, "boss_approvals"),
          ("legacy", co.ANALYST_OUTPUT, "task_projections"),
          ("envelope", co.ANALYST_OUTPUT, "task_projection_envelopes"),
          ("clerk", co.CLERK_PUBLICATION, "clerk_runs")]


@pytest.mark.parametrize("entry,boundary,table", WRITES)
def test_disabled_real_publisher_refuses_before_any_write(entry, boundary, table):
    store = TradingMemory(":memory:")
    co.set_route(boundary, "disabled", actor="test", reason="stop")
    before = store.conn.total_changes
    with pytest.raises(BoundaryWriteRefused) as exc:
        write(store, entry)
    assert exc.value.reason_code == "boundary_disabled"
    assert store.conn.total_changes == before
    assert store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("entry,boundary,table", WRITES)
def test_isolated_real_publisher_only_writes_its_database(tmp_path, entry, boundary, table):
    original = TradingMemory(tmp_path / "production.sqlite")
    before = original.conn.total_changes
    with isolated_run("publication", root=tmp_path / "isolated") as env:
        isolated = TradingMemory(env.path_for("ATS_DB_PATH"))
        write(isolated, entry)
        assert isolated.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        # A stale connection's declared path is deliberately misleading.
        original.path = isolated.path
        with pytest.raises(BoundaryWriteRefused, match="destination"):
            write(original, entry)
    assert original.conn.total_changes == before
    assert original.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_clerk_rechecks_between_steps_and_never_swallows_boundary_refusal(monkeypatch):
    from ats.execution import clerk

    store, calls = TradingMemory(":memory:"), []
    def first(*args, **kwargs):
        calls.append("first")
        co.set_route(co.CLERK_PUBLICATION, "disabled", actor="test", reason="stop")
        return {}
    monkeypatch.setattr(clerk, "_STEPS", {"first": first, "second": lambda *a, **k: calls.append("second")})
    with pytest.raises(BoundaryWriteRefused):
        clerk_run(store=store, broker=object(), steps=("first", "second"))
    assert calls == ["first"]
    assert store.conn.execute("SELECT status FROM clerk_runs").fetchone()[0] == "running"


@pytest.fixture
def synthetic_target(monkeypatch):
    monkeypatch.setattr("ats.workflow.boundary_evidence.assert_enforced", lambda *a, **k: None)
    def move(identity):
        transition_route(co.PROJECTION_READ, identity, "target", actor="test", reason="synthetic",
                         expected_generation=0,
                         qualification=lambda i: {"identity": i.as_row(), "status": "eligible"},
                         report=lambda i: {"identity": i.as_row(), "valid": True, "reference": "fake"},
                         fallback=lambda i: {"identity": i.as_row(), "valid": True, "reference": "fake"})
    return move


def entity(consumer="information", symbol="AMD", point=POINT):
    return rr.business_identity(consumer, kind="entity", scope_id=symbol, entities=[symbol], as_of=point)


def test_native_input_uses_exact_identity_and_rechecks_revocation(synthetic_target, monkeypatch):
    now = datetime.now(UTC)
    scope = {"kind": "entity", "id": "AMD", "entities": ["AMD"], "time_range": {
        "start": (now - timedelta(hours=1)).isoformat(), "end": (now + timedelta(hours=1)).isoformat()}}
    identity = rr.business_identity("risk", kind="entity", scope_id="AMD", entities=["AMD"], explicit=scope)
    seen = []
    synthetic_target(identity)
    def qualify(**kwargs):
        seen.append(kwargs)
        return {"status": "eligible" if len(seen) == 1 else "ineligible"}
    monkeypatch.setattr("ats.data.assurance.qualification", qualify)
    packet = read_input("risk", "RISK_RULES", scope={}, risk_config={"max": 10}, business_scope=identity)
    assert packet.status == "complete"
    with pytest.raises(RouteUnavailable):
        read_input("risk", "RISK_RULES", scope={}, risk_config={}, business_scope=identity)
    assert all(row["scope"] == identity.scope and row["domain_id"] == identity.domain_id and
               row["contract_version"] == identity.contract_version for row in seen)
    assert "products" not in seen[0]["scope"]


@pytest.mark.parametrize("change", ["entity", "time", "consumer", "contract"])
def test_scope_changes_cannot_borrow_qualification(change, synthetic_target, monkeypatch):
    first = entity("risk")
    second = entity("risk", "NVDA") if change == "entity" else entity(
        "risk", point="2026-10-09T00:00:00+00:00") if change == "time" else entity("clerk")
    synthetic_target(first)
    if change != "contract":
        synthetic_target(second)
    seen = []
    def qualify(**kwargs):
        seen.append(kwargs)
        return {"status": "eligible" if kwargs["scope"] == first.scope and
                kwargs["consumer_id"] == first.consumer_id else "ineligible"}
    monkeypatch.setattr("ats.data.assurance.qualification", qualify)
    assert rr.gate_read(first).qualified
    if change == "contract":
        forged = first.as_row()
        forged["contract_version"] = "other"
        with pytest.raises(RouteUnavailable, match="contract mismatch"):
            rr.business_identity("risk", kind="entity", scope_id="AMD", entities=["AMD"],
                                 as_of=POINT, explicit=forged)
    else:
        with pytest.raises(RouteUnavailable):
            rr.gate_read(second)
        assert seen[-1]["scope"] == second.scope


def test_bound_native_input_refuses_entity_consumer_and_time_escape():
    with rr.bind_read(entity("risk")):
        for consumer, query, point in [("risk", {"entity": "NVDA"}, None),
                                       ("clerk", {}, None),
                                       ("risk", {}, datetime(2026, 10, 9, tzinfo=UTC))]:
            with pytest.raises(RouteUnavailable, match="escapes"):
                rr.guard_input(consumer, query, as_of=point)
    assert rr.current_read_context() is None
    with pytest.raises(RouteUnavailable, match="explicit business scope"):
        read_input("risk", "RISK_RULES", scope={}, risk_config={})


@pytest.mark.parametrize("name,kwargs", [
    ("run_pead", {"symbol": "AMD", "phase": "prep"}), ("run_chief", {}),
    ("run_layer_review", {}), ("run_sector_review", {}), ("run_sector_html", {}),
    ("run_information_pass", {}), ("run_technical_review", {}), ("run_macro_review", {}),
    ("_run_analyst_fundamental", {"args": SimpleNamespace(symbol="AMD")})])
def test_disabled_actual_cli_entries_stop_before_provider(name, kwargs):
    from ats.runtime import cli

    co.set_route(co.PROJECTION_READ, "disabled", actor="test", reason="stop")
    with pytest.raises(RouteUnavailable, match="disabled"):
        getattr(cli, name)(**kwargs)


def test_actual_cli_checks_scoped_target_before_provider_and_clears_context(
        synthetic_target, monkeypatch):
    from ats.runtime import cli

    now = datetime.now(UTC)
    scope = {"kind": "portfolio", "id": "portfolio", "entities": rr.configured_entities("portfolio", "portfolio"),
             "time_range": {"start": (now - timedelta(hours=1)).isoformat(),
                            "end": (now + timedelta(hours=1)).isoformat()}}
    identity = rr.business_identity("technical", kind="portfolio", scope_id="portfolio",
                                    entities=scope["entities"], explicit=scope)
    synthetic_target(identity)
    calls = []
    def run(*args, **kwargs):
        calls.append(rr.current_read_context().identity)
        return SimpleNamespace(strategy="fake", summary_line=lambda: "fake", notes=[], readings=[])
    monkeypatch.setattr("ats.agents.technical.review.run", run)
    monkeypatch.setattr("ats.data.assurance.qualification", lambda **k: {"status": "eligible"})
    assert cli.run_technical_review(read_scope=scope) == 0
    assert calls == [identity] and rr.current_read_context() is None
    monkeypatch.setattr("ats.data.assurance.qualification", lambda **k: {"status": "ineligible"})
    with pytest.raises(RouteUnavailable):
        cli.run_technical_review(read_scope=scope)
    assert len(calls) == 1


def plan(tmp_path, *, namespace="dispatcher"):
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    (config / "pead.yaml").write_text("targets: [AMD]\n")
    return build_plan(requested_tasks=["technical-review"], scope=ProjectionScope(kind="entity", id="AMD"),
                      trigger=TriggerContext(kind="manual", workflow_id="technical-review"),
                      as_of=POINT, run_id="read-gate", config_dir=config, namespace=namespace)


def prepare_schedule_unit(tmp_path, monkeypatch, owner="dispatcher"):
    from ats.workflow.schedule_runtime import initialize
    import yaml
    root=tmp_path/"schedule-unit-config"/"workflow"
    root.mkdir(parents=True,exist_ok=True)
    (root/"workflow_owners.yaml").write_text(yaml.safe_dump({"workflows":{"technical-review":{"mode":owner}}}))
    monkeypatch.setenv("ATS_DISPATCH_STATE_PATH",str(tmp_path/"schedule-unit.sqlite"))
    initialize(config_dir=root.parent)


def test_actual_dispatcher_rechecks_before_saved_result_reuse(tmp_path, synthetic_target, monkeypatch):
    prepare_schedule_unit(tmp_path,monkeypatch)
    business_plan = plan(tmp_path)
    identity = rr.task_identity(business_plan, business_plan.tasks[0])
    synthetic_target(identity)
    calls = []
    def adapter(context):
        calls.append(rr.current_read_context().identity)
        context.store.save_task_projection_envelope(build_envelope(
            role="technical_review", scope=context.task.scope, as_of=POINT,
            payload={"entity": "AMD", "signal": "neutral", "summary": "fake", "levels": {"close": 100}}))
        return TaskAdapterResult()
    monkeypatch.setattr("ats.data.assurance.qualification", lambda **k: {"status": "eligible"})
    dispatcher = Dispatcher(workflow_store=WorkflowStore(tmp_path / "workflow.sqlite"),
                            adapters={"technical-review": adapter})
    assert dispatcher.dispatch(business_plan).status == "complete"
    assert calls == [identity]
    monkeypatch.setattr("ats.data.assurance.qualification", lambda **k: {"status": "ineligible"})
    with pytest.raises(RouteUnavailable):
        dispatcher.dispatch(business_plan)
    assert calls == [identity]


def test_actual_owned_workflow_gates_before_trigger_claim(tmp_path, monkeypatch):
    from ats.workflow import ownership

    prepare_schedule_unit(tmp_path,monkeypatch,"shadow")
    business_plan = plan(tmp_path)
    monkeypatch.setattr(ownership, "load_workflow_owners", lambda *a, **k: {
        "workflows": {"technical-review": {"mode": "shadow", "task_ids": ["technical-review"]}}})
    monkeypatch.setenv("ATS_SHADOW_DB_PATH", str(tmp_path / "shadow.sqlite"))
    co.set_route(co.PROJECTION_READ, "disabled", actor="test", reason="stop")
    with pytest.raises(RouteUnavailable, match="disabled"):
        ownership.run_owned_workflow("technical-review", scope=business_plan.request_scope,
                                     trigger=TriggerContext(kind="manual", workflow_id="technical-review"),
                                     request={"requested_tasks": ["technical-review"], "as_of": POINT},
                                     config_dir=tmp_path / "config")
    store = WorkflowStore(tmp_path / "shadow.sqlite")
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trigger_runs").fetchone()[0] == 0


def test_shadow_cannot_publish_through_original_memory_handle(tmp_path, monkeypatch):
    production = TradingMemory(os.environ["ATS_DB_PATH"])
    shadow = tmp_path / "shadow.sqlite"
    monkeypatch.setenv("ATS_SHADOW_DB_PATH", str(shadow))
    broker_guard.prohibit_broker_writes()
    with rr.bind_read(entity("risk"), mode="shadow", publication_path=shadow):
        with pytest.raises(BoundaryWriteRefused):
            write(production, "approval")
        local = TradingMemory(shadow)
        assert write(local, "approval")["approval_id"] == "approval"


def test_clerk_children_use_the_bound_publication_connection(tmp_path, monkeypatch):
    from ats.execution import clerk
    from ats.memory import get_store

    with isolated_run("clerk-children", root=tmp_path / "isolation") as env:
        store = TradingMemory(env.path_for("ATS_SHADOW_DB_PATH"))
        def step(*args, **kwargs):
            assert get_store() is store
            write(get_store(), "approval")
            return {}
        monkeypatch.setattr(clerk, "_STEPS", {"approval": step})
        assert clerk_run(store=store, broker=object(), steps=("approval",))["status"] == "completed"
        assert store.conn.execute("SELECT COUNT(*) FROM boss_approvals").fetchone()[0] == 1


def test_chief_does_not_swallow_disabled_approval_write(monkeypatch):
    from ats.graph import chief
    from ats.graph.chief_state import ChiefDecisionState
    from ats.schemas.decision import BossApproval

    store = TradingMemory(":memory:")
    monkeypatch.setattr(chief, "_decision_repo", lambda: DecisionAuditRepository(store))
    state = ChiefDecisionState(cycle_id="cycle", as_of=datetime.fromisoformat(POINT),
                              revision_no=1, revision_hash="hash")
    co.set_route(co.APPROVAL_LIFECYCLE, "disabled", actor="test", reason="stop")
    with pytest.raises(BoundaryWriteRefused):
        chief._record_approval(state, BossApproval(status="approved", reviewer="test"))
    assert store.conn.execute("SELECT COUNT(*) FROM boss_approvals").fetchone()[0] == 0


def test_actual_analyst_cli_refuses_disabled_output_before_analysis(monkeypatch):
    from ats.runtime import cli

    def unexpected(*args, **kwargs):
        pytest.fail("disabled analyst called its provider")
    monkeypatch.setattr("ats.agents.technical.review.run", unexpected)
    co.set_route(co.ANALYST_OUTPUT, "disabled", actor="test", reason="stop")
    with pytest.raises(BoundaryWriteRefused):
        cli.run_technical_review()


def test_actual_dispatcher_mixed_entities_only_blocks_ineligible_scope(
        tmp_path, synthetic_target, monkeypatch):
    prepare_schedule_unit(tmp_path,monkeypatch)
    business_plan = plan(tmp_path)
    (tmp_path / "config/pead.yaml").write_text("targets: [AMD, NVDA]\n")
    business_plan = build_plan(requested_tasks=["technical-review"],
                               scope=ProjectionScope(kind="portfolio"),
                               trigger=TriggerContext(kind="manual", workflow_id="technical-review"),
                               as_of=POINT, run_id="mixed", config_dir=tmp_path / "config")
    for task in business_plan.tasks:
        synthetic_target(rr.task_identity(business_plan, task))
    calls, seen = [], []
    def qualify(**kwargs):
        seen.append(kwargs)
        return {"status": "eligible" if kwargs["scope"]["id"] == "AMD" else "ineligible"}
    monkeypatch.setattr("ats.data.assurance.qualification", qualify)
    def adapter(context):
        calls.append(context.task.scope.id)
        context.store.save_task_projection_envelope(build_envelope(
            role="technical_review", scope=context.task.scope, as_of=POINT,
            payload={"entity": context.task.scope.id, "signal": "neutral", "summary": "fake", "levels": {"close": 100}}))
        return TaskAdapterResult()
    result = Dispatcher(workflow_store=WorkflowStore(tmp_path / "mixed.sqlite"),
                        adapters={"technical-review": adapter}).dispatch(business_plan)
    assert result.status == "incomplete" and calls == ["AMD"]
    assert {outcome.scope.id: outcome.status for outcome in result.outcomes} == {
        "AMD": "succeeded", "NVDA": "blocked"}
    assert {row["scope"]["id"] for row in seen} == {"AMD", "NVDA"}


def test_unknown_legacy_scope_is_queried_and_never_inherits_target(synthetic_target, monkeypatch):
    first, second = entity(), entity(symbol="NVDA")
    synthetic_target(first)
    seen = []
    def qualify(**kwargs):
        seen.append(kwargs["scope"])
        return {"status": "eligible" if kwargs["scope"] == first.scope else "ineligible"}
    monkeypatch.setattr("ats.data.assurance.qualification", qualify)
    assert rr.gate_read(first).route == "target"
    decision = rr.gate_read(second)
    assert decision.route == "legacy" and not decision.qualified
    assert seen == [first.scope, second.scope]


def test_native_isolated_candidate_read_does_not_require_production_qualification(tmp_path):
    with isolated_run("native-candidate", root=tmp_path / "isolation"):
        with rr.bind_read(entity("risk")):
            packet = read_input("risk", "RISK_RULES", scope={}, risk_config={"max": 10})
            assert packet.status == "complete"
        assert rr.gate_read(entity("risk")).qualification["status"] == "ineligible"


def test_partial_isolation_cannot_read_production_or_claim_candidate_permission(tmp_path, monkeypatch):
    with isolated_run("partial", root=tmp_path / "isolation"):
        monkeypatch.setenv("ATS_DATA_DB_PATH", str(tmp_path / "production.sqlite"))
        with pytest.raises(RouteUnavailable, match="environment is incomplete"):
            rr.gate_read(entity("risk"))
        assert not (tmp_path / "production.sqlite").exists()


def test_configuration_drift_does_not_reinterpret_frozen_plan_scope(tmp_path):
    business_plan = plan(tmp_path)
    before = rr.task_identity(business_plan, business_plan.tasks[0])
    (tmp_path / "config/pead.yaml").write_text("targets: [NVDA]\n")
    with pytest.raises(RouteUnavailable, match="configuration drifted"):
        rr.task_identity(business_plan, business_plan.tasks[0])
    assert before.scope["entities"] == ["AMD"]


def test_real_cli_json_scope_protocol_reaches_the_business_gate(monkeypatch):
    from ats.runtime import cli

    now = datetime.now(UTC)
    scope = {"kind": "portfolio", "id": "portfolio", "entities": rr.configured_entities("portfolio", "portfolio"),
             "time_range": {"start": (now - timedelta(hours=1)).isoformat(),
                            "end": (now + timedelta(hours=1)).isoformat()}}
    def run(*args, **kwargs):
        assert rr.current_read_context().identity.scope["time_range"]["end"] == scope["time_range"]["end"]
        return SimpleNamespace(strategy="fake", summary_line=lambda: "fake", notes=[], readings=[])
    monkeypatch.setattr("ats.agents.technical.review.run", run)
    assert cli.main(["--read-scope-json", json.dumps({"technical": scope}), "technical", "review",
                     "--offline", "--no-report"]) == 0
    assert rr.current_read_context() is None


def test_native_persistent_read_inherits_frozen_cutoff_when_as_of_is_omitted(tmp_path):
    seen = []
    class Products:
        def neutral_evidence(self, **kwargs):
            seen.append(kwargs["as_of"])
            return {"rows": [{"fact_id": "fact", "observed_at": POINT}], "rejected": []}
    with isolated_run("native-cutoff", root=tmp_path / "isolated"), rr.bind_read(entity("fundamental")):
        packet = read_input("fundamental", "COMPANY_DATA", scope={"entity": "AMD", "kind": "evidence"},
                            products=Products())
    assert packet.status == "complete"
    assert packet.as_of == datetime.fromisoformat(POINT)
    assert seen == [datetime.fromisoformat(POINT)]
