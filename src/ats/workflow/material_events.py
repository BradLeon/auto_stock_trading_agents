"""material_development: rated event components → ad hoc reviews (event-impact-routing group 7, 2026-11-16).

The information brief rates each event component (scope, materiality, direction, novelty). This module turns the
qualifying ones into triggers on the dispatcher's calendar-consume tick:

  * a component qualifies when its materiality is at or above `trigger_min_materiality` (major) in EITHER
    direction and its novelty is `first` or `escalation`; a `follow_up` of an already-seen cluster is recorded as
    "deduplicated" and triggers nothing;
  * scope `entity` → `fundamental-material` for that company; scope `layer` / `cross_layer` → `layer-review` for
    every layer it names; a chain member's own major component ALSO → its layer's `layer-review` (D2b — the layer
    analysis decides whether anything transmits); every tick that ran a layer review then runs `sector-review`
    once, as an ad hoc review (`trigger_source: material_development`, at most one notch down);
  * within one tick, every qualifying component for the same (task, scope) rides on ONE run; the trigger is keyed
    (task, scope, UTC day) and versioned by the set of components, so the same components seen again are a no-op
    while an escalation or a new component later the same day triggers again (2026-11-16 replay: one tick ran
    L5 four times before this);
  * every decision — triggered, deduplicated, rejected (unregistered layer or ticker), paused — is written to
    `material_event_ledger` with the brief projection, component id and cluster;
  * nothing here enters a decision cycle: the Chief reads the outputs in its next routine run.

The whole path is off until config/workflow/event_impact.yaml `routing.enabled` is on.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from ..agent.task_projection import MATERIALITY_ORDER, ProjectionScope
from ..config import REPO_ROOT

log = logging.getLogger("ats.workflow.material_events")

RESOLVERS = {"component_entity", "component_layers"}
SECTOR = "ai_hardware"
_LEDGER_DDL = (
    "CREATE TABLE IF NOT EXISTS material_event_ledger ("
    " seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, brief_projection_id TEXT NOT NULL,"
    " component_id TEXT NOT NULL, cluster_key TEXT NOT NULL, materiality TEXT NOT NULL, novelty TEXT NOT NULL,"
    " workflow_id TEXT NOT NULL, scope TEXT NOT NULL, dedupe_key TEXT NOT NULL, event_version TEXT NOT NULL,"
    " outcome TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', trigger_key TEXT NOT NULL DEFAULT '',"
    " run_id TEXT NOT NULL DEFAULT '')")


class RouteError(ValueError):
    pass


def _config_root(config_dir: str | Path | None) -> Path:
    return Path(config_dir or REPO_ROOT / "config")


def settings(config_dir: str | Path | None = None) -> dict[str, Any]:
    try:
        doc = yaml.safe_load((_config_root(config_dir) / "workflow" / "event_impact.yaml").read_text(
            encoding="utf-8")) or {}
    except FileNotFoundError:
        doc = {}
    routing = doc.get("routing") or {}
    return {"enabled": bool(routing.get("enabled", False)),
            "min_materiality": str(routing.get("trigger_min_materiality", "major")),
            "lookback_hours": int(routing.get("lookback_hours", 6))}


def load_routes(config_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """The allow-listed `material_development` routes; refuses anything the registry does not know."""
    from .planning import task_registry

    doc = yaml.safe_load((_config_root(config_dir) / "workflow" / "event_routes.yaml").read_text(
        encoding="utf-8")) or {}
    allowed = set(task_registry().task_ids())
    routes = list(doc.get("material_development") or [])
    for rule in routes:
        if rule.get("scope_resolver") not in RESOLVERS:
            raise RouteError(f"material_development route has an unsupported scope_resolver: {rule.get('scope_resolver')!r}")
        tasks = set(rule.get("workflow_ids") or ()) | ({rule["then"]} if rule.get("then") else set())
        if not rule.get("workflow_ids") or tasks - allowed:
            raise RouteError(f"material_development route names unknown task IDs: {sorted(tasks - allowed)}")
        if rule.get("enter_decision_cycle"):
            raise RouteError("material_development routes may not enter a decision cycle")
    return routes


def chain_index(sector: str = SECTOR) -> dict[str, Any]:
    """Registered layers and, per ticker, the layers it is a member of."""
    from ..config import load_sector_config

    cfg = load_sector_config(sector)
    member_of: dict[str, list[str]] = {}
    for layer in cfg.layers:
        for t in layer.tickers:
            member_of.setdefault(t.symbol.upper(), []).append(layer.key)
    return {"layers": {layer.key for layer in cfg.layers}, "member_of": member_of}


def _when(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _fingerprint(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", (text or "").strip().lower()).encode()).hexdigest()[:16]


def candidates(store, *, now: datetime, conf: dict[str, Any]) -> list[dict[str, Any]]:
    """Components at or above the trigger materiality in briefs published within the lookback, oldest first."""
    floor = MATERIALITY_ORDER.get(conf["min_materiality"], MATERIALITY_ORDER["major"])
    since = now - timedelta(hours=conf["lookback_hours"])
    out = []
    for row in store.task_projection_envelopes(agent_role="information_brief", limit=2000):
        when = _when(row.get("created_at") or row.get("as_of") or "")
        if when is None or not (since <= when <= now):
            continue
        entity = str(row.get("scope_id") or "").upper()
        for comp in (row.get("payload") or {}).get("event_components") or []:
            if MATERIALITY_ORDER.get(comp.get("materiality") or "minor", 0) < floor:
                continue
            out.append({"brief": row["projection_id"], "at": when, "entity": entity,
                        "component_id": str(comp.get("component_id") or ""),
                        "cluster_key": str(comp.get("cluster_key") or ""),
                        "scope": comp.get("scope"), "layers": list(comp.get("layers") or []),
                        "entities": [str(e).upper() for e in comp.get("entities") or []],
                        "materiality": comp.get("materiality"), "novelty": comp.get("novelty") or "first",
                        "fingerprint": _fingerprint(str(comp.get("content") or ""))})
    return sorted(out, key=lambda c: c["at"])


def targets(candidate: Mapping[str, Any], routes: list[dict[str, Any]], chain: Mapping[str, Any]
            ) -> tuple[list[tuple[str, ProjectionScope]], list[str]]:
    """(workflow, scope) pairs one component routes to, and the reasons for anything refused."""
    out: list[tuple[str, ProjectionScope]] = []
    refused: list[str] = []
    for rule in routes:
        resolver = rule["scope_resolver"]
        if resolver == "component_entity" and candidate["scope"] == "entity":
            entity = candidate["entity"]
            if not _covered(entity):
                # Only Fundamental targets get a material review. A chain member that is not one (the light-brief
                # names: AMD, 005930.KS, ...) still reaches its layer below (D2b); its brief exists for the
                # components, not for a Fundamental view (design, 2026-11-15). Was "member OR target" until
                # 2026-11-24, which sent those names to a material review they have no baseline or register for.
                refused.append(f"entity {entity} is not a Fundamental target (its layer still sees the component)")
                continue
            out += [(wf, ProjectionScope(kind="entity", id=entity)) for wf in rule["workflow_ids"]]
        elif resolver == "component_layers":
            if candidate["scope"] in ("layer", "cross_layer"):
                layers = candidate["layers"]
                unknown = [k for k in layers if k not in chain["layers"]]
                if unknown or not layers:
                    refused.append(f"layers {unknown or '(none)'} are not registered in {SECTOR}")
                    continue
            else:
                layers = list(chain["member_of"].get(candidate["entity"], []))   # D2b: a member's own major part
            out += [(wf, ProjectionScope(kind="layer", id=k)) for k in layers for wf in rule["workflow_ids"]]
    return out, refused


def _covered(entity: str) -> bool:
    try:
        from ..config import is_pead_target

        return bool(is_pead_target(entity))
    except Exception:  # noqa: BLE001 - without the roster only chain members are routable
        return False


def dedupe_key(at: datetime, workflow_id: str, scope: ProjectionScope) -> str:
    """One downstream run per (task, scope, UTC day) and tick — every qualifying component of the tick rides on it.

    Was (cluster, task, scope, day) until the 2026-11-16 replay: two clusters touching L5 in one tick, or two
    components of one cluster, each started their own L5 review (11-09 13:05: L5 four times, NVDA twice).
    """
    return f"material:{workflow_id}:{scope.kind}:{scope.id}:{at.astimezone(timezone.utc).date().isoformat()}"


def component_version(candidate: Mapping[str, Any]) -> str:
    return f"{candidate['materiality']}:{candidate['fingerprint']}"


def _ledger(path: str | None):
    from .store import default_workflow_db_path

    conn = sqlite3.connect(path or default_workflow_db_path(), timeout=30)
    conn.execute(_LEDGER_DDL)
    return conn


def record(path: str | None, *, at: datetime, candidate: Mapping[str, Any], workflow_id: str, scope: str,
           key: str, version: str, outcome: str, reason: str = "", trigger_key: str = "", run_id: str = "") -> None:
    with closing(_ledger(path)) as conn:
        conn.execute("INSERT INTO material_event_ledger (at,brief_projection_id,component_id,cluster_key,"
                     "materiality,novelty,workflow_id,scope,dedupe_key,event_version,outcome,reason,trigger_key,"
                     "run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (at.isoformat(timespec="seconds"), candidate["brief"], candidate["component_id"],
                      candidate["cluster_key"], candidate["materiality"], candidate["novelty"], workflow_id, scope,
                      key, version, outcome, reason, trigger_key, run_id))
        conn.commit()


def ledger_rows(path: str | None = None, *, component_id: str = "", cluster_key: str = "",
                limit: int = 200) -> list[dict[str, Any]]:
    with closing(_ledger(path)) as conn:
        conn.row_factory = sqlite3.Row
        sql, args = "SELECT * FROM material_event_ledger WHERE 1=1", []
        if component_id:
            sql, args = sql + " AND component_id=?", [*args, component_id]
        if cluster_key:
            sql, args = sql + " AND cluster_key=?", [*args, cluster_key]
        return [dict(r) for r in conn.execute(sql + " ORDER BY seq DESC LIMIT ?", (*args, limit))]


def _already(path: str | None, key: str, version: str) -> bool:
    with closing(_ledger(path)) as conn:
        return conn.execute("SELECT 1 FROM material_event_ledger WHERE dedupe_key=? AND event_version=? AND "
                            "outcome IN ('triggered','deduplicated','rejected') LIMIT 1", (key, version)).fetchone() is not None


def _task_inputs(workflow_id: str, members: list[Mapping[str, Any]], scope: ProjectionScope) -> dict[str, Any]:
    if workflow_id == "fundamental-material":
        return {workflow_id: {"component_refs": [f"{m['brief']}#{m['component_id']}" for m in members]},
                "trigger": "material_development"}
    if workflow_id == "layer-review":
        return {workflow_id: {"sector": SECTOR}, "trigger": "material_development"}
    return {"trigger": "material_development"}


def dispatch_material_developments(*, store=None, now: datetime | None = None,
                                   config_dir: str | Path | None = None, ledger_path: str | None = None,
                                   runner: Callable[..., dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """One consume tick. Returns what was decided per (component, workflow, scope)."""
    from .run_contracts import TriggerContext

    conf = settings(config_dir)
    if not conf["enabled"]:
        return []
    now = now or datetime.now(timezone.utc)
    if store is None:
        from ..memory import get_store

        store = get_store()
    if runner is None:
        from .ownership import run_owned_workflow as runner
    routes = load_routes(config_dir)
    chain = chain_index()
    then = {rule["then"] for rule in routes if rule.get("then")}
    outcomes: list[dict[str, Any]] = []
    layer_runs: list[str] = []

    # 1) Resolve every candidate; components already on the ledger (a rescanned brief) are skipped, follow-ups are
    #    recorded as deduplicated, the rest are grouped per (task, scope, day).
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for cand in candidates(store, now=now, conf=conf):
        pairs, refused = targets(cand, routes, chain)
        for reason in refused:
            # Once per (component, reason): the same brief is rescanned every tick for `lookback_hours`, and the
            # 2026-11-24 ledger repeated the same five refusals every 30 minutes.
            key, version = f"rejected:{cand['brief']}#{cand['component_id']}", reason
            if _already(ledger_path, key, version):
                continue
            record(ledger_path, at=now, candidate=cand, workflow_id="", scope="", key=key, version=version,
                   outcome="rejected", reason=reason)
            outcomes.append({"component": cand["component_id"], "outcome": "rejected", "reason": reason})
        for workflow_id, scope in pairs:
            key, version = dedupe_key(cand["at"], workflow_id, scope), component_version(cand)
            if _already(ledger_path, key, version):
                continue
            if cand["novelty"] not in ("first", "escalation"):
                record(ledger_path, at=now, candidate=cand, workflow_id=workflow_id, scope=scope.key, key=key,
                       version=version, outcome="deduplicated", reason=f"novelty={cand['novelty']}")
                outcomes.append({"component": cand["component_id"], "workflow_id": workflow_id,
                                 "scope": scope.key, "key": key, "outcome": "deduplicated"})
                continue
            group = groups.setdefault((workflow_id, scope.key, key), {"scope": scope, "members": []})
            if all(component_version(m) != version for m in group["members"]):
                group["members"].append(cand)

    # 2) One run per group. Its trigger version is the set of member components, so the same set is a no-op in the
    #    trigger ledger while a new component or an escalation later the same day starts another run.
    for (workflow_id, _scope_key, key), group in groups.items():
        scope, members = group["scope"], group["members"]
        version = hashlib.sha256(json.dumps(sorted(component_version(m) for m in members)).encode()).hexdigest()[:16]
        ctx = TriggerContext(kind="event", workflow_id=workflow_id, event_id=key, event_version=version)
        base = {"workflow_id": workflow_id, "scope": scope.key, "key": key,
                "components": [m["component_id"] for m in members]}
        try:
            result = runner(workflow_id, scope=scope, trigger=ctx,
                            request={"requested_tasks": [workflow_id], "enter_decision_cycle": False,
                                     "task_inputs": _task_inputs(workflow_id, members, scope),
                                     "as_of": now.isoformat()},
                            now=now, config_dir=config_dir)
        except Exception as exc:  # noqa: BLE001 - one route must not stop the others
            log.exception("material route %s %s failed", workflow_id, scope.key)
            for m in members:
                record(ledger_path, at=now, candidate=m, workflow_id=workflow_id, scope=scope.key, key=key,
                       version=component_version(m), outcome="failed", reason=f"{type(exc).__name__}: {exc}"[:300])
            outcomes.append({**base, "outcome": "failed"})
            continue
        status = str(result.get("status") or "")
        outcome = ("deduplicated" if result.get("duplicate") else
                   "triggered" if status in ("complete", "incomplete") else status or "unknown")
        for m in members:
            record(ledger_path, at=now, candidate=m, workflow_id=workflow_id, scope=scope.key, key=key,
                   version=component_version(m), outcome=outcome, reason=str(result.get("reason") or ""),
                   trigger_key=str(result.get("trigger_key") or ""), run_id=str(result.get("run_id") or ""))
        outcomes.append({**base, "outcome": outcome})
        if workflow_id == "layer-review" and outcome == "triggered":
            layer_runs.append(str(result.get("trigger_key") or key))

    for workflow_id in sorted(then):
        if not layer_runs:
            break
        day = now.astimezone(timezone.utc).date().isoformat()
        version = hashlib.sha256(json.dumps(sorted(layer_runs)).encode()).hexdigest()[:16]
        ctx = TriggerContext(kind="event", workflow_id=workflow_id, event_id=f"material:{workflow_id}:{SECTOR}:{day}",
                             event_version=version)
        try:
            result = runner(workflow_id, scope=ProjectionScope(kind="sector", id=SECTOR), trigger=ctx,
                            request={"requested_tasks": [workflow_id], "enter_decision_cycle": False,
                                     "task_inputs": {workflow_id: {"trigger_source": "material_development"},
                                                     "trigger": "material_development"},
                                     "as_of": now.isoformat()},
                            now=now, config_dir=config_dir)
            outcomes.append({"workflow_id": workflow_id, "scope": f"sector:{SECTOR}",
                             "outcome": "triggered" if not result.get("duplicate") else "deduplicated",
                             "after": sorted(layer_runs)})
        except Exception as exc:  # noqa: BLE001
            log.exception("material route %s failed", workflow_id)
            outcomes.append({"workflow_id": workflow_id, "outcome": "failed", "reason": str(exc)[:300]})
    if outcomes:
        log.info("material_development: %s", outcomes)
    return outcomes


__all__ = ["RouteError", "candidates", "dispatch_material_developments", "ledger_rows", "load_routes", "targets"]
