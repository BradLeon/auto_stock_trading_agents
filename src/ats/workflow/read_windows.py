"""Bind invocation clocks to explicitly committed, workflow-specific read windows.

This module reads existing joint authorization/history; it creates no authority.
A current route alone or a global target flag cannot register a window.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

from . import cutover as plane
from . import joint_cutover as joint
from .cutover_routing import RouteUnavailable
from .scoped_routes import RouteIdentity


def invocation_time(value):
    point = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(point, datetime) or point.tzinfo is None:
        raise RouteUnavailable("business as_of requires a timezone")
    return point.astimezone(UTC)


def check_time(identity, as_of):
    point = invocation_time(as_of)
    if identity.scope.get("time_policy") == "per_invocation":
        return point
    window = identity.scope["time_range"]
    if not datetime.fromisoformat(window["start"]) <= point <= datetime.fromisoformat(window["end"]):
        raise RouteUnavailable("business as_of is outside authorized window")
    return point


def _same_business(left, right, *, purpose=None):
    from .invocation_bindings import matches
    return matches(left, right, purpose=purpose or left.consumer_id)


def _deployment(identity, *, workflow=None, path=None):
    """Resolve the current generation's immutable evidence, not an old grant."""
    with closing(joint._read(path)) as conn:
        row = conn.execute(
            "SELECT * FROM scoped_cutover_routes WHERE boundary=? AND identity_key=?",
            (plane.PROJECTION_READ, identity.key),
        ).fetchone()
        if row is None or row["route"] != plane.ROUTE_TARGET or json.loads(row["identity_json"]) != identity.as_row():
            raise RouteUnavailable("authorized target read window is not active")
        history = conn.execute(
            "SELECT * FROM scoped_cutover_history WHERE boundary=? AND identity_key=? AND generation=?",
            (plane.PROJECTION_READ, identity.key, row["generation"]),
        ).fetchall()
        if len(history) != 1 or not history[0]["reason"].startswith("joint:"):
            raise RouteUnavailable("read window lacks an exact committed deployment")
        evidence = json.loads(history[0]["evidence_json"])
        operation = conn.execute("SELECT * FROM joint_cutover_operations WHERE batch_id=?",
            (history[0]["reason"].removeprefix("joint:"),)).fetchone()
        if operation is None or operation["state"] != "committed":
            raise RouteUnavailable("read window deployment is not committed")
        request = joint._validate(json.loads(operation["request_json"]))
        if workflow is not None and request["workflow"] != workflow:
            raise RouteUnavailable("read window belongs to another workflow")
        member = next((m for m in request["members"] if m["boundary"] == plane.PROJECTION_READ
                       and m["identity"] == identity.as_row()), None)
        auth = evidence["authorization"]
        if (member is None or member["route"] != plane.ROUTE_TARGET
                or member["expected_generation"] + 1 != row["generation"]
                or auth.get("member") != member or auth.get("workflow") != request["workflow"]
                or auth.get("valid") is not True):
            raise RouteUnavailable("read window authorization/generation mismatch")
    joint._authorization(auth, request, path, runtime=True)
    return {**evidence, "deployment": {"workflow": request["workflow"],
        "batch_id": request["batch_id"], "permission": auth["reference"],
        "policy": request.get("deployment_policy", "finite"), "generation": row["generation"],
        "qualification_scope": identity.scope, "report_scope": evidence["report"]["identity"]["scope"],
        "report_reference": evidence["report"]["reference"]}}


def assert_current(identity, *, workflow=None, path=None):
    """Recheck mutable authorization and signed report status at every entry."""
    from .shadow_reports import check_batch_report, require_batch_report

    try:
        joint.assert_scope_open(identity, path=path)
        evidence = _deployment(identity, workflow=workflow, path=path)
        proof = evidence["report"]
        if proof.get("identity") != identity.as_row() or proof.get("valid") is not True:
            raise RouteUnavailable("read window report identity mismatch")
        batch = SimpleNamespace(shadow_report_id=proof["reference"], scope=identity.scope,
            batch_class=proof.get("batch_class", "internal_state_approval" if identity.consumer_id in {"risk", "trader", "clerk"} else "research_read"),
            consumers=(identity.consumer_id,), required_surfaces=tuple(proof.get("required_surfaces",
                ("input_snapshot", "analyst_output", "risk_verdict", "approval_chain", "trade_attribution")
                if identity.consumer_id in {"risk", "trader", "clerk"} else ("input_snapshot", "analyst_output"))))
        ok, problems = require_batch_report(batch, check_batch_report)
        if not ok:
            raise RouteUnavailable("; ".join(problems))
        return evidence
    except (plane.CutoverError, sqlite3.Error, ValueError, KeyError, TypeError, OSError) as exc:
        raise RouteUnavailable("read window authority refused: " + str(exc)) from exc


def _consumer_wide_binding(identity, *, purpose=None, path=None):
    """The identity itself, when its consumer's whole research read is on the target route and no exact
    window is registered for it (Phase F simplification, 2026-10-15/16).

    Consumer-wide routes (`consumer_cutover`) carry no per-window authorization, so the Dispatcher cannot
    pick a pre-registered window for them; the plan's own business identity is bound instead. An exact
    registered window, valid or not, always wins and keeps failing as before. A consumer on the legacy
    route, or without a route row, gets nothing here and is refused by the caller.
    """
    from .consumer_cutover import lookup

    try:
        with closing(joint._read(path)) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='scoped_cutover_routes'").fetchone():
                for row in conn.execute("SELECT identity_json FROM scoped_cutover_routes WHERE boundary=?",
                                        (plane.PROJECTION_READ,)):
                    if _same_business(identity, RouteIdentity(**json.loads(row[0])), purpose=purpose):
                        return None
            wide = lookup(conn, plane.PROJECTION_READ, identity)
    except sqlite3.Error:
        return None
    return identity if wide and wide["route"] == plane.ROUTE_TARGET else None


def select(identity, *, workflow, as_of, explicit=None, path=None, purpose=None, config_dir=None):
    """Select one explicitly registered exact business window; ambiguity refuses.

    Explicit selection can disambiguate two windows, but must itself have the
    same business identity and a current committed authorization for workflow.
    Invalid windows are never filtered out merely to find a convenient survivor.
    """
    try:
        wide = _consumer_wide_binding(identity, purpose=purpose, path=path)
        if wide is not None:
            from .runtime_reads import gate_read
            gate_read(wide)
            return wide
        if explicit is not None:
            supplied = explicit.as_row() if isinstance(explicit, RouteIdentity) else explicit
            who = RouteIdentity(**supplied) if "scope" in supplied else RouteIdentity(
                identity.domain_id, identity.consumer_id, identity.contract_version, supplied)
            candidates = [who] if _same_business(identity, who, purpose=purpose) else []
        else:
            with closing(joint._read(path)) as conn:
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='scoped_cutover_routes'").fetchone():
                    raise RouteUnavailable("workflow has no explicitly authorized read window")
                candidates = []
                for row in conn.execute("SELECT identity_json FROM scoped_cutover_routes WHERE boundary=?",
                                        (plane.PROJECTION_READ,)):
                    who = RouteIdentity(**json.loads(row[0]))
                    if not _same_business(identity, who, purpose=purpose):
                        continue
                    try:
                        check_time(who, as_of)
                    except RouteUnavailable:
                        continue
                    # Only exact workflow registrations participate; revoked or
                    # stopped matching registrations still fail at admission.
                    rows = joint._operations(conn)
                    if any(json.loads(r["request_json"])["workflow"] == workflow and any(
                            m["boundary"] == plane.PROJECTION_READ and m["identity"] == who.as_row()
                            for m in json.loads(r["request_json"])["members"]) for r in rows):
                        candidates.append(who)
        if len(candidates) != 1:
            raise RouteUnavailable("workflow read window is missing or ambiguous")
        who = candidates[0]
        check_time(who, as_of)
        assert_current(who, workflow=workflow, path=path)
        if who.scope.get("time_policy") == "per_invocation":
            from .invocation_bindings import invocation
            invocation(who, identity, workflow=workflow, as_of=as_of,
                purpose=purpose or identity.consumer_id, config_dir=config_dir)
        from .runtime_reads import gate_read
        gate_read(who)
        return who
    except (sqlite3.Error, ValueError, KeyError, TypeError, OSError) as exc:
        raise RouteUnavailable("workflow read window binding refused: " + str(exc)) from exc


def has_persistent_binding(identity, *, purpose, path=None):
    """Legacy entries remain legacy; a matching stable registration cannot be bypassed."""
    with closing(joint._read(path)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='scoped_cutover_routes'").fetchone():
            return False
        for row in conn.execute("SELECT identity_json FROM scoped_cutover_routes WHERE boundary=?", (plane.PROJECTION_READ,)):
            who = RouteIdentity(**json.loads(row[0]))
            if who.scope.get("time_policy") == "per_invocation" and _same_business(identity, who, purpose=purpose):
                return True
        return False


def bind_plan(plan):
    """Persist exact windows in the frozen plan, retaining its independent as_of."""
    from .runtime_reads import business_identity, configured_entities, task_identity

    workflow = plan.trigger["workflow_id"]
    supplied = plan.task_inputs.get("read_scopes", {}) or {}
    unbound = replace(plan, task_inputs={**plan.task_inputs, "read_scopes": {}})
    bindings = {}
    invocations = {}
    for task in plan.tasks:
        point = task_identity(unbound, task)
        explicit = supplied.get(task.instance_key, supplied.get(f"{point.consumer_id}:{task.scope.key}"))
        who = select(point, workflow=workflow, as_of=plan.as_of, explicit=explicit,
            purpose=task.task_id, config_dir=plan.config_root or None)
        bindings[task.instance_key] = who.as_row()
        if who.scope.get("time_policy") == "per_invocation":
            from .invocation_bindings import invocation
            invocations[task.instance_key] = invocation(who, point, workflow=workflow,
                as_of=plan.as_of, purpose=task.task_id, run_id=plan.run_id,
                config_dir=plan.config_root or None)
    if plan.enter_decision_cycle:
        point = business_identity("chief", kind="portfolio", scope_id="portfolio",
            entities=configured_entities("portfolio", "portfolio", config_dir=plan.config_root),
            as_of=plan.as_of)
        bindings["chief:portfolio"] = select(point, workflow=workflow, as_of=plan.as_of,
            explicit=supplied.get("chief:portfolio"), purpose="chief", config_dir=plan.config_root or None).as_row()
        who = RouteIdentity(**bindings["chief:portfolio"])
        if who.scope.get("time_policy") == "per_invocation":
            from .invocation_bindings import invocation
            invocations["chief:portfolio"] = invocation(who, point, workflow=workflow,
                as_of=plan.as_of, purpose="chief", run_id=plan.run_id, config_dir=plan.config_root or None)
    from .phase_e import build_plan
    from .run_contracts import TriggerContext

    return build_plan(requested_tasks=plan.requested_tasks, scope=plan.request_scope,
        trigger=TriggerContext(**plan.trigger), as_of=plan.as_of, run_id=plan.run_id,
        profile_id=plan.profile_id, enter_decision_cycle=plan.enter_decision_cycle,
        config_dir=plan.config_root, namespace=plan.tasks[0].instance_key.split(":", 1)[0],
        task_inputs={**plan.task_inputs, "read_scopes": bindings, "read_invocations": invocations})
