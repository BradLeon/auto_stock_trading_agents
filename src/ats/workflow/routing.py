"""Validated event-calendar metadata to workflow routing.

Calendar records select an allow-listed task ID; they never carry executable text.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from ..config import REPO_ROOT
from .phase_e import phase_e_registry


class RouteError(ValueError):
    pass


@dataclass(frozen=True)
class EventRoute:
    workflow_ids: tuple[str, ...]
    scope_resolver: str
    route_kind: str
    event_id: str
    event_version: str
    event_state: str
    required_material_kinds: tuple[str, ...] = ()
    minimum_material_kinds_any: tuple[str, ...] = ()
    lead_days_before: int = 0
    window_timezone: str = "America/New_York"


def _routes(config_dir: str | Path | None = None) -> list[dict[str, Any]]:
    root = Path(config_dir or REPO_ROOT / "config")
    path = root / "workflow" / "event_routes.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if int(payload.get("schema_version", 0)) != 1:
        raise RouteError("unsupported event route schema_version")
    registry = phase_e_registry()
    allowed = set(registry.task_ids())
    for rule in payload.get("events", []) or []:
        kind = rule.get("event_type")
        kinds = {str(x) for x in kind} if isinstance(kind, list) else {str(kind)}
        if not kinds or "None" in kinds:
            raise RouteError("every event route requires event_type")
        tasks = tuple(rule.get("workflow_ids", ()))
        if not tasks or set(tasks) - allowed:
            raise RouteError(f"event route contains unknown task IDs: {set(tasks) - allowed}")
        if rule.get("enter_decision_cycle"):
            raise RouteError("calendar event routes may not enter a decision cycle")
        if rule.get("scope_resolver") not in {"event_entity", "portfolio"}:
            raise RouteError("event route has an unsupported scope_resolver")
        if int(rule.get("lead_days_before", 0)) < 0:
            raise RouteError("event route lead_days_before must be non-negative")
        try:
            ZoneInfo(str(rule.get("window_timezone", "America/New_York")))
        except ZoneInfoNotFoundError as exc:
            raise RouteError("event route must use a valid IANA window_timezone") from exc
        if (rule.get("required_admitted_material")
                and not (rule.get("required_material_kinds")
                         or rule.get("minimum_material_kinds_any"))):
            raise RouteError("admitted-material routes must name required material kinds")
    return payload.get("events", []) or []


def resolve_event_route(event: Mapping[str, Any], *, materials: list[Mapping[str, Any]] = (),
                        config_dir: str | Path | None = None,
                        validate_materials: bool = True) -> EventRoute:
    event_type = str(event.get("event_type") or event.get("kind") or "").lower()
    subtype = str(event.get("event_subtype") or event.get("subtype") or "release").lower()
    state = str(event.get("status") or event.get("event_state") or "").lower()
    rules = _routes(config_dir)
    match = None
    for rule in rules:
        kinds = rule.get("event_type")
        kinds = {str(x).lower() for x in kinds} if isinstance(kinds, list) else {
            str(kinds).lower()}
        if event_type in kinds and str(rule.get("event_subtype", "")).lower() == subtype:
            match = rule
            break
    if match is None:
        raise RouteError(f"no allow-listed route for {event_type!r}/{subtype!r}")
    if state != str(match.get("required_event_state", "")).lower():
        raise RouteError(f"event state {state!r} does not satisfy route")
    event_id, version = str(event.get("event_id", "")), str(event.get("event_version", ""))
    if not event_id or not version:
        raise RouteError("routed events require stable event_id and event_version")
    required = tuple(str(x) for x in match.get("required_material_kinds", ()))
    minimum_any = tuple(str(x) for x in match.get("minimum_material_kinds_any", ()))
    if match.get("required_admitted_material") and validate_materials:
        admitted = {str(item.get("kind", item.get("material_kind", ""))).lower()
                    for item in materials if item.get("status") == "admitted"}
        missing = set(required) - admitted
        if missing:
            raise RouteError("required event material is not admitted: " + ", ".join(sorted(missing)))
        if minimum_any and not (set(minimum_any) & admitted):
            raise RouteError("no required event material is admitted: " + ", ".join(minimum_any))
    lead_days = int(match.get("lead_days_before", 0))
    if lead_days < 0:
        raise RouteError("lead_days_before must be non-negative")
    return EventRoute(
        workflow_ids=tuple(str(x) for x in match["workflow_ids"]),
        scope_resolver=str(match["scope_resolver"]), route_kind=f"{event_type}:{subtype}",
        event_id=event_id, event_version=version, event_state=state,
        required_material_kinds=required, minimum_material_kinds_any=minimum_any,
        lead_days_before=lead_days,
        window_timezone=str(match.get("window_timezone", "America/New_York")))


__all__ = ["EventRoute", "RouteError", "resolve_event_route"]
