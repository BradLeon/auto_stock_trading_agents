"""Workflow run contracts: request, result, task registry and trigger identity.

This module defines structures and validation rules ONLY — there is no scheduler here.
The point is that once a run is described by these types, three things become decidable
without reading the call site:

* what a task depends on (declared in the registry, never in call order);
* whether the run may enter the decision cycle (a terminal state, not a convention);
* whether a redelivered trigger is the same logical task (a derived idempotency key,
  not the dispatcher's in-memory state).

The last one is why trigger identity lives here rather than in the scheduler: APScheduler
(or any successor) may wake the process, but it cannot survive a restart or multiple
workers, so it must not be the authority on "have I seen this one before".
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..agent.task_projection import AgentRole, ProjectionScope

TriggerKind = Literal["manual", "schedule", "event"]

TaskStatus = Literal["succeeded", "failed", "blocked", "missing", "stale"]
RunTerminal = Literal["complete", "incomplete"]


class ContractError(ValueError):
    """A contract-level refusal: the request cannot be honoured as stated."""


# --------------------------------------------------------------------------- #
# Trigger
# --------------------------------------------------------------------------- #

class TriggerContext(BaseModel):
    """Manual, schedule and event triggers reduced to one shape."""

    model_config = ConfigDict(extra="forbid")

    kind: TriggerKind
    workflow_id: str = ""
    trigger_id: str = ""
    # Schedule: the plan's identity and the moment it was PLANNED for. Compensation
    # after downtime must reuse the planned instant, never the catch-up instant —
    # otherwise a misfire mints a second logical run for the same tick.
    schedule_id: str = ""
    scheduled_for: str = ""
    # Event: identity plus version, so a corrected re-delivery is a new logical task
    # while an unchanged replay is the same one.
    event_id: str = ""
    event_version: str = ""
    requested_at: str = ""

    @field_validator("trigger_id", mode="before")
    @classmethod
    def _normalize_trigger_id(cls, value: object) -> object:
        if value is None:
            return ""
        return str(value).strip()

    def validate_for_use(self) -> TriggerContext:
        """Fill what can be derived; refuse what a trigger of this kind must carry.

        Manual triggers are the one case where the caller may omit the key: the system
        generates it and hands it back, and a retry MUST resubmit that same value.
        """
        if not self.requested_at:
            self.requested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if self.kind == "manual":
            if not self.trigger_id:
                self.trigger_id = f"manual-{uuid.uuid4().hex[:16]}"
            return self
        if self.kind == "schedule":
            if not (self.schedule_id and self.scheduled_for):
                raise ContractError(
                    "a schedule trigger must carry schedule_id and scheduled_for; "
                    "the planned instant is what makes a misfire compensation the same "
                    "logical run as the tick it missed")
            return self
        if not (self.event_id and self.event_version):
            raise ContractError(
                "an event trigger must carry event_id and event_version; without the "
                "version a corrected re-delivery would be indistinguishable from a "
                "replay of the original")
        return self

    def idempotency_key(self, task_id: str) -> str:
        """Stable identity of one task instance produced by this trigger.

        Deliberately excludes `requested_at`: the same scheduled instant replayed after
        a restart, or compensated at a different wall-clock time, must derive the same
        key. Including the compensation time is exactly how a misfire becomes a
        duplicate.
        """
        if self.kind == "manual":
            body = f"manual|{self.trigger_id}|{task_id}"
        elif self.kind == "schedule":
            body = f"schedule|{self.schedule_id}|{self.scheduled_for}|{task_id}"
        else:
            body = f"event|{self.event_id}|{self.event_version}|{task_id}"
        return hashlib.sha1(body.encode()).hexdigest()[:32]


# --------------------------------------------------------------------------- #
# Decision-required categories (Phase D task 1.4)
# --------------------------------------------------------------------------- #

# The six decision-required analysis categories (docs/TARGET_WORKFLOW_DATAFLOW.md
# §10.1). Requirement satisfaction is judged per CATEGORY, not per task_id: the
# fundamental analyst runs in exactly one of two modes per cycle, so demanding
# both task ids would leave every cycle permanently incomplete. A category is
# satisfied when ANY task whose agent_role is listed here delivered.
DECISION_CATEGORY_ROLES: dict[str, frozenset[str]] = {
    "layer_analysis": frozenset({"layer_analysis"}),
    "information_brief": frozenset({"information_brief"}),
    "sector_allocation": frozenset({"sector_allocation"}),
    "fundamental_analysis": frozenset({
        "fundamental_expectation_update", "fundamental_event_review"}),
    "macro_review": frozenset({"macro_review"}),
    "technical_review": frozenset({"technical_review"}),
}

ROLE_TO_CATEGORY: dict[str, str] = {
    role: category
    for category, roles in DECISION_CATEGORY_ROLES.items()
    for role in roles
}


# --------------------------------------------------------------------------- #
# Task registry
# --------------------------------------------------------------------------- #

class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=1, ge=1)
    backoff_seconds: float = Field(default=0.0, ge=0.0)
    retry_on: tuple[str, ...] = ()


class WorkflowTaskSpec(BaseModel):
    """One schedulable task, declared rather than sequenced in code."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    agent_role: AgentRole | None = None
    depends_on: tuple[str, ...] = ()
    trigger_modes: tuple[TriggerKind, ...] = ("manual", "schedule", "event")
    input_contract: str = ""
    output_schema: str = ""
    # Freshness: how long a projection from this task may be reused. Zero means
    # "never reuse" — every run recomputes.
    freshness_seconds: int = Field(default=0, ge=0)
    timeout_seconds: int = Field(default=0, ge=0)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    resource_group: str = "default"
    required_for_decision: bool = True

    @field_validator("depends_on", "trigger_modes", mode="before")
    @classmethod
    def _as_tuple(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        return tuple(value)


class UnregisteredTaskError(ContractError):
    """A task that was never declared in the registry."""


class TaskRegistry:
    """The declared set of schedulable tasks and their dependencies.

    Adding a task means adding a spec — never editing a dispatcher's ordering. That is
    the whole reason this exists: "who runs before whom" belongs to data, not control
    flow, because control flow cannot be queried, tested or diffed.
    """

    def __init__(self, specs: Sequence[WorkflowTaskSpec] = ()) -> None:
        self._specs: dict[str, WorkflowTaskSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: WorkflowTaskSpec) -> None:
        self._specs[spec.task_id] = spec

    def spec(self, task_id: str) -> WorkflowTaskSpec:
        try:
            return self._specs[task_id]
        except KeyError:
            raise UnregisteredTaskError(
                f"task {task_id!r} is not registered; unregistered tasks are never "
                "scheduled") from None

    def has(self, task_id: str) -> bool:
        return task_id in self._specs

    def task_ids(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def dependencies_for(self, task_id: str) -> tuple[str, ...]:
        """Transitive upstream of `task_id`, in dependency-first order.

        Every name is validated against the registry first: a dependency that was never
        declared is a registration bug, and silently scheduling around it would hide the
        fact that the graph is incomplete.
        """
        ordered: list[str] = []
        seen: set[str] = set()
        stack = [task_id]
        while stack:
            current = stack.pop()
            for parent in self.spec(current).depends_on:
                if parent in seen:
                    continue
                seen.add(parent)
                ordered.append(parent)
                stack.append(parent)
        return tuple(ordered)

    def resolve_order(self, requested: Sequence[str]) -> tuple[str, ...]:
        """Requested tasks plus their declared upstream, in a runnable order.

        Order comes from the dependency declarations, not from the order the caller
        listed them in — an explicit sequence in a request would be a second, competing
        source of truth for the same graph.
        """
        ordered: list[str] = []
        seen: set[str] = set()
        for task_id in requested:
            for step in (*self.dependencies_for(task_id), task_id):
                if step in seen:
                    continue
                seen.add(step)
                ordered.append(step)
        return tuple(ordered)

    def dependents_of(self, task_id: str) -> set[str]:
        """Transitive downstream — who must NOT run if `task_id` failed."""
        out: set[str] = set()
        changed = True
        while changed:
            changed = False
            for spec in self._specs.values():
                if spec.task_id in out:
                    continue
                if task_id in spec.depends_on or (out & set(spec.depends_on)):
                    out.add(spec.task_id)
                    changed = True
        return out

    def allowed_for(self, task_id: str, trigger: TriggerContext) -> bool:
        return trigger.kind in self.spec(task_id).trigger_modes

    # --- decision-required categories (Phase D tasks 1.4/1.5) --------------- #

    def satisfying_task_ids(self, category: str) -> tuple[str, ...]:
        """Registered decision-required task ids that can satisfy `category`.

        The mapping is the declared "role → task ids" structure: fundamental is
        satisfied by either mode's task, everything else is one-to-one. A task
        counts only when the registry marks it `required_for_decision` — an
        optional task can never fill a required category.
        """
        roles = DECISION_CATEGORY_ROLES.get(category)
        if not roles:
            return ()
        return tuple(task_id for task_id, spec in self._specs.items()
                     if spec.required_for_decision and spec.agent_role in roles)

    def required_categories(self) -> tuple[str, ...]:
        """Decision categories this registry can (partially) satisfy.

        Categories whose roles are not registered at all are omitted — the
        snapshot builder also folds an unmapped required role into its own
        category, so an ad-hoc registry never silently drops a requirement.
        """
        return tuple(category for category in DECISION_CATEGORY_ROLES
                     if self.satisfying_task_ids(category))


# --------------------------------------------------------------------------- #
# Run request
# --------------------------------------------------------------------------- #

class WorkflowRunRequest(BaseModel):
    """Everything one run needs, in one object."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    trigger: TriggerContext
    tasks: tuple[str, ...] = Field(min_length=1)
    scope: ProjectionScope
    as_of: str = Field(min_length=1)
    # Opt-in, never default: an analysis run that quietly continued into proposals and
    # risk review would turn a research pass into a trading decision.
    enter_decision_cycle: bool = False

    @field_validator("tasks", mode="before")
    @classmethod
    def _tasks_as_tuple(cls, value: object) -> object:
        if isinstance(value, str):
            return (value,)
        return tuple(value or ())

    def validate_against(self, registry: TaskRegistry) -> WorkflowRunRequest:
        """Refuse a request that names a task the registry does not declare."""
        self.trigger.validate_for_use()
        unknown = [t for t in self.tasks if not registry.has(t)]
        if unknown:
            raise UnregisteredTaskError(
                f"run {self.run_id!r} requests unregistered tasks: {', '.join(unknown)}")
        for task_id in registry.resolve_order(self.tasks):
            if not registry.allowed_for(task_id, self.trigger):
                raise ContractError(
                    f"task {task_id!r} does not accept {self.trigger.kind!r} triggers")
        return self


# --------------------------------------------------------------------------- #
# Run result
# --------------------------------------------------------------------------- #

class Requirement(BaseModel):
    """One thing the run needed and did not have.

    Named explicitly — `missing`, `failed`, `stale` — because "absent" and "present but
    expired" demand different responses, and collapsing them into a null is how a stale
    analysis gets treated as a completed one.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    kind: Literal["missing", "failed", "stale", "blocked"]
    detail: str = ""


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: TaskStatus
    projection_refs: tuple[str, ...] = ()
    as_of: str = ""
    expires_at: str = ""
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.status == "succeeded"


class WorkflowRunResult(BaseModel):
    """The terminal state of one run, with its gaps spelled out."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    task_results: tuple[TaskResult, ...] = ()
    projection_refs: tuple[str, ...] = ()
    missing_requirements: tuple[Requirement, ...] = ()
    terminal: RunTerminal = "complete"
    decision_cycle_entered: bool = False
    decision_block_reason: str = ""

    @property
    def complete(self) -> bool:
        return self.terminal == "complete"

    def projection_refs_for(self, task_id: str) -> tuple[str, ...]:
        for result in self.task_results:
            if result.task_id == task_id:
                return result.projection_refs
        return ()


def should_enter_decision_cycle(request: WorkflowRunRequest,
                                result: WorkflowRunResult) -> tuple[bool, str]:
    """Whether this run may continue past analysis into proposals and risk review."""
    if not request.enter_decision_cycle:
        return False, "not_requested"
    if not result.complete:
        return False, f"run_incomplete:{result.terminal}"
    return True, "allowed"


def build_run_result(request: WorkflowRunRequest, registry: TaskRegistry,
                     outcomes: Mapping[str, TaskResult]) -> WorkflowRunResult:
    """Turn per-task outcomes into the run's terminal state.

    Two rules do the work:

    * A task whose upstream failed is `blocked`, not `failed` — it never ran, and
      reporting it as a failure would blame the wrong component.
    * A task with no dependency on the failure still completes. Cancelling the whole run
      because one branch broke is the failure mode this contract exists to prevent: the
      other analyses are still good, and the run should say so and stop short of the
      decision cycle rather than pretend nothing happened.
    """
    planned = registry.resolve_order(request.tasks)
    failed = {t for t, outcome in outcomes.items()
              if outcome.status in ("failed", "missing", "stale")}
    blocked: set[str] = set()
    for task_id in failed:
        blocked |= registry.dependents_of(task_id)

    results: list[TaskResult] = []
    requirements: list[Requirement] = []
    for task_id in planned:
        outcome = outcomes.get(task_id)
        if outcome is None:
            results.append(TaskResult(task_id=task_id, status="missing",
                                      detail="no outcome reported"))
            requirements.append(Requirement(task_id=task_id, kind="missing",
                                            detail="no outcome reported"))
            continue
        if task_id in blocked and outcome.status == "succeeded":
            # Should not happen (a blocked task cannot run), but a stale success must
            # not be silently promoted over a broken dependency.
            results.append(TaskResult(task_id=task_id, status="blocked",
                                      detail="upstream dependency failed"))
            requirements.append(Requirement(task_id=task_id, kind="blocked",
                                            detail="upstream dependency failed"))
            continue
        results.append(outcome)
        if outcome.status in ("failed", "missing", "stale"):
            requirements.append(Requirement(task_id=task_id, kind=outcome.status,
                                            detail=outcome.detail))
        elif outcome.status == "blocked":
            requirements.append(Requirement(task_id=task_id, kind="blocked",
                                            detail=outcome.detail))

    terminal: RunTerminal = "incomplete" if requirements else "complete"
    draft = WorkflowRunResult(
        run_id=request.run_id, task_results=tuple(results),
        projection_refs=tuple(ref for r in results for ref in r.projection_refs),
        missing_requirements=tuple(requirements), terminal=terminal)
    allowed, reason = should_enter_decision_cycle(request, draft)
    return draft.model_copy(update={"decision_cycle_entered": allowed,
                                    "decision_block_reason": "" if allowed else reason})


def default_registry() -> TaskRegistry:
    """The analysis tasks the workflow knows about today.

    Kept as data so a new task is a new entry rather than a new branch in a dispatcher.
    """
    return TaskRegistry([
        WorkflowTaskSpec(task_id="macro_review", agent_role="macro_review",
                         output_schema="MacroReview", freshness_seconds=86_400,
                         timeout_seconds=600),
        WorkflowTaskSpec(task_id="sector_allocation", agent_role="sector_allocation",
                         depends_on=("macro_review",), output_schema="SectorAllocation",
                         freshness_seconds=86_400, timeout_seconds=600),
        WorkflowTaskSpec(task_id="layer_analysis", agent_role="layer_analysis",
                         depends_on=("macro_review",), output_schema="LayerAnalysis",
                         freshness_seconds=86_400, timeout_seconds=900),
        WorkflowTaskSpec(task_id="information_brief", agent_role="information_brief",
                         output_schema="InformationBrief", freshness_seconds=3_600,
                         timeout_seconds=300),
        WorkflowTaskSpec(
            task_id="fundamental_expectation_update",
            agent_role="fundamental_expectation_update",
            depends_on=("information_brief",),
            output_schema="FundamentalExpectationUpdate", freshness_seconds=3_600,
            timeout_seconds=600),
        WorkflowTaskSpec(task_id="fundamental_event_review",
                         agent_role="fundamental_event_review",
                         depends_on=("information_brief",),
                         output_schema="FundamentalEventReview",
                         freshness_seconds=3_600, timeout_seconds=600),
        WorkflowTaskSpec(task_id="technical_review", agent_role="technical_review",
                         output_schema="TechnicalReview", freshness_seconds=3_600,
                         timeout_seconds=300, required_for_decision=True),
    ])
