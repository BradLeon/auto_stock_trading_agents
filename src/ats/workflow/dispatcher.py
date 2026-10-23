"""Bounded, restartable execution of Phase E analysis plans.

APScheduler, CLI and event routes are wake-up mechanisms only; this module owns the
dependency graph, task attempts, projection provenance, and the fail-closed Chief gate.
"""

from __future__ import annotations

from ats.workflow.evaluation_clock import now as evaluation_now

import json
import logging
from contextvars import copy_context
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable, Mapping

from ..agent.task_projection import (
    PAYLOAD_SCHEMA_BY_ROLE,
    ProjectionScope,
    TaskProjectionEnvelope,
    content_hash,
)
from ..memory import task_store_scope
from .planning import TASK_ROLE, TaskInstance, WorkflowPlan, task_registry
from .run_contracts import TriggerContext
from .store import WorkflowStore

log = logging.getLogger("ats.workflow.dispatcher")


class AdapterUnavailable(RuntimeError):
    """A role has no usable runtime entrypoint; never fabricate a projection."""


class DependencyUnavailable(RuntimeError):
    """A required input is not admitted or is not a usable projection."""


class ProjectionMissing(RuntimeError):
    """The agent returned without publishing its contracted output."""


class RetryableTaskError(RuntimeError):
    """An adapter-classified transient failure eligible for the task retry policy."""

    def __init__(self, message: str, *, code: str = "transient") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TaskAdapterResult:
    status: str = "succeeded"
    detail: str = ""


@dataclass(frozen=True)
class TaskOutcome:
    instance_key: str
    task_id: str
    scope: ProjectionScope
    status: str
    projection_refs: tuple[str, ...] = ()
    projections: tuple[TaskProjectionEnvelope, ...] = ()
    detail: str = ""
    attempts: int = 0
    reused: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_key": self.instance_key, "task_id": self.task_id,
            "scope": self.scope.model_dump(mode="json"), "status": self.status,
            "projection_refs": list(self.projection_refs), "detail": self.detail,
            "attempts": self.attempts, "reused": self.reused,
        }


@dataclass(frozen=True)
class DispatchResult:
    run_id: str
    status: str
    outcomes: tuple[TaskOutcome, ...]
    missing_requirements: tuple[dict[str, str], ...] = ()
    decision_cycle_ready: bool = False
    decision_cycle_entered: bool = False
    decision_block_reason: str = "not_requested"
    chief_snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.status == "complete"

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "status": self.status,
            "outcomes": [item.as_dict() for item in self.outcomes],
            "missing_requirements": list(self.missing_requirements),
            "decision_cycle_ready": self.decision_cycle_ready,
            "decision_cycle_entered": self.decision_cycle_entered,
            "decision_block_reason": self.decision_block_reason,
            "chief_snapshot": self.chief_snapshot,
        }


@dataclass
class TaskContext:
    plan: WorkflowPlan
    task: TaskInstance
    trigger: TriggerContext
    agent_run_id: str
    store: Any
    dependencies: Mapping[str, TaskProjectionEnvelope]
    input_refs: tuple[str, ...]
    data_vintage_refs: tuple[str, ...]
    task_inputs: dict[str, Any]
    run_once: Callable[[str, Callable[[], Any]], Any]

    @property
    def run_id(self) -> str:
        return self.plan.run_id


TaskAdapter = Callable[[TaskContext], TaskAdapterResult | None]


class _RunOnce:
    """Run one shared information extraction exactly once across entity fan-out."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._values: dict[str, tuple[bool, Any]] = {}

    def __call__(self, key: str, action: Callable[[], Any]) -> Any:
        with self._lock:
            if key not in self._values:
                try:
                    self._values[key] = (True, action())
                except Exception as exc:  # shared preparation failure fans out explicitly
                    self._values[key] = (False, exc)
            ok, value = self._values[key]
            if not ok:
                raise value
            return value


class Dispatcher:
    def __init__(self, *, workflow_store: WorkflowStore | None = None,
                 adapters: Mapping[str, TaskAdapter] | None = None,
                 max_workers: int = 4,
                 resource_limits: Mapping[str, int] | None = None,
                 timeout_overrides: Mapping[str, float] | None = None,
                 chief_runner: Callable[[Any, list[dict]], Any] | None = None,
                 calendar_store: Any = None,
                 vintage_resolver: Callable[[Any, Any, dict], list[str]] | None = None,
                 product_health: Callable[[str], dict | None] | None = None):
        self.workflow_store = workflow_store or WorkflowStore()
        # Optional pre-run data-product check (`consumer_health.gate`): given a task ID, the failed health of
        # the data products its consumer reads, or None. Production entry points inject it; it is consulted
        # only when a task would actually run (not when an earlier projection is reused), at most once per
        # task ID per dispatch.
        self.product_health = product_health
        self.adapters = dict(adapters or default_adapters())
        self.max_workers = max(1, int(max_workers))
        self.resource_limits = {"llm": 2, "information": 2, "pead": 1,
                                "market-data": 2, **dict(resource_limits or {})}
        self.timeout_overrides = dict(timeout_overrides or {})
        # Optional and never inferred from a task input. Phase E never wires broker
        # execution; a deployment may inject its already-authorized Chief runner.
        self.chief_runner = chief_runner
        self.calendar_store = calendar_store
        # Production entries inject `default_data_vintage`; without it a task that brings no data
        # vintage of its own is always recomputed, as before.
        self.vintage_resolver = vintage_resolver

    def dispatch(self, plan: WorkflowPlan, *, trigger_key: str = "",
                 policy_version: str = "phase-e-v1") -> DispatchResult:
        from ..execution.broker_write_guard import startup

        startup(caller="Dispatcher.dispatch", mode=(
            "shadow" if any(task.instance_key.startswith("shadow:")
                            for task in plan.tasks) else ""))
        from .cutover_routing import RouteUnavailable
        from .runtime_reads import check_plan_reads
        from . import schedule_runtime as scheduling

        scheduling.startup()
        for task in plan.tasks:
            scheduling.check_owner(task.task_id, task.scope.model_dump(mode="json"),
                "shadow" if task.instance_key.startswith("shadow:") else "dispatcher")

        from .isolation import verified_isolation_root
        if (verified_isolation_root() is None and
                any(task.instance_key.startswith("dispatcher:") for task in plan.tasks)):
            from .read_windows import bind_plan
            plan = bind_plan(plan)
        read_errors = check_plan_reads(plan, collect=True)
        request = {
            "run_id": plan.run_id, "trigger": plan.trigger,
            "requested_tasks": list(plan.requested_tasks),
            "scope": plan.request_scope.model_dump(mode="json"),
            "as_of": plan.as_of, "task_inputs": plan.task_inputs,
            "enter_decision_cycle": plan.enter_decision_cycle,
        }
        existing = self.workflow_store.create_run(
            run_id=plan.run_id, request=request, plan=plan.as_dict(),
            trigger_key=trigger_key, profile_version=plan.profile_version,
            plan_hash=plan.plan_hash, status="planned")
        if existing["status"] in {"complete", "incomplete", "failed", "cancelled"}:
            if read_errors:
                raise RouteUnavailable(f"saved run scope no longer readable: {read_errors}")
            saved = existing.get("result") or {}
            if saved:
                return self._result_from_saved(saved)
        self.workflow_store.set_run_state(plan.run_id, status="running",
                                          result={}, expected=("planned",))

        registry = task_registry()
        trigger = TriggerContext.model_validate(plan.trigger).validate_for_use()
        run_once = _RunOnce()
        attempts = self.workflow_store.attempts_for_run(plan.run_id)
        latest_by_instance: dict[str, dict] = {}
        for attempt in attempts:
            old = latest_by_instance.get(attempt["task_instance_key"])
            if old is None or attempt["attempt_no"] > old["attempt_no"]:
                latest_by_instance[attempt["task_instance_key"]] = attempt

        outcomes: dict[str, TaskOutcome] = {}
        pending = {task.instance_key: task for task in plan.tasks}
        with task_store_scope(self.workflow_store.path) as memory:
            # Restore stable task identities before scheduling new attempts.
            for key, task in list(pending.items()):
                if key in read_errors:
                    outcomes[key] = self._finish_without_execution(
                        task, plan, status="blocked", detail=read_errors[key],
                        code="read_scope_unavailable", attempt_no=1)
                    pending.pop(key)
                    continue
                previous = latest_by_instance.get(key)
                restored = self._restore_task(memory, task, previous, plan.run_id)
                if restored is not None:
                    outcomes[key] = restored
                    pending.pop(key, None)

            return self._execute(plan, trigger, registry, memory, run_once,
                                 pending, outcomes, latest_by_instance,
                                 trigger_key=trigger_key, policy_version=policy_version)

    def _restore_task(self, memory, task: TaskInstance, previous: dict | None,
                      run_id: str) -> TaskOutcome | None:
        if previous is None:
            return None
        if previous["status"] == "running":
            rows = memory.task_projection_envelopes(
                agent_role=TASK_ROLE[task.task_id], workflow_run_id=run_id,
                scope_kind=task.scope.kind, scope_id=task.scope.id, limit=50)
            recovered = [self._envelope(row) for row in rows
                         if row.get("agent_run_id") == previous["agent_run_id"]]
            if recovered and all(self._valid_output(task, env, run_id=run_id,
                                                    agent_run_id=previous["agent_run_id"])
                                 and sorted(env.input_refs) == sorted(previous["input_refs"])
                                 and sorted(env.data_vintage_refs)
                                 == sorted(previous["data_vintage_refs"])
                                 for env in recovered):
                refs = tuple(env.projection_id for env in recovered)
                self.workflow_store.finish_attempt(previous["agent_run_id"],
                                                   status="succeeded",
                                                   projection_refs=refs)
                return TaskOutcome(task.instance_key, task.task_id, task.scope,
                                   "succeeded", refs, tuple(recovered),
                                   detail="recovered_published_projection", attempts=1)
            self.workflow_store.finish_attempt(
                previous["agent_run_id"], status="stale", error_code="worker_lost",
                error_detail="attempt was running at restart and no complete projection was found")
            return None
        if previous["status"] != "succeeded" or not previous.get("projection_refs"):
            return None
        recovered = []
        for projection_id in previous["projection_refs"]:
            row = memory.get_task_projection(projection_id)
            if row is None:
                return None
            env = self._envelope(row)
            if (not self._valid_output(task, env)
                    or sorted(env.input_refs) != sorted(previous["input_refs"])
                    or sorted(env.data_vintage_refs)
                    != sorted(previous["data_vintage_refs"])):
                return None
            recovered.append(env)
        return TaskOutcome(task.instance_key, task.task_id, task.scope, "succeeded",
                           tuple(env.projection_id for env in recovered), tuple(recovered),
                           detail="recovered_completed_attempt",
                           attempts=int(previous["attempt_no"]))

    def _execute(self, plan, trigger, registry, memory, run_once, pending,
                 outcomes, latest_by_instance, *, trigger_key, policy_version):
        executor = ThreadPoolExecutor(max_workers=self.max_workers,
                                      thread_name_prefix="ats-workflow")
        active: dict[Future, dict[str, Any]] = {}
        resource_active: dict[str, int] = {}
        retry_after: dict[str, float] = {}
        attempt_counts = {key: int(latest_by_instance.get(key, {}).get("attempt_no", 0))
                          for key in pending}
        product_health: dict[str, dict | None] = {}
        try:
            while pending or active:
                made_progress = False
                now_monotonic = time.monotonic()
                for key, task in list(pending.items()):
                    dep_states = [outcomes.get(dep) for dep in task.dependencies]
                    if any(state is None for state in dep_states):
                        continue
                    failed_deps = [state for state in dep_states if state.status != "succeeded"]
                    if failed_deps:
                        outcome = self._finish_without_execution(
                            task, plan, status="blocked", detail="upstream dependency failed",
                            code="dependency_failed", attempt_no=attempt_counts.get(key, 0) + 1)
                        outcomes[key] = outcome
                        pending.pop(key)
                        made_progress = True
                        continue
                    wait_until = retry_after.get(key, 0.0)
                    if wait_until > now_monotonic:
                        continue
                    group = task.resource_group
                    if len(active) >= self.max_workers or resource_active.get(group, 0) >= \
                            self.resource_limits.get(group, self.max_workers):
                        continue

                    dependencies = {dep.instance_key: env
                                    for dep in dep_states
                                    for env in dep.projections}
                    input_refs, data_refs, task_inputs = self._task_inputs(plan, task, dependencies)
                    from .cutover_routing import RouteUnavailable
                    from .runtime_reads import gate_read, task_identity

                    try:
                        gate_read(task_identity(plan, task))
                    except RouteUnavailable as exc:
                        outcomes[key] = self._finish_without_execution(
                            task, plan, status="blocked", detail=str(exc),
                            code="read_scope_unavailable", attempt_no=attempt_counts.get(key, 0) + 1)
                        pending.pop(key)
                        made_progress = True
                        continue
                    reusable, reuse_detail = self._select_reusable(
                        memory, task, input_refs, data_refs, plan.as_of)
                    if reusable is None and self.product_health is not None:
                        if task.task_id not in product_health:
                            product_health[task.task_id] = self.product_health(task.task_id)
                        unhealthy = product_health[task.task_id]
                        if unhealthy:
                            outcomes[key] = self._finish_without_execution(
                                task, plan, status="blocked",
                                detail="data products unhealthy: " + "; ".join(
                                    str(p) for p in unhealthy.get("problems", []))[:500],
                                code="data_product_unhealthy", attempt_no=attempt_counts.get(key, 0) + 1)
                            pending.pop(key)
                            made_progress = True
                            continue
                    attempt_no = attempt_counts.get(key, 0) + 1
                    attempt = self.workflow_store.start_attempt(
                        run_id=plan.run_id, task_instance_key=key, task_id=task.task_id,
                        scope=task.scope.model_dump(mode="json"), attempt_no=attempt_no,
                        input_refs=input_refs, data_vintage_refs=data_refs,
                        reuse_decision=reuse_detail)
                    attempt_counts[key] = attempt_no
                    context = TaskContext(
                        plan=plan, task=task, trigger=trigger,
                        agent_run_id=attempt["agent_run_id"], store=memory,
                        dependencies=dependencies, input_refs=tuple(input_refs),
                        data_vintage_refs=tuple(data_refs), task_inputs=task_inputs,
                        run_once=run_once)
                    context.reusable_projection = reusable
                    adapter = self.adapters.get(task.task_id)
                    if adapter is None:
                        self.workflow_store.finish_attempt(
                            attempt["agent_run_id"], status="missing",
                            error_code="adapter_missing",
                            error_detail=f"no adapter registered for {task.task_id}")
                        outcomes[key] = TaskOutcome(key, task.task_id, task.scope, "missing",
                                                    detail="adapter_missing", attempts=attempt_no)
                        pending.pop(key)
                        made_progress = True
                        continue
                    future = executor.submit(copy_context().run, self._invoke_adapter, adapter, context)
                    timeout = self.timeout_overrides.get(
                        task.task_id, registry.spec(task.task_id).timeout_seconds)
                    active[future] = {"task": task, "attempt": attempt,
                                      "context": context, "started": time.monotonic(),
                                      "timeout": float(timeout), "attempt_no": attempt_no,
                                      "group": group, "timed_out": False}
                    resource_active[group] = resource_active.get(group, 0) + 1
                    pending.pop(key)
                    made_progress = True

                # Deadline handling marks the attempt terminal before any late agent
                # output can publish. TradingMemory enforces this fence at projection
                # write time.
                now_monotonic = time.monotonic()
                for future, state in list(active.items()):
                    timeout = state["timeout"]
                    if (not state["timed_out"] and timeout > 0
                            and now_monotonic - state["started"] > timeout):
                        state["timed_out"] = True
                        future.cancel()
                        future.add_done_callback(lambda done: done.exception()
                                                 if not done.cancelled() else None)
                        self.workflow_store.finish_attempt(
                            state["attempt"]["agent_run_id"], status="cancelled",
                            error_code="timeout", error_detail=f"task timed out after {timeout}s")
                        retry_or_finish(state, outcomes, pending, retry_after,
                                        attempt_counts, status="stale", code="timeout",
                                        detail=f"task timed out after {timeout}s",
                                        trigger_key=trigger_key, policy_version=policy_version)
                        active.pop(future, None)
                        resource_active[state["group"]] = max(
                            0, resource_active.get(state["group"], 1) - 1)
                        made_progress = True

                if active:
                    completed, _ = wait(tuple(active), timeout=0.05,
                                        return_when=FIRST_COMPLETED)
                    for future in completed:
                        state = active[future]
                        active.pop(future)
                        group = state["group"]
                        resource_active[group] = max(0, resource_active.get(group, 1) - 1)
                        if state["timed_out"]:
                            # Quarantined attempt: it has no authority to replace the
                            # timeout result or its retry.
                            continue
                        try:
                            projections, detail = future.result()
                            refs = tuple(env.projection_id for env in projections)
                            self.workflow_store.finish_attempt(
                                state["attempt"]["agent_run_id"], status="succeeded",
                                projection_refs=refs)
                            outcomes[state["task"].instance_key] = TaskOutcome(
                                state["task"].instance_key, state["task"].task_id,
                                state["task"].scope, "succeeded", refs, tuple(projections),
                                detail=detail, attempts=state["attempt_no"],
                                reused=detail in {"projection_reused", "shared_trigger_reused"})
                            made_progress = True
                        except AdapterUnavailable as exc:
                            self.workflow_store.finish_attempt(
                                state["attempt"]["agent_run_id"], status="missing",
                                error_code="adapter_unavailable", error_detail=str(exc))
                            retry_or_finish(state, outcomes, pending, retry_after,
                                            attempt_counts, status="missing",
                                            code="adapter_unavailable", detail=str(exc),
                                            trigger_key=trigger_key,
                                            policy_version=policy_version)
                            made_progress = True
                        except (DependencyUnavailable, RouteUnavailable) as exc:
                            self.workflow_store.finish_attempt(
                                state["attempt"]["agent_run_id"], status="blocked",
                                error_code="dependency_unavailable", error_detail=str(exc))
                            retry_or_finish(state, outcomes, pending, retry_after,
                                            attempt_counts, status="blocked",
                                            code="dependency_unavailable", detail=str(exc),
                                            trigger_key=trigger_key,
                                            policy_version=policy_version)
                            made_progress = True
                        except ProjectionMissing as exc:
                            self.workflow_store.finish_attempt(
                                state["attempt"]["agent_run_id"], status="missing",
                                error_code="projection_missing", error_detail=str(exc))
                            retry_or_finish(state, outcomes, pending, retry_after,
                                            attempt_counts, status="missing",
                                            code="projection_missing", detail=str(exc),
                                            trigger_key=trigger_key,
                                            policy_version=policy_version)
                            made_progress = True
                        except RetryableTaskError as exc:
                            self.workflow_store.finish_attempt(
                                state["attempt"]["agent_run_id"], status="failed",
                                error_code=exc.code, error_detail=str(exc))
                            retry_or_finish(state, outcomes, pending, retry_after,
                                            attempt_counts, status="failed", code=exc.code,
                                            detail=str(exc), trigger_key=trigger_key,
                                            policy_version=policy_version)
                            made_progress = True
                        except Exception as exc:  # noqa: BLE001 - per-task isolation
                            log.exception("workflow task %s failed", state["task"].task_id)
                            self.workflow_store.finish_attempt(
                                state["attempt"]["agent_run_id"], status="failed",
                                error_code="task_exception", error_detail=str(exc))
                            retry_or_finish(state, outcomes, pending, retry_after,
                                            attempt_counts, status="failed",
                                            code="task_exception", detail=str(exc),
                                            trigger_key=trigger_key,
                                            policy_version=policy_version)
                            made_progress = True

                if pending and not active and not made_progress:
                    future_retry = min((when for key, when in retry_after.items()
                                        if key in pending and when > time.monotonic()),
                                       default=None)
                    if future_retry is not None:
                        time.sleep(min(0.05, max(0.0, future_retry - time.monotonic())))
                        continue
                    # Every remaining task is either waiting for a failed/missing
                    # dependency (which should have been terminalized above) or a
                    # corrupt plan. Fail closed instead of spinning forever.
                    for key, task in list(pending.items()):
                        outcomes[key] = self._finish_without_execution(
                            task, plan, status="blocked", detail="plan made no progress",
                            code="scheduler_no_progress", attempt_no=attempt_counts.get(key, 0) + 1)
                        pending.pop(key)
        finally:
            # Do not wait indefinitely for timed-out synchronous Agent code. Its
            # projection writes are fenced by the persisted attempt state.
            executor.shutdown(wait=False, cancel_futures=True)

        missing = tuple({"task_id": item.task_id, "scope": item.scope.key,
                         "kind": "missing" if item.status == "missing" else item.status,
                         "detail": item.detail}
                        for item in outcomes.values() if item.status != "succeeded")
        terminal = "incomplete" if missing or len(outcomes) != len(plan.tasks) else "complete"
        result = DispatchResult(plan.run_id, terminal,
                                tuple(outcomes[task.instance_key] for task in plan.tasks),
                                missing_requirements=missing,
                                decision_block_reason=("not_requested" if not plan.enter_decision_cycle
                                                      else "run_incomplete" if terminal != "complete"
                                                      else "snapshot_not_built"))
        if plan.enter_decision_cycle and terminal == "complete":
            result = self._chief_snapshot_gate(result, plan, memory)
        self.workflow_store.set_run_state(
            plan.run_id, status=result.status, result=result.as_dict(), expected=("running",))
        return result

    def _finish_without_execution(self, task, plan, *, status, detail, code, attempt_no):
        attempt = self.workflow_store.start_attempt(
            run_id=plan.run_id, task_instance_key=task.instance_key, task_id=task.task_id,
            scope=task.scope.model_dump(mode="json"), attempt_no=attempt_no,
            reuse_decision={"decision": "not_run", "reason": code})
        self.workflow_store.finish_attempt(attempt["agent_run_id"], status=status,
                                           error_code=code, error_detail=detail)
        return TaskOutcome(task.instance_key, task.task_id, task.scope, status,
                           detail=detail, attempts=attempt_no)

    def _task_inputs(self, plan, task, dependencies):
        root = plan.task_inputs
        task_inputs = dict(root.get(task.task_id, {})) if isinstance(root.get(task.task_id), dict) else {}
        by_scope = root.get("by_scope", {})
        scoped = by_scope.get(task.scope.key, {}) if isinstance(by_scope, dict) else {}
        if isinstance(scoped, dict):
            task_inputs.update(scoped.get(task.task_id, scoped))
        for key in ("event", "event_id", "event_version", "event_type", "event_state",
                    "fiscal_label", "cutoff", "trigger", "fundamental_trigger",
                    "admitted_material_refs", "material_state"):
            if key in root and key not in task_inputs:
                task_inputs[key] = root[key]
        input_refs = list(task_inputs.get("input_refs", root.get("input_refs", [])) or [])
        input_refs.extend(f"projection:{env.projection_id}:{env.content_hash}"
                          for env in dependencies.values())
        input_refs.extend(env.projection_id for env in dependencies.values())
        input_refs.extend(str(ref) for ref in task_inputs.get("admitted_material_refs", []) or [])
        input_refs.extend(f"config:{path}@{digest}"
                          for path, digest in sorted(plan.source_config_hashes.items()))
        data_refs = task_inputs.get("data_vintage_refs", root.get("data_vintage_refs", [])) or []
        resolver = getattr(self, "vintage_resolver", None)   # some callers invoke this unbound
        if not data_refs and resolver is not None:
            try:
                data_refs = list(resolver(plan, task, task_inputs) or [])
            except Exception as exc:  # noqa: BLE001 - an unknown vintage only means "recompute"
                log.warning("data vintage for %s unavailable: %s", task.instance_key, exc)
                data_refs = []
        return sorted(set(map(str, input_refs))), sorted(set(map(str, data_refs))), task_inputs

    def _select_reusable(self, memory, task, input_refs, data_refs, as_of):
        spec = task_registry().spec(task.task_id)
        role = TASK_ROLE[task.task_id]
        schema = PAYLOAD_SCHEMA_BY_ROLE[role]
        if spec.freshness_seconds <= 0:
            return None, {"decision": "recompute", "reason": "reuse_disabled"}
        if not data_refs:
            return None, {"decision": "recompute", "reason": "missing_data_vintage"}
        rows = memory.task_projection_envelopes(
            agent_role=role, scope_kind=task.scope.kind, scope_id=task.scope.id,
            status="published", limit=500 if task.task_id == "fundamental-routine" else 50)
        by_id = {row["projection_id"]: row for row in rows}
        candidates = []
        for row in rows:
            envelope = self._envelope(row)
            if not envelope.agent_run_id:
                reusable, reason = False, "agent_attempt_missing"
            else:
                attempt = memory.conn.execute(
                    "SELECT status,projection_refs_json FROM agent_runs WHERE agent_run_id=?",
                    (envelope.agent_run_id,)).fetchone()
                if attempt is None or attempt["status"] != "succeeded":
                    reusable, reason = False, "agent_attempt_not_terminal"
                elif task.task_id == "fundamental-routine" and json.loads(attempt["projection_refs_json"]) != [envelope.projection_id]:
                    # A Routine run publishes one projection per fact change. Reuse is all-or-nothing: every
                    # member of the producer's bundle must still validate, and the whole bundle is returned,
                    # so a complete per-fact bundle is never turned into a partial success.
                    refs = json.loads(attempt["projection_refs_json"])
                    members = [self._envelope(by_id[ref]) if ref in by_id else None for ref in refs]
                    ok = bool(refs) and all(
                        m is not None and m.agent_run_id == envelope.agent_run_id and self._projection_reuse_check(
                            m, task, input_refs, data_refs, as_of, spec.freshness_seconds,
                            schema.role_schema_name())[0] for m in members)
                    if ok:
                        return tuple(members), {"decision": "hit", "reason": "reusable_bundle",
                                                "projection_ids": refs, "candidates": candidates}
                    reusable, reason = False, "multi_projection_recompute_required"
                else:
                    reusable, reason = self._projection_reuse_check(
                        envelope, task, input_refs, data_refs, as_of,
                        spec.freshness_seconds, schema.role_schema_name())
            candidates.append({"projection_id": envelope.projection_id, "reason": reason})
            if reusable:
                return envelope, {"decision": "hit", "reason": "reusable",
                                  "projection_id": envelope.projection_id,
                                  "content_hash": envelope.content_hash,
                                  "candidates": candidates}
        reason = candidates[0]["reason"] if candidates else "no_candidate"
        return None, {"decision": "recompute", "reason": reason,
                      "candidates": candidates}

    @staticmethod
    def _projection_reuse_check(envelope, task, input_refs, data_refs, as_of,
                                freshness_seconds, schema_name):
        from ..agent.task_projection import reuse_decision

        if task.task_id == "fundamental-routine":
            from .decision_requirements import validate_routine_value_contract
            from .cutover_routing import RouteUnavailable
            try:
                validate_routine_value_contract(envelope.payload)
            except RouteUnavailable:
                return False, "routine_value_contract_invalid"
        if envelope.agent_role != TASK_ROLE[task.task_id]:
            return False, "role_mismatch"
        if envelope.scope.key != task.scope.key:
            return False, "scope_mismatch"
        if envelope.schema_name != schema_name or envelope.schema_version != "v1":
            return False, "schema_mismatch"
        if envelope.status != "published":
            return False, "not_published"
        if not Dispatcher._valid_output(task, envelope):
            return False, "content_hash_mismatch"
        # Every ref this plan requires must be in the envelope. An adapter adds its own document refs on
        # top of the plan-level ones when it publishes, so strict equality could never hit for Information
        # or Fundamental (ARCHITECTURE §8.2-5 reuses on unchanged scope, schema and data vintage). A projection
        # from a different lineage still lacks the required refs and is refused.
        if set(input_refs) - set(envelope.input_refs):
            return False, "input_refs_changed"
        if sorted(envelope.data_vintage_refs) != sorted(data_refs):
            return False, "data_vintage_changed"
        try:
            projection_as_of = datetime.fromisoformat(envelope.as_of.replace("Z", "+00:00"))
            requested_as_of = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            created = datetime.fromisoformat((envelope.created_at or envelope.as_of)
                                             .replace("Z", "+00:00"))
            if projection_as_of > requested_as_of:
                return False, "projection_from_future"
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if evaluation_now(timezone.utc) - created > timedelta(seconds=freshness_seconds):
                return False, "expired"
        except ValueError:
            return False, "invalid_timestamp"
        reusable, reason = reuse_decision(
            envelope, scope=task.scope, input_refs=input_refs,
            data_vintage_refs=data_refs, schema_name=schema_name,
            schema_version="v1")
        return reusable, reason

    def _invoke_adapter(self, adapter, context):
        from .runtime_reads import bind_read, task_identity

        mode = "shadow" if context.task.instance_key.startswith("shadow:") else None
        with bind_read(task_identity(context.plan, context.task), as_of=context.plan.as_of, mode=mode,
                publication_path=str(self.workflow_store.path),
                invocation=(context.plan.task_inputs.get("read_invocations") or {}).get(context.task.instance_key),
                execution_plan_hash=context.plan.plan_hash):
            return self._invoke_bound_adapter(adapter, context)

    @staticmethod
    def _record_reused(context, envelopes):
        from .consumer_reads import record_read
        from .runtime_reads import current_read_context, gate_read
        from .shadow_replay import digest

        bound = current_read_context()
        if bound is not None and bound.invocation:
            gate_read(bound.identity)
            record_read(bound.identity.consumer_id, "Dispatcher.projection_reuse",
                refs=[e.projection_id for e in envelopes],
                source_as_of=[e.as_of for e in envelopes],
                queried_at=evaluation_now(timezone.utc).isoformat(),
                payload_hash=digest([e.model_dump(mode="json") for e in envelopes]))

    def _invoke_bound_adapter(self, adapter, context):
        from . import schedule_runtime as scheduling

        owner = "shadow" if context.task.instance_key.startswith("shadow:") else "dispatcher"
        result = scheduling.execute(context.task.task_id,
            context.task.scope.model_dump(mode="json"), context.trigger,
            lambda: self._claimed_or_reused(adapter, context), owner=owner,
            actor=context.agent_run_id)
        if isinstance(result, dict) and result.get("schedule_skipped"):
            if result["status"] != "complete":
                raise DependencyUnavailable("logical trigger held, incomplete or void; no automatic rerun")
            with task_store_scope(self.workflow_store.path) as store:
                rows = store.task_projection_envelopes(agent_role=TASK_ROLE[context.task.task_id],
                    scope_kind=context.task.scope.kind, scope_id=context.task.scope.id, limit=100)
                envelopes = [self._envelope(row) for row in rows if row["projection_id"] in result["result_refs"]]
                if not envelopes:
                    raise ProjectionMissing("shared completed trigger has no readable projection refs")
                valid = [env for env in envelopes if self._projection_reuse_check(env, context.task, context.input_refs, context.data_vintage_refs, context.plan.as_of, task_registry().spec(context.task.task_id).freshness_seconds, PAYLOAD_SCHEMA_BY_ROLE[TASK_ROLE[context.task.task_id]].role_schema_name())[0]]
                if not valid:
                    raise ProjectionMissing("shared trigger projections fail current reuse validation")
                self._record_reused(context, valid)
                return tuple(valid), "shared_trigger_reused"
        return result

    def _claimed_or_reused(self, adapter, context):
        from .schedule_runtime import record_reuse

        reusable = getattr(context, "reusable_projection", None)
        if reusable is not None:
            envelopes = tuple(reusable) if isinstance(reusable, (tuple, list)) else (reusable,)
            self._record_reused(context, envelopes)
            record_reuse([envelope.projection_id for envelope in envelopes])
            return envelopes, "projection_reused"
        return self._invoke_claimed_adapter(adapter, context)

    def _invoke_claimed_adapter(self, adapter, context):
        from ..agent.task_projection import projection_context
        from .runtime_reads import bind_read, task_identity

        mode = "shadow" if context.task.instance_key.startswith("shadow:") else None
        with bind_read(task_identity(context.plan, context.task), as_of=context.plan.as_of, mode=mode,
                       publication_path=str(self.workflow_store.path),
                       invocation=(context.plan.task_inputs.get("read_invocations") or {}).get(context.task.instance_key),
                       execution_plan_hash=context.plan.plan_hash), \
                task_store_scope(self.workflow_store.path) as store:
            context.store = store
            with projection_context(
                    workflow_run_id=context.run_id, agent_run_id=context.agent_run_id,
                    input_refs=context.input_refs,
                    data_vintage_refs=context.data_vintage_refs, as_of=context.plan.as_of):
                adapter_result = adapter(context) or TaskAdapterResult()
            if adapter_result.status != "succeeded":
                if adapter_result.status == "missing":
                    raise AdapterUnavailable(adapter_result.detail or "role output missing")
                if adapter_result.status == "blocked":
                    raise DependencyUnavailable(adapter_result.detail or "dependency unavailable")
                raise RuntimeError(adapter_result.detail or adapter_result.status)
            rows = store.task_projection_envelopes(
                agent_role=TASK_ROLE[context.task.task_id],
                scope_kind=context.task.scope.kind, scope_id=context.task.scope.id,
                workflow_run_id=context.run_id, status="published", limit=100)
            envelopes = [self._envelope(row) for row in rows
                         if row.get("agent_run_id") == context.agent_run_id]
            envelopes = [env for env in envelopes
                         if self._valid_output(context.task, env, run_id=context.run_id,
                                               agent_run_id=context.agent_run_id)]
            if not envelopes:
                raise ProjectionMissing(
                    f"{context.task.task_id} returned without publishing a valid "
                    f"{context.task.output_schema} projection for {context.task.scope.key}")
            return tuple(envelopes), adapter_result.detail

    @staticmethod
    def _envelope(row):
        return TaskProjectionEnvelope(
            projection_id=row["projection_id"], workflow_run_id=row.get("workflow_run_id") or "",
            agent_run_id=row.get("agent_run_id") or "", agent_role=row["agent_role"],
            scope=ProjectionScope(kind=row["scope_kind"], id=row.get("scope_id") or ""),
            as_of=row["as_of"], valid_until=row.get("valid_until") or "",
            schema_name=row.get("schema_name") or "", schema_version=row.get("schema_version") or "v1",
            input_refs=row.get("input_refs") or [], data_vintage_refs=row.get("data_vintage_refs") or [],
            model_version=row.get("model_version") or "", prompt_version=row.get("prompt_version") or "",
            payload=row.get("payload") or {}, content_hash=row.get("content_hash") or "",
            status=row.get("status") or "published", created_at=row.get("created_at") or "",
            supersedes_projection_id=row.get("supersedes_projection_id") or "")

    @staticmethod
    def _valid_output(task, envelope, *, run_id="", agent_run_id=""):
        from ..agent.task_projection import validate_payload

        expected_role = TASK_ROLE[task.task_id]
        expected_schema = PAYLOAD_SCHEMA_BY_ROLE[expected_role]
        if (envelope.agent_role != expected_role or envelope.scope.key != task.scope.key
                or envelope.schema_name != expected_schema.role_schema_name()
                or envelope.schema_version != "v1" or envelope.status != "published"):
            return False
        if run_id and envelope.workflow_run_id != run_id:
            return False
        if agent_run_id and envelope.agent_run_id != agent_run_id:
            return False
        try:
            validated_payload = validate_payload(envelope.agent_role, envelope.payload)
            expected_hash = content_hash(
                role=envelope.agent_role, scope=envelope.scope, as_of=envelope.as_of,
                schema_name=envelope.schema_name, schema_version=envelope.schema_version,
                payload=validated_payload, input_refs=envelope.input_refs,
                data_vintage_refs=envelope.data_vintage_refs)
        except Exception:
            return False
        return expected_hash == envelope.content_hash

    def _chief_snapshot_gate(self, result: DispatchResult, plan, memory) -> DispatchResult:
        from .cutover_routing import RouteUnavailable
        from .runtime_reads import bind_read, business_identity, configured_entities

        identity = business_identity(
            "chief", kind="portfolio", scope_id="portfolio",
            entities=configured_entities("portfolio", "portfolio", config_dir=plan.config_root or None),
            as_of=plan.as_of, explicit=(plan.task_inputs.get("read_scopes", {}) or {}).get("chief:portfolio"))
        mode = "shadow" if any(t.instance_key.startswith("shadow:") for t in plan.tasks) else None
        try:
            with bind_read(identity, as_of=plan.as_of, mode=mode, publication_path=self.workflow_store.path,
                           invocation=(plan.task_inputs.get("read_invocations") or {}).get("chief:portfolio"),
                           execution_plan_hash=plan.plan_hash):
                return self._assemble_chief_snapshot(result, plan, memory)
        except RouteUnavailable as exc:
            return DispatchResult(
                run_id=result.run_id, status=result.status, outcomes=result.outcomes,
                missing_requirements=result.missing_requirements,
                decision_cycle_ready=False, decision_cycle_entered=False,
                decision_block_reason=f"chief_read_scope_unavailable:{exc}")

    def _assemble_chief_snapshot(self, result: DispatchResult, plan, memory) -> DispatchResult:
        trigger = TriggerContext.model_validate(plan.trigger)
        if trigger.kind == "event":
            # Event research may finish after its source calendar entry was revised.
            # Preserve that analysis, but do not let its stale version enter Chief.
            from ..data.stores.schedule_calendar import ScheduleCalendarStore

            calendar = self.calendar_store or ScheduleCalendarStore()
            current = next((item for item in calendar.latest_events(limit=10_000)
                            if item["event_id"] == trigger.event_id), None)
            current_version = str(current["event_version"]) if current else ""
            expected_version = str(trigger.event_version)
            if current is None or current_version != expected_version:
                return DispatchResult(
                    run_id=result.run_id, status=result.status, outcomes=result.outcomes,
                    missing_requirements=result.missing_requirements,
                    decision_cycle_ready=False, decision_cycle_entered=False,
                    decision_block_reason="stale_event_version",
                    chief_snapshot={"event_guard": {
                        "status": "blocked", "event_id": trigger.event_id,
                        "expected_version": expected_version,
                        "current_version": current_version,
                    }})
        manifest = [
            {"role": env.agent_role, "scope_kind": env.scope.kind,
             "scope_id": env.scope.id, "projection_id": env.projection_id,
             "content_hash": env.content_hash}
            for outcome in result.outcomes for env in outcome.projections]
        from ..agents.chief.assemble import build_chief_snapshot
        from . import decision_requirements as dr

        snapshot, detail = build_chief_snapshot(
            memory, at=datetime.fromisoformat(plan.as_of), projection_manifest=manifest,
            query_plan=dr.query_plan(plan))
        dr.attach(snapshot, dr.freeze(plan, outcomes=[o.as_dict() for o in result.outcomes],
            manifest=manifest, workflow_store_path=self.workflow_store.path), detail)
        block_reason = "complete_snapshot" if snapshot.complete else "incomplete_research_snapshot"
        ready = snapshot.complete
        if ready:
            dr.validate(memory, snapshot.to_payload(), at=datetime.fromisoformat(plan.as_of))
        entered, chief_payload = False, {}
        if ready and self.chief_runner is not None:
            try:
                chief_payload = self.chief_runner(snapshot, manifest) or {}
                entered = True
            except Exception as exc:  # noqa: BLE001 - the run remains auditable
                block_reason = f"chief_runner_failed:{type(exc).__name__}:{exc}"
        elif ready:
            block_reason = "chief_runner_not_configured"
        snapshot_payload = {
            "scope": snapshot.scope.key, "built_at": snapshot.built_at,
            "complete": snapshot.complete,
            "items": [vars(item) for item in snapshot.items],
            "detail": detail, "manifest": manifest, "chief_result": chief_payload,
            "requirements": snapshot.requirements,
        }
        snapshot_gaps = tuple(
            {"task_id": item.task_id, "scope": f"{item.scope_kind}:{item.scope_id}".rstrip(":"),
             "kind": "missing" if item.reason == "missing" else "stale",
             "detail": item.reason}
            for item in snapshot.gaps())
        missing = result.missing_requirements + snapshot_gaps
        return DispatchResult(
            run_id=result.run_id,
            status=("incomplete" if snapshot_gaps else result.status),
            outcomes=result.outcomes, missing_requirements=missing,
            decision_cycle_ready=ready, decision_cycle_entered=entered,
            decision_block_reason=block_reason, chief_snapshot=snapshot_payload)

    @staticmethod
    def _result_from_saved(saved: dict) -> DispatchResult:
        outcomes = []
        for item in saved.get("outcomes", []):
            outcomes.append(TaskOutcome(
                instance_key=item["instance_key"], task_id=item["task_id"],
                scope=ProjectionScope(**item["scope"]), status=item["status"],
                projection_refs=tuple(item.get("projection_refs", [])),
                detail=item.get("detail", ""), attempts=item.get("attempts", 0),
                reused=item.get("reused", False)))
        return DispatchResult(
            run_id=saved["run_id"], status=saved["status"], outcomes=tuple(outcomes),
            missing_requirements=tuple(saved.get("missing_requirements", [])),
            decision_cycle_ready=saved.get("decision_cycle_ready", False),
            decision_cycle_entered=saved.get("decision_cycle_entered", False),
            decision_block_reason=saved.get("decision_block_reason", ""),
            chief_snapshot=saved.get("chief_snapshot", {}))


def retry_or_finish(state, outcomes, pending, retry_after, attempt_counts, *, status,
                    code, detail, trigger_key, policy_version):
    task = state["task"]
    policy = task_registry().spec(task.task_id).retry
    may_retry = code in policy.retry_on and attempt_counts[task.instance_key] < policy.max_attempts
    if may_retry:
        retry_after[task.instance_key] = time.monotonic() + policy.backoff_seconds
        pending[task.instance_key] = task
        return
    outcomes[task.instance_key] = TaskOutcome(
        task.instance_key, task.task_id, task.scope, status, detail=detail,
        attempts=attempt_counts[task.instance_key])


def default_adapters() -> dict[str, TaskAdapter]:
    return {
        "layer-review": _layer_adapter,
        "information-brief": _information_adapter,
        "sector-review": _sector_adapter,
        "fundamental-routine": _fundamental_routine_adapter,
        "fundamental-event": _fundamental_event_adapter,
        "macro-review": _macro_adapter,
        "technical-review": _technical_adapter,
    }


def _use_llm(context: TaskContext) -> bool:
    return bool(context.task_inputs.get("use_llm", True))


def _layer_adapter(context: TaskContext) -> TaskAdapterResult:
    from ..agents.layer import layer_review
    from ..config import load_sector_config

    sector = (context.plan.request_scope.id if context.plan.request_scope.kind == "sector"
              else str(context.task_inputs.get("sector", "")))
    if not sector:
        raise AdapterUnavailable("a layer-scoped request must name its sector in task_inputs.sector")
    cfg = load_sector_config(sector)
    layer = next((item for item in cfg.layers if item.key == context.task.scope.id), None)
    if layer is None:
        raise AdapterUnavailable(f"layer {context.task.scope.id!r} is not in sector {sector!r}")
    _, ok = layer_review.run(cfg, layer, use_llm=_use_llm(context), store=context.store,
                             workflow_run_id=context.run_id, input_refs=context.input_refs,
                             data_vintage_refs=context.data_vintage_refs)
    if not ok:
        raise ProjectionMissing(f"Layer Analyst produced no projection for {layer.key}")
    return TaskAdapterResult(detail=f"layer={layer.key}")


def _information_adapter(context: TaskContext) -> TaskAdapterResult:
    from ..agents.information.entry import run_information_target

    result = run_information_target(
        context.task.scope.id, store=context.store, use_llm=_use_llm(context),
        run_extraction=True, run_once=context.run_once)
    return TaskAdapterResult(detail=result.get("event_summary", ""))


def _sector_adapter(context: TaskContext) -> TaskAdapterResult:
    from ..agents.sector.review import run

    layer_inputs = {
        env.scope.id: env for env in context.dependencies.values()
        if env.agent_role == "layer_analysis"
    }
    if not layer_inputs:
        raise DependencyUnavailable("sector-review received no declared LayerAnalysis inputs")
    review = run(
        context.task.scope.id, use_llm=_use_llm(context), live_data=True,
        layers=True, write_reports=True, upstream_layer_projections=layer_inputs)
    if not review.layer_verdicts:
        raise ProjectionMissing("Sector Analyst produced no allocation projection")
    # The sector report, the per-layer reports and the html dashboard are written by `run` itself, from the
    # data only that run has (a report failure is logged there and never fails the task).
    return TaskAdapterResult(detail=f"sector={review.sector}")


def _information_dependency_refs(context: TaskContext) -> list[str]:
    return [f"projection:{env.projection_id}:{env.content_hash}"
            for env in context.dependencies.values()
            if env.agent_role == "information_brief"]


def _fundamental_routine_adapter(context: TaskContext) -> TaskAdapterResult:
    from ..agents.fundamental.entry import routine_request, run_fundamental_pass

    symbol = context.task.scope.id
    trigger = str(context.task_inputs.get("fundamental_trigger", "information_brief_update"))
    # A baseline rebuild on demand is an operator action: only a manual trigger may ask for it.
    refresh = bool(context.task_inputs.get("refresh_baseline")) and getattr(context.trigger, "kind", "") == "manual"
    request = routine_request(symbol, trigger=trigger, use_llm=_use_llm(context),
                              extra={"input_refs": _information_dependency_refs(context),
                                     "data_vintage_refs": list(context.data_vintage_refs),
                                     "refresh_baseline": refresh})
    result = run_fundamental_pass(request)
    return TaskAdapterResult(detail=str(result.get("summary", "routine completed")))


def _fundamental_event_adapter(context: TaskContext) -> TaskAdapterResult:
    from ..agents.fundamental.entry import event_request, run_fundamental_pass

    inputs = context.task_inputs
    material_refs = list(inputs.get("admitted_material_refs", []))
    if inputs.get("material_state") != "admitted" or not material_refs:
        raise DependencyUnavailable(
            "Fundamental event review requires an admitted earnings release/call/guidance material")
    fiscal_label = str(inputs.get("fiscal_label", ""))
    if not fiscal_label:
        raise DependencyUnavailable("Fundamental event review requires fiscal_label")
    trigger = str(inputs.get("fundamental_trigger", "earnings_release"))
    request = event_request(
        context.task.scope.id, trigger=trigger, fiscal_label=fiscal_label,
        cutoff=str(inputs.get("cutoff", "")), use_llm=_use_llm(context),
        extra={"input_refs": [*_information_dependency_refs(context), *material_refs],
               "data_vintage_refs": list(context.data_vintage_refs)})
    result = run_fundamental_pass(request)
    return TaskAdapterResult(detail=str(result.get("summary", "event review completed")))


def _macro_adapter(context: TaskContext) -> TaskAdapterResult:
    from ..agents.macro.review import run

    from datetime import datetime, timezone

    started = datetime.now(timezone.utc)
    use_llm = _use_llm(context)
    review = run(name="macro", use_llm=use_llm, live_data=True)
    if not review.regime or review.regime.startswith("("):
        raise ProjectionMissing("Macro Analyst did not publish a formal regime projection")
    detail = f"regime={review.regime}"
    # The readable weekly report: the CLI path always wrote it, this path did not (found 2026-10-17). A new
    # version file, never an overwrite. `run` returns the PRIOR review when the model call fails, so a stale
    # review must not be written out under today's name.
    if use_llm and review.sector_tilts and review.as_of >= started:
        from ..agents.macro import report
        from ..config import load_macro_config

        path = report.write_new_version(review, load_macro_config("macro"))
        if path:
            detail += f"; report={path.name}"
    return TaskAdapterResult(detail=detail)


def _technical_adapter(context: TaskContext) -> TaskAdapterResult:
    from ..agents.technical.review import run

    review = run(name="technical", live_data=True, persist=True, write_report=False,
                 symbols=[context.task.scope.id])
    if not review.readings:
        raise ProjectionMissing(f"Technical Analyst produced no reading for {context.task.scope.id}")
    return TaskAdapterResult(detail=f"symbol={context.task.scope.id}")


__all__ = ["AdapterUnavailable", "DependencyUnavailable", "DispatchResult", "Dispatcher",
           "ProjectionMissing", "RetryableTaskError", "TaskAdapterResult", "TaskContext",
           "TaskOutcome", "default_adapters"]


def _digest(values) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(sorted(values), ensure_ascii=False).encode()).hexdigest()[:16]


def default_data_vintage(plan: WorkflowPlan, task: TaskInstance, task_inputs: dict) -> list[str]:
    """Data-vintage references of a task, plus a unique one when the request forces a recompute.

    `task_inputs["force_recompute"]` is the manual "run it again on unchanged data" switch (for example after the
    projection contract gained fields, which does not change any data version): it makes the references unique to
    this run, so no earlier projection is reusable, and leaves every other task alone.
    """
    refs = _data_vintage_refs(plan, task, task_inputs)
    if task_inputs.get("force_recompute") and refs is not None:
        refs = [*refs, f"forced-recompute:{plan.run_id}"]
    return refs


def _data_vintage_refs(plan: WorkflowPlan, task: TaskInstance, task_inputs: dict) -> list[str]:
    """The shared-data versions a research task reads (ARCHITECTURE §8.2-5), so unchanged data is reused.

    Information: the admitted document versions in its lookback for the entity. Routine Fundamental:
    the financial snapshot, the consensus snapshot time and the pre-earnings window state (a window that
    opens must rebuild the baseline even when nothing else moved). Macro: the stored indicator series
    (newest known_at each), the admitted theme news versions, the regional and FactSet releases. Other
    tasks bring none and recompute.
    """
    if task.task_id in {"layer-review", "sector-review"}:
        return _layer_sector_vintage(plan, task, task_inputs)
    if task.task_id == "macro-review":
        from ..data.products import get_platform_data_products, macro_inputs

        products = get_platform_data_products(readonly=True)
        try:
            as_of = datetime.fromisoformat(str(plan.as_of).replace("Z", "+00:00"))
            state = macro_inputs.stored_input_state(products, as_of=as_of)
        finally:
            products.structured.close()
        # A review is reusable only if every input that decides its content was readable: a failed
        # series or news read would otherwise look like "unchanged" and pin an incomplete review.
        if state["series"]["error"] or state["news"]["error"]:
            return []
        return macro_inputs.input_vintage_refs(state)
    if task.scope.kind != "entity" or task.task_id not in {"information-brief", "fundamental-routine",
                                                             "technical-review"}:
        return []
    symbol = task.scope.id.upper()
    as_of = datetime.fromisoformat(str(plan.as_of).replace("Z", "+00:00"))
    if task.task_id == "technical-review":
        # Daily bars: the reading can only change with a new session close, so it is reused within the
        # same New York trading date and the same side of the 16:00 close (no network call needed here).
        from zoneinfo import ZoneInfo

        local = as_of.astimezone(ZoneInfo("America/New_York"))
        side = "after-close" if (local.hour, local.minute) >= (16, 0) else "before-close"
        return [f"market-session:{symbol}:{local.date().isoformat()}:{side}"]
    from ..data.products import get_platform_data_products

    products = get_platform_data_products(readonly=True)
    try:
        if task.task_id == "information-brief":
            from ..config import load_pead_global
            from ..data.products.unstructured import admitted_documents

            days = int((load_pead_global().get("monitor") or {}).get("lookback_days", 7))
            rows = admitted_documents(entities=[symbol], as_of=as_of, repository=products.unstructured,
                                      published_since=(as_of - timedelta(days=days)).isoformat(), limit=500)
            refs = [f"{row.document_id}@{row.version_id}" for row in rows]
            return [f"documents:{symbol}:{len(refs)}@{_digest(refs)}"] if refs else []
        snapshot = products.company_financial_snapshot(entity=symbol, as_of=as_of)
        observations = [str(row.get("observation_id")) for row in snapshot.get("rows") or []]
        consensus = products.consensus_snapshot(entity=symbol, as_of=as_of)
    finally:
        products.structured.close()
    from ..agents.fundamental.routine import baseline_window

    window = baseline_window(symbol, now=as_of)
    refs = [f"financials:{symbol}:{len(observations)}@{_digest(observations)}",
            f"consensus:{symbol}@{consensus.get('known_at') or 'none'}",
            f"baseline-window:{symbol}:{window.get('label') or 'none'}:{'open' if window.get('open') else 'closed'}"]
    if task_inputs.get("refresh_baseline"):
        refs.append(f"baseline-refresh:{plan.run_id}")
    return refs


def _layer_sector_vintage(plan: WorkflowPlan, task: TaskInstance, task_inputs: dict) -> list[str]:
    """Data-vintage references of Layer and Sector reviews (10.5, 2026-10-17); local reads, no network.

    Layer: the neutral-evidence observations its claims read and the knowledge-note versions. Sector: the
    governed financials, consensus snapshots and FactSet packet of its cross-section, the last NYSE session
    side (runtime prices), and the layer-budget switch; the eight LayerAnalysis projections it consumes are
    already plan-level input refs, so a rebuilt layer changes the Sector reference without being listed here.
    Anything unreadable raises and the caller treats it as "recompute".
    """
    from . import layer_sector_inputs as inputs

    sector = (plan.request_scope.id if plan.request_scope.kind == "sector"
              else str(task_inputs.get("sector", "")))
    if not sector:
        return []
    if task.task_id == "layer-review":
        return inputs.layer_vintage(sector, task.scope.id)
    return inputs.sector_vintage(sector, datetime.fromisoformat(str(plan.as_of).replace("Z", "+00:00")))
