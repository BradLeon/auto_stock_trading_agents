"""Execution authorization gate (§10.4, tasks 7.1–7.5).

A real order may only be submitted against ONE complete authorization, derived
from the audit store — never constructed by the caller: current revision +
its passed risk review + its valid human approval. The gate re-verifies every
component right before submission; any failure refuses the order.

Like the rest of `execution/`, this module is deterministic (no LLM).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..decision.repository import DecisionAuditRepository

# The §10.4 field set. Every field must be present for the authorization to
# be complete; a partially-filled authorization is not an authorization.
FIELDS = ("cycle_id", "revision_no", "decision_hash", "review_id", "review_at",
          "approval_id", "approval_at", "ruleset_version",
          "portfolio_snapshot_id", "market_as_of")

# Rejection reasons that mean "the world moved on, re-run the review" rather
# than "this authorization is void": a fresh review + fresh approval is the
# remedy, and the graph routes back to the risk gate for exactly these.
RECOVERABLE = frozenset({"snapshot_stale"})


def _round_of(record_id: str) -> int:
    """Parse the round suffix of a round-scoped record id (`...:approval:r3`)."""
    try:
        return int(record_id.rsplit(":r", 1)[-1])
    except (ValueError, IndexError):
        return 0


class AuthorizationError(Exception):
    """The prerequisites for an execution authorization are not all present."""


class ExecutionAuthorization(BaseModel):
    """The ten §10.4 fields binding one order batch to its causal chain."""

    cycle_id: str
    revision_no: int
    decision_hash: str
    review_id: str
    review_at: str
    approval_id: str
    approval_at: str
    ruleset_version: str
    portfolio_snapshot_id: str
    market_as_of: str

    # Phase F 2.5: the trade route and generation this authorization was issued
    # under. Deliberately NOT part of `FIELDS`: that tuple is the §10.4 contract
    # and other code asserts its length, so widening it here would change a
    # published contract rather than extend this one. These two are validated
    # separately, and their absence is a rejection reason rather than a default —
    # an authorization that does not say which authority issued it cannot be
    # checked against one.
    route_id: str = ""
    route_generation: int = 0

    def bound_to_route(self, route_id: str, generation: int) -> "ExecutionAuthorization":
        """A copy bound to a route. Returns self when already bound to it.

        The comparison uses BOTH fields, so after an A→B→A round trip an
        authorization from the first A is distinguishable from one issued by the
        second A — which is the entire reason the generation exists.
        """
        if self.route_id == route_id and int(self.route_generation) == int(generation):
            return self
        return self.model_copy(update={"route_id": route_id,
                                       "route_generation": int(generation)})


def build_authorization(repo: "DecisionAuditRepository",
                        cycle_id: str) -> ExecutionAuthorization:
    """Derive the authorization from storage (design D8).

    Every component is read from the audit tables; if any prerequisite is
    missing — no audit row, no revision, no PASSED review bound to the current
    revision, no approval bound to the current revision — construction fails
    with the full list of gaps. Callers cannot fill gaps in.
    """
    missing: list[str] = []
    cycle = repo.get_cycle(cycle_id)
    if cycle is None:
        raise AuthorizationError(f"no audit row for cycle {cycle_id!r}")
    rev = repo.latest_revision(cycle_id)
    if rev is None:
        raise AuthorizationError(f"no revision for cycle {cycle_id!r}")
    review = repo.effective_review(cycle_id, rev["revision_no"],
                                   rev["decision_hash"])
    if review is None or review["verdict"] != "approved":
        missing.append("passed risk review bound to the current revision")
    elif not all((review["ruleset_version"], review["portfolio_snapshot_id"],
                  review["market_as_of"])):
        # Phase D 6.4: a review without its three bindings (portfolio snapshot
        # id, market as-of, ruleset version) cannot be traced to the world it
        # judged — it is NOT usable to release an order, fail closed.
        missing.append("risk review missing basis bindings "
                       "(ruleset_version / portfolio_snapshot_id / market_as_of)")
    approval = repo.effective_approval(cycle_id, rev["revision_no"],
                                       rev["decision_hash"])
    if approval is None:
        missing.append("human approval bound to the current revision")
    elif review is not None and (
            _round_of(approval["approval_id"]) < _round_of(review["review_id"])):
        # Task 7.6: an approval from an EARLIER review round is the OLD blessing
        # (e.g. given before a stale-snapshot re-review) — never reused. Rounds
        # are parsed from the ids, so backfilled timestamps cannot fake this.
        missing.append("human approval obtained AFTER the effective review")
    if missing:
        raise AuthorizationError(
            f"cannot authorize {cycle_id!r}: " + "; ".join(missing))
    return ExecutionAuthorization(
        cycle_id=cycle_id, revision_no=rev["revision_no"],
        decision_hash=rev["decision_hash"], review_id=review["review_id"],
        review_at=review["created_at"], approval_id=approval["approval_id"],
        approval_at=approval["created_at"],
        ruleset_version=review["ruleset_version"],
        portfolio_snapshot_id=review["portfolio_snapshot_id"],
        market_as_of=review["market_as_of"])


def validate_authorization(repo: "DecisionAuditRepository", auth: ExecutionAuthorization,
                           *, snapshot_as_of: datetime | None = None,
                           max_snapshot_age_seconds: float = 60.0,
                           now: datetime | None = None,
                           route_state: Any = None) -> list[str]:
    """Pre-submission re-verification (task 7.3). Returns rejection reasons.

    Checks, in order: completeness of all ten fields, hash + revision match
    against the CURRENT revision, review and approval still effective, the
    portfolio snapshot still fresh (7.5), and — Phase F 2.5 — that the
    authorization is bound to the currently active route generation.

    `route_state` is the `RouteState` to check against; pass None to skip only the
    route check, which is what a caller with no registry should NOT do. Omitting it
    leaves an unbound authorization rejected via `route_unbound`, so the
    fail-closed default does not depend on the caller passing anything.
    Empty list = the authorization may proceed.
    """
    reasons: list[str] = []
    empty = [name for name in FIELDS
             if not getattr(auth, name) and getattr(auth, name) != 0]
    if empty:
        reasons.append(f"authorization_incomplete: missing {', '.join(empty)}")
        return reasons                       # cannot verify anything else meaningfully
    rev = repo.latest_revision(auth.cycle_id)
    if rev is None or rev["decision_hash"] != auth.decision_hash:
        reasons.append("hash_mismatch: authorization does not bind the current revision")
    elif rev["revision_no"] != auth.revision_no:
        reasons.append("stale_revision: a newer revision exists")
    if rev is not None:
        # Effectiveness is judged against the CURRENT revision: an authorization
        # citing an older one is already dead via hash_mismatch/stale_revision,
        # and the current revision must carry its own review + approval.
        if repo.effective_review(auth.cycle_id, rev["revision_no"],
                                 rev["decision_hash"]) is None:
            reasons.append("review_not_effective")
        if repo.effective_approval(auth.cycle_id, rev["revision_no"],
                                   rev["decision_hash"]) is None:
            reasons.append("approval_not_effective")
    if snapshot_as_of is None:
        reasons.append("missing_portfolio_snapshot")
    else:
        now = now or datetime.now(timezone.utc)
        age = (now - snapshot_as_of).total_seconds()
        if age > max_snapshot_age_seconds:
            reasons.append(f"snapshot_stale: portfolio snapshot is {age:.0f}s old "
                           f"(limit {max_snapshot_age_seconds:.0f}s)")
    # Phase F 2.5: the authorization must name the route it was issued under, and
    # that generation must still be active. Checked last so a complete-but-stale
    # authorization reports the substantive problem (hash, review, approval) first.
    if not auth.route_id or not int(auth.route_generation or 0):
        reasons.append("route_unbound: authorization does not record the trade "
                       "route and generation it was issued under")
    elif route_state is not None:
        if auth.route_id != getattr(route_state, "route_id", ""):
            reasons.append(f"route_mismatch: issued for route {auth.route_id!r}, "
                           f"active route is {getattr(route_state, 'route_id', '')!r}")
        elif int(auth.route_generation) != int(getattr(route_state, "generation", 0)):
            reasons.append(f"route_generation_stale: issued at generation "
                           f"{auth.route_generation}, active generation is "
                           f"{getattr(route_state, 'generation', 0)}")
    return reasons


# --- route binding helpers (Phase F 2.5) -------------------------------------- #

def active_route_state() -> Any:
    """The active `RouteState`, or None when the registry has no route.

    None is the fail-closed input: `validate_authorization` then reports
    `route_unbound` on an authorization that never got bound, rather than the
    caller having to remember to check.
    """
    from .route_registry import try_read_state

    return try_read_state()


def bind_to_active_route(auth: ExecutionAuthorization) -> ExecutionAuthorization:
    """Bind `auth` to the currently active route and generation.

    Returns the authorization unchanged when no route is registered, which leaves
    it unbound — and unbound is a rejection, so an installation that has not
    installed a route yet cannot execute orders.

    Phase F 2.6 step 1: binding IS issuance (it is the moment an authorization
    becomes executable), so it refuses while submissions are frozen and counts
    itself otherwise. Counting here rather than in the signer is deliberate — this
    is the single point every path reaches, including a resumed loop.
    """
    from .route_registry import read_freeze, record_issuance

    freeze = read_freeze()
    if freeze.frozen:
        raise AuthorizationError(
            f"cannot bind an authorization while submissions are frozen for route "
            f"switch {freeze.switch_token!r}; the freeze exists precisely so that "
            f"nothing new becomes executable during a cutover")
    state = active_route_state()
    if state is None:
        return auth
    bound = auth.bound_to_route(state.route_id, state.generation)
    record_issuance(bound.approval_id or bound.cycle_id or bound.decision_hash)
    return bound
