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
    """One required task's projection state inside the snapshot."""

    task_id: str
    agent_role: AgentRole | None
    projection_id: str = ""
    content_hash: str = ""
    as_of: str = ""
    reusable: bool = False
    reason: str = "missing"

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
    """Snapshot every decision-required task's projection state (task 3.1).

    Each item is traceable back to a concrete envelope: `projection_id` and
    `content_hash` are copied from it, and reuse is judged by Phase A's
    `reuse_decision` so the reason vocabulary never forks. `required_scopes`
    names, per task, the scope the task was ASKED about — a decision cycle
    reads projections of many scopes (a layer brief, a name review), so a
    blanket portfolio query would misjudge every one of them; a task without
    an entry is only checked for freshness and publication.
    """
    wanted = required_scopes or {}
    stamp = (at or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    items: list[SnapshotItem] = []
    for task_id in registry.task_ids():
        spec = registry.spec(task_id)
        if not spec.required_for_decision or spec.agent_role is None:
            continue
        envelope = projections.get(task_id)
        if envelope is None:
            items.append(SnapshotItem(task_id=task_id,
                                      agent_role=spec.agent_role))
            continue
        reusable, reason = reuse_decision(
            envelope, scope=wanted.get(task_id, envelope.scope),
            input_refs=input_refs, data_vintage_refs=data_vintage_refs, at=at)
        items.append(SnapshotItem(
            task_id=task_id, agent_role=spec.agent_role,
            projection_id=envelope.projection_id,
            content_hash=envelope.content_hash, as_of=envelope.as_of,
            reusable=reusable, reason=reason))
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
