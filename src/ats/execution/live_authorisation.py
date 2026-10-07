"""Live-trading authorisation gate, and the drills that prove it closed (11.1–11.3).

Three permissions exist in this system and confusing any two of them is the
failure this module exists to prevent:

- **Deployment authorisation** (`DEP-*`, read_cutover / schedule_cutover) — a
  human approved changing routes.
- **Execution authorisation** (group 2, `AuthorizationLifecycle`) — a decision
  revision passed risk review and was approved.
- **Live authorisation** (this module, `LIVE-*`) — a human approved sending
  *real orders at a broker*.

Passing the first two says the *system* agrees the change is safe. Only the
third says a human authorised **actual money movement**. Design decision 12
already recorded that planning approval and prerequisite passes do not
constitute it; this module is where that becomes mechanical rather than a
sentence in a design document.

**What the gate refuses, and why each refusal is worth having**

1. Every gate green but no live authorisation — the state in which an
   unauthorised change looks most reasonable, so it must be the default-deny.
2. An authorisation missing any of issuer, operator, scope, expiry — an
   unauditable permission is not a weaker one; a permission nobody can check
   cannot be relied on, and the next reader is the one who needs it.
3. A *deployment* authorisation offered for the live switch — the two name
   different actions. A deployment approval that also authorised real orders
   would make the phrase "deployment" meaningless.
4. An authorisation issued by the system itself (a shadow or paper run) —
   **this is the subtle one.** A shadow run that is allowed to self-authorise can
   produce an authorisation that then looks externally granted, and the
   distinction between "simulated" and "someone authorised this" collapses at
   exactly the wrong moment.

**Why the switch itself is never performed here**

This module decides and records. Executing a live route change is
`route_switch.perform_switch`, reached only after this gate returns a reference.
Keeping the two apart means the gate can be exercised fully — in isolation, with
simulated brokers — without any path existing by which a test could move a real
route. `PLAN_GATEWAY` is the seam: the live action happens outside this module,
and what this module guarantees is that the seam has a verified authorisation to
open.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

DEFAULT_PATH = "var/phase_f_live_authorizations.sqlite"

# Live authorisation identity. `LIVE-` rather than reusing `DEP-` so that a
# reference from one can never be mistaken for the other — the confusion this
# module guards against should also be impossible in the identifiers.
LIVE_PREFIX = "LIVE-"

# Where an authorisation may come from. Anything else is refused.
ISSUER_HUMAN = "human"
ISSUER_SHADOW = "shadow_run"
ISSUER_PAPER = "paper_run"
ALLOWED_ISSUERS = frozenset({ISSUER_HUMAN})

# Verdicts.
AUTHORISED = "authorised"
REFUSED = "refused"

# The gate's own reason codes. Distinct per failure so a refusal can be counted
# — a wall of identical text tells an operator nothing about which gate to fix.
REASON_NO_LIVE_AUTHORISATION = "no_live_authorisation"
REASON_NOT_AUDITABLE = "live_authorisation_not_auditable"
REASON_EXPIRED = "live_authorisation_expired"
REASON_OUT_OF_SCOPE = "live_authorisation_out_of_scope"
REASON_SELF_ISSUED = "live_authorisation_self_issued"
REASON_ENVIRONMENT_MISMATCH = "live_environment_mismatch"
REASON_ACCOUNT_MISMATCH = "live_account_mismatch"


class LiveAuthorisationError(RuntimeError):
    """A live switch was refused. Carries the reason code for counting."""


@dataclass(frozen=True)
class LiveAuthorisation:
    """A permission to send real orders, from a named human, for a named scope.

    Auditable means all five are present and non-empty: reference, issuer,
    operator, scope, expiry. `issued_by` is deliberately separate from
    `authorised_by` — one person approving their own action is a weaker control
    than two, and the distinction is free to record.
    """

    reference: str
    issuer: str
    authorised_by: str
    issued_by: str
    scope: tuple[str, ...]
    environment: str
    account: str
    valid_until: str
    note: str = ""

    @property
    def is_auditable(self) -> bool:
        return all((self.reference, self.issuer, self.authorised_by,
                    self.issued_by, self.environment, self.account,
                    self.valid_until)) and bool(self.scope)

    @property
    def missing_fields(self) -> tuple[str, ...]:
        named = (("reference", self.reference), ("issuer", self.issuer),
                 ("authorised_by", self.authorised_by),
                 ("issued_by", self.issued_by),
                 ("environment", self.environment),
                 ("account", self.account), ("valid_until", self.valid_until))
        absent = tuple(name for name, value in named if not str(value or "").strip())
        return absent + (() if self.scope else ("scope",))

    @property
    def is_live_reference(self) -> bool:
        return str(self.reference or "").startswith(LIVE_PREFIX)

    def expiry(self):
        return _parse(self.valid_until)

    def covers(self, consumer_id: str) -> bool:
        return consumer_id in self.scope

    def as_row(self) -> dict[str, Any]:
        return {
            "reference": self.reference, "issuer": self.issuer,
            "authorised_by": self.authorised_by, "issued_by": self.issued_by,
            "scope": ",".join(self.scope), "environment": self.environment,
            "account": self.account, "valid_until": self.valid_until,
            "note": self.note,
        }


@dataclass
class GateDecision:
    """Whether the live switch may proceed, and on what basis."""

    verdict: str
    reason: str = ""
    detail: str = ""
    reference: str = ""
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def opened(self) -> bool:
        return self.verdict == AUTHORISED

    def raise_if_closed(self) -> None:
        """Raise rather than return.

        The caller's next action after this call is a real order. A returned
        boolean invites `if not ok: log(...)` and then the order anyway.
        """
        if self.opened:
            return
        raise LiveAuthorisationError(
            f"{self.reason}: {self.detail} "
            f"(live authorisation reference: {self.reference or 'none'})")

    def as_row(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "reason": self.reason,
                "detail": self.detail, "reference": self.reference,
                "checks": dict(self.checks)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(value: str):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _connect(path: str | Path | None) -> sqlite3.Connection:
    target = Path(path or DEFAULT_PATH)
    if not target.is_absolute():
        target = Path(__file__).resolve().parents[3] / target
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def record_authorisation(auth: LiveAuthorisation, *, actor: str = "",
                         path: str | Path | None = None) -> str:
    """Record a live authorisation. Append-only by reference.

    Recorded rather than passed in because a permission that lives only in the
    caller's arguments cannot answer "who authorised this?" afterwards — and
    that question is the only reason this gate exists.
    """
    if not auth.is_auditable:
        raise LiveAuthorisationError(
            f"{REASON_NOT_AUDITABLE}: missing {list(auth.missing_fields)}. An "
            "authorisation that cannot be checked by the next reader cannot be "
            "relied on by this one")
    if not auth.is_live_reference:
        raise LiveAuthorisationError(
            f"{REASON_NOT_AUDITABLE}: a live authorisation must be referenced as "
            f"{LIVE_PREFIX}…; got {auth.reference!r}. Reusing a deployment "
            "reference here is the confusion this gate exists to prevent")
    if auth.issuer not in ALLOWED_ISSUERS:
        raise LiveAuthorisationError(
            f"{REASON_SELF_ISSUED}: issuer {auth.issuer!r} is not a human "
            f"operator (allowed: {sorted(ALLOWED_ISSUERS)}). A shadow or paper "
            "run may not grant itself permission to move real money")

    conn = _connect(path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS live_authorizations (
                   reference TEXT PRIMARY KEY,
                   issuer TEXT NOT NULL,
                   authorised_by TEXT NOT NULL,
                   issued_by TEXT NOT NULL,
                   scope TEXT NOT NULL,
                   environment TEXT NOT NULL,
                   account TEXT NOT NULL,
                   valid_until TEXT NOT NULL,
                   note TEXT NOT NULL DEFAULT '',
                   recorded_at TEXT NOT NULL,
                   recorded_by TEXT NOT NULL DEFAULT '')""")
        now = _now()
        if conn.execute("SELECT 1 FROM live_authorizations WHERE reference = ?",
                        (auth.reference,)).fetchone() is not None:
            # Re-recording must not silently widen the scope: a stale narrow
            # grant replaced by a broad one under the same name would leave a
            # history showing one event.
            raise LiveAuthorisationError(
                f"{auth.reference!r} is already recorded. Record a new reference "
                "instead: overwriting would hide the fact that the grant changed")
        conn.execute(
            """INSERT INTO live_authorizations
                   (reference, issuer, authorised_by, issued_by, scope,
                    environment, account, valid_until, note, recorded_at,
                    recorded_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (auth.reference, auth.issuer, auth.authorised_by, auth.issued_by,
             ",".join(auth.scope), auth.environment, auth.account,
             auth.valid_until, auth.note, now, actor))
        return now
    finally:
        conn.close()


def read_authorisation(reference: str, *,
                       path: str | Path | None = None) -> LiveAuthorisation | None:
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT * FROM live_authorizations WHERE reference = ?",
            (reference,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    return LiveAuthorisation(
        reference=row["reference"], issuer=row["issuer"],
        authorised_by=row["authorised_by"], issued_by=row["issued_by"],
        scope=tuple(s for s in row["scope"].split(",") if s),
        environment=row["environment"], account=row["account"],
        valid_until=row["valid_until"], note=row["note"])


def evaluate_live_switch(*, authorisation_ref: str,
                         consumer_id: str = "",
                         environment: str = "", account: str = "",
                         path: str | Path | None = None) -> GateDecision:
    """11.1/11.2: may the live route switch, and on whose authority?

    Every refusal names a reason code, because a wall of identical text tells an
    operator nothing about which of the four gates to fix. Checked in the order a
    reader would want them explained: presence, auditability, provenance,
    binding, then time.
    """
    decision = GateDecision(verdict=REFUSED)

    if not authorisation_ref or not str(authorisation_ref).strip():
        decision.reason = REASON_NO_LIVE_AUTHORISATION
        decision.detail = (
            "every other gate may be green and the switch is still refused: the "
            "deployment authorisation approves changing routes, not sending real "
            "orders. A live authorisation is a separate artifact (design "
            "decision 12)")
        decision.checks["present"] = False
        return decision
    decision.checks["present"] = True

    if not str(authorisation_ref).startswith(LIVE_PREFIX):
        decision.reason = REASON_NOT_AUDITABLE
        decision.detail = (
            f"{authorisation_ref!r} is not a live authorisation reference "
            f"(must start with {LIVE_PREFIX}). A deployment authorisation offered "
            "here would make the word 'deployment' meaningless")
        return decision

    auth = read_authorisation(authorisation_ref, path=path)
    if auth is None:
        decision.reason = REASON_NO_LIVE_AUTHORISATION
        decision.detail = (
            f"{authorisation_ref!r} is not recorded. An authorisation that cannot "
            "be read back cannot be audited, and an unauditable permission is not "
            "a weaker one")
        decision.checks["recorded"] = False
        return decision
    decision.checks["recorded"] = True
    decision.reference = auth.reference

    if not auth.is_auditable:
        decision.reason = REASON_NOT_AUDITABLE
        decision.detail = f"missing {list(auth.missing_fields)}"
        decision.checks["auditable"] = False
        return decision
    decision.checks["auditable"] = True

    # 11.2: provenance. A shadow or paper run's own output must never count as
    # an external grant, or the line between "simulated" and "somebody
    # authorised this" disappears exactly when it matters most.
    if auth.issuer not in ALLOWED_ISSUERS:
        decision.reason = REASON_SELF_ISSUED
        decision.detail = (
            f"issuer {auth.issuer!r} is not a human operator. A shadow or paper "
            "run may issue reference for comparison, never authority")
        decision.checks["provenance"] = False
        return decision
    decision.checks["provenance"] = True

    if auth.issuer != ISSUER_HUMAN:
        decision.checks["provenance"] = False
    decision.checks["authorised_by"] = auth.authorised_by
    decision.checks["issued_by"] = auth.issued_by

    if environment and auth.environment != environment:
        decision.reason = REASON_ENVIRONMENT_MISMATCH
        decision.detail = (
            f"authorised for environment {auth.environment!r}, requested "
            f"{environment!r}. An authorisation for paper does not cover live, "
            "which is the whole point of naming it")
        return decision
    decision.checks["environment"] = auth.environment

    if account and auth.account != account:
        decision.reason = REASON_ACCOUNT_MISMATCH
        decision.detail = (
            f"authorised for account {auth.account!r}, requested {account!r}. The "
            "broker's connected account is the authority, not the caller's claim")
        return decision
    decision.checks["account"] = auth.account

    if consumer_id and not auth.covers(consumer_id):
        decision.reason = REASON_OUT_OF_SCOPE
        decision.detail = (
            f"authorisation {auth.reference!r} covers {list(auth.scope)}, not "
            f"{consumer_id!r}. A blanket grant is how a narrow permission becomes "
            "a broad one")
        return decision
    decision.checks["scope"] = list(auth.scope)

    expiry = auth.expiry()
    if expiry is not None and expiry <= datetime.now(timezone.utc):
        decision.reason = REASON_EXPIRED
        decision.detail = f"expired at {auth.valid_until}"
        decision.checks["expired"] = True
        return decision
    decision.checks["expired"] = False

    decision.verdict = AUTHORISED
    decision.reason = ""
    decision.detail = (f"live sending authorised by {auth.authorised_by} "
                       f"({auth.reference})")
    return decision


def assert_live_authorised(*, authorisation_ref: str,
                            consumer_id: str = "",
                            environment: str = "", account: str = "",
                            path: str | Path | None = None) -> GateDecision:
    """Raise unless the gate opens. The only supported way into a live switch."""
    decision = evaluate_live_switch(
        authorisation_ref=authorisation_ref, consumer_id=consumer_id,
        environment=environment, account=account, path=path)
    decision.raise_if_closed()
    return decision


def as_shadow_reference(auth: LiveAuthorisation | None) -> str:
    """The label a shadow run may use for a real authorisation, for comparison.

    A shadow run needs to *look* at the real thing to test its own behaviour, but
    its output must be recognisable as a mirror rather than as an authority. The
    `shadow:` prefix is that recognisability: it cannot collide with `LIVE-`, and
    passing it to the gate above will not resolve to a recorded authorisation.
    """
    if auth is None or not auth.is_auditable:
        return "shadow:none"
    return (f"shadow:{auth.reference}@{auth.environment}/{auth.account}"
            f" until {auth.valid_until}")


def _record_gate_decision(decision: GateDecision, *, actor: str, kind: str,
                          environment: str = "", account: str = "",
                          path: str | Path | None = None) -> None:
    """Append the decision. A refusal is exactly the reading worth keeping."""
    conn = _connect(path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS live_gate_decisions (
                   seq INTEGER PRIMARY KEY AUTOINCREMENT,
                   kind TEXT NOT NULL,
                   reference TEXT NOT NULL DEFAULT '',
                   verdict TEXT NOT NULL,
                   reason TEXT NOT NULL DEFAULT '',
                   detail TEXT NOT NULL DEFAULT '',
                   environment TEXT NOT NULL DEFAULT '',
                   account TEXT NOT NULL DEFAULT '',
                   actor TEXT NOT NULL DEFAULT '',
                   recorded_at TEXT NOT NULL)""")
        conn.execute(
            """INSERT INTO live_gate_decisions
                   (kind, reference, verdict, reason, detail, environment,
                    account, actor, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (kind, decision.reference, decision.verdict, decision.reason,
             decision.detail, environment, account, actor, _now()))
    finally:
        conn.close()


def gate_history(*, limit: int = 50,
                 path: str | Path | None = None) -> list[dict[str, Any]]:
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM live_gate_decisions ORDER BY seq DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()
