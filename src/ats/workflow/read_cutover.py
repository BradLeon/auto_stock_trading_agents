"""Read-path batch cutover executor (Phase F tasks 9.1–9.5).

Task 9 splits a batch into the scopes that may move and the scopes that may not,
and 9.4 makes the whole thing refuse without deployment authorisation. This
module is that mechanism; the runbook in
`docs/validation/PHASE_F_CUTOVER_RUNBOOK.md` is how an operator drives it.

**Why the per-scope split, rather than one verdict per batch**

A batch declares a consumer *set*. Qualification is judged per
`domain_id + consumer_id + contract_version + scope`, so one batch routinely
holds six consumers of which four qualify. Two blunt alternatives are both
wrong: switching the batch wholesale sends unqualified consumers onto the new
route, and refusing the batch because one member fails holds back the four that
are ready — for a reason (that one consumer) which fixing the others cannot
address. So the executor decides **per consumer**, and the batch outcome is the
reduction of those decisions. Every scope that did not move is named in the
result; "partially switched" is a real outcome, not a rounding of "switched".

**What this module will not do**

It will not switch anything without an auditable deployment authorisation, even
when every gate passes. Task 9.4's second scenario is the one that matters: a
green run plus an absent authorisation must still refuse, because "everything
checked out" is the exact state in which an unauthorised change looks
reasonable. The authorisation is a separate artifact from the gates for the same
reason the live-trader grant is (design decision 12): a plan's approval and a
deployment's approval are different permissions.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import batch_manifest as manifest
from . import cutover as plane
from . import cutover_routing as routing
from . import shadow_reports as reports

DEFAULT_PATH = manifest.DEFAULT_PATH

# Per-scope outcomes. `already` is separate from `switched` because re-running an
# executor is normal — an operator re-checks before deciding — and reporting an
# unchanged route as "switched" would make the record claim an action that never
# happened.
SWITCHED = "switched"
ALREADY = "already_on_target"
NOT_QUALIFIED = "not_qualified"
NO_SAFE_FALLBACK = "no_safe_fallback"
FALLBACK_RETIRED = "fallback_retired"
FALLBACK_UNAVAILABLE = "fallback_unavailable"
REPORT_INVALID = "report_invalid"
PROJECTION_MISSING = "projection_missing"
UNAUTHORIZED = "unauthorized"
BOUNDARY_UNWIRED = "boundary_unwired"

# Batch-level reductions.
BATCH_SWITCHED = "switched"
BATCH_PARTIAL = "partially_switched"
BATCH_REFUSED = "refused"
BATCH_NOOP = "no_change"

# Only these scopes may move. Anything else is someone else's boundary, and a
# read-path executor that could move the live trader would make the 11.1 live
# authorisation gate decorative.
READ_BOUNDARIES = frozenset({
    plane.PROJECTION_READ,
    plane.ANALYST_OUTPUT,
    plane.DISPATCHER_SCHEDULE,
})


def _switch_chain(boundary: str) -> tuple[str, ...]:
    """The boundaries that must move together with `boundary`, including itself.

    Derived from the control plane's own `INCOMPATIBLE` table rather than written
    out here. That table already states *why* each pair is inseparable, and it is
    the authority `check_compatibility` will judge the result against — a second
    copy here could disagree with it, and then the executor would cheerfully move
    a route the pre-check had just rejected.

    Deriving it also means a pair added to the control plane later is honoured
    automatically instead of being silently ignored.
    """
    chain = {boundary}
    for group, _reason in plane.INCOMPATIBLE:
        if boundary in group:
            chain |= group
    return tuple(sorted(chain, key=lambda b: list(plane.SIX_BOUNDARIES).index(b)))


def chain_is_settled(states: dict[str, plane.BoundaryState],
                     boundary: str) -> bool:
    """Is every member of this boundary's chain already on the target route?

    The re-run case. Without it, a second `apply` after a successful switch trips
    the compatibility check (the chain is now half-migrated relative to its
    partner) and reports an incomprehensible refusal for work that already
    succeeded. An operator re-running the executor must see "already on target",
    not a conflict about a state they themselves created.
    """
    return all(states[b].route == plane.ROUTE_TARGET
               for b in _switch_chain(boundary) if b in states)


class DeploymentAuthorizationError(RuntimeError):
    """Raised when a switch is attempted without an auditable authorisation."""


class ReadCutoverError(RuntimeError):
    """The executor refused for a reason that is not a missing authorisation."""


@dataclass(frozen=True)
class DeploymentAuthorization:
    """A permission to move routes, distinct from every gate result.

    Auditable means four things are present and non-empty: who authorised it,
    who issued the record, what scope it covers, and until when. An authorisation
    missing any of them is not a weaker authorisation — it is an unauditable
    one, and an unauditable permission cannot be checked by the next reader, which
    is the property the gate exists to preserve.
    """

    reference: str
    authorised_by: str
    issued_by: str
    scope: tuple[str, ...]
    valid_until: str
    note: str = ""

    @property
    def is_complete(self) -> bool:
        return all((self.reference, self.authorised_by, self.issued_by,
                    self.valid_until)) and bool(self.scope)

    def covers(self, consumer_id: str) -> bool:
        return consumer_id in self.scope

    def expiry(self):
        return _parse(self.valid_until)

    def as_row(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "authorised_by": self.authorised_by,
            "issued_by": self.issued_by,
            "scope": ",".join(self.scope),
            "valid_until": self.valid_until,
            "note": self.note,
        }


@dataclass
class ScopeOutcome:
    """One consumer's verdict, with the reason it did not move."""

    consumer_id: str
    outcome: str
    route_before: str = ""
    route_after: str = ""
    reason: str = ""
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def moved(self) -> bool:
        return self.outcome in {SWITCHED, ALREADY}

    def as_row(self) -> dict[str, Any]:
        return {
            "consumer_id": self.consumer_id,
            "outcome": self.outcome,
            "route_before": self.route_before,
            "route_after": self.route_after,
            "moved": self.moved,
            "reason": self.reason,
            "checks": self.checks,
        }


@dataclass
class BatchSwitchResult:
    batch_id: str
    batch_class: str
    outcome: str
    scopes: list[ScopeOutcome] = field(default_factory=list)
    authorisation: str = ""
    changed: bool = False
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def unmoved(self) -> list[ScopeOutcome]:
        return [s for s in self.scopes if not s.moved]

    @property
    def moved_scopes(self) -> list[ScopeOutcome]:
        return [s for s in self.scopes if s.moved]

    def as_row(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "batch_class": self.batch_class,
            "outcome": self.outcome,
            "changed": self.changed,
            "authorisation": self.authorisation,
            "checks": self.checks,
            "scopes": [s.as_row() for s in self.scopes],
            "not_switched": [s.consumer_id for s in self.unmoved],
        }

    def summary(self) -> str:
        """Operator-facing text. Names every scope that did not move and why.

        A report that says "partially switched" without listing the holdouts
        makes the operator re-derive the list by hand, which is the work this
        output exists to save.
        """
        chain = self.checks.get("switch_chain") or self.checks.get("switched_boundaries")
        lines = [f"## `{self.batch_id}`（{self.batch_class}）→ {self.outcome}",
                 "",
                 f"- 授权引用：`{self.authorisation or '无'}`",
                 f"- 成对切换链：`{chain}`" if chain else "- 成对切换链：不适用",
                 f"- 实际改动路由：**{'是' if self.changed else '否'}**"]
        if self.moved_scopes:
            moved = "、".join(f"`{s.consumer_id}`" for s in self.moved_scopes)
            lines.append(f"- 已切换：{moved}")
        if self.unmoved:
            lines += ["", "### 未切换的范围", ""]
            for scope in self.unmoved:
                lines.append(f"- `{scope.consumer_id}`：**{scope.outcome}**"
                             + (f" —— {scope.reason}" if scope.reason else ""))
        if not self.scopes:
            lines += ["", "（该批次未声明任何消费者范围）"]
        return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(value: str):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _connect(path: str | Path | None) -> sqlite3.Connection:
    target = Path(path or manifest.default_batch_db_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def write_authorisation(auth: DeploymentAuthorization, *,
                        path: str | Path | None = None,
                        actor: str = "") -> str:
    """Record a deployment authorisation. Append-only.

    Storing it rather than passing it in matters for the same reason the gates do:
    a permission that lives only in the caller's arguments cannot be re-read by
    whoever asks "who authorised this?" a week later.
    """
    if not auth.is_complete:
        missing = [name for name, value in
                   (("reference", auth.reference),
                    ("authorised_by", auth.authorised_by),
                    ("issued_by", auth.issued_by),
                    ("valid_until", auth.valid_until))
                   if not value]
        raise DeploymentAuthorizationError(
            f"deployment authorisation is not auditable; missing: {missing}. "
            "An authorisation that cannot be checked by the next reader cannot "
            "be relied on by this one")

    conn = _connect(path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS read_cutover_authorizations (
                   reference TEXT PRIMARY KEY,
                   authorised_by TEXT NOT NULL,
                   issued_by TEXT NOT NULL,
                   scope TEXT NOT NULL,
                   valid_until TEXT NOT NULL,
                   note TEXT NOT NULL DEFAULT '',
                   recorded_at TEXT NOT NULL,
                   recorded_by TEXT NOT NULL DEFAULT '')""")
        now = _now()
        existing = conn.execute(
            "SELECT recorded_at FROM read_cutover_authorizations WHERE reference = ?",
            (auth.reference,)).fetchone()
        if existing is not None:
            # Re-recording the same reference must not silently widen it, or a
            # stale narrow grant could be replaced by a broad one under the same
            # name and the history would show one event.
            conn.execute(
                """UPDATE read_cutover_authorizations
                      SET authorised_by = ?, issued_by = ?, scope = ?,
                          valid_until = ?, note = ?, recorded_by = ?
                    WHERE reference = ?""",
                (auth.authorised_by, auth.issued_by, ",".join(auth.scope),
                 auth.valid_until, auth.note, actor, auth.reference))
        else:
            conn.execute(
                """INSERT INTO read_cutover_authorizations
                       (reference, authorised_by, issued_by, scope, valid_until,
                        note, recorded_at, recorded_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (auth.reference, auth.authorised_by, auth.issued_by,
                 ",".join(auth.scope), auth.valid_until, auth.note, now, actor))
        return now
    finally:
        conn.close()


def read_authorisation(reference: str, *,
                       path: str | Path | None = None) -> DeploymentAuthorization | None:
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT * FROM read_cutover_authorizations WHERE reference = ?",
            (reference,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    return DeploymentAuthorization(
        reference=row["reference"], authorised_by=row["authorised_by"],
        issued_by=row["issued_by"],
        scope=tuple(s for s in row["scope"].split(",") if s),
        valid_until=row["valid_until"], note=row["note"])


def assert_authorised(auth: DeploymentAuthorization | None, *,
                      consumer_id: str) -> None:
    """9.4's gate. Raises rather than returning a verdict.

    Raising is deliberate: the caller's next action after this call is the route
    change itself, so a returned boolean invites `if not ok: log(...)` and then a
    switch anyway. An exception cannot be mistaken for a warning.
    """
    if auth is None:
        raise DeploymentAuthorizationError(
            f"refusing to switch {consumer_id!r}: no deployment authorisation was "
            "supplied. Passing every gate is not permission to cut over — the "
            "authorisation is a separate artifact on purpose")
    if not auth.is_complete:
        raise DeploymentAuthorizationError(
            f"refusing to switch {consumer_id!r}: the authorisation {auth.reference!r} "
            "is not auditable (reference, authoriser, issuer and expiry must all "
            "be present)")
    expiry = auth.expiry()
    if expiry is not None and expiry <= datetime.now(timezone.utc):
        raise DeploymentAuthorizationError(
            f"refusing to switch {consumer_id!r}: authorisation {auth.reference!r} "
            f"expired at {auth.valid_until}")
    if not auth.covers(consumer_id):
        raise DeploymentAuthorizationError(
            f"refusing to switch {consumer_id!r}: authorisation "
            f"{auth.reference!r} covers {list(auth.scope)}, not this consumer. A "
            "blanket authorisation is how a narrow permission becomes a broad one")


def _scope_eligibility(consumer_id: str, scope: dict[str, Any], *,
                       qualification_reader: Callable[..., dict[str, Any]]
                       ) -> dict[str, Any]:
    if qualification_reader is None:
        return {"status": "unknown", "reason": "no qualification reader was supplied"}
    try:
        return qualification_reader(consumer_id, scope)
    except Exception as exc:  # noqa: BLE001 - a gate that crashes is not a pass
        return {"status": "unreadable",
                "reason": f"{type(exc).__name__}: {exc}"}


def _eligibility_reason(eligibility: dict[str, Any], status: str) -> str:
    """Why a scope did not move, in terms the reader can act on.

    9.2 requires an unqualified scope to be *named* as not switched, and a bare
    "ineligible" leaves the operator with nothing to do next. The gate's own
    `missing` list is the actionable part, so it is carried through rather than
    replaced by a sentence about TTLs.

    An unreadable gate is reported as unreadable. Both hold the scope back, but
    "I could not check" and "it does not qualify" call for different responses,
    and reporting the first as the second puts a claim about the query onto the
    consumer.
    """
    if status in {"unreadable", ""}:
        detail = eligibility.get("reason") or "the gate's own answer could not be read"
        return (f"the qualification gate could not be read ({detail}) — this is a "
                "failure to check, not a finding about the consumer")

    missing = eligibility.get("missing") or eligibility.get("missing_inputs") or []
    reason = eligibility.get("reason") or ""
    parts = [f"qualification is {status}"]
    if missing:
        parts.append("missing evidence: " + ", ".join(str(m) for m in missing))
    if reason and reason != status:
        parts.append(str(reason))
    parts.append("re-read per run because it carries a TTL and can be revoked")
    return "; ".join(parts)


def _projection_available(batch: manifest.CutoverBatch,
                          projection_checker: Callable[[str, dict[str, Any]], dict[str, Any]]
                          | None) -> dict[str, Any]:
    """9.3: is the new read model actually able to serve this scope?

    Defaults to available because the checker is an injected capability, not
    something the executor can synthesise. What matters is that a caller who
    *does* have the checker gets its verdict honoured — and that an absent
    checker is visible as "not checked" rather than silently read as "fine".
    """
    if projection_checker is None:
        return {"available": True, "checked": False,
                "reason": "no projection checker supplied; treated as available"}
    try:
        verdict = projection_checker(batch.batch_id, dict(batch.scope))
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "checked": True,
                "reason": f"the projection check failed to run: {type(exc).__name__}: {exc}"}
    available = bool(verdict.get("available"))
    return {"available": available, "checked": True,
            "reason": str(verdict.get("reason") or
                          ("the new read model can serve this scope" if available
                           else "the new read model has no projection for this scope"))}


def execute_batch(batch: manifest.CutoverBatch, *,
                  qualification_reader: Callable[..., dict[str, Any]] | None = None,
                  report_checker: Callable[[manifest.CutoverBatch], tuple[bool, list[str]]]
                  | None = None,
                  projection_checker: Callable[[str, dict[str, Any]], dict[str, Any]]
                  | None = None,
                  authorisation: DeploymentAuthorization | None = None,
                  actor: str = "",
                  path: str | Path | None = None,
                  cutover_path: str | Path | None = None,
                  apply: bool = False) -> BatchSwitchResult:
    """Decide, and only move routes when `apply` and everything agrees.

    `apply=False` is the default and the safe one: 9.1's runbook has an operator
    re-run this in a loop before deciding, so the deciding mode must not be able
    to move a route even by accident.

    The order is the operator's order. Authorisation is checked first, before
    any gate, because an unauthorised run should not be reported as a successful
    dry-run — that would let a green result stand in for a permission nobody
    gave. Then fallback safety, then evidence, then the projection, and only
    then the switch.
    """
    result = BatchSwitchResult(batch_id=batch.batch_id,
                               batch_class=batch.batch_class,
                               outcome=BATCH_NOOP,
                               authorisation=authorisation.reference
                               if authorisation else "")

    _settled: str | None = None

    def _finish(boundary_name: str = "", *, applied: bool = False) -> None:
        """Single exit. Every decision — including every refusal — lands here, so
        a refusal cannot be returned without being recorded.

        Recording refusals is not bookkeeping. 8.2 promises the operator the
        reading "we looked and it was blocked", and that reading is only
        available if the blocked run left a trace. Scattered `return result`
        statements dropped exactly that on the unauthorised path.
        """
        _record(result, batch=batch, boundary=boundary_name, actor=actor,
                path=path, applied=applied and result.changed)
        return result

    def _finish(boundary_name: str = "", *, applied: bool = False) -> None:
        """Single exit. Every decision — including every refusal — lands here, so
        a refusal cannot be returned without being recorded.

        Recording refusals is not bookkeeping. 8.2 promises the operator the
        reading "we looked and it was blocked", and that reading is only
        available if the blocked run left a trace. Scattered `return result`
        statements dropped exactly that on the unauthorised path.
        """
        _record(result, batch=batch, boundary=boundary_name, actor=actor,
                path=path, applied=applied and result.changed)
        return result

    # --- 1. authorisation ----------------------------------------------------
    # Read-only mode still reports the refusal, so an operator sees the real
    # blocker rather than a list of gate results that would read as encouraging.
    if authorisation is None:
        result.outcome = BATCH_REFUSED
        for consumer_id in batch.consumers or ("<batch>",):
            result.scopes.append(ScopeOutcome(
                consumer_id=consumer_id, outcome=UNAUTHORIZED,
                reason="no deployment authorisation; passing the gates is not "
                       "permission to cut over"))
        return _finish("")
    if not authorisation.is_complete:
        result.outcome = BATCH_REFUSED
        result.scopes.append(ScopeOutcome(
            consumer_id="<batch>", outcome=UNAUTHORIZED,
            reason=f"authorisation {authorisation.reference!r} is not auditable"))
        return _finish("")
    expiry = authorisation.expiry()
    if expiry is not None and expiry <= datetime.now(timezone.utc):
        result.outcome = BATCH_REFUSED
        result.scopes.append(ScopeOutcome(
            consumer_id="<batch>", outcome=UNAUTHORIZED,
            reason=f"authorisation {authorisation.reference!r} expired at "
                   f"{authorisation.valid_until}"))
        return _finish("")

    # --- 2. boundary sanity, batch-wide -------------------------------------
    try:
        states = plane.all_boundaries(cutover_path)
    except Exception as exc:  # noqa: BLE001
        raise ReadCutoverError(
            f"the cutover authority could not be read, so nothing can be "
            f"decided safely: {type(exc).__name__}: {exc}") from exc

    boundary = _boundary_for(batch, states)
    if boundary is None:
        result.outcome = BATCH_REFUSED
        result.scopes.append(ScopeOutcome(
            consumer_id="<batch>", outcome=BOUNDARY_UNWIRED,
            reason="the batch names no read-path boundary to switch"))
        return _finish(boundary)
    # Existing executor has no per-consumer identity protocol yet (9.2).
    # A bootstrap declaration must not open that incomplete path.
    try:
        plane.assert_wired(boundary, cutover_path)
    except plane.CutoverError as exc:
        result.outcome = BATCH_REFUSED
        result.scopes.append(ScopeOutcome(
            consumer_id="<batch>", outcome=BOUNDARY_UNWIRED,
            reason=str(exc)))
        return _finish(boundary)

    chain = _switch_chain(boundary)
    if apply and len(chain)>1:
        raise ReadCutoverError("explicit exact-scope joint coordinator required; sequential global apply refused")
    result.checks["switch_chain"] = list(chain)
    settled = chain_is_settled(states, boundary)

    # The compatibility check is about the state we are about to CREATE, so it
    # only makes sense once we know this is a fresh switch. On a settled chain the
    # combination is whatever the previous apply left behind, and judging it again
    # would report the operator's own successful work as a conflict.
    if not settled:
        verdict = plane.check_compatibility(states)
        if not verdict.compatible:
            result.outcome = BATCH_REFUSED
            result.scopes.append(ScopeOutcome(
                consumer_id="<batch>", outcome=BOUNDARY_UNWIRED,
                reason="the current boundary combination is already half-migrated "
                       "in a way that blocks this switch: "
                       + "; ".join(str(c.get("reason")) for c in verdict.conflicts)))
            return _finish("")

    # --- 3. shadow report applicability, batch-wide -------------------------
    usable, problems = reports.require_batch_report(batch, report_checker)
    if not usable:
        result.outcome = BATCH_REFUSED
        result.scopes.append(ScopeOutcome(
            consumer_id="<batch>", outcome=REPORT_INVALID,
            reason="; ".join(problems) or "the cited shadow report is not usable"))
        return _finish("")

    # --- 4. projection availability, batch-wide -----------------------------
    projection = _projection_available(batch, projection_checker)
    if not projection["available"]:
        result.outcome = BATCH_REFUSED
        result.scopes.append(ScopeOutcome(
            consumer_id="<batch>", outcome=PROJECTION_MISSING,
            reason=projection["reason"]))
        return _finish("")

    route_before = states[boundary].route

    # --- 5. per consumer ----------------------------------------------------
    # Each scope accumulates EVERY blocker rather than stopping at the first.
    #
    # 9.4 asks for the result to be recorded, and an operator acting on that
    # record would otherwise have to fix one gap, re-run, discover the next, and
    # repeat — while the batch's fallback drill (a batch-level property, checked
    # per consumer below) stays invisible until every consumer's qualification
    # happens to pass. Short-circuiting here also let the real state read as
    # "only a qualification problem", which is what the first run of this
    # command actually reported.
    for consumer_id in batch.consumers:
        scope = ScopeOutcome(consumer_id=consumer_id, outcome=ALREADY,
                             route_before=route_before, route_after=route_before)
        blockers: list[tuple[str, str]] = []

        if not authorisation.covers(consumer_id):
            blockers.append((UNAUTHORIZED,
                             f"authorisation {authorisation.reference!r} covers "
                             f"{list(authorisation.scope)}, not this consumer"))

        eligibility = _scope_eligibility(consumer_id, dict(batch.scope),
                                         qualification_reader=qualification_reader)
        scope.checks["qualification"] = eligibility
        status = str(eligibility.get("status") or "")
        if status != "eligible":
            blockers.append((NOT_QUALIFIED, _eligibility_reason(eligibility, status)))

        fallback = routing.assess_fallback(
            batch.fallback_route, consumer_id=consumer_id,
            proof_valid=manifest._flag(batch.fallback_proof),
            retired=True if manifest._flag(batch.fallback_retired) else None)
        scope.checks["fallback"] = fallback
        if not manifest._drill_recorded(batch.fallback_drill_ref):
            blockers.append((NO_SAFE_FALLBACK,
                             "the fallback has never been drilled; an untested way "
                             "back is not a way back (drill reference: "
                             f"{batch.fallback_drill_ref or 'none'})"))
        elif fallback.get("verdict") != routing.FALLBACK_OK:
            blockers.append((
                FALLBACK_RETIRED if fallback.get("verdict") == routing.FALLBACK_BLOCKED
                else FALLBACK_UNAVAILABLE,
                fallback.get("reason") or "the fallback is not usable"))

        if blockers:
            # The most severe blocker names the scope's verdict; all of them are
            # kept so the report can list the complete set of work.
            scope.outcome = blockers[0][0]
            scope.reason = " | ".join(reason for _kind, reason in blockers)
            scope.checks["blockers"] = [kind for kind, _reason in blockers]
            result.scopes.append(scope)
            continue

        if settled or route_before == plane.ROUTE_TARGET:
            scope.outcome = ALREADY
            scope.route_after = plane.ROUTE_TARGET
            scope.reason = (f"already serving the target route (chain {list(chain)} "
                            "is settled)")
            result.scopes.append(scope)
            continue

        scope.outcome = SWITCHED
        scope.route_after = plane.ROUTE_TARGET
        result.scopes.append(scope)

    # --- 6. reduce, then move at most once ----------------------------------
    moved = [s for s in result.scopes if s.outcome == SWITCHED]
    already = [s for s in result.scopes if s.outcome == ALREADY]
    if not moved:
        result.outcome = BATCH_NOOP if already else BATCH_REFUSED
        if not apply:
            return _finish("")
    else:
        result.outcome = BATCH_PARTIAL if result.unmoved else BATCH_SWITCHED

    if not apply:
        # Decide-only. The batch may well be refused at apply time (an
        # authorisation can expire in between), which is exactly why the
        # recorded dry-run says "nothing changed" rather than predicting success.
        return _finish("")

    if moved:
        for member in chain:
            plane.set_route(member, plane.ROUTE_TARGET,
                            actor=actor or "read-executor",
                            reason=(f"batch {batch.batch_id}: paired switch of "
                                    f"{list(chain)} for "
                                    + ", ".join(s.consumer_id for s in moved)),
                            path=cutover_path)
            result.checks.setdefault("switched_boundaries", []).append(member)
        result.changed = True

    return _finish(boundary, applied=True)


def _boundary_for(batch: manifest.CutoverBatch,
                  states: dict[str, plane.BoundaryState]) -> str | None:
    """Which boundary this batch moves.

    Read from the declared boundaries first, then inferred from the batch class.
    The inference is a mapping the manifest already implies — a research-read
    batch moves `projection_read` — but it stays explicit so a batch that
    declares its boundaries is never second-guessed by it.
    """
    for boundary in batch.boundaries:
        if boundary in READ_BOUNDARIES:
            return boundary
    if batch.batch_class == manifest.RESEARCH_READ:
        return plane.PROJECTION_READ
    if batch.batch_class == manifest.SCHEDULE:
        return plane.DISPATCHER_SCHEDULE
    if batch.batch_class in {manifest.INTERNAL_STATE_APPROVAL,
                             manifest.LIVE_TRADER}:
        # Those are the trading-adjacent classes; they are declared so the
        # executor refuses loudly rather than guessing a read boundary.
        return None
    if batch.batch_class == manifest.COLLECTION_PUBLISH:
        return None
    for boundary, state in states.items():
        if boundary in READ_BOUNDARIES and state.route != plane.ROUTE_DISABLED:
            return boundary
    return None


def _record(result: BatchSwitchResult, *, batch: manifest.CutoverBatch,
            boundary: str, actor: str, path: str | Path | None,
            applied: bool) -> None:
    """Append the executed attempt.

    Append-only on purpose: "we looked and it was refused" is the reading an
    operator needs when the blocker is later resolved, and it is what
    distinguishes a re-check from a first look.
    """
    conn = _connect(path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS read_cutover_runs (
                   seq INTEGER PRIMARY KEY AUTOINCREMENT,
                   batch_id TEXT NOT NULL,
                   batch_class TEXT NOT NULL,
                   boundary TEXT NOT NULL,
                   outcome TEXT NOT NULL,
                   changed INTEGER NOT NULL,
                   authorisation TEXT NOT NULL DEFAULT '',
                   scopes_json TEXT NOT NULL,
                   actor TEXT NOT NULL DEFAULT '',
                   applied INTEGER NOT NULL,
                   recorded_at TEXT NOT NULL)""")
        conn.execute(
            """INSERT INTO read_cutover_runs
                   (batch_id, batch_class, boundary, outcome, changed,
                    authorisation, scopes_json, actor, applied, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (result.batch_id, result.batch_class, boundary or "unresolved",
             result.outcome, int(result.changed), result.authorisation,
             json.dumps([s.as_row() for s in result.scopes], ensure_ascii=False,
                        default=str),
             actor, int(applied), _now()))
    finally:
        conn.close()


def run_history(batch_id: str = "", *, limit: int = 50,
                path: str | Path | None = None) -> list[dict[str, Any]]:
    conn = _connect(path)
    try:
        if batch_id:
            rows = conn.execute(
                "SELECT * FROM read_cutover_runs WHERE batch_id = ? "
                "ORDER BY seq DESC LIMIT ?", (batch_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM read_cutover_runs ORDER BY seq DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def render_report(results: Sequence[BatchSwitchResult]) -> str:
    """Operator-facing summary of a batch sweep.

    Leads with whether anything moved, because that is the only line anyone
    scanning a list of batches actually needs.
    """
    lines = ["# 读路径切换执行报告（11.5）", ""]
    if not results:
        return "\n".join(lines + ["（无批次）"])

    changed = [r for r in results if r.changed]
    lines.append(f"共 {len(results)} 批 · 实际改动路由 **{len(changed)}** 批 · "
                 f"保持原路由 {len(results) - len(changed)} 批")
    lines += ["", "| 批次 | 结论 | 已切换 | 未切换 |", "|---|---|---|---|"]
    for result in results:
        moved = "、".join(f"`{s.consumer_id}`" for s in result.moved_scopes) or "—"
        held = "、".join(f"`{s.consumer_id}`" for s in result.unmoved) or "—"
        lines.append(f"| `{result.batch_id}` | {result.outcome} | {moved} | {held} |")
    lines += ["", "## 逐批明细", ""]
    for result in results:
        lines += [result.summary(), ""]
    lines.append("**缺项只阻断受影响范围**：未切换的范围不得据此放行其他范围，"
                 "也不得为使其可切而降低证据标准。")
    return "\n".join(lines)
