"""One frozen, versioned requirement set for analysis and actual Chief entry."""
from datetime import datetime, timezone

from ..agent.task_projection import ProjectionScope, reuse_decision
from .cutover_routing import RouteUnavailable
from .phase_e import TASK_ROLE, _hash, build_plan
from .run_contracts import TriggerContext


def freeze(plan, *, outcomes=None, manifest=None, workflow_store_path=""):
    if not plan.enter_decision_cycle:
        raise RouteUnavailable("decision request was not declared")
    value = {"version": "decision-requirements-v1", "plan": plan.as_dict(),
            "plan_hash": plan.plan_hash, "outcomes": outcomes,
            "manifest": manifest, "workflow_store_path": str(workflow_store_path)}
    value["requirements_hash"] = _hash(value)
    return value


def restore(requirements):
    if not requirements or requirements.get("version") != "decision-requirements-v1":
        raise RouteUnavailable("fixed decision requirements missing")
    if requirements.get("requirements_hash") != _hash({k: v for k, v in requirements.items()
                                                      if k != "requirements_hash"}):
        raise RouteUnavailable("fixed decision requirements integrity mismatch")
    raw = requirements.get("plan") or {}
    try:
        plan = build_plan(
            requested_tasks=raw["requested_tasks"], scope=ProjectionScope.model_validate(raw["request_scope"]),
            trigger=TriggerContext.model_validate(raw["trigger"]), as_of=raw["as_of"],
            run_id=raw["run_id"], profile_id=raw["profile_id"], enter_decision_cycle=True,
            task_inputs=raw["task_inputs"], config_dir=raw["config_root"])
    except Exception as exc:
        raise RouteUnavailable("fixed decision requirements cannot be reconstructed") from exc
    if plan.as_dict() != raw or plan.plan_hash != requirements.get("plan_hash"):
        raise RouteUnavailable("fixed decision requirement/configuration drift")
    return plan


def standalone(state):
    mode = "fundamental-event" if state.fundamental_mode == "event" else "fundamental-routine"
    plan = build_plan(requested_tasks=("sector-review", mode, "macro-review", "technical-review"),
        scope=ProjectionScope(kind="sector", id=state.decision_profile),
        trigger=TriggerContext(kind="manual", trigger_id=state.cycle_id),
        as_of=state.as_of.isoformat(), run_id=state.cycle_id,
        profile_id=state.decision_profile, enter_decision_cycle=True)
    return freeze(plan)


def query_plan(plan):
    result = {}
    for task in plan.tasks:
        role = TASK_ROLE[task.task_id]
        result.setdefault(role, {"role": role, "scopes": []})["scopes"].append(task.scope)
    return result


def attach(snapshot, requirements, detail):
    """Freeze actual, checked standalone reuse without claiming a Dispatcher run."""
    requirements = dict(requirements)
    plan = restore(requirements)
    if requirements.get("manifest") is None:
        requirements["manifest"] = [{"role": d["role"], "scope_kind": d["scope"].partition(":")[0],
            "scope_id": d["scope"].partition(":")[2], "projection_id": d["projection_id"],
            "content_hash": d["content_hash"]} for d in detail if d["projection_id"]]
    if requirements.get("outcomes") is None:
        requirements["outcomes"] = [{"instance_key": task.instance_key,
            "task_id": task.task_id, "scope": task.scope.model_dump(mode="json"),
            "status": "succeeded" if any(i.agent_role == TASK_ROLE[task.task_id] and
                i.scope_kind == task.scope.kind and i.scope_id == task.scope.id and i.reusable
                for i in snapshot.items) else "missing",
            "projection_refs": [i.projection_id for i in snapshot.items
                if i.agent_role == TASK_ROLE[task.task_id] and i.scope_kind == task.scope.kind and
                i.scope_id == task.scope.id and i.projection_id], "reused": True}
            for task in plan.tasks]
    requirements["requirements_hash"] = _hash({k: v for k, v in requirements.items()
                                               if k != "requirements_hash"})
    snapshot.requirements = requirements
    return requirements


def validate(store, snapshot_payload, *, at=None):
    """Re-resolve every fixed binding before a model, revision or cycle write."""
    from ..agents.chief.assemble import _envelope_from_row
    from .consumer_reads import read_projection

    requirements = snapshot_payload.get("requirements") or {}
    plan = restore(requirements)
    items = snapshot_payload.get("items") or []
    outcome_rows = requirements.get("outcomes") or []
    outcomes = {row["instance_key"]: row for row in outcome_rows}
    if len(outcome_rows) != len(plan.tasks) or set(outcomes) != {task.instance_key for task in plan.tasks}:
        raise RouteUnavailable("selected task dependency closure incomplete")
    if len(items) != len(plan.tasks):
        raise RouteUnavailable("fixed category/scope basis incomplete")
    manifest = {(m["role"], m["scope_kind"], m["scope_id"]): m
                for m in requirements.get("manifest") or []}
    bindings = {(TASK_ROLE[t.task_id], t.scope.kind, t.scope.id) for t in plan.tasks}
    if len(requirements.get("manifest") or []) != len(plan.tasks) or set(manifest) != bindings:
        raise RouteUnavailable("fixed manifest dependency closure differs")
    stamp = at or datetime.now(timezone.utc)
    selected = {}
    for task in plan.tasks:
        outcome = outcomes[task.instance_key]
        if outcome.get("task_id") != task.task_id or outcome.get("scope") != task.scope.model_dump(mode="json"):
            raise RouteUnavailable("selected task outcome scope differs")
        role = TASK_ROLE[task.task_id]
        hits = [item for item in items if item.get("agent_role") == role and
                item.get("scope_kind") == task.scope.kind and item.get("scope_id") == task.scope.id]
        if len(hits) != 1 or not hits[0].get("reusable") or outcome.get("status") != "succeeded":
            raise RouteUnavailable("selected task missing/failed/stale: " + task.instance_key)
        item = hits[0]
        reference = manifest.get((role, task.scope.kind, task.scope.id)) or {}
        if any(reference.get(k) != item.get(k) for k in ("projection_id", "content_hash")):
            raise RouteUnavailable("snapshot differs from fixed manifest")
        if outcome.get("projection_refs") != [item.get("projection_id")]:
            raise RouteUnavailable("selected task projection differs from frozen run")
        row = read_projection(store, consumer="chief", role=role, scope=task.scope, at=stamp,
                              projection_id=item["projection_id"], content_hash=item["content_hash"])
        if not row:
            raise RouteUnavailable("required decision projection unavailable: " + task.instance_key)
        envelope = _envelope_from_row(row, task.scope)
        ok, reason = reuse_decision(envelope, scope=task.scope, at=stamp,
            schema_name=task.output_schema, schema_version="v1")
        from .phase_e import FRESHNESS_SECONDS

        source_time = datetime.fromisoformat(envelope.as_of.replace("Z", "+00:00"))
        if (source_time.tzinfo is None or source_time > stamp or
                (stamp-source_time).total_seconds() > FRESHNESS_SECONDS[task.task_id]):
            ok, reason = False, "freshness_unconfirmed"
        if not ok:
            raise RouteUnavailable("required decision input invalid: " + task.instance_key + ":" + reason)
        selected[task.instance_key] = envelope
    for task in plan.tasks:
        envelope = selected[task.instance_key]
        from .dispatcher import Dispatcher

        wanted_inputs, wanted_vintages, _ = Dispatcher._task_inputs(
            None, plan, task, {parent: selected[parent] for parent in task.dependencies})
        if not set(wanted_inputs) <= set(envelope.input_refs) or not set(wanted_vintages) <= set(envelope.data_vintage_refs):
            raise RouteUnavailable("fixed input/vintage lineage differs: " + task.instance_key)
        for parent in task.dependencies:
            if selected[parent].projection_id not in envelope.input_refs and not any(
                    selected[parent].projection_id in ref and selected[parent].content_hash in ref
                    for ref in envelope.input_refs):
                raise RouteUnavailable("selected task dependency lineage missing: " + task.instance_key)
    if requirements.get("workflow_store_path"):
        # The overall run may still be awaiting Chief. Analysis attempts are
        # already terminal and independently checked against their durable rows.
        import json
        import sqlite3

        connection = sqlite3.connect("file:" + requirements["workflow_store_path"] + "?mode=ro", uri=True)
        try:
            saved = connection.execute("SELECT plan_hash,status FROM workflow_runs WHERE run_id=?",
                                       (plan.run_id,)).fetchone()
            if not saved or saved[0] != plan.plan_hash or saved[1] not in {"running", "complete"}:
                raise RouteUnavailable("workflow run incomplete or mismatched")
            for task in plan.tasks:
                envelope = selected[task.instance_key]
                attempt = connection.execute("SELECT status,run_id,task_id,task_instance_key FROM agent_runs WHERE agent_run_id=?",
                                             (envelope.agent_run_id,)).fetchone()
                if not attempt or attempt != ("succeeded", envelope.workflow_run_id, task.task_id, task.instance_key):
                    raise RouteUnavailable("selected task attempt not terminal or mismatched")
                current = connection.execute(
                    "SELECT status,projection_refs_json FROM agent_runs WHERE run_id=? "
                    "AND task_instance_key=? ORDER BY attempt_no DESC LIMIT 1",
                    (plan.run_id, task.instance_key)).fetchone()
                if not current or current[0] != "succeeded" or json.loads(current[1]) != [envelope.projection_id]:
                    raise RouteUnavailable("current selected task attempt incomplete or mismatched")
        finally:
            connection.close()
    return plan


def validate_chief(store, payload, *, at=None):
    from .runtime_reads import bind_read, business_identity, configured_entities

    plan = restore(payload.get("requirements") or {})
    identity = business_identity("chief", kind="portfolio", scope_id="portfolio",
        entities=configured_entities("portfolio", "portfolio", config_dir=plan.config_root), as_of=plan.as_of,
        explicit=(plan.task_inputs.get("read_scopes") or {}).get("chief:portfolio"))
    with bind_read(identity):
        return validate(store, payload, at=at)
