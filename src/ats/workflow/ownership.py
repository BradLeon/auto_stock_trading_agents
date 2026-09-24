"""Per-workflow legacy/shadow/dispatcher ownership and safe invocation helpers."""

from __future__ import annotations

from datetime import date, datetime, timezone
import os
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import yaml

from ..agent.task_projection import ProjectionScope
from ..config import REPO_ROOT
from .dispatcher import Dispatcher
from .phase_e import TASK_ROLE, build_plan
from .run_contracts import TriggerContext
from .store import WorkflowStore
from .triggers import TriggerService, load_trigger_policies, trigger_key


class OwnershipError(ValueError):
    pass


def load_workflow_owners(config_dir: str | Path | None = None) -> dict[str, Any]:
    root = Path(config_dir or os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))
    path = root / "workflow" / "workflow_owners.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    workflows = payload.get("workflows", {}) or {}
    for workflow_id, value in workflows.items():
        mode = value.get("mode", "legacy")
        if mode not in {"legacy", "shadow", "dispatcher"}:
            raise OwnershipError(f"workflow {workflow_id!r} has invalid owner mode {mode!r}")
        task_ids = tuple(value.get("task_ids", ()))
        if not task_ids or set(task_ids) - set(TASK_ROLE):
            raise OwnershipError(f"workflow {workflow_id!r} has invalid Phase E task IDs")
    return payload


def owner_mode(workflow_id: str, *, config_dir: str | Path | None = None) -> str:
    config = load_workflow_owners(config_dir)
    entry = (config.get("workflows", {}) or {}).get(workflow_id)
    if entry is None:
        raise OwnershipError(f"workflow {workflow_id!r} has no declared owner")
    return str(entry.get("mode", "legacy"))


def _database_path(mode: str, *, config_dir: str | Path | None = None) -> str | None:
    if mode != "shadow":
        return None
    configured = os.environ.get("ATS_SHADOW_DB_PATH", "")
    if configured:
        path = Path(configured).expanduser()
    else:
        config = load_workflow_owners(config_dir)
        path = Path(str(config.get("shadow_database", "var/shadow/phase-e.sqlite")))
        if not path.is_absolute():
            path = REPO_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _request_body(entry: Mapping[str, Any], scope: ProjectionScope,
                  request: Mapping[str, Any] | None) -> dict[str, Any]:
    frozen_request = dict(request or {})
    task_ids = tuple(entry.get("task_ids", ()))
    requested = tuple(frozen_request.get("requested_tasks", task_ids))
    return {
        **frozen_request,
        "requested_tasks": list(requested),
        "scope": scope.model_dump(mode="json"),
        "task_inputs": frozen_request.get("task_inputs", {}),
        "profile_id": frozen_request.get("profile_id", ""),
        "enter_decision_cycle": bool(frozen_request.get("enter_decision_cycle", False)),
    }


def workflow_store_for_owner(workflow_id: str, *, config_dir: str | Path | None = None
                              ) -> WorkflowStore:
    owners = load_workflow_owners(config_dir)
    entry = (owners.get("workflows", {}) or {}).get(workflow_id)
    if entry is None:
        raise OwnershipError(f"workflow {workflow_id!r} has no declared owner")
    return WorkflowStore(_database_path(entry.get("mode", "legacy"), config_dir=config_dir))


def run_owned_workflow(workflow_id: str, *, scope: ProjectionScope,
                       trigger: TriggerContext, request: Mapping[str, Any] | None = None,
                       now: datetime | None = None,
                       config_dir: str | Path | None = None) -> dict[str, Any]:
    """Run only a declared non-legacy owner; shadow uses a separate entire SQLite DB."""
    owners = load_workflow_owners(config_dir)
    entry = (owners.get("workflows", {}) or {}).get(workflow_id)
    if entry is None:
        raise OwnershipError(f"workflow {workflow_id!r} has no declared owner")
    mode = entry.get("mode", "legacy")
    if mode == "legacy":
        return {"workflow_id": workflow_id, "owner": mode, "status": "legacy"}
    task_ids = tuple(entry.get("task_ids", ()))
    request_body = _request_body(entry, scope, request)
    requested = tuple(request_body["requested_tasks"])
    if set(requested) - set(task_ids):
        raise OwnershipError(
            f"request contains tasks not owned by {workflow_id!r}: {set(requested) - set(task_ids)}")
    ctx = trigger.model_copy(deep=True)
    ctx.workflow_id = workflow_id
    ctx.validate_for_use()
    path = _database_path(mode, config_dir=config_dir)
    store = WorkflowStore(path)
    policies = load_trigger_policies(config_dir)
    policy = policies.get(workflow_id, policies["default"])
    def plan_factory(context, body, run_id):
        return build_plan(
            requested_tasks=body["requested_tasks"], scope=scope,
            trigger=context, as_of=body.get("as_of", context.requested_at),
            run_id=run_id, profile_id=body.get("profile_id", ""),
            enter_decision_cycle=body["enter_decision_cycle"],
            config_dir=config_dir, namespace=mode,
            task_inputs=body.get("task_inputs", {}))

    result = TriggerService(store, policy=policy).dispatch(
        ctx, request=request_body, plan_factory=plan_factory,
        dispatcher=Dispatcher(workflow_store=store), now=now)
    return {"workflow_id": workflow_id, "owner": mode, **result}


def phase_e_schedule_entries(*, config_dir: str | Path | None = None) -> dict[str, dict[str, Any]]:
    root = Path(config_dir or os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))
    payload = yaml.safe_load((root / "workflow" / "phase_e_schedules.yaml").read_text(
        encoding="utf-8")) or {}
    owners = load_workflow_owners(config_dir).get("workflows", {})
    entries = {}
    for workflow_id, value in (payload.get("workflows", {}) or {}).items():
        owner = owners.get(workflow_id)
        if owner is None:
            raise OwnershipError(f"scheduled workflow {workflow_id!r} has no owner entry")
        if owner.get("mode") not in {"shadow", "dispatcher"} or not value.get("enabled", False):
            continue
        task_ids = tuple(value.get("task_ids", ()))
        if set(task_ids) - set(owner.get("task_ids", ())):
            raise OwnershipError(f"schedule {workflow_id!r} contains tasks outside its owner")
        cron = value.get("cron", {})
        if not all(key in cron for key in ("hour", "minute", "timezone")):
            raise OwnershipError(f"schedule {workflow_id!r} has incomplete cron settings")
        entries[workflow_id] = {**value, "task_ids": task_ids,
                                "scope": ProjectionScope.model_validate(value["scope"])}
    return entries


def dispatch_calendar_event(event: Mapping[str, Any], *,
                            admitted_materials: list[Mapping[str, Any]],
                            now: datetime | None = None,
                            config_dir: str | Path | None = None,
                            calendar_store=None) -> list[dict[str, Any]]:
    """Route a versioned released event only with caller-verified admitted materials."""
    from .routing import resolve_event_route
    from ..data.stores.schedule_calendar import ScheduleCalendarStore

    route = resolve_event_route(event, materials=admitted_materials, config_dir=config_dir)
    calendar_store = calendar_store or ScheduleCalendarStore()
    current = next((item for item in calendar_store.latest_events(limit=10_000)
                    if item["event_id"] == route.event_id), None)
    if (current is None or str(current["event_version"]) != route.event_version):
        raise OwnershipError("calendar event version is no longer current; refresh before routing")
    if current.get("quality_status") != "ok":
        raise OwnershipError("calendar event has unresolved source conflicts; routing is blocked")
    payload = event.get("payload", {}) or {}
    if route.scope_resolver == "event_entity":
        entity = str(payload.get("entity") or event.get("entity") or "").upper()
        if not entity:
            identity = str(event.get("event_id", "")).split(":")
            entity = identity[1].upper() if len(identity) > 2 and identity[0] == "earnings" else ""
        if not entity:
            raise OwnershipError("event_entity route requires a canonical entity")
        scope = ProjectionScope(kind="entity", id=entity)
    else:
        scope = ProjectionScope(kind="portfolio", id="portfolio")
    references = [str(item.get("ref") or item.get("reference") or item.get("document_id") or "")
                  for item in admitted_materials if item.get("status") == "admitted"]
    references = [item for item in references if item]
    fiscal_label = str(payload.get("fiscal_label") or payload.get("reference_period") or "")
    outcomes = []
    for workflow_id in route.workflow_ids:
        mode = owner_mode(workflow_id, config_dir=config_dir)
        if mode == "legacy":
            outcomes.append({"workflow_id": workflow_id, "owner": mode, "status": mode})
            continue
        workflow_store_for_owner(workflow_id, config_dir=config_dir).supersede_event(
            event_id=route.event_id, current_version=route.event_version, at=now)
        task_inputs = {
            "event_id": route.event_id,
            "event_version": route.event_version,
            "event_type": route.route_kind,
            "event_state": route.event_state,
            "material_state": "admitted" if references else "missing",
            "admitted_material_refs": references,
            "fiscal_label": fiscal_label,
            "trigger": ("earnings_release" if route.route_kind == "earnings:release" else
                        "information_brief_update" if route.route_kind == "earnings:plan" else
                        "company_event"),
            "fundamental_trigger": (
                "earnings_release" if route.route_kind == "earnings:release" else
                "information_brief_update" if route.route_kind == "earnings:plan" else
                "company_event"),
        }
        context = TriggerContext(kind="event", workflow_id=workflow_id,
                                 event_id=route.event_id,
                                 event_version=route.event_version)
        result = run_owned_workflow(
            workflow_id, scope=scope, trigger=context,
            request={"requested_tasks": [workflow_id], "task_inputs": task_inputs,
                     "as_of": str(event.get("valid_from")
                                  if route.route_kind.endswith(":release") else
                                  (now or datetime.now(timezone.utc)).isoformat())},
            now=now, config_dir=config_dir)
        outcomes.append(result)
    return outcomes


def dispatch_planned_calendar_events(*, calendar_store=None, now: datetime | None = None,
                                     config_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """Run allow-listed preparation workflows once inside configured lead windows."""
    from ..data.stores.schedule_calendar import ScheduleCalendarStore
    from .routing import resolve_event_route

    repository = calendar_store or ScheduleCalendarStore()
    instant = now or datetime.now(timezone.utc)
    outcomes = []
    for event in repository.latest_events(limit=10_000):
        if event["status"] != "planned":
            continue
        planned = {**event, "event_subtype": "plan"}
        try:
            route = resolve_event_route(planned, materials=[], config_dir=config_dir)
        except Exception:
            continue  # not a planned event type with an explicit route
        if route.lead_days_before <= 0 or not event.get("event_date"):
            continue
        today = instant.astimezone(ZoneInfo(route.window_timezone)).date()
        event_date = date.fromisoformat(str(event["event_date"]))
        days_until = (event_date - today).days
        if not 0 <= days_until <= route.lead_days_before:
            continue
        try:
            results = dispatch_calendar_event(
                planned, admitted_materials=[], now=instant, config_dir=config_dir,
                calendar_store=repository)
            outcomes.append({"event_id": event["event_id"],
                             "event_version": event["event_version"],
                             "days_until": days_until, "workflows": results})
        except Exception as exc:
            outcomes.append({"event_id": event["event_id"],
                             "event_version": event["event_version"],
                             "status": "blocked", "reason": str(exc)[:300]})
    return outcomes


def dispatch_released_calendar_events(*, calendar_store=None, now: datetime | None = None,
                                      config_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """Wake allow-listed analysis workflows for currently released, admitted events."""
    from ..data.stores.schedule_calendar import ScheduleCalendarStore

    repository = calendar_store or ScheduleCalendarStore()
    outcomes = []
    for event in repository.latest_events(limit=10_000):
        if event["status"] != "released":
            continue
        materials = []
        for confirmation in event.get("release_confirmations", []):
            materials.extend({**item, "status": "admitted"}
                             for item in confirmation.get("materials", []))
        if not materials:
            continue
        try:
            results = dispatch_calendar_event(event, admitted_materials=materials, now=now,
                                              config_dir=config_dir, calendar_store=repository)
            outcomes.append({"event_id": event["event_id"],
                             "event_version": event["event_version"],
                             "workflows": results})
        except Exception as exc:
            outcomes.append({"event_id": event["event_id"],
                             "event_version": event["event_version"],
                             "status": "blocked", "reason": str(exc)[:300]})
    return outcomes


def record_due_calendar_material_waits(*, calendar_store=None, now: datetime | None = None,
                                       config_dir: str | Path | None = None
                                       ) -> list[dict[str, Any]]:
    """Record due planned releases that still lack admitted materials.

    Date-only calendar entries become due on the event date in the route's local
    timezone; this records a retryable state without inventing a release time or
    invoking an analysis task.
    """
    from ..data.stores.schedule_calendar import ScheduleCalendarStore
    from .routing import resolve_event_route

    repository = calendar_store or ScheduleCalendarStore()
    instant = now or datetime.now(timezone.utc)
    outcomes = []
    for event in repository.latest_events(limit=10_000):
        if event["status"] != "planned" or not event.get("event_date"):
            continue
        released = {**event, "event_subtype": "release", "status": "released"}
        try:
            route = resolve_event_route(released, materials=[], config_dir=config_dir,
                                        validate_materials=False)
        except Exception:
            continue  # no explicit release route for this planned event

        if event.get("time_precision") == "minute" and event.get("utc_at"):
            try:
                release_at = datetime.fromisoformat(
                    str(event["utc_at"]).replace("Z", "+00:00"))
                if release_at.tzinfo is None or instant.astimezone(timezone.utc) < \
                        release_at.astimezone(timezone.utc):
                    continue
            except ValueError:
                continue
        else:
            route_day = instant.astimezone(ZoneInfo(route.window_timezone)).date()
            if date.fromisoformat(str(event["event_date"])) > route_day:
                continue

        waiting = []
        for workflow_id in route.workflow_ids:
            mode = owner_mode(workflow_id, config_dir=config_dir)
            if mode == "legacy":
                continue
            store = workflow_store_for_owner(workflow_id, config_dir=config_dir)
            store.supersede_event(event_id=route.event_id,
                                  current_version=route.event_version, at=instant)
            context = TriggerContext(kind="event", workflow_id=workflow_id,
                                     event_id=route.event_id,
                                     event_version=route.event_version)
            context.validate_for_use()
            key = trigger_key(context)
            request = {"calendar_event_id": route.event_id,
                       "event_version": route.event_version,
                       "workflow_id": workflow_id,
                       "reason_code": "release_material_not_admitted"}
            row = store.record_trigger(
                trigger_key=key, kind="event", workflow_id=workflow_id,
                request=request, event_id=route.event_id,
                event_version=route.event_version, at=instant)
            if row["status"] in {"planned", "pending"}:
                store.resolve_planned_trigger(
                    key, status="waiting_material",
                    reason_code="release_material_not_admitted", at=instant)
                row = store.get_trigger(key) or row
            waiting.append({"workflow_id": workflow_id, "trigger_key": key,
                            "status": row["status"],
                            "reason": row.get("reason_code", "")})
        if waiting:
            outcomes.append({"event_id": route.event_id,
                             "event_version": route.event_version,
                             "status": "waiting_material", "workflows": waiting})
    return outcomes


def reconcile_calendar_trigger_versions(*, calendar_store=None, now: datetime | None = None,
                                         config_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """Supersede old event triggers, including cancellations and in-flight work."""
    from ..data.stores.schedule_calendar import ScheduleCalendarStore
    from .routing import _routes

    repository = calendar_store or ScheduleCalendarStore()
    workflow_ids = sorted({str(workflow_id)
                           for rule in _routes(config_dir)
                           for workflow_id in rule.get("workflow_ids", ())})
    outcomes = []
    for event in repository.latest_events(limit=10_000, include_cancelled=True):
        for workflow_id in workflow_ids:
            if owner_mode(workflow_id, config_dir=config_dir) == "legacy":
                continue
            changed = workflow_store_for_owner(workflow_id, config_dir=config_dir).supersede_event(
                event_id=event["event_id"], current_version=str(event["event_version"]), at=now)
            if changed:
                outcomes.append({"event_id": event["event_id"],
                                 "event_version": event["event_version"],
                                 "workflow_id": workflow_id, "superseded": changed})
    return outcomes


__all__ = ["OwnershipError", "load_workflow_owners", "owner_mode",
           "dispatch_calendar_event", "dispatch_released_calendar_events",
           "record_due_calendar_material_waits",
           "dispatch_planned_calendar_events", "phase_e_schedule_entries",
           "reconcile_calendar_trigger_versions", "run_owned_workflow",
           "workflow_store_for_owner"]
