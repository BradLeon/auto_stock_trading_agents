"""Decision cycle lifecycle states (docs/TARGET_WORKFLOW_DATAFLOW.md §5.1).

One enum, one terminal set. Every table column, graph node and gate that talks
about "which state is the cycle in" must reference these names — a second list
of status strings would drift exactly the way the action vocabulary used to.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class CycleStatus(str, Enum):
    """States of a decision cycle. Members are the §5.1 enumeration, verbatim."""

    DRAFT = "draft"                          # 草案：cycle 已建，尚无修订
    PENDING_RISK = "pending_risk"            # 待风控：修订已提交，等待确定性审查
    RISK_REJECTED = "risk_rejected"          # 已驳回：当前修订被驳回，可修订后重审
    PENDING_APPROVAL = "pending_approval"    # 待审批：风控通过，等待主理人批准
    EXECUTED = "executed"                    # 已执行：授权下的订单已提交
    MANUAL_REVIEW = "manual_review"          # 人工复核：自动循环退出，等人处理
    NO_ACTION = "no_action"                  # 不行动：带理由的正式终态
    APPROVAL_REJECTED = "approval_rejected"  # 审批拒绝：主理人拒绝当前修订
    SUPERSEDED = "superseded"                # 失效：研究快照过期或过程被取代


#: §5.1: once a cycle reaches a terminal state it never returns to an
#: executable one. `RISK_REJECTED` is deliberately NOT terminal — the loop
#: continues through `chief_revise`; `NO_ACTION` is a real outcome, not a gap.
TERMINAL_STATUSES: frozenset[CycleStatus] = frozenset({
    CycleStatus.EXECUTED,
    CycleStatus.MANUAL_REVIEW,
    CycleStatus.NO_ACTION,
    CycleStatus.APPROVAL_REJECTED,
    CycleStatus.SUPERSEDED,
})


def is_terminal(status: "CycleStatus | str") -> bool:
    """True when the cycle can no longer issue any state transition."""
    return CycleStatus(status) in TERMINAL_STATUSES


# --- Revision provenance ----------------------------------------------------- #
# `revision_source` records how a revision came to exist. It is the honesty
# marker of the migration: a legacy revision with recoverable content is
# `legacy_revision`; one with gaps is `legacy_unknown` and must never execute.
REVISION_SOURCE_CHIEF = "chief"
REVISION_SOURCE_LEGACY = "legacy_revision"
REVISION_SOURCE_LEGACY_UNKNOWN = "legacy_unknown"

#: Revision sources that can never obtain execution authorization.
NON_AUTHORIZABLE_REVISION_SOURCES: frozenset[str] = frozenset({
    REVISION_SOURCE_LEGACY_UNKNOWN,
})


def revision_authorization_blockers(revision: Mapping[str, Any]) -> list[str]:
    """Structural reasons a revision row may not be executed (empty = none).

    This is the enforcement point of the migration honesty rule: an
    `legacy_unknown` revision has no content hash and no recoverable proposal,
    so there is nothing for an authorization to be bound to. Kept here rather
    than inside the execution gate so the store migration, the gate and the
    tests all read one definition.
    """
    blockers: list[str] = []
    source = revision.get("revision_source")
    if source in NON_AUTHORIZABLE_REVISION_SOURCES:
        blockers.append(f"revision_source:{source}")
    if not revision.get("decision_hash"):
        blockers.append("missing_decision_hash")
    return blockers
