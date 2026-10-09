"""Pre-run new-entry requirements. Historical comparison matrices stay readable.

This fixes expectations, not results. Assertion execution and report signoff are
separate gates (3.3–3.6); constructing a matrix never grants eligibility.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from . import shadow_inputs as inputs
from .phase_e import FRESHNESS_SECONDS, build_plan
from .run_contracts import TriggerContext
from .scoped_routes import canonical_json
from .shadow_matrix import BATCH_CLASSES, DECISION, RESEARCH_READ, TRADING, MatrixError

VERSION = "new-entry-matrix-v2"
REQUIREMENTS_VERSION = "phase-f-new-entry-v1"
DIMENSIONS = ("inputs", "schedule", "analysis", "risk", "approval", "attribution")


@dataclass(frozen=True)
class RequirementMatrix:
    body_json: str

    @property
    def body(self):
        return json.loads(self.body_json)

    @property
    def matrix_hash(self):
        return hashlib.sha256(self.body_json.encode()).hexdigest()

    def as_row(self):
        return {**self.body, "matrix_hash": self.matrix_hash}


def freeze(plan, *, workflow_id, identity, batch_class=RESEARCH_READ,
           runtime_surfaces=(), model_execution=True, criteria=None):
    """Compile mandatory expectations from the actual expanded WorkflowPlan.

    Runtime consumption is declared before execution, then checked against actual
    reads. A research workflow may use quotes without becoming a trading batch.
    """
    if not workflow_id.strip() or batch_class not in BATCH_CLASSES:
        raise MatrixError("workflow and known batch class required")
    if plan.enter_decision_cycle != (batch_class in {DECISION, TRADING}):
        raise MatrixError("decision scope cannot be declared research-only")
    modes = {t.task_id.removeprefix("fundamental-") for t in plan.tasks
             if t.task_id.startswith("fundamental-")}
    if len(modes) > 1:
        raise MatrixError("select exactly one Fundamental mode")
    runtime = set(runtime_surfaces)
    allowed = {inputs.MARKET_RUNTIME, inputs.ACCOUNT_STATE, inputs.HISTORY_STATE}
    if not runtime <= allowed:
        raise MatrixError("unknown runtime input surface")
    # Technical actually consumes price history; callers cannot declare it away.
    if any(t.task_id in {"technical-review", "sector-review"} or
           (t.task_id in {"macro-review", "fundamental-event"} and
            plan.task_inputs.get(t.task_id, {}).get("live_data", True)) for t in plan.tasks):
        runtime.add(inputs.MARKET_RUNTIME)
    if not model_execution and any(plan.task_inputs.get(t.task_id, {}).get("use_llm", True)
                                   for t in plan.tasks):
        raise MatrixError("model input cannot be omitted for a model-enabled task")
    if batch_class in {DECISION, TRADING}:
        runtime |= allowed
    required = {inputs.PERSISTENT_REFS, inputs.PROJECTION_HASH, inputs.LOGICAL_EVAL_TIME, *runtime}
    if model_execution or batch_class in {DECISION, TRADING}:
        required.add(inputs.MODEL_CONFIG)
    if batch_class in {DECISION, TRADING}:
        required.add(inputs.RULESET_VERSION)
    assertions = [
        {"id": "inputs.fixed", "dimension": "inputs", "required": True,
         "expected": {"surfaces": sorted(required), "replay_without_source_refresh": True}},
        {"id": "schedule.coverage", "dimension": "schedule", "required": True,
         "expected": {"basis": "independent schedule/event configuration", "missing": 0,
                      "duplicate_publications": 0}},
    ]
    for task in plan.tasks:
        from .runtime_reads import task_identity

        assertions.append({"id": "analysis." + task.instance_key, "dimension": "analysis",
            "required": True, "expected": {"task": task.as_dict(), "schema_version": "v1",
                "read_identity": task_identity(plan, task).as_row(),
                "max_age_seconds": FRESHNESS_SECONDS[task.task_id],
                "status": "succeeded-or-valid-reuse", "scope_hash_lineage_valid": True}})
    for dimension, condition in (
        ("risk", "required inputs complete; policy constraints enforced; review bound to revision"),
        ("approval", "human approval bound to exact reviewed revision; stale or changed revision refused"),
        ("attribution", "snapshot/revision/review/approval/intent/receipt chain; isolated ledger; idempotence"),
    ):
        mandatory = batch_class == TRADING or (dimension == "risk" and batch_class == DECISION)
        assertions.append({"id": dimension + ".contract", "dimension": dimension,
            "required": mandatory, "expected": condition if mandatory else "not-applicable",
            "reason": "selected research/decision scope does not execute this boundary" if not mandatory else ""})
    from .acceptance_assertions import validate_criteria
    criteria = validate_criteria(criteria or {}, assertions)
    from .schedule_runtime import identity as trigger_identity
    for assertion in assertions:
        assertion["criteria"] = criteria.get(assertion["id"], [])
    schedule = []
    for task in plan.tasks:
        key, logical = trigger_identity(task.task_id, task.scope.model_dump(mode="json"), plan.trigger)
        schedule.append({"key": key, "identity": logical, "instance_key": task.instance_key})
    return RequirementMatrix(canonical_json({"version": VERSION,
        "requirements_version": REQUIREMENTS_VERSION, "workflow_id": workflow_id,
        "identity": identity.as_row(), "batch_class": batch_class,
        "selected_mode": next(iter(modes), "none"), "plan": plan.as_dict(),
        "plan_hash": plan.plan_hash, "runtime_surfaces": sorted(runtime),
        "model_execution": model_execution, "assertions": assertions,
        "criteria": criteria, "expected_schedule": schedule,
        "input_fixing": {s: "content-or-immutable-resolvable-reference" if s in required
                         else inputs.NOT_APPLICABLE for s in inputs.ALL_SURFACES},
        "legacy_required": False}))


def restore(row):
    """Recompile rather than trust a rehashed omission or not-applicable flag."""
    from ..agent.task_projection import ProjectionScope
    from .scoped_routes import RouteIdentity

    try:
        body = {k: v for k, v in row.items() if k != "matrix_hash"}
        matrix = RequirementMatrix(canonical_json(body))
        if row["matrix_hash"] != matrix.matrix_hash or body["version"] != VERSION:
            raise ValueError("hash/version")
        raw = body["plan"]
        plan = build_plan(requested_tasks=raw["requested_tasks"],
            scope=ProjectionScope.model_validate(raw["request_scope"]),
            trigger=TriggerContext.model_validate(raw["trigger"]), as_of=raw["as_of"],
            run_id=raw["run_id"], profile_id=raw["profile_id"],
            enter_decision_cycle=raw["enter_decision_cycle"], task_inputs=raw["task_inputs"],
            config_dir=raw["config_root"] or None)
        expected = freeze(plan, workflow_id=body["workflow_id"],
            identity=RouteIdentity(**body["identity"]), batch_class=body["batch_class"],
            runtime_surfaces=body["runtime_surfaces"], model_execution=body["model_execution"],
            criteria=body["criteria"])
        if expected != matrix:
            raise ValueError("expectations/configuration drift")
        return matrix
    except (KeyError, TypeError, ValueError) as exc:
        raise MatrixError("new-entry matrix invalid or drifted") from exc


def check_inputs(matrix, packet, *, actual_runtime_surfaces=()):
    """Required runtime cannot be hidden by a not-applicable declaration."""
    matrix = restore(matrix.as_row())
    body = matrix.body
    if (packet.batch_class != body["batch_class"]
            or packet.consumer_id != body["identity"]["consumer_id"]
            or packet.scope != body["identity"]["scope"]
            or packet.surfaces.get(inputs.LOGICAL_EVAL_TIME) != body["plan"]["as_of"]):
        raise MatrixError("packet scope/class/time differs from fixed matrix")
    packet.assert_usable_as_evidence()
    required = {s for s, degree in body["input_fixing"].items() if degree != inputs.NOT_APPLICABLE}
    if not set(actual_runtime_surfaces) <= set(body["runtime_surfaces"]):
        raise MatrixError("actual runtime consumption omitted from pre-run matrix")
    for surface in inputs.ALL_SURFACES:
        value = packet.surfaces.get(surface)
        if surface in required and (not value or value == inputs.NOT_APPLICABLE):
            raise MatrixError("required input not fixed: " + surface)
        if surface not in required and value != inputs.NOT_APPLICABLE:
            raise MatrixError("undeclared input consumption: " + surface)
    return True
