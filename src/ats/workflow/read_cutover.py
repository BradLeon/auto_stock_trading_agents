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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import batch_manifest as manifest
from . import cutover as plane

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
    return datetime.now(UTC).isoformat()


def _parse(value: str):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
        existing = conn.execute("SELECT * FROM read_cutover_authorizations WHERE reference=?",
                                (auth.reference,)).fetchone()
        values = (auth.authorised_by, auth.issued_by, ",".join(auth.scope),
                  auth.valid_until, auth.note)
        if existing is not None:
            if tuple(existing[k] for k in ("authorised_by", "issued_by", "scope", "valid_until", "note")) != values:
                raise DeploymentAuthorizationError("authorization reference is immutable")
        else:
            conn.execute("INSERT INTO read_cutover_authorizations VALUES (?,?,?,?,?,?,?,?)",
                         (auth.reference, *values, now, actor))
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
    if expiry is None or expiry <= datetime.now(UTC):
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

    An absent checker refuses: availability must be measured by the actual reader.
    """
    if projection_checker is None:
        return {"available": False, "checked": False,
                "reason": "actual projection checker required"}
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
                  apply: bool = False,
                  record: bool = True) -> BatchSwitchResult:
    """Formal exact-scope read executor; coordinator rechecks all gates at commit.

    Old product-list batches are audit history only. No global boundary or
    implicitly derived schedule chain can be changed through this entry.
    """
    from . import joint_cutover as joint
    from . import read_deployment as deployment
    from .scoped_routes import resolve_route

    result = BatchSwitchResult(batch.batch_id, batch.batch_class, BATCH_REFUSED,
        authorisation=authorisation.reference if authorisation else "")
    boundary = plane.DISPATCHER_SCHEDULE if batch.batch_class == manifest.SCHEDULE else plane.PROJECTION_READ
    try:
        if authorisation is None or not authorisation.is_complete:
            raise DeploymentAuthorizationError("auditable deployment authorisation required")
        if authorisation.expiry() is None or authorisation.expiry() <= datetime.now(UTC):
            raise DeploymentAuthorizationError("deployment authorisation expired or invalid")
        groups = deployment.requests(batch)
        if Path(cutover_path or plane.default_cutover_db_path()).resolve() != Path(
                plane.default_cutover_db_path()).resolve():
            raise ReadCutoverError("executor must use active runtime control authority")
        for who, request in groups:
            before = resolve_route(boundary, who, path=cutover_path)
            scope = ScopeOutcome(who.consumer_id, REPORT_INVALID,
                route_before=before["route"], route_after=before["route"])
            scope.checks["identity"] = who.as_row()
            scope.checks["request"] = request
            result.scopes.append(scope)
            try:
                assert_authorised(authorisation, consumer_id=who.consumer_id)
                if qualification_reader is None:
                    raise ReadCutoverError("current exact-scope qualification reader required")
                gates = deployment.gate_adapters(batch, who, request, authorisation,
                    report_checker=report_checker, qualification_reader=qualification_reader,
                    projection_checker=projection_checker,
                    path=cutover_path or plane.default_cutover_db_path())
                # Same adapters as the coordinator; no route writes in plan mode.
                joint.preflight(request, gates=gates,
                    path=cutover_path or plane.default_cutover_db_path(), mode="production")
                if apply:
                    operation = joint.execute(request, gates=gates, actor=actor,
                        reason="explicit read batch " + batch.batch_id, path=cutover_path)
                    scope.checks["joint_operation"] = operation
                after = resolve_route(boundary, who, path=cutover_path)
                scope.route_after = after["route"] if apply else next(
                    m["route"] for m in request["members"] if m["boundary"] == boundary)
                scope.outcome = (ALREADY if before["generation"] == after["generation"] and
                    before["route"] == scope.route_after else SWITCHED)
                result.changed |= apply and before["generation"] != after["generation"]
            except Exception as exc:  # noqa: BLE001 - a failed gate must be recorded and refuse
                scope.outcome = UNAUTHORIZED if isinstance(exc, DeploymentAuthorizationError) else REPORT_INVALID
                scope.reason = str(exc)
        result.outcome = (BATCH_PARTIAL if result.unmoved and result.moved_scopes else
            BATCH_REFUSED if result.unmoved else BATCH_SWITCHED if any(
                s.outcome == SWITCHED for s in result.scopes) else BATCH_NOOP)
    except (DeploymentAuthorizationError, plane.CutoverError, ValueError, KeyError,
            TypeError, OSError, sqlite3.Error) as exc:
        result.scopes.extend(ScopeOutcome(consumer,
            UNAUTHORIZED if isinstance(exc, DeploymentAuthorizationError) else REPORT_INVALID,
            reason=str(exc)) for consumer in batch.consumers or ("<batch>",))
    if record:
        _record(result, batch=batch, boundary=boundary, actor=actor,
                path=path, applied=apply and result.changed)
    return result


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
