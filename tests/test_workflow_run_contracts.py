"""Workflow run contracts: request/result shape, registry-declared dependencies,
stable trigger identity, and the rule that analysis stops before the decision cycle
unless a run explicitly asks for it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ats.agent.task_projection import ProjectionScope
from ats.workflow.run_contracts import (
    ContractError,
    RetryPolicy,
    TaskRegistry,
    TaskResult,
    TriggerContext,
    UnregisteredTaskError,
    WorkflowRunRequest,
    WorkflowTaskSpec,
    build_run_result,
    default_registry,
    should_enter_decision_cycle,
)

SCOPE = ProjectionScope(kind="entity", id="NVDA")
AS_OF = "2026-09-22T00:00:00+00:00"


def _trigger(**kwargs):
    params = {"kind": "manual", "trigger_id": "manual-1"}
    params.update(kwargs)
    return TriggerContext(**params)


def _request(**kwargs):
    params = dict(run_id="run-1", trigger=_trigger(), tasks=("macro_review",),
                  scope=SCOPE, as_of=AS_OF)
    params.update(kwargs)
    return WorkflowRunRequest(**params)


def _ok(task_id: str, *refs: str) -> TaskResult:
    return TaskResult(task_id=task_id, status="succeeded", projection_refs=refs,
                      as_of=AS_OF)


# --- 6.1 request ----------------------------------------------------------- #

def test_a_complete_request_is_accepted() -> None:
    request = _request()
    assert request.run_id == "run-1"
    assert request.enter_decision_cycle is False      # opt-in, never default
    assert request.as_of == AS_OF


@pytest.mark.parametrize("field", ["run_id", "as_of", "trigger", "scope", "tasks"])
def test_a_request_missing_a_required_field_is_rejected(field: str) -> None:
    body = {"run_id": "run-1", "trigger": _trigger(), "tasks": ("macro_review",),
            "scope": SCOPE, "as_of": AS_OF}
    body.pop(field)
    with pytest.raises(ValidationError):
        WorkflowRunRequest(**body)


def test_an_empty_task_set_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _request(tasks=())


def test_a_request_naming_an_unregistered_task_is_refused() -> None:
    with pytest.raises(UnregisteredTaskError):
        _request(tasks=("invented_task",)).validate_against(default_registry())


def test_a_task_that_rejects_the_trigger_kind_is_refused() -> None:
    registry = TaskRegistry([WorkflowTaskSpec(
        task_id="only_manual", trigger_modes=("manual",))])
    with pytest.raises(ContractError):
        _request(tasks=("only_manual",),
                 trigger=_trigger(kind="event", event_id="e1",
                                  event_version="v1")).validate_against(registry)


# --- 6.2 result ------------------------------------------------------------ #

def test_a_fully_successful_run_is_complete_and_lists_its_projections() -> None:
    request = _request(tasks=("macro_review",))
    result = build_run_result(request, default_registry(),
                              {"macro_review": _ok("macro_review", "proj-1")})
    assert result.terminal == "complete" and result.complete
    assert result.projection_refs == ("proj-1",)
    assert result.missing_requirements == ()


@pytest.mark.parametrize("status", ["missing", "failed", "stale"])
def test_any_non_success_makes_the_run_incomplete(status: str) -> None:
    request = _request(tasks=("macro_review",))
    result = build_run_result(
        request, default_registry(),
        {"macro_review": TaskResult(task_id="macro_review", status=status,
                                    detail="boom")})
    assert result.terminal == "incomplete"
    assert [(r.task_id, r.kind) for r in result.missing_requirements] == [
        ("macro_review", status)]


def test_a_task_with_no_outcome_is_reported_as_missing_not_silently_dropped() -> None:
    request = _request(tasks=("macro_review",))
    result = build_run_result(request, default_registry(), {})
    assert result.terminal == "incomplete"
    assert result.missing_requirements[0].kind == "missing"
    assert result.task_results[0].status == "missing"


def test_a_single_task_run_does_not_fail_the_tasks_it_did_not_ask_for() -> None:
    """Unrequested tasks must not appear as failures — only as absent, which is fine."""
    request = _request(tasks=("macro_review",))
    result = build_run_result(request, default_registry(),
                              {"macro_review": _ok("macro_review", "proj-1")})
    assert [r.task_id for r in result.task_results] == ["macro_review"]
    assert result.terminal == "complete"


# --- 6.3 registry ---------------------------------------------------------- #

def test_dependencies_come_from_the_registry_not_the_request_order() -> None:
    registry = default_registry()
    assert registry.dependencies_for("sector_allocation") == ("macro_review",)
    # Requested last, scheduled first: the declaration decides, not the listing order.
    assert registry.resolve_order(("sector_allocation", "macro_review")) == (
        "macro_review", "sector_allocation")


def test_a_newly_registered_task_is_schedulable_without_touching_the_dispatcher() -> None:
    registry = default_registry()
    registry.register(WorkflowTaskSpec(task_id="new_task", depends_on=("macro_review",)))
    assert registry.resolve_order(("new_task",)) == ("macro_review", "new_task")


def test_an_unregistered_dependency_is_reported_not_skipped() -> None:
    registry = TaskRegistry([WorkflowTaskSpec(task_id="orphan", depends_on=("ghost",))])
    with pytest.raises(UnregisteredTaskError):
        registry.dependencies_for("orphan")


def test_a_task_spec_carries_its_scheduling_policy() -> None:
    spec = WorkflowTaskSpec(task_id="t", freshness_seconds=60, timeout_seconds=30,
                            retry=RetryPolicy(max_attempts=3, backoff_seconds=1.5),
                            resource_group="llm-heavy")
    assert spec.retry.max_attempts == 3
    assert spec.resource_group == "llm-heavy"


# --- 6.4 trigger identity -------------------------------------------------- #

def test_a_replayed_event_derives_the_same_idempotency_key() -> None:
    first = _trigger(kind="event", event_id="ev-9", event_version="v1",
                     requested_at="2026-09-22T00:00:00+00:00").validate_for_use()
    replay = _trigger(kind="event", event_id="ev-9", event_version="v1",
                      requested_at="2026-09-22T06:00:00+00:00").validate_for_use()
    assert first.idempotency_key("macro_review") == replay.idempotency_key("macro_review")


def test_a_corrected_event_version_is_a_different_task() -> None:
    first = _trigger(kind="event", event_id="ev-9", event_version="v1").validate_for_use()
    corrected = _trigger(kind="event", event_id="ev-9",
                         event_version="v2").validate_for_use()
    assert first.idempotency_key("macro_review") != corrected.idempotency_key("macro_review")


def test_a_misfire_compensation_keeps_the_planned_instant() -> None:
    planned = _trigger(kind="schedule", schedule_id="daily",
                       scheduled_for="2026-09-22T09:00:00+00:00").validate_for_use()
    compensated = _trigger(
        kind="schedule", schedule_id="daily", scheduled_for="2026-09-22T09:00:00+00:00",
        requested_at="2026-09-22T14:30:00+00:00").validate_for_use()
    assert planned.idempotency_key("macro_review") == \
        compensated.idempotency_key("macro_review")


def test_a_schedule_trigger_without_a_planned_instant_is_refused() -> None:
    with pytest.raises(ContractError):
        _trigger(kind="schedule", schedule_id="daily").validate_for_use()


def test_a_manual_trigger_without_a_key_gets_one_that_must_be_reused() -> None:
    trigger = _trigger(trigger_id="").validate_for_use()
    assert trigger.trigger_id
    same = _trigger(trigger_id=trigger.trigger_id).validate_for_use()
    assert same.idempotency_key("macro_review") == trigger.idempotency_key("macro_review")


def test_the_same_task_under_different_triggers_is_a_different_instance() -> None:
    manual = _trigger(trigger_id="m1").validate_for_use()
    event = _trigger(kind="event", event_id="ev-9",
                     event_version="v1").validate_for_use()
    assert manual.idempotency_key("macro_review") != event.idempotency_key("macro_review")


# --- 6.5 analysis ends at the projection ----------------------------------- #

def test_a_run_that_did_not_ask_for_the_decision_cycle_does_not_enter_it() -> None:
    request = _request(tasks=("macro_review",), enter_decision_cycle=False)
    result = build_run_result(request, default_registry(),
                              {"macro_review": _ok("macro_review", "proj-1")})
    assert result.decision_cycle_entered is False
    assert result.decision_block_reason == "not_requested"
    assert should_enter_decision_cycle(request, result) == (False, "not_requested")


def test_a_complete_run_that_asked_for_the_decision_cycle_may_enter_it() -> None:
    request = _request(tasks=("macro_review",), enter_decision_cycle=True)
    result = build_run_result(request, default_registry(),
                              {"macro_review": _ok("macro_review", "proj-1")})
    assert result.decision_cycle_entered is True
    assert result.decision_block_reason == ""


def test_an_incomplete_run_is_blocked_from_the_decision_cycle() -> None:
    request = _request(tasks=("macro_review",), enter_decision_cycle=True)
    result = build_run_result(
        request, default_registry(),
        {"macro_review": TaskResult(task_id="macro_review", status="failed",
                                    detail="model error")})
    assert result.decision_cycle_entered is False
    assert result.decision_block_reason == "run_incomplete:incomplete"


# --- 6.6 failure isolation ------------------------------------------------- #

def test_a_failure_does_not_cancel_unrelated_tasks() -> None:
    """The run is incomplete — but the analyses that had nothing to do with the
    failure still finish, and say so."""
    request = _request(tasks=("sector_allocation", "technical_review"),
                       enter_decision_cycle=True)
    result = build_run_result(request, default_registry(), {
        "macro_review": TaskResult(task_id="macro_review", status="failed",
                                   detail="upstream outage"),
        "sector_allocation": TaskResult(task_id="sector_allocation", status="blocked",
                                        detail="upstream dependency failed"),
        "technical_review": _ok("technical_review", "proj-tech"),
    })
    statuses = {r.task_id: r.status for r in result.task_results}
    assert statuses["technical_review"] == "succeeded"
    assert statuses["sector_allocation"] == "blocked"
    assert result.projection_refs == ("proj-tech",)
    assert result.terminal == "incomplete"
    assert result.decision_cycle_entered is False


def test_a_dependent_of_a_failure_is_marked_blocked_not_failed() -> None:
    registry = default_registry()
    assert "sector_allocation" in registry.dependents_of("macro_review")
    assert "technical_review" not in registry.dependents_of("macro_review")


def test_a_blocked_task_counts_as_a_requirement_gap() -> None:
    request = _request(tasks=("sector_allocation",))
    result = build_run_result(request, default_registry(), {
        "macro_review": TaskResult(task_id="macro_review", status="failed", detail="x"),
        "sector_allocation": TaskResult(task_id="sector_allocation", status="blocked",
                                        detail="upstream dependency failed"),
    })
    kinds = {r.kind for r in result.missing_requirements}
    assert kinds == {"failed", "blocked"}
    assert result.terminal == "incomplete"
