"""Phase E task registry, frozen scope expansion, and decision-profile validation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

import yaml

from ..agent.task_projection import (PAYLOAD_SCHEMA_BY_ROLE, AgentRole,
                                     ProjectionScope)
from ..config import REPO_ROOT
from .run_contracts import (DECISION_CATEGORY_ROLES, ContractError, RetryPolicy,
                            TaskRegistry, TriggerContext, WorkflowTaskSpec)

TASK_ROLE: dict[str, AgentRole] = {
    "layer-review": "layer_analysis",
    "information-brief": "information_brief",
    "sector-review": "sector_allocation",
    "fundamental-routine": "fundamental_expectation_update",
    "fundamental-event": "fundamental_event_review",
    "macro-review": "macro_review",
    "technical-review": "technical_review",
}
ALLOWED_CROSS_ANALYST_EDGES = {
    ("layer_analysis", "sector_allocation"),
    ("information_brief", "fundamental_expectation_update"),
    ("information_brief", "fundamental_event_review"),
}
TASK_POLICIES: dict[str, dict[str, Any]] = {
    "layer-review": {"trigger_modes": ("manual", "schedule", "event"), "resource_group": "llm"},
    "information-brief": {"trigger_modes": ("manual", "schedule", "event"),
                           "resource_group": "information"},
    "sector-review": {"trigger_modes": ("manual", "schedule", "event"), "resource_group": "llm"},
    "fundamental-routine": {"trigger_modes": ("manual", "schedule", "event"),
                             "resource_group": "llm"},
    "fundamental-event": {"trigger_modes": ("manual", "event"), "resource_group": "pead"},
    "macro-review": {"trigger_modes": ("manual", "schedule", "event"),
                      "resource_group": "llm"},
    "technical-review": {"trigger_modes": ("manual", "schedule", "event"),
                         "resource_group": "market-data"},
}
TASK_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "layer-review": (), "information-brief": (),
    "sector-review": ("layer-review",),
    "fundamental-routine": ("information-brief",),
    "fundamental-event": ("information-brief",),
    "macro-review": (), "technical-review": (),
}
FRESHNESS_SECONDS: dict[str, int] = {
    "layer-review": 7 * 86_400, "information-brief": 3_600,
    "sector-review": 7 * 86_400, "fundamental-routine": 3_600,
    "fundamental-event": 4 * 86_400, "macro-review": 7 * 86_400,
    "technical-review": 3_600,
}
TIMEOUT_SECONDS: dict[str, int] = {
    "layer-review": 900, "information-brief": 900, "sector-review": 1_200,
    "fundamental-routine": 900, "fundamental-event": 1_200,
    "macro-review": 900, "technical-review": 600,
}


@dataclass(frozen=True)
class TaskInstance:
    task_id: str
    instance_key: str
    scope: ProjectionScope
    dependencies: tuple[str, ...]
    required_for_decision: bool
    output_schema: str
    resource_group: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "instance_key": self.instance_key,
            "scope": self.scope.model_dump(mode="json"),
            "dependencies": list(self.dependencies),
            "required_for_decision": self.required_for_decision,
            "output_schema": self.output_schema,
            "resource_group": self.resource_group,
        }


@dataclass(frozen=True)
class WorkflowPlan:
    run_id: str
    plan_id: str
    registry_version: str
    profile_id: str
    profile_version: str
    profile_hash: str
    source_config_hashes: dict[str, str]
    requested_tasks: tuple[str, ...]
    request_scope: ProjectionScope
    trigger: dict[str, Any]
    task_inputs: dict[str, Any]
    tasks: tuple[TaskInstance, ...]
    edges: tuple[tuple[str, str], ...]
    as_of: str
    enter_decision_cycle: bool

    @property
    def plan_hash(self) -> str:
        return _hash(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "registry_version": self.registry_version,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "profile_hash": self.profile_hash,
            "source_config_hashes": self.source_config_hashes,
            "requested_tasks": list(self.requested_tasks),
            "request_scope": self.request_scope.model_dump(mode="json"),
            "trigger": self.trigger,
            "task_inputs": self.task_inputs,
            "tasks": [task.as_dict() for task in self.tasks],
            "edges": [list(edge) for edge in self.edges],
            "as_of": self.as_of,
            "enter_decision_cycle": self.enter_decision_cycle,
        }


def _config_dir(config_dir: str | Path | None = None) -> Path:
    return Path(config_dir or os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def phase_e_registry() -> TaskRegistry:
    specs = []
    for task_id, role in TASK_ROLE.items():
        policy = TASK_POLICIES[task_id]
        schema = PAYLOAD_SCHEMA_BY_ROLE[role]
        specs.append(WorkflowTaskSpec(
            task_id=task_id, agent_role=role, depends_on=TASK_DEPENDENCIES[task_id],
            trigger_modes=policy["trigger_modes"], input_contract="frozen scope + admitted data",
            output_schema=schema.role_schema_name(),
            freshness_seconds=FRESHNESS_SECONDS[task_id],
            timeout_seconds=TIMEOUT_SECONDS[task_id], resource_group=policy["resource_group"],
            retry=RetryPolicy(max_attempts=2, backoff_seconds=1.0,
                              retry_on=("timeout", "transient")),
            required_for_decision=True))
    registry = TaskRegistry(specs)
    validate_phase_e_registry(registry)
    return registry


def validate_phase_e_registry(registry: TaskRegistry) -> None:
    registry.validate()
    for task_id in registry.task_ids():
        spec = registry.spec(task_id)
        expected_role = TASK_ROLE.get(task_id)
        if expected_role is None or spec.agent_role != expected_role:
            raise ContractError(f"task {task_id!r} has an unexpected analyst role")
        if spec.output_schema != PAYLOAD_SCHEMA_BY_ROLE[expected_role].role_schema_name():
            raise ContractError(f"task {task_id!r} has an unresolved output schema")
        if not spec.trigger_modes or set(spec.trigger_modes) - {"manual", "schedule", "event"}:
            raise ContractError(f"task {task_id!r} has an invalid trigger policy")
        if spec.resource_group not in {"llm", "information", "pead", "market-data"}:
            raise ContractError(f"task {task_id!r} has an unknown resource group")
        for dependency in spec.depends_on:
            parent_role = registry.spec(dependency).agent_role
            edge = (parent_role, spec.agent_role)
            if parent_role != spec.agent_role and edge not in ALLOWED_CROSS_ANALYST_EDGES:
                raise ContractError(f"unauthorized cross-analyst dependency: {dependency} -> {task_id}")


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ContractError(f"expected a YAML mapping in {path}")
    return value


def _targets(config_dir: Path) -> list[str]:
    pead = _load_yaml(config_dir / "pead.yaml")
    targets = [str(item).upper() for item in pead.get("targets", [])]
    if not targets:
        raise ContractError("config/pead.yaml has no PEAD targets for the frozen scope")
    return list(dict.fromkeys(targets))


def _scopes(task_id: str, requested_scope: ProjectionScope, config_dir: Path) -> list[ProjectionScope]:
    if task_id == "layer-review":
        if requested_scope.kind == "layer":
            return [requested_scope]
        if requested_scope.kind != "sector":
            raise ContractError("layer-review requires a layer or sector scope")
        sector_cfg = _load_yaml(config_dir / "sectors" / f"{requested_scope.id}.yaml")
        layers = [str(layer["key"]) for layer in sector_cfg.get("layers", [])
                  if layer.get("key")]
        if not layers:
            raise ContractError(f"sector {requested_scope.id!r} has no configured layers")
        return [ProjectionScope(kind="layer", id=layer) for layer in layers]
    if task_id in {"information-brief", "fundamental-routine", "fundamental-event",
                   "technical-review"}:
        symbols = ([requested_scope.id.upper()] if requested_scope.kind == "entity"
                   else _targets(config_dir))
        if not symbols:
            raise ContractError(f"{task_id} has an empty entity scope")
        return [ProjectionScope(kind="entity", id=symbol) for symbol in symbols]
    if task_id == "sector-review":
        if requested_scope.kind != "sector":
            raise ContractError("sector-review requires a sector scope")
        return [requested_scope]
    if task_id == "macro-review":
        return [ProjectionScope(kind="portfolio")]
    raise ContractError(f"no scope resolver is registered for {task_id!r}")


def build_plan(*, requested_tasks: Iterable[str], scope: ProjectionScope,
               trigger: TriggerContext, as_of: str, run_id: str,
               profile_id: str = "", enter_decision_cycle: bool = False,
               config_dir: str | Path | None = None,
               namespace: str = "dispatcher",
               task_inputs: dict[str, Any] | None = None) -> WorkflowPlan:
    """Expand a request to stable task instances and freeze all resolver inputs."""
    registry = phase_e_registry()
    trigger.validate_for_use()
    requested = tuple(dict.fromkeys(requested_tasks))
    if not requested:
        raise ContractError("a workflow request must name at least one task")
    for task_id in requested:
        if not registry.has(task_id):
            raise ContractError(f"unregistered Phase E task: {task_id}")
        if not registry.allowed_for(task_id, trigger):
            raise ContractError(f"task {task_id!r} does not accept {trigger.kind!r} triggers")
    ordered_task_ids = registry.resolve_order(requested)
    for task_id in ordered_task_ids:
        if not registry.allowed_for(task_id, trigger):
            raise ContractError(f"dependency {task_id!r} does not accept {trigger.kind!r} triggers")

    root = _config_dir(config_dir)
    profile: dict[str, Any] = {}
    profile_version = ""
    profile_hash = ""
    source_hashes: dict[str, str] = {}
    if profile_id:
        profiles_doc = _load_yaml(root / "workflow" / "decision_profiles.yaml")
        profile = profiles_doc.get("profiles", {}).get(profile_id, {})
        if not profile:
            raise ContractError(f"unknown decision profile {profile_id!r}")
        profile_version = str(profile.get("version", ""))
        profile_hash = _hash(profile)
        for rel_path in profile.get("source_configs", {}).values():
            source_rel = Path(rel_path)
            if source_rel.parts and source_rel.parts[0] == "config":
                source_rel = Path(*source_rel.parts[1:])
            source = (root / source_rel).resolve()
            if not source.exists():
                raise ContractError(f"profile source config is missing: {rel_path}")
            source_hashes[rel_path] = _file_hash(source)
    elif enter_decision_cycle:
        raise ContractError("decision-cycle requests require a versioned decision profile")

    expected_scope = profile.get("scope", {}) if profile else {}
    if expected_scope and (expected_scope.get("kind") != scope.kind
                           or str(expected_scope.get("id", "")).casefold()
                           != scope.id.casefold()):
        raise ContractError(
            f"scope {scope.key!r} does not match profile {profile_id!r} scope")

    source_paths: set[str] = set()
    if any(task in ordered_task_ids for task in ("layer-review", "sector-review")):
        source_paths |= {f"sectors/{scope.id}.yaml", "risk.yaml"}
    if any(task in ordered_task_ids for task in (
            "information-brief", "fundamental-routine", "fundamental-event",
            "technical-review")):
        source_paths.add("pead.yaml")
    if "technical-review" in ordered_task_ids:
        source_paths.add("technical.yaml")
    if "macro-review" in ordered_task_ids:
        source_paths.add("macro.yaml")
    for rel_path in sorted(source_paths):
        if rel_path in source_hashes:
            continue
        source = root / rel_path
        if source.exists():
            source_hashes[f"config/{rel_path}"] = _file_hash(source)

    instances_by_task: dict[str, list[TaskInstance]] = {}
    all_instances: list[TaskInstance] = []
    edges: list[tuple[str, str]] = []
    config_version_hash = _hash({"profile": profile_hash, "sources": source_hashes})
    for task_id in ordered_task_ids:
        spec = registry.spec(task_id)
        task_instances: list[TaskInstance] = []
        for item_scope in _scopes(task_id, scope, root):
            key = f"{namespace}:{task_id}:{item_scope.key}:{config_version_hash[:12]}"
            dependencies: list[str] = []
            for parent in spec.depends_on:
                matching = [candidate for candidate in instances_by_task.get(parent, [])
                            if (task_id == "sector-review"
                                or candidate.scope.key == item_scope.key)]
                if not matching:
                    raise ContractError(
                        f"scope resolver created no {parent} dependency for "
                        f"{task_id}:{item_scope.key}")
                dependencies.extend(candidate.instance_key for candidate in matching)
            instance = TaskInstance(
                task_id=task_id, instance_key=key, scope=item_scope,
                dependencies=tuple(dependencies), required_for_decision=spec.required_for_decision,
                output_schema=spec.output_schema, resource_group=spec.resource_group)
            task_instances.append(instance)
            all_instances.append(instance)
            edges.extend((dependency, key) for dependency in dependencies)
        instances_by_task[task_id] = task_instances

    if enter_decision_cycle:
        required_categories = set(profile.get("required_categories", []))
        if required_categories != set(DECISION_CATEGORY_ROLES):
            raise ContractError(
                f"profile {profile_id!r} does not declare the full required decision category set")
        planned_roles = {registry.spec(task_id).agent_role for task_id in ordered_task_ids}
        missing_categories = [category for category, roles in DECISION_CATEGORY_ROLES.items()
                              if not (roles & planned_roles)]
        if missing_categories:
            raise ContractError(
                "decision-cycle request is incomplete; missing categories: "
                + ", ".join(missing_categories))
        fundamental_modes = planned_roles & {
            "fundamental_expectation_update", "fundamental_event_review"}
        if len(fundamental_modes) != 1:
            raise ContractError("a decision profile must select exactly one Fundamental mode")

    trigger_json = trigger.model_dump(mode="json")
    # Delivery time is audit metadata, not part of a stable schedule/event plan.
    # Persisting a fresh requested_at on replay would otherwise change plan_hash
    # for the same trigger key after a crash/restart.
    trigger_json.pop("requested_at", None)
    inputs_json = dict(task_inputs or {})
    content = {
        "run_id": run_id, "requested": requested, "profile": profile_hash,
        "source_hashes": source_hashes, "tasks": [item.as_dict() for item in all_instances],
        "edges": edges, "as_of": as_of, "trigger": trigger_json,
        "scope": scope.model_dump(mode="json"), "task_inputs": inputs_json,
    }
    return WorkflowPlan(
        run_id=run_id, plan_id=_hash(content)[:32], registry_version="phase-e-v1",
        profile_id=profile_id, profile_version=profile_version, profile_hash=profile_hash,
        source_config_hashes=source_hashes, requested_tasks=requested,
        request_scope=scope, trigger=trigger_json, task_inputs=inputs_json,
        tasks=tuple(all_instances), edges=tuple(edges), as_of=as_of,
        enter_decision_cycle=enter_decision_cycle)


__all__ = ["ALLOWED_CROSS_ANALYST_EDGES", "TASK_ROLE", "TaskInstance", "WorkflowPlan",
           "build_plan", "phase_e_registry", "validate_phase_e_registry"]
