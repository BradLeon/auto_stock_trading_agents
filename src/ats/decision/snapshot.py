"""Research snapshot and decision-cycle creation (§10.1, tasks 3.1–3.5).

A cycle may only open on top of a COMPLETE research snapshot: every task the
registry marks `required_for_decision` has a published, fresh, in-scope
projection. Reuse judgements come from Phase A's `reuse_decision` — the same
reason enumeration downstream roles already speak (`missing` is the one new
state here: no envelope at all).

Two honesty rules fall out of the spec:
- An incomplete snapshot REFUSES to create the cycle — a gap report at best,
  never an order (§10.1; automatic trading stays blocked).
- A snapshot that goes stale MID-cycle supersedes the cycle instead of silently
  replacing its inputs; the new inputs can only open a NEW cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from ..agent.task_projection import (AgentRole, ProjectionScope,
                                     TaskProjectionEnvelope, reuse_decision)
from ..workflow.run_contracts import TaskRegistry
from .repository import DecisionAuditRepository
from .state import CycleStatus

class IncompleteResearchSnapshotError(Exception):
    """A required projection is missing or stale; the cycle must not open."""

    def __init__(self, snapshot: "ResearchSnapshot") -> None:
        self.snapshot = snapshot
        gaps = ", ".join(f"{item.task_id}:{item.reason}"
                         for item in snapshot.gaps())
        super().__init__(
            f"research snapshot incomplete ({gaps}); decision cycle blocked")


@dataclass
class SnapshotItem:
    """One decision-required category's projection state inside the snapshot.

    Satisfaction is judged per CATEGORY, not per task id (Phase D task 1.4):
    `task_id` names the task whose envelope satisfied the category — for a
    category no task satisfied, it names the representative task id (or the
    category itself when several tasks could have satisfied it).
    """

    task_id: str
    agent_role: AgentRole | None
    category: str = ""
    projection_id: str = ""
    content_hash: str = ""
    as_of: str = ""
    reusable: bool = False
    reason: str = "missing"
    scope_kind: str = ""
    scope_id: str = ""

    @property
    def freshness(self) -> str:
        """`fresh` / `stale` / `missing` — the coarse state the gate speaks."""
        if self.reusable:
            return "fresh"
        return "missing" if self.reason == "missing" else "stale"


@dataclass
class ResearchSnapshot:
    """The frozen input basis of one decision cycle."""

    scope: ProjectionScope
    built_at: str
    items: list[SnapshotItem] = field(default_factory=list)

    def gaps(self) -> list[SnapshotItem]:
        return [item for item in self.items if not item.reusable]

    @property
    def complete(self) -> bool:
        return not self.gaps()

    def to_payload(self) -> dict[str, Any]:
        """JSON-serializable form stored on `decision_cycles.research_snapshot`."""
        return {
            "scope": self.scope.key,
            "built_at": self.built_at,
            "items": [vars(item) for item in self.items],
        }


def _category_task_ids(registry: TaskRegistry) -> list[tuple[str, list[str], list[str]]]:
    """(category, satisfying task ids, unmapped fallback roles) for one build.

    Categories come from `DECISION_CATEGORY_ROLES`; a registry-required task
    whose role is not mapped anywhere forms its own ad-hoc category so an
    unexpected role can never silently escape the gate.
    """
    from ..workflow.run_contracts import ROLE_TO_CATEGORY

    mapped: dict[str, list[str]] = {}
    unmapped: dict[str, list[str]] = {}
    for task_id in registry.task_ids():
        spec = registry.spec(task_id)
        if not spec.required_for_decision or spec.agent_role is None:
            continue
        category = ROLE_TO_CATEGORY.get(spec.agent_role)
        if category is None:
            unmapped.setdefault(spec.agent_role, []).append(task_id)
        else:
            mapped.setdefault(category, []).append(task_id)
    out: list[tuple[str, list[str], list[str]]] = []
    for category in mapped:
        out.append((category, mapped[category], []))
    for role, task_ids in unmapped.items():
        out.append((role, [], task_ids))
    # Stable, registry-order-independent: sort by category name.
    out.sort(key=lambda entry: entry[0])
    return out


def build_research_snapshot(
    *,
    registry: TaskRegistry,
    projections: Mapping[str, TaskProjectionEnvelope | None],
    scope: ProjectionScope,
    required_scopes: Mapping[str, ProjectionScope] | None = None,
    input_refs: Any = None,
    data_vintage_refs: Any = None,
    at: datetime | None = None,
) -> ResearchSnapshot:
    """Snapshot every decision-required category's projection state (task 3.1).

    Each item is traceable back to a concrete envelope: `projection_id` and
    `content_hash` are copied from it, and reuse is judged by Phase A's
    `reuse_decision` so the reason vocabulary never forks. Satisfaction is per
    category: the fundamental category is satisfied by EITHER mode's task, and
    the hit's task id is what the item records (Phase D task 1.4).

    `required_scopes` names, per task, the scope the task was ASKED about — a
    decision cycle reads projections of many scopes (a layer brief, a name
    review), so a blanket portfolio query would misjudge every one of them; a
    task without an entry is only checked for freshness and publication.
    """
    wanted = required_scopes or {}
    stamp = (at or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    items: list[SnapshotItem] = []

    for category, task_ids, fallback_task_ids in _category_task_ids(registry):
        # A category no registered task can satisfy (unmapped role) is a gap
        # right away — there is nothing that could ever fill it.
        candidates: list[str] = task_ids or fallback_task_ids
        if not candidates:
            continue
        best: SnapshotItem | None = None
        for task_id in candidates:
            spec = registry.spec(task_id)
            envelope = projections.get(task_id)
            if envelope is None:
                item = SnapshotItem(task_id=task_id, agent_role=spec.agent_role,
                                    category=category)
            else:
                query_scope = wanted.get(task_id, envelope.scope)
                reusable, reason = reuse_decision(
                    envelope, scope=query_scope,
                    input_refs=input_refs, data_vintage_refs=data_vintage_refs,
                    at=at)
                item = SnapshotItem(
                    task_id=task_id, agent_role=spec.agent_role, category=category,
                    projection_id=envelope.projection_id,
                    content_hash=envelope.content_hash, as_of=envelope.as_of,
                    reusable=reusable, reason=reason,
                    scope_kind=query_scope.kind, scope_id=query_scope.id)
            if item.reusable:
                best = item
                break
            # Prefer the most informative failure: an envelope that failed reuse
            # (stale/scope) beats "we never got one".
            if best is None or (best.projection_id == "" and item.projection_id):
                best = item
        if best is not None:
            items.append(best)

    return ResearchSnapshot(scope=scope, built_at=stamp, items=items)


def open_decision_cycle(
    repo: DecisionAuditRepository, *, cycle_id: str, trigger_source: str,
    trigger_id: str = "", registry: TaskRegistry,
    projections: Mapping[str, TaskProjectionEnvelope | None],
    scope: ProjectionScope, required_scopes: Mapping[str, ProjectionScope] | None = None,
    input_refs: Any = None,
    data_vintage_refs: Any = None, created_at: str | None = None,
) -> tuple[Any, ResearchSnapshot]:
    """Build the snapshot, refuse if incomplete, else create the cycle (3.2).

    Nothing is written when the snapshot is incomplete: the refusal happens
    before `create_cycle`, so no revision, review or order can exist for a
    cycle whose inputs were never complete.
    """
    snapshot = build_research_snapshot(
        registry=registry, projections=projections, scope=scope,
        required_scopes=required_scopes, input_refs=input_refs,
        data_vintage_refs=data_vintage_refs)
    if not snapshot.complete:
        raise IncompleteResearchSnapshotError(snapshot)
    cycle = repo.create_cycle(
        cycle_id=cycle_id, trigger_source=trigger_source, trigger_id=trigger_id,
        research_snapshot=snapshot.to_payload(), created_at=created_at)
    return cycle, snapshot


def supersede_cycle(repo: DecisionAuditRepository, cycle_id: str, *,
                    reason: str, actor: str = "system",
                    created_at: str | None = None) -> bool:
    """Expire the cycle's input basis: cycle → SUPERSEDED with the reason (3.3).

    The cycle keeps its original snapshot — superseding never swaps the inputs.
    Work continues on a NEW cycle id with the new inputs.
    """
    event, changed = repo.transition(
        cycle_id, to_status=CycleStatus.SUPERSEDED, actor=actor,
        payload={"reason": reason}, created_at=created_at)
    if changed:
        repo.conn.execute(
            "UPDATE decision_cycles SET final_outcome = ?, superseded_reason = ?, "
            "updated_at = ? WHERE cycle_id = ?",
            ("superseded", reason, created_at or event["created_at"], cycle_id))
        repo.conn.commit()
    return changed


def record_no_action(repo: DecisionAuditRepository, cycle_id: str, *,
                     reason: str, actor: str = "chief",
                     created_at: str | None = None) -> bool:
    """No Action is a formal terminal outcome WITH a reason (3.4).

    Terminal by construction (`CycleStatus.NO_ACTION`), so the graph can never
    route it into risk review, approval or execution; the reason is the audit
    content of the outcome.
    """
    event, changed = repo.transition(
        cycle_id, to_status=CycleStatus.NO_ACTION, actor=actor,
        payload={"reason": reason}, created_at=created_at)
    if changed:
        repo.conn.execute(
            "UPDATE decision_cycles SET final_outcome = ?, updated_at = ? "
            "WHERE cycle_id = ?",
            (reason, created_at or event["created_at"], cycle_id))
        repo.conn.commit()
    return changed


# --------------------------------------------------------------------------- #
# Frozen snapshots on the graph state (Phase D Group 7, tasks 7.2/7.3/7.7)
# --------------------------------------------------------------------------- #

def frozen_snapshot_items(payload: Mapping[str, Any] | None) -> list[SnapshotItem]:
    """Re-hydrate the SnapshotItems stored on `decision_cycles.research_snapshot`."""
    if not payload:
        return []
    out: list[SnapshotItem] = []
    for raw in payload.get("items", []):
        out.append(SnapshotItem(
            task_id=str(raw.get("task_id", "")),
            agent_role=raw.get("agent_role"),
            category=str(raw.get("category", "")),
            projection_id=str(raw.get("projection_id", "")),
            content_hash=str(raw.get("content_hash", "")),
            as_of=str(raw.get("as_of", "")),
            reusable=bool(raw.get("reusable")),
            reason=str(raw.get("reason", "missing")),
            scope_kind=str(raw.get("scope_kind", "")),
            scope_id=str(raw.get("scope_id", ""))))
    return out


def frozen_snapshot_complete(payload: Mapping[str, Any] | None) -> bool:
    """True only when the frozen payload lists items and every one is reusable.

    An EMPTY payload is never complete — a decide-path cycle must never be
    written from a state that never built a snapshot (task 7.2, fail closed).
    """
    items = frozen_snapshot_items(payload)
    return bool(items) and all(item.reusable for item in items)


def frozen_snapshot_stale_reasons(store: Any, payload: Mapping[str, Any] | None,
                                  *, at: datetime | None = None) -> list[str]:
    """Mid-cycle invalidation checks for a frozen snapshot (task 7.7).

    Three ways a cited input can stop being the input it was, each reported
    separately so the supersede reason names the actual failure:

    * the cited projection row is gone or no longer published (撤销/失败);
    * the cited row's content hash changed in place (被替换);
    * a NEWER published projection for the same role+scope exists — the
      analysis moved on, and if its data vintages differ the inputs are stale
      by vintage change, otherwise by plain supersession.
    """
    from ..agent.task_projection import normalize_refs

    stamp = at or datetime.now(timezone.utc)
    reasons: list[str] = []
    for item in frozen_snapshot_items(payload):
        if not item.reusable or not item.projection_id:
            continue
        where = f"{item.category}[{item.scope_kind}:{item.scope_id}]"
        row = store.get_task_projection(item.projection_id)
        if row is None or row.get("status") != "published":
            reasons.append(f"{where}: 快照引用的投影已撤销或失败 ({item.projection_id})")
            continue
        if row.get("content_hash") != item.content_hash:
            reasons.append(f"{where}: 投影内容与快照不一致（content_hash 漂移）")
            continue
        valid_until = str(row.get("valid_until") or "")
        if valid_until:
            try:
                expiry = datetime.fromisoformat(valid_until)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry <= stamp:
                    reasons.append(f"{where}: 快照引用的投影已过期 ({valid_until})")
                    continue
            except ValueError:
                pass
        newer = store.task_projection_envelopes(
            agent_role=row.get("agent_role"), scope_kind=item.scope_kind,
            scope_id=item.scope_id, limit=1)
        if newer and newer[0].get("projection_id") != item.projection_id:
            if (normalize_refs(newer[0].get("data_vintage_refs"))
                    != normalize_refs(row.get("data_vintage_refs"))):
                reasons.append(f"{where}: 关键数据 vintage 变化，已出现新版本分析")
            else:
                reasons.append(f"{where}: 投影被新版本替换 ({newer[0]['projection_id']})")
    return reasons
