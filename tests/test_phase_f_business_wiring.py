"""Real business gates: scope binding and isolated publication (route and qualification cases retired, r5 9.3)."""
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.data.consumer_api import read_input
from ats.decision.repository import DecisionAuditRepository
from ats.execution import broker_write_guard as broker_guard
from ats.execution.clerk import clerk_run
from ats.memory.store import TradingMemory
from ats.workflow import runtime_reads as rr
from ats.workflow.read_scope import ReadUnavailable
from ats.workflow.isolation import IsolatedWriteRefused
from ats.workflow.dispatcher import Dispatcher, TaskAdapterResult
from ats.workflow.isolation import isolated_run
from ats.workflow.planning import build_plan
from ats.workflow.run_contracts import TriggerContext
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


WRITES = [("review", "decision_risk_reviews"),
          ("approval", "boss_approvals"),
          ("legacy", "task_projections"),
          ("envelope", "task_projection_envelopes"),
          ("clerk", "clerk_runs")]


@pytest.mark.parametrize("entry,table", WRITES)
def test_isolated_real_publisher_only_writes_its_database(tmp_path, entry, table):
    original = TradingMemory(tmp_path / "production.sqlite")
    before = original.conn.total_changes
    with isolated_run("publication", root=tmp_path / "isolated") as env:
        isolated = TradingMemory(env.path_for("ATS_DB_PATH"))
        write(isolated, entry)
        assert isolated.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        # A stale connection's declared path is deliberately misleading.
        original.path = isolated.path
        with pytest.raises(IsolatedWriteRefused, match="destination"):
            write(original, entry)
    assert original.conn.total_changes == before
    assert original.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_shadow_cannot_publish_through_original_memory_handle(tmp_path, monkeypatch):
    production = TradingMemory(os.environ["ATS_DB_PATH"])
    shadow = tmp_path / "shadow.sqlite"
    monkeypatch.setenv("ATS_SHADOW_DB_PATH", str(shadow))
    broker_guard.prohibit_broker_writes()
    with rr.bind_read(entity("risk"), mode="shadow", publication_path=shadow):
        with pytest.raises(IsolatedWriteRefused):
            write(production, "approval")
        local = TradingMemory(shadow)
        assert write(local, "approval")["approval_id"] == "approval"


def entity(consumer="information", symbol="AMD", point=POINT):
    return rr.business_identity(consumer, kind="entity", scope_id=symbol, entities=[symbol], as_of=point)


def test_bound_native_input_refuses_entity_consumer_and_time_escape():
    with rr.bind_read(entity("risk")):
        for consumer, query, point in [("risk", {"entity": "NVDA"}, None),
                                       ("clerk", {}, None),
                                       ("risk", {}, datetime(2026, 10, 9, tzinfo=UTC))]:
            with pytest.raises(ReadUnavailable, match="escapes"):
                rr.guard_input(consumer, query, as_of=point)
    assert rr.current_read_context() is None
    with pytest.raises(ReadUnavailable, match="explicit business scope"):
        read_input("risk", "RISK_RULES", scope={}, risk_config={})


def plan(tmp_path, *, namespace="dispatcher"):
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    (config / "pead.yaml").write_text("targets: [AMD]\n")
    return build_plan(requested_tasks=["technical-review"], scope=ProjectionScope(kind="entity", id="AMD"),
                      trigger=TriggerContext(kind="manual", workflow_id="technical-review"),
                      as_of=POINT, run_id="read-gate", config_dir=config, namespace=namespace)


def prepare_schedule_unit(tmp_path, monkeypatch, owner="dispatcher"):
    import yaml

    from ats.workflow.isolation import verified_isolation_root
    from ats.workflow.schedule_runtime import initialize

    isolated_root = verified_isolation_root()
    root=tmp_path/"schedule-unit-config"/"workflow"
    root.mkdir(parents=True,exist_ok=True)
    (root/"workflow_owners.yaml").write_text(yaml.safe_dump({"workflows":{"technical-review":{"mode":owner}}}))
    if isolated_root is None:
        monkeypatch.setenv("ATS_DISPATCH_STATE_PATH",str(tmp_path/"schedule-unit.sqlite"))
    initialize(config_dir=root.parent)


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


def test_partial_isolation_cannot_read_production_or_claim_candidate_permission(tmp_path, monkeypatch):
    with isolated_run("partial", root=tmp_path / "isolation"):
        monkeypatch.setenv("ATS_DATA_DB_PATH", str(tmp_path / "production.sqlite"))
        with pytest.raises(ReadUnavailable, match="environment is incomplete"):
            rr.gate_read(entity("risk"))
        assert not (tmp_path / "production.sqlite").exists()


def test_configuration_drift_does_not_reinterpret_frozen_plan_scope(tmp_path):
    business_plan = plan(tmp_path)
    before = rr.task_identity(business_plan, business_plan.tasks[0])
    (tmp_path / "config/pead.yaml").write_text("targets: [NVDA]\n")
    with pytest.raises(ReadUnavailable, match="configuration drifted"):
        rr.task_identity(business_plan, business_plan.tasks[0])
    assert before.scope["entities"] == ["AMD"]


def test_cli_entry_function_with_an_explicit_scope_reaches_the_business_gate(monkeypatch):
    from ats.runtime import cli

    now = datetime.now(UTC)
    scope = {"kind": "portfolio", "id": "portfolio", "entities": rr.configured_entities("portfolio", "portfolio"),
             "time_range": {"start": (now - timedelta(hours=1)).isoformat(),
                            "end": (now + timedelta(hours=1)).isoformat()}}
    def run(*args, **kwargs):
        assert rr.current_read_context().identity.scope["time_range"]["end"] == scope["time_range"]["end"]
        return SimpleNamespace(strategy="fake", summary_line=lambda: "fake", notes=[], readings=[])
    monkeypatch.setattr("ats.agents.technical.review.run", run)
    assert cli.run_technical_review(live_data=False, write_report=False, read_scope=scope) == 0
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
