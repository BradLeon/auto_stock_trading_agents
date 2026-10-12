"""One frozen, versioned requirement set for analysis and actual Chief entry."""
from datetime import UTC, datetime

from ..agent.task_projection import ProjectionScope, reuse_decision
from .cutover_routing import RouteUnavailable
from .phase_e import TASK_ROLE, _hash, build_plan
from .run_contracts import TriggerContext


def freeze(plan, *, outcomes=None, manifest=None, workflow_store_path=""):
    if not plan.enter_decision_cycle:
        raise RouteUnavailable("decision request was not declared")
    value = {"version": "decision-requirements-v1", "plan": plan.as_dict(),
            "plan_hash": plan.plan_hash, "outcomes": outcomes,
            "manifest": manifest, "workflow_store_path": str(workflow_store_path),
            "read_dependencies": freeze_read_dependencies(plan)}
    value["requirements_hash"] = _hash(value)
    return value


def dependency_identities(plan):
    from .runtime_reads import business_identity, configured_entities, task_identity
    identities = [task_identity(plan, task) for task in plan.tasks]
    identities.append(business_identity("chief", kind="portfolio", scope_id="portfolio",
        entities=configured_entities("portfolio", "portfolio", config_dir=plan.config_root),
        as_of=plan.as_of, explicit=(plan.task_inputs.get("read_scopes") or {}).get("chief:portfolio")))
    return {who.key: who for who in identities}


def freeze_read_dependencies(plan):
    from . import cutover as plane
    from .isolation import verified_isolation_root
    from .read_recovery import current_policy
    from .runtime_reads import gate_read
    from .scoped_routes import resolve_route

    rows = []
    isolated = verified_isolation_root() is not None
    for who in dependency_identities(plan).values():
        decision = gate_read(who)
        authority = resolve_route(plane.PROJECTION_READ, who)
        policy = current_policy(who)
        if not isolated and (decision.route != "target" or decision.qualification.get("status") != "eligible"):
            raise RouteUnavailable("new decision dependency requires qualified target business scope")
        rows.append({"identity": who.as_row(), "route": authority["route"],
                     "generation": authority["generation"], "candidate_isolated": isolated,
                     "recovery_generation": policy["event_id"] if policy else 0})
    return rows


def validate_read_dependencies(plan, requirements):
    from . import cutover as plane
    from .isolation import verified_isolation_root
    from .read_recovery import current_policy
    from .runtime_reads import gate_read
    from .scoped_routes import resolve_route

    expected = dependency_identities(plan)
    rows = requirements.get("read_dependencies") or []
    if len(rows) != len(expected) or {row.get("identity", {}).get("consumer_id") for row in rows} != {
            who.consumer_id for who in expected.values()}:
        raise RouteUnavailable("frozen read dependency closure missing")
    seen = set()
    for row in rows:
        from .scoped_routes import RouteIdentity
        who = RouteIdentity(**row["identity"])
        if who.key not in expected or who.key in seen:
            raise RouteUnavailable("frozen read dependency scope differs")
        seen.add(who.key)
        if row["candidate_isolated"] and verified_isolation_root() is None:
            raise RouteUnavailable("isolated research basis cannot authorize production")
        authority = resolve_route(plane.PROJECTION_READ, who)
        if any(row[key] != authority[key] for key in ("route", "generation")):
            raise RouteUnavailable("research read dependency route/generation changed")
        policy = current_policy(who)
        if row["recovery_generation"] != (policy["event_id"] if policy else 0):
            raise RouteUnavailable("research read recovery policy changed")
        if who.scope.get("time_policy") == "per_invocation":
            from .invocation_bindings import recheck
            binding = next((value for value in (plan.task_inputs.get("read_invocations") or {}).values()
                            if value.get("deployment_identity") == who.as_row()), None)
            recheck(who, binding)
        decision = gate_read(who)  # rechecks scope stop, revocation, TTL and enforcement
        if not row["candidate_isolated"] and (decision.route != "target" or
                decision.qualification.get("status") != "eligible"):
            raise RouteUnavailable("research dependency qualification invalid")
    if seen != set(expected):
        raise RouteUnavailable("required read dependency omitted")


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


def validate_routine_value_contract(payload):
    """A current qualitative update cannot pretend to contain measured KPI values."""
    if payload.get("metric") == "narrative":
        if payload.get("previous_value") is not None or payload.get("new_value") is not None:
            raise RouteUnavailable("qualitative Routine update has no measurable values")
    elif payload.get("new_value") is None:
        raise RouteUnavailable("numeric Routine update requires a measurable value")


def routine_producer_refs(store, row, scope):
    """Resolve the complete terminal Routine bundle through the workflow boundary."""
    import json
    import sqlite3

    try:
        producer = store.conn.execute(
            "SELECT status,run_id,task_id,scope_json,projection_refs_json "
            "FROM agent_runs WHERE agent_run_id=?", (row["agent_run_id"],)).fetchone()
        refs = json.loads(producer[4]) if producer else []
        valid = (producer is not None and producer[0] == "succeeded"
            and producer[1] == row.get("workflow_run_id")
            and producer[2] == "fundamental-routine"
            and json.loads(producer[3]) == scope.model_dump(mode="json")
            and isinstance(refs, list) and refs
            and all(isinstance(ref, str) and ref for ref in refs)
            and len(set(refs)) == len(refs) and row["projection_id"] in refs)
    except (sqlite3.Error, ValueError, TypeError):
        return []
    return refs if valid else []


def validate(store, snapshot_payload, *, at=None):
    """Re-resolve every fixed binding before a model, revision or cycle write."""
    from ..agents.chief.assemble import _envelope_from_row
    from .consumer_reads import read_projection

    requirements = snapshot_payload.get("requirements") or {}
    plan = restore(requirements)
    validate_read_dependencies(plan, requirements)
    items = snapshot_payload.get("items") or []
    outcome_rows = requirements.get("outcomes") or []
    outcomes = {row["instance_key"]: row for row in outcome_rows}
    if len(outcome_rows) != len(plan.tasks) or set(outcomes) != {task.instance_key for task in plan.tasks}:
        raise RouteUnavailable("selected task dependency closure incomplete")
    references = requirements.get("manifest") or []
    manifest = {}
    for reference in references:
        key = (reference["role"], reference["scope_kind"], reference["scope_id"])
        manifest.setdefault(key, []).append(reference)
    bindings = {(TASK_ROLE[t.task_id], t.scope.kind, t.scope.id) for t in plan.tasks}
    if set(manifest) != bindings:
        raise RouteUnavailable("fixed manifest dependency closure differs")
    ids = [m.get("projection_id") for m in references]
    item_ids = [item.get("projection_id") for item in items]
    if (not all(ids) or len(set(ids)) != len(ids) or len(set(item_ids)) != len(item_ids)
            or len(items) != len(references) or set(item_ids) != set(ids)):
        raise RouteUnavailable("fixed category/scope basis incomplete")
    stamp = at or datetime.now(UTC)
    from .runtime_reads import current_read_context
    context = current_read_context()
    read_cutoff = context.cutoff if context else stamp
    selected = {}
    for task in plan.tasks:
        outcome = outcomes[task.instance_key]
        if outcome.get("task_id") != task.task_id or outcome.get("scope") != task.scope.model_dump(mode="json"):
            raise RouteUnavailable("selected task outcome scope differs")
        role = TASK_ROLE[task.task_id]
        hits = [item for item in items if item.get("agent_role") == role and
                item.get("scope_kind") == task.scope.kind and item.get("scope_id") == task.scope.id]
        refs = manifest[(role, task.scope.kind, task.scope.id)]
        if (not hits or len(hits) != len(refs) or not all(i.get("reusable") for i in hits)
                or outcome.get("status") != "succeeded"
                or (task.task_id != "fundamental-routine" and len(hits) != 1)):
            raise RouteUnavailable("selected task missing/failed/stale: " + task.instance_key)
        expected_refs = outcome.get("projection_refs") or []
        if (len(set(expected_refs)) != len(expected_refs)
                or set(expected_refs) != {item["projection_id"] for item in hits}):
            raise RouteUnavailable("selected task projection differs from frozen run")
        envelopes = []
        by_id = {item["projection_id"]: item for item in hits}
        reference_by_id = {reference["projection_id"]: reference for reference in refs}
        for projection_id in expected_refs:
            item = by_id[projection_id]
            reference = reference_by_id.get(projection_id) or {}
            if any(reference.get(k) != item.get(k) for k in ("projection_id", "content_hash")):
                raise RouteUnavailable("snapshot differs from fixed manifest")
            row = read_projection(store, consumer="chief", role=role, scope=task.scope, at=read_cutoff,
                                  projection_id=projection_id, content_hash=item["content_hash"])
            if not row:
                raise RouteUnavailable("required decision projection unavailable: " + task.instance_key)
            envelope = _envelope_from_row(row, task.scope)
            if task.task_id == "fundamental-routine":
                validate_routine_value_contract(envelope.payload)
            ok, reason = reuse_decision(envelope, scope=task.scope, at=stamp,
                schema_name=task.output_schema, schema_version="v1")
            from .phase_e import FRESHNESS_SECONDS

            source_time = datetime.fromisoformat(envelope.as_of)
            if (source_time.tzinfo is None or source_time > min(stamp, read_cutoff) or
                    (stamp-source_time).total_seconds() > FRESHNESS_SECONDS[task.task_id]):
                ok, reason = False, "freshness_unconfirmed"
            if not ok:
                raise RouteUnavailable("required decision input invalid: " + task.instance_key + ":" + reason)
            envelopes.append(envelope)
        selected[task.instance_key] = envelopes
    for task in plan.tasks:
        from .dispatcher import Dispatcher

        parents = {parent: selected[parent] for parent in task.dependencies}
        if any(len(values) != 1 for values in parents.values()):
            raise RouteUnavailable("multi-output analysis dependency has no declared input contract")
        wanted_inputs, wanted_vintages, _ = Dispatcher._task_inputs(
            None, plan, task, {parent: values[0] for parent, values in parents.items()})
        for envelope in selected[task.instance_key]:
            if not set(wanted_inputs) <= set(envelope.input_refs) or not set(wanted_vintages) <= set(envelope.data_vintage_refs):
                raise RouteUnavailable("fixed input/vintage lineage differs: " + task.instance_key)
            for parent, values in parents.items():
                if values[0].projection_id not in envelope.input_refs and not any(
                        values[0].projection_id in ref and values[0].content_hash in ref
                        for ref in envelope.input_refs):
                    raise RouteUnavailable("selected task dependency lineage missing: " + task.instance_key)
    workflow_path = requirements.get("workflow_store_path")
    standalone_routine = any(task.task_id == "fundamental-routine" and
        any(envelope.agent_run_id for envelope in selected[task.instance_key]) for task in plan.tasks)
    if workflow_path or standalone_routine:
        # The overall run may still be awaiting Chief. Analysis attempts are
        # already terminal and independently checked against their durable rows.
        import json
        import sqlite3

        connection = sqlite3.connect("file:" + workflow_path + "?mode=ro", uri=True) if workflow_path else store.conn
        try:
            if workflow_path:
                saved = connection.execute("SELECT plan_hash,status FROM workflow_runs WHERE run_id=?",
                                           (plan.run_id,)).fetchone()
                if not saved or saved[0] != plan.plan_hash or saved[1] not in {"running", "complete"}:
                    raise RouteUnavailable("workflow run incomplete or mismatched")
            for task in plan.tasks:
                if not workflow_path and task.task_id != "fundamental-routine":
                    continue
                envelopes = selected[task.instance_key]
                expected_refs = [envelope.projection_id for envelope in envelopes]
                for envelope in envelopes:
                    attempt = connection.execute("SELECT status,run_id,task_id,task_instance_key,projection_refs_json FROM agent_runs WHERE agent_run_id=?",
                                                 (envelope.agent_run_id,)).fetchone()
                    if (not attempt or tuple(attempt[:4]) != ("succeeded", envelope.workflow_run_id, task.task_id, task.instance_key)
                            or json.loads(attempt[4]) != expected_refs):
                        raise RouteUnavailable("selected task attempt not terminal or mismatched")
                if workflow_path:
                    current = connection.execute(
                        "SELECT status,projection_refs_json FROM agent_runs WHERE run_id=? "
                        "AND task_instance_key=? ORDER BY attempt_no DESC LIMIT 1",
                        (plan.run_id, task.instance_key)).fetchone()
                    if not current or current[0] != "succeeded" or json.loads(current[1]) != expected_refs:
                        raise RouteUnavailable("current selected task attempt incomplete or mismatched")
        finally:
            if workflow_path:
                connection.close()
    return plan


def validate_chief(store, payload, *, at=None):
    from .runtime_reads import bind_read, business_identity, configured_entities

    plan = restore(payload.get("requirements") or {})
    identity = business_identity("chief", kind="portfolio", scope_id="portfolio",
        entities=configured_entities("portfolio", "portfolio", config_dir=plan.config_root), as_of=plan.as_of,
        explicit=(plan.task_inputs.get("read_scopes") or {}).get("chief:portfolio"))
    with bind_read(identity, as_of=plan.as_of,
                   invocation=(plan.task_inputs.get("read_invocations") or {}).get("chief:portfolio")):
        return validate(store, payload, at=at)
