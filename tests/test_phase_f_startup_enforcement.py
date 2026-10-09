"""Run real entrypoints and Dispatcher with synthetic tasks and fake transport."""
import subprocess
import sys

import pytest
from phase_f_broker_harness import FakeIB, broker_for

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.execution import broker_write_guard as guard
from ats.schemas.decision import TradeDecision
from ats.workflow.dispatcher import Dispatcher, TaskAdapterResult
from ats.workflow.phase_e import build_plan
from ats.workflow.run_contracts import TriggerContext
from ats.workflow.store import WorkflowStore


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    guard.reset_for_tests()
    monkeypatch.setenv("ATS_DB_PATH", str(tmp_path / "memory.sqlite"))
    monkeypatch.setenv("ATS_SHADOW_DB_PATH", str(tmp_path / "shadow.sqlite"))
    # A YAML adapter alone no longer owns scheduling. Install a fresh fixture
    # authority through the real initializer; never mutate production or borrow
    # the default legacy SQL owner. This test covers startup, not handover.
    import yaml
    from ats.workflow.schedule_runtime import initialize
    config=tmp_path/"startup-config"/"workflow"
    config.mkdir(parents=True)
    (config/"workflow_owners.yaml").write_text(yaml.safe_dump({"workflows":{"macro-review":{"mode":"shadow"}}}))
    monkeypatch.setenv("ATS_DISPATCH_STATE_PATH", str(tmp_path/"startup-dispatch.sqlite"))
    initialize(config_dir=config.parent)
    yield
    guard.reset_for_tests()


def plan():
    return build_plan(requested_tasks=["macro-review"],
                      scope=ProjectionScope(kind="portfolio", id="portfolio"),
                      trigger=TriggerContext(kind="manual", workflow_id="macro-review"),
                      as_of="2026-10-08T00:00:00+00:00", run_id="startup-probe",
                      namespace="shadow")


def attempt():
    ib = FakeIB()
    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        broker_for(ib).place_orders([(TradeDecision(symbol="AMD", action="buy"), 1)], "c")
    assert exc.value.reason_code in {guard.REASON_SHADOW_RUN, guard.REASON_ISOLATED}
    assert not ib.accepted
    return exc.value.reason_code


def adapter(context):
    attempt()
    # The expected broker refusal is observed; the synthetic analysis still
    # publishes and completes normally through the real Dispatcher.
    envelope = build_envelope(
        role="macro_review", payload={"regime": "transition", "summary": "probe",
                                      "indicators": ["synthetic"]},
        scope=context.task.scope, as_of=context.plan.as_of,
        input_refs=context.input_refs, data_vintage_refs=context.data_vintage_refs)
    context.store.save_task_projection_envelope(envelope)
    return TaskAdapterResult(detail="startup write prohibition observed")


def owner_setup(monkeypatch, tmp_path):
    from ats.workflow import ownership
    monkeypatch.setattr(ownership, "load_workflow_owners", lambda *a, **k: {
        "workflows": {"macro-review": {"mode": "shadow", "task_ids": ["macro-review"]}}})
    monkeypatch.setattr("ats.workflow.dispatcher.default_adapters",
                        lambda: {"macro-review": adapter})
    return lambda: ownership.run_owned_workflow(
        "macro-review", scope=ProjectionScope(kind="portfolio", id="portfolio"),
        trigger=TriggerContext(kind="manual", workflow_id="macro-review"))


def test_owner_to_real_dispatcher_to_broker_refuses_and_analysis_completes(tmp_path, monkeypatch):
    run = owner_setup(monkeypatch, tmp_path)
    result = run()
    assert result["status"] == "complete"
    assert guard.refusal_rows()[-1]["caller"] == "IBKRBroker.place_orders"


def test_direct_shadow_plan_dispatch_to_broker_refuses_and_completes(tmp_path):
    result = Dispatcher(workflow_store=WorkflowStore(tmp_path / "shadow.sqlite"),
                        adapters={"macro-review": adapter}).dispatch(plan())
    assert result.status == "complete"
    assert guard.refusal_rows()


def test_cli_shadow_entry_arms_before_any_work(monkeypatch, tmp_path):
    from ats.runtime import cli
    monkeypatch.setattr("ats.workflow.shadow_ledger.shadow_attestation",
                        lambda **kwargs: {"refusal": attempt()})
    assert cli.main(["shadow", "attest", "--run-id", "probe",
                     "--order-db", str(tmp_path / "orders.sqlite")]) == 0


def test_scheduler_environment_arms_before_daily_work(monkeypatch):
    from ats.runtime import scheduler
    monkeypatch.setenv("ATS_RUN_MODE", "shadow")
    monkeypatch.setattr(scheduler, "_daily", lambda **kwargs: attempt())
    scheduler.start(run_once=True)
    assert guard.refusal_rows()


@pytest.mark.parametrize("entry", ["cli", "scheduler", "owner", "dispatcher", "isolated"])
def test_missing_prohibition_refuses_each_actual_startup(tmp_path, monkeypatch, entry):
    from ats.runtime import cli, scheduler
    from ats.workflow.isolation import isolated_run
    # Simulate a deployment whose installation is ineffective; the real entry
    # must independently assert the capability and stop before task execution.
    monkeypatch.setattr(guard, "install_prohibition", lambda *args, **kwargs: None)
    if entry == "cli":
        call = lambda: cli.main(["shadow", "attest", "--run-id", "probe"])
    elif entry == "scheduler":
        monkeypatch.setenv("ATS_RUN_MODE", "shadow")
        call = lambda: scheduler.start(run_once=True)
    elif entry == "owner":
        call = owner_setup(monkeypatch, tmp_path)
    elif entry == "dispatcher":
        call = lambda: Dispatcher(workflow_store=WorkflowStore(tmp_path / "shadow.sqlite"),
                                  adapters={"macro-review": adapter}).dispatch(plan())
    else:
        def call():
            with isolated_run("probe", root=tmp_path / "isolated"):
                pytest.fail("unprotected run started")
    with pytest.raises(guard.BrokerWriteProhibited) as exc:
        call()
    assert exc.value.reason_code == "guard_missing"
    assert not guard.refusal_rows()  # task/broker never started


def test_isolation_prohibition_reaches_child_process_and_nested_env_restores(tmp_path, monkeypatch):
    import os

    from ats.workflow.isolation import isolated_run
    monkeypatch.setenv("ATS_RUN_MODE", "shadow")
    monkeypatch.setenv("ATS_BROKER_WRITE_PROHIBITION", "shadow")
    with isolated_run("outer", root=tmp_path / "outer"):
        with isolated_run("inner", root=tmp_path / "inner"):
            pass
        assert os.environ["ATS_RUN_MODE"] == "isolated"
        code = """
from ats.execution import broker_write_guard as g
from ats.broker.ibkr import IBKRBroker
from ats.schemas.decision import TradeDecision
try:
    IBKRBroker.__new__(IBKRBroker).place_orders([(TradeDecision(symbol='AMD', action='buy'),1)],'c')
except g.BrokerWriteProhibited as e:
    print(e.reason_code)
"""
        done = subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, timeout=15, check=False)
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == guard.REASON_ISOLATED
    assert os.environ["ATS_RUN_MODE"] == "shadow"
    assert os.environ["ATS_BROKER_WRITE_PROHIBITION"] == "shadow"


def test_shadow_startup_revokes_effect_of_preexisting_grant():
    guard.grant_write("A", 1, environment="paper", account="DU1")
    guard.startup(caller="test", mode="shadow")
    attempt()
