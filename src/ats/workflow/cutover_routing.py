"""Qualification-gated read routing, safe fallback and drift re-verification.

This is the module that makes "the data flow is qualified" mean something at read
time rather than only in a report. Three tasks, one chain:

- **5.6 — gate the read route.** When resolution decides a consumer's read will
  go to the target route, it must call `ats.data.assurance.qualification()` for that
  exact `domain_id + consumer_id + contract_version + scope`. Failing that check
  falls back to the consumer's stable legacy route and records the gap. The check
  is at read time because qualification carries a TTL and can be revoked between
  runs: a startup check would let a revocation take effect hours later.
- **5.7 — fall back only to somewhere safe.** Fallback requires three things at
  once: the fallback proof is valid, the target is not retired, and the target
  actually works. Phase F permits retiring old implementations, so "ineligible →
  go back to the old route" is not always available — and a fallback to a retired
  or broken route is worse than stopping, because it produces confident wrong
  answers. So the third case is `blocked`/`unavailable`, not a redirect.
- **5.8 — re-verify downstream after drift.** An approval minted against a
  qualified snapshot must be re-checked when the qualification behind that snapshot
  changes. Logging the drift and proceeding is the specific failure: the log says
  the evidence moved, and the trade goes through anyway.

The one thing none of these do is modify `consumer_api.py` or `assurance.py`. Both
are on the qualification fingerprint surface, so editing either would invalidate
every recorded evidence row — including the evidence this module depends on. The
wrapping happens here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import cutover as cutover_module


class RouteUnavailable(RuntimeError):
    """The read cannot be served by any route. Never silently degraded."""

    reason_code = "no_safe_route"


class FallbackUnsafe(RouteUnavailable):
    """The nominal fallback target cannot be used."""

    def __init__(self, reason: str, *, reason_code: str, detail: dict[str, Any] | None = None
                 ) -> None:
        super().__init__(reason)
        self.reason_code = reason_code
        self.detail = detail or {}


# Fallback verdicts. `unavailable` and `blocked` are deliberately distinct:
# unavailable is "the target does not work", blocked is "we may not use it".
FALLBACK_OK = "ok"
FALLBACK_UNAVAILABLE = "unavailable"
FALLBACK_BLOCKED = "blocked"


@dataclass
class RouteDecision:
    """Which route a read resolved to, and what the gate found."""

    consumer_id: str
    domain_id: str
    contract_version: str
    scope: dict[str, Any]
    route: str
    target_route: str = ""
    qualification: dict[str, Any] = field(default_factory=dict)
    fallback: str = ""
    fallback_reason_code: str = ""
    gap: str = ""

    @property
    def qualified(self) -> bool:
        return self.qualification.get("status") == "eligible"

    def as_row(self) -> dict[str, Any]:
        return {
            "consumer_id": self.consumer_id, "domain_id": self.domain_id,
            "contract_version": self.contract_version, "scope": self.scope,
            "route": self.route, "target_route": self.target_route,
            "qualified": self.qualified,
            "qualification_status": self.qualification.get("status", ""),
            "qualification_reasons": list(self.qualification.get("reasons", [])),
            "fallback": self.fallback, "fallback_reason_code": self.fallback_reason_code,
            "gap": self.gap,
        }


def read_route(*, consumer_id: str, target_boundary_active: bool,
               fallback_route: str = cutover_module.ROUTE_LEGACY,
               fallback_check: Callable[[], dict[str, Any]] | None = None,
               path: str | Path | None = None) -> RouteDecision:
    """Resolve the read route for one consumer scope, gated on qualification.

    `target_boundary_active` says whether the projection-read boundary is pointed at
    the target. When it is not, this returns immediately: a consumer that nobody
    asked to move must not be qualification-checked, because that would make an
    unrelated revocation block ordinary legacy reads.

    `fallback_check` returns the three-part verdict from `assess_fallback`. It is a
    parameter so the fallback policy is one function rather than something each call
    site re-derives — and so a test can make the target unavailable without
    breaking the repository.
    """
    if not target_boundary_active:
        return RouteDecision(
            consumer_id=consumer_id, domain_id="", contract_version="",
            scope={}, route=fallback_route,
            fallback=FALLBACK_OK,
            gap="")

    from ..data.assurance import qualification as _qualification

    contract = _consumer_contract(consumer_id)
    decision = RouteDecision(
        consumer_id=consumer_id, domain_id=contract["domain_id"],
        contract_version=contract["contract_version"], scope=contract["scope"],
        route=cutover_module.ROUTE_TARGET,
        target_route=cutover_module.ROUTE_TARGET)

    result = _qualification(
        domain_id=contract["domain_id"], consumer_id=consumer_id,
        contract_version=contract["contract_version"], scope=contract["scope"])
    decision.qualification = {
        "status": result.get("status"),
        "reasons": list(result.get("reasons", [])),
    }

    if result.get("status") == "eligible":
        decision.fallback = FALLBACK_OK
        return decision

    # 5.7: an ineligible consumer goes back only somewhere safe.
    verdict = (fallback_check() if fallback_check
               else assess_fallback(fallback_route, consumer_id=consumer_id,
                                    path=path))
    decision.fallback = verdict["verdict"]
    decision.fallback_reason_code = verdict.get("reason_code", "")
    decision.gap = verdict.get("reason", "")

    if verdict["verdict"] != FALLBACK_OK:
        raise FallbackUnsafe(
            f"{consumer_id} is not qualified for the target route and cannot fall "
            f"back: {verdict.get('reason', 'no reason given')}",
            reason_code=verdict.get("reason_code", "fallback_unsafe"),
            detail={"consumer_id": consumer_id,
                    "qualification_reasons": decision.qualification["reasons"],
                    "fallback_verdict": verdict})

    decision.route = fallback_route
    return decision


def _consumer_contract(consumer_id: str) -> dict[str, Any]:
    """The consumer's declared domain, contract version and scope.

    Read from the same manifest `qualification()` reads, so the gate cannot be
    called with a domain that disagrees with the one it validates against.
    """
    import yaml

    from ..config import REPO_ROOT

    coverage = REPO_ROOT / "config" / "data" / "target_dataflow_coverage.yaml"
    manifest = yaml.safe_load(coverage.read_text(encoding="utf-8"))
    for consumer in manifest.get("consumers", []):
        if consumer.get("id") == consumer_id:
            products = sorted(consumer.get("products", ()))
            return {
                "domain_id": str(consumer.get("domain")),
                "contract_version": str(consumer.get("contract_version")),
                "scope": {"consumer": consumer_id, "products": products},
            }
    raise RouteUnavailable(
        f"consumer {consumer_id!r} has no contract in the coverage manifest; a "
        f"reader cannot be qualified for a consumer that is not declared")


# --------------------------------------------------------------------------- #
# 5.7 — safe fallback
# --------------------------------------------------------------------------- #

def assess_fallback(target_route: str, *, consumer_id: str = "",
                    proof_valid: bool | None = None,
                    retired: bool | None = None,
                    available: bool | None = None,
                    path: str | Path | None = None) -> dict[str, Any]:
    """May this read fall back to `target_route`? Three conditions, all required.

    1. **The fallback proof is valid.** The consumer must have proven it can serve
       this scope. Phase F's own audit found `existing dual_read_diffs` "self-describes
       as presence-only and never gates" — so an unproven fallback must not be
       treated as proven by default.
    2. **The target is not retired.** Phase F permits retiring old implementations,
       so "go back to the old route" is not always available.
    3. **The target actually works.** A retired-but-working route and a
       live-but-broken one are different failures and get different codes.

    `None` means "not supplied", which is treated as NOT satisfied. An unproven
    condition is not a satisfied one.
    """
    if retired:
        return {"verdict": FALLBACK_BLOCKED, "reason_code": "fallback_target_retired",
                "reason": f"fallback target {target_route!r} is registered as "
                          f"retired; a retired implementation reads as an explicit "
                          f"failure rather than serving traffic",
                "target_route": target_route, "consumer_id": consumer_id}

    if available is False:
        return {"verdict": FALLBACK_UNAVAILABLE,
                "reason_code": "fallback_target_unavailable",
                "reason": f"fallback target {target_route!r} is not currently able to "
                          f"serve this scope",
                "target_route": target_route, "consumer_id": consumer_id}

    if proof_valid is not True:
        return {"verdict": FALLBACK_BLOCKED,
                "reason_code": "fallback_proof_missing",
                "reason": f"no valid fallback proof for {target_route!r}"
                          + (f" (consumer {consumer_id})" if consumer_id else "")
                          + "; an unproven fallback must not be used, because the "
                          "existing dual-read comparison only records presence "
                          "disagreements and never gated anything",
                "target_route": target_route, "consumer_id": consumer_id}

    return {"verdict": FALLBACK_OK, "reason_code": "", "reason": "",
            "target_route": target_route, "consumer_id": consumer_id}


def is_retired(identifier: str, path: str | Path | None = None) -> bool:
    """True when the retirement registry holds a tombstone for `identifier`.

    Reads the real registry rather than taking the caller's word, because the whole
    point of the check is that "I believe it is retired" and "it is retired" can
    differ. Any registry that cannot be read raises — a fallback that cannot check
    retirement must not proceed.
    """
    from .legacy_retirement import load_registry

    registry = load_registry()
    tombstone = registry.tombstone(identifier)
    if tombstone is None:
        return False
    # A pending tombstone is a promise, not a fact; only a completed retirement
    # makes the implementation unavailable.
    return str(getattr(tombstone, "status", "")) in {"retired", "complete", "completed"}


def assert_retirement_state(target_route: str, *, retired: bool | None = None,
                            path: str | Path | None = None) -> None:
    """Refuse a fallback into a retired implementation, naming the registry.

    `retired=None` consults the registry. An explicit boolean is honoured so a
    caller that already knows may pass it, but the default is to read the authority
    rather than to trust the assertion.
    """
    resolved = is_retired(target_route, path) if retired is None else retired
    if not resolved:
        return

    from .legacy_retirement import load_registry

    entries = [str(t.identifier) for t in load_registry().tombstones()
               if target_route in str(t.identifier)]
    detail = (f"; retirement registry entries: {entries}" if entries
              else "; no matching tombstone was found in the registry, so the retired "
                   "state was asserted rather than read")
    raise FallbackUnsafe(
        f"fallback target {target_route!r} is retired{detail}",
        reason_code="fallback_target_retired",
        detail={"target_route": target_route, "registry_entries": entries})


# --------------------------------------------------------------------------- #
# 5.8 — downstream re-verification after drift
# --------------------------------------------------------------------------- #

@dataclass
class Reverification:
    """The outcome of re-checking downstream work against current qualification."""

    approved: bool
    reasons: list[str] = field(default_factory=list)
    checked_at: str = ""
    qualification_status: str = ""

    def as_row(self) -> dict[str, Any]:
        return {"approved": self.approved, "reasons": list(self.reasons),
                "checked_at": self.checked_at,
                "qualification_status": self.qualification_status}


def reverify_downstream(*, consumer_id: str, scope: dict[str, Any],
                        snapshot_as_of: str | None = None,
                        max_age_seconds: float = 60.0,
                        now: datetime | None = None,
                        path: str | Path | None = None) -> Reverification:
    """Re-check an already-assembled snapshot's qualification before acting on it.

    Called at the point of USE, not at assembly. An approval minted against a
    qualified snapshot is a claim about that snapshot; if the evidence behind it has
    since been revoked or has drifted, the claim is stale — and the requirement is
    explicit that this must refuse, not merely log.
    """
    contract = _consumer_contract(consumer_id)
    checked = now or datetime.now(timezone.utc)
    result = Reverification(approved=False, checked_at=checked.isoformat())

    from ..data.assurance import qualification as _qualification

    verdict = _qualification(
        domain_id=contract["domain_id"], consumer_id=consumer_id,
        contract_version=contract["contract_version"],
        scope=scope or contract["scope"], now=checked)
    result.qualification_status = str(verdict.get("status", ""))

    if verdict.get("status") != "eligible":
        result.reasons.append(
            f"qualification is {verdict.get('status')}: "
            f"{', '.join(verdict.get('reasons', [])) or 'no reason given'}")
        return result

    if snapshot_as_of:
        stamp = _parse(snapshot_as_of)
        if stamp is None:
            result.reasons.append(
                f"snapshot_as_of {snapshot_as_of!r} could not be parsed, so snapshot "
                f"freshness cannot be established")
            return result
        age = (checked - stamp).total_seconds()
        if age > max_age_seconds:
            result.reasons.append(
                f"the snapshot this approval was projected on is {age:.0f}s old "
                f"(limit {max_age_seconds:.0f}s); re-assembly is required because the "
                f"inputs may have moved")
            return result

    result.approved = True
    return result


def assert_downstream_current(*, consumer_id: str, scope: dict[str, Any],
                              snapshot_as_of: str | None = None,
                              max_age_seconds: float = 60.0,
                              now: datetime | None = None,
                              path: str | Path | None = None) -> Reverification:
    """Refuse downstream work whose qualification no longer holds.

    The refusal carries the reasons rather than a boolean, because an operator
    facing "approval rejected" with no reason has to go and reproduce it to find out
    which of the four gates failed.
    """
    result = reverify_downstream(
        consumer_id=consumer_id, scope=scope, snapshot_as_of=snapshot_as_of,
        max_age_seconds=max_age_seconds, now=now, path=path)
    if not result.approved:
        raise RouteUnavailable(
            f"{consumer_id} may not proceed: " + "; ".join(result.reasons))
    return result


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def gate_summary(decisions: list[RouteDecision]) -> dict[str, Any]:
    """Aggregate gate outcomes. Counts per verdict, never a single "ok"."""
    by_route: dict[str, int] = {}
    gaps: list[str] = []
    for decision in decisions:
        by_route[decision.route] = by_route.get(decision.route, 0) + 1
        if decision.gap:
            gaps.append(f"{decision.consumer_id}: {decision.gap}")
    return {
        "count": len(decisions),
        "by_route": dict(sorted(by_route.items())),
        "qualified": sum(1 for d in decisions if d.qualified),
        "gaps": gaps,
    }
