"""Frozen-lineage fixtures for tests that drive the Chief decision graph end to end.

The decision gate (`decision_requirements.validate`) re-resolves every research input
against the plan frozen at cycle start: category and scope closure from the task
registry, and for each projection the input references (dependency projection ids and
hashes, plus the hashes of the configuration it was planned from). A projection that was
merely written to the store therefore does not count as an input. These helpers build the
same lineage the Dispatcher produces, on a pinned copy of the real configuration, so a
test can publish a small universe and still pass the production gate unchanged.

The lineage is the one PRODUCTION has: each research task family runs as its own workflow,
so a projection records only the configuration its own task reads, never the decision
plan's full set (switch-trade-path-and-retire-r5, 3.2 finding).
"""
from __future__ import annotations

import shutil

import yaml

from ats.agent.task_projection import build_envelope
from ats.config import REPO_ROOT, reset_config_cache


def freeze_config(tmp_path, monkeypatch, *, targets=("COHR",),
                  layers=("L1_app", "L3_optics")):
    """Pin the planning universe to `targets` and the first `len(layers)` sector layers."""
    root = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", root)
    path = root / "pead.yaml"
    cfg = yaml.safe_load(path.read_text())
    cfg["targets"] = list(targets)
    path.write_text(yaml.safe_dump(cfg))
    path = root / "sectors/ai_hardware.yaml"
    cfg = yaml.safe_load(path.read_text())
    cfg["layers"] = cfg["layers"][:len(layers)]
    for layer, key in zip(cfg["layers"], layers):
        layer["key"] = key
    path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setenv("ATS_CONFIG_DIR", str(root))
    reset_config_cache()
    return root


def publish_with_lineage(store, rows, state):
    """Save `rows` (name -> envelope) re-built with the input references the plan expects.

    `state` selects the plan (Fundamental mode, profile); it must describe the same
    decision the test then runs. Envelopes with no matching task in the plan are saved
    unchanged.
    """
    from ats.workflow import decision_requirements as dr
    from ats.workflow.planning import TASK_ROLE

    plan = dr.restore(dr.standalone(state))
    by_binding = {(e.agent_role, e.scope.key): e for e in rows.values()}
    selected = {}
    for task in plan.tasks:
        env = by_binding.get((TASK_ROLE[task.task_id], task.scope.key))
        if env is None:
            continue
        inputs = producer_inputs(plan, task, {k: selected[k] for k in task.dependencies})
        env = build_envelope(role=env.agent_role, payload=env.payload, scope=env.scope,
                             as_of=env.as_of, valid_until=env.valid_until,
                             input_refs=inputs, data_vintage_refs=env.data_vintage_refs,
                             workflow_run_id=env.workflow_run_id, agent_run_id=env.agent_run_id)
        by_binding[(env.agent_role, env.scope.key)] = env
        selected[task.instance_key] = env
    linked = {}
    for key, env in rows.items():
        linked[key] = by_binding[(env.agent_role, env.scope.key)]
        store.save_task_projection_envelope(linked[key])
    return linked


def producer_inputs(plan, task, parents):
    """Input refs `task` gets when its own task family runs as a single workflow.

    `plan` is the decision plan; the producer plan requests only `task.task_id` (its
    declared upstream comes with it), over the same scope and frozen configuration.
    """
    from ats.workflow.dispatcher import Dispatcher
    from ats.workflow.planning import build_plan
    from ats.workflow.run_contracts import TriggerContext

    producer = build_plan(requested_tasks=(task.task_id,), scope=plan.request_scope,
                          trigger=TriggerContext(kind="manual", trigger_id="producer-" + task.task_id),
                          as_of=plan.as_of, run_id="producer-" + task.task_id)
    own = next(t for t in producer.tasks
               if t.task_id == task.task_id and t.scope.key == task.scope.key)
    inputs, _, _ = Dispatcher._task_inputs(None, producer, own, parents)
    return inputs
