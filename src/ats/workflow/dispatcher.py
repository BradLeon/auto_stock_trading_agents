"""Bounded, restartable execution of Phase E analysis plans.

APScheduler, CLI and event routes are wake-up mechanisms only; this module owns the
dependency graph, task attempts, projection provenance, and the fail-closed Chief gate.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from threading import Lock
import time
from typing import Any, Callable, Mapping

from ..agent.task_projection import (PAYLOAD_SCHEMA_BY_ROLE, ProjectionScope,
                                     TaskProjectionEnvelope, content_hash)
from ..memory import task_store_scope
from .phase_e import TASK_ROLE, TaskInstance, WorkflowPlan, phase_e_registry
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
                 calendar_store: Any = None):
        self.workflow_store = workflow_store or WorkflowStore()
        self.adapters = dict(adapters or default_adapters())
        self.max_workers = max(1, int(max_workers))
        self.resource_limits = {"llm": 2, "information": 2, "pead": 1,
                                "market-data": 2, **dict(resource_limits or {})}
        self.timeout_overrides = dict(timeout_overrides or {})
        # Optional and never inferred from a task input. Phase E never wires broker
        # execution; a deployment may inject its already-authorized Chief runner.
        self.chief_runner = chief_runner
        self.calendar_store = calendar_store

    def dispatch(self, plan: WorkflowPlan, *, trigger_key: str = "",
                 policy_version: str = "phase-e-v1") -> DispatchResult:
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
            saved = existing.get("result") or {}
            if saved:
                return self._result_from_saved(saved)
        self.workflow_store.set_run_state(plan.run_id, status="running",
                                          result={}, expected=("planned",))

        registry = phase_e_registry()
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
                    reusable, reuse_detail = self._select_reusable(
                        memory, task, input_refs, data_refs, plan.as_of)
                    attempt_no = attempt_counts.get(key, 0) + 1
                    attempt = self.workflow_store.start_attempt(
                        run_id=plan.run_id, task_instance_key=key, task_id=task.task_id,
                        scope=task.scope.model_dump(mode="json"), attempt_no=attempt_no,
                        input_refs=input_refs, data_vintage_refs=data_refs,
                        reuse_decision=reuse_detail)
                    attempt_counts[key] = attempt_no
                    if reusable:
                        refs = (reusable.projection_id,)
                        self.workflow_store.finish_attempt(attempt["agent_run_id"],
                                                           status="succeeded",
                                                           projection_refs=refs)
                        outcomes[key] = TaskOutcome(
                            key, task.task_id, task.scope, "succeeded", refs,
                            (reusable,), detail="projection_reused", attempts=attempt_no,
                            reused=True)
                        pending.pop(key)
                        made_progress = True
                        continue
                    context = TaskContext(
                        plan=plan, task=task, trigger=trigger,
                        agent_run_id=attempt["agent_run_id"], store=memory,
                        dependencies=dependencies, input_refs=tuple(input_refs),
                        data_vintage_refs=tuple(data_refs), task_inputs=task_inputs,
                        run_once=run_once)
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
                    future = executor.submit(self._invoke_adapter, adapter, context)
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
                                detail=detail, attempts=state["attempt_no"])
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
                        except DependencyUnavailable as exc:
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
        return sorted(set(map(str, input_refs))), sorted(set(map(str, data_refs))), task_inputs

    def _select_reusable(self, memory, task, input_refs, data_refs, as_of):
        spec = phase_e_registry().spec(task.task_id)
        role = TASK_ROLE[task.task_id]
        schema = PAYLOAD_SCHEMA_BY_ROLE[role]
        if spec.freshness_seconds <= 0:
            return None, {"decision": "recompute", "reason": "reuse_disabled"}
        if not data_refs:
            return None, {"decision": "recompute", "reason": "missing_data_vintage"}
        rows = memory.task_projection_envelopes(
            agent_role=role, scope_kind=task.scope.kind, scope_id=task.scope.id,
            status="published", limit=50)
        candidates = []
        for row in rows:
            envelope = self._envelope(row)
            if not envelope.agent_run_id:
                reusable, reason = False, "agent_attempt_missing"
            else:
                attempt = memory.conn.execute(
                    "SELECT status FROM agent_runs WHERE agent_run_id=?",
                    (envelope.agent_run_id,)).fetchone()
                if attempt is None or attempt["status"] != "succeeded":
                    reusable, reason = False, "agent_attempt_not_terminal"
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
        if sorted(envelope.input_refs) != sorted(input_refs):
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
            if datetime.now(timezone.utc) - created > timedelta(seconds=freshness_seconds):
                return False, "expired"
        except ValueError:
            return False, "invalid_timestamp"
        reusable, reason = reuse_decision(
            envelope, scope=task.scope, input_refs=input_refs,
            data_vintage_refs=data_refs, schema_name=schema_name,
            schema_version="v1")
        return reusable, reason

    def _invoke_adapter(self, adapter, context):
        from ..agent.task_projection import projection_context

        with task_store_scope(self.workflow_store.path) as store:
            context.store = store
            with projection_context(
                    workflow_run_id=context.run_id, agent_run_id=context.agent_run_id,
                    input_refs=context.input_refs,
                    data_vintage_refs=context.data_vintage_refs):
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

        snapshot, detail = build_chief_snapshot(
            memory, at=datetime.now(timezone.utc), projection_manifest=manifest)
        block_reason = "complete_snapshot" if snapshot.complete else "incomplete_research_snapshot"
        ready = snapshot.complete
        entered, chief_payload = False, {}
        if ready and self.chief_runner is not None:
            try:
                chief_payload = self.chief_runner(snapshot, manifest) or {}
                entered = True
            except Exception as exc:  # noqa: BLE001 - the run remains auditable
                block_reason = f"chief_runner_failed:{type(exc).__name__}"
        elif ready:
            block_reason = "chief_runner_not_configured"
        snapshot_payload = {
            "scope": snapshot.scope.key, "built_at": snapshot.built_at,
            "complete": snapshot.complete,
            "items": [vars(item) for item in snapshot.items],
            "detail": detail, "manifest": manifest, "chief_result": chief_payload,
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
    policy = phase_e_registry().spec(task.task_id).retry
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
        layers=True, write_reports=False, upstream_layer_projections=layer_inputs)
    if not review.layer_verdicts:
        raise ProjectionMissing("Sector Analyst produced no allocation projection")
    return TaskAdapterResult(detail=f"sector={review.sector}")


def _information_dependency_refs(context: TaskContext) -> list[str]:
    return [f"projection:{env.projection_id}:{env.content_hash}"
            for env in context.dependencies.values()
            if env.agent_role == "information_brief"]


def _fundamental_routine_adapter(context: TaskContext) -> TaskAdapterResult:
    from ..agents.fundamental.entry import routine_request, run_fundamental_pass

    symbol = context.task.scope.id
    trigger = str(context.task_inputs.get("fundamental_trigger", "information_brief_update"))
    request = routine_request(symbol, trigger=trigger, use_llm=_use_llm(context),
                              extra={"input_refs": _information_dependency_refs(context),
                                     "data_vintage_refs": list(context.data_vintage_refs)})
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

    review = run(name="macro", use_llm=_use_llm(context), live_data=True)
    if not review.regime or review.regime.startswith("("):
        raise ProjectionMissing("Macro Analyst did not publish a formal regime projection")
    return TaskAdapterResult(detail=f"regime={review.regime}")


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
