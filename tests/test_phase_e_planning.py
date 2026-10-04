import pytest

from ats.agent.task_projection import ProjectionScope
from ats.workflow.phase_e import (build_plan, phase_e_registry,
                                   validate_phase_e_registry)
from ats.workflow.run_contracts import (ContractError, TaskRegistry, TriggerContext,
                                        WorkflowTaskSpec)


def _scheduled():
    return TriggerContext(kind="schedule", schedule_id="weekly",
                          scheduled_for="2026-09-24T00:00:00+00:00")


def test_phase_e_registry_has_only_the_two_declared_cross_analyst_edges():
    registry = phase_e_registry()
    assert registry.dependencies_for("sector-review") == ("layer-review",)
    assert registry.dependencies_for("fundamental-routine") == ("information-brief",)
    assert registry.dependencies_for("fundamental-event") == ("information-brief",)

    mutated = [spec.model_copy(update={"depends_on": ("macro-review",)})
               if spec.task_id == "sector-review" else spec
               for spec in (registry.spec(task_id) for task_id in registry.task_ids())]
    with pytest.raises(ContractError, match="cross-analyst"):
        validate_phase_e_registry(TaskRegistry(mutated))


def test_registry_rejects_duplicate_unknown_dependency_and_cycles():
    with pytest.raises(ContractError, match="duplicate"):
        TaskRegistry([WorkflowTaskSpec(task_id="x"), WorkflowTaskSpec(task_id="x")])
    with pytest.raises(Exception, match="unregistered"):
        TaskRegistry([WorkflowTaskSpec(task_id="x", depends_on=("missing",))]).validate()
    cyclic = TaskRegistry([
        WorkflowTaskSpec(task_id="a", depends_on=("b",)),
        WorkflowTaskSpec(task_id="b", depends_on=("a",)),
    ])
    with pytest.raises(ContractError, match="cycle"):
        cyclic.validate()


def test_ai_hardware_decision_plan_fans_out_and_pins_exact_dependency_instances():
    plan = build_plan(
        requested_tasks=("sector-review", "fundamental-routine", "macro-review",
                         "technical-review"),
        scope=ProjectionScope(kind="sector", id="ai_hardware"), trigger=_scheduled(),
        as_of="2026-09-24T00:00:00+00:00", run_id="r1",
        profile_id="ai_hardware", enter_decision_cycle=True)
    by_task = {}
    for task in plan.tasks:
        by_task.setdefault(task.task_id, []).append(task)
    assert len(by_task["layer-review"]) == 8
    assert len(by_task["information-brief"]) == 11
    assert len(by_task["fundamental-routine"]) == 11
    assert len(by_task["sector-review"][0].dependencies) == 8
    info_keys = {item.instance_key for item in by_task["information-brief"]}
    for fundamental in by_task["fundamental-routine"]:
        assert fundamental.dependencies[0] in info_keys
        info = next(item for item in by_task["information-brief"]
                    if item.instance_key == fundamental.dependencies[0])
        assert info.scope.key == fundamental.scope.key
    assert plan.profile_version == "1"
    assert plan.source_config_hashes["config/sectors/ai_hardware.yaml"]


def test_partial_analysis_cannot_enter_decision_cycle():
    with pytest.raises(ContractError, match="missing categories"):
        build_plan(requested_tasks=("macro-review",),
                   scope=ProjectionScope(kind="sector", id="ai_hardware"),
                   trigger=_scheduled(), as_of="2026-09-24T00:00:00+00:00",
                   run_id="r1", profile_id="ai_hardware", enter_decision_cycle=True)


def test_config_changes_do_not_mutate_an_already_built_plan(tmp_path):
    config = tmp_path / "config"
    (config / "workflow").mkdir(parents=True)
    (config / "sectors").mkdir()
    (config / "workflow" / "decision_profiles.yaml").write_text(
        "schema_version: 1\nprofiles: {}\n", encoding="utf-8")
    sector_path = config / "sectors" / "custom.yaml"
    sector_path.write_text("name: custom\nlayers:\n  - key: layer_a\n", encoding="utf-8")
    (config / "risk.yaml").write_text("sector_layer_caps: {}\n", encoding="utf-8")

    first = build_plan(requested_tasks=("sector-review",),
                       scope=ProjectionScope(kind="sector", id="custom"),
                       trigger=_scheduled(), as_of="2026-09-24T00:00:00+00:00",
                       run_id="r1", config_dir=config)
    sector_path.write_text("name: custom\nlayers:\n  - key: layer_a\n  - key: layer_b\n",
                           encoding="utf-8")
    second = build_plan(requested_tasks=("sector-review",),
                        scope=ProjectionScope(kind="sector", id="custom"),
                        trigger=_scheduled(), as_of="2026-09-24T00:00:00+00:00",
                        run_id="r1", config_dir=config)
    assert first.plan_hash != second.plan_hash
    assert len([t for t in first.tasks if t.task_id == "layer-review"]) == 1
    assert len([t for t in second.tasks if t.task_id == "layer-review"]) == 2
