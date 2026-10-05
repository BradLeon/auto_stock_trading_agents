"""Process-level broker write prohibition (Phase F task 1.1).

Shadow and isolated runs must be unable to reach a broker write path **by
construction**, not by convention. The existing `dry_run` flag is a caller
assertion: whoever calls `place_orders` decides whether it is dry, so a new
call site that forgets the flag writes real orders during a shadow run. Task
1.1 therefore puts the prohibition one level lower — in the process — and the
broker's submit layer consults it.

Three properties matter, and each one exists because of a specific way the
weaker designs fail:

- **The prohibition is process state, not a parameter.** A caller cannot opt
  into writing. `prohibit_broker_writes()` is called once when a shadow or
  isolated run starts; from then on every submit path in this process refuses.
- **Absence of the prohibition is itself a failure.** A shadow run that starts
  without the guard installed must not proceed — otherwise "the capability is
  missing" degrades into "the capability was never checked".
- **Refusal is an auditable record, not a silent no-op.** A shadow run that
  tried to place an order and got nothing back is indistinguishable from one
  that had no orders. Every refusal is appended to a ledger with the caller and
  reason so the acceptance evidence can be read back.

Generation-scoped route identity and the account/environment binding arrive with
task 2.1–2.3; this module is the capability they extend, so the submit check
lives here from the start and the generation check is added to the same call.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Sentinel for "the guard was never installed". Distinct from an installed
# prohibition, which is an affirmative decision: keeping them apart is what lets
# a shadow run refuse to start rather than silently write orders.
UNSET = "unset"

# Refusal reason codes. Stable strings because the shadow acceptance evidence is
# read back by code, not by a human squinting at a log.
REASON_SHADOW_RUN = "shadow_run_prohibited"
REASON_ISOLATED = "isolated_run_prohibited"
REASON_EXPLICIT = "explicitly_prohibited"

# Grant refusal reason codes (Phase F 2.2/2.3). Distinct from the prohibition
# codes above: a prohibition means "this process may never write", while these mean
# "this process may write, but not under the authority it is holding" — a stale
# generation, or an account that is not the one the broker is connected to.
REASON_NO_GRANT = "no_write_grant"
REASON_GENERATION_STALE = "write_grant_generation_stale"
REASON_ACCOUNT_MISMATCH = "write_grant_account_mismatch"
REASON_ENVIRONMENT_MISMATCH = "write_grant_environment_mismatch"
# Phase F 2.6: submissions are closed while a route switch is between its first
# and last step. Distinct from a prohibition — the process may write, just not
# right now — and distinct from a stale generation, because the generation has not
# moved yet at this point. Conflating the three would make a frozen cutover
# indistinguishable from a revoked capability in the refusal ledger.
REASON_FROZEN = "write_grant_submissions_frozen"


class BrokerWriteProhibited(RuntimeError):
    """A broker submit was attempted in a process that may not write.

    Carries ``reason_code`` and ``refusal_id`` so the caller can record the
    attempt without string-matching the message.
    """

    def __init__(self, message: str, *, reason_code: str, refusal_id: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.refusal_id = refusal_id


@dataclass(frozen=True)
class WriteGrant:
    """Permission to submit under one specific authority (Phase F task 2.2).

    A grant is not "this process may write" — it is "this process may write as the
    route that was active when the grant was issued, to the account it named". All
    four fields are re-verified at submission, so a grant that outlives its
    generation stops working the moment a cutover moves the generation, and a grant
    issued for a paper account cannot be used against a live one.
    """

    route_id: str
    generation: int
    environment: str = ""
    account: str = ""
    granted_at: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id, "generation": self.generation,
            "environment": self.environment, "account": self.account,
            "granted_at": self.granted_at,
        }


@dataclass(frozen=True)
class RefusalRecord:
    """One refused broker write. The evidence that the prohibition held."""

    refusal_id: str
    at: str
    reason_code: str
    operation: str
    caller: str
    symbol: str = ""
    quantity: float | None = None
    detail: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "refusal_id": self.refusal_id,
            "at": self.at,
            "reason_code": self.reason_code,
            "operation": self.operation,
            "caller": self.caller,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "detail": self.detail,
        }


@dataclass
class _GuardState:
    mode: str = UNSET
    reason_code: str = ""
    refusals: list[RefusalRecord] = field(default_factory=list)
    # The authority this process was granted (Phase F 2.2). None means no grant
    # was issued — which is a refusal reason in itself, because "nobody granted
    # this" must not read as "anything goes".
    grant: WriteGrant | None = None
    revocation_reason: str = ""
    # Guards the *check*, not the write. Two threads racing a cutover must not
    # both observe "permitted"; a single lock makes the read-modify-decide
    # sequence atomic. The write itself stays outside — holding a lock across a
    # broker round trip would serialise independent order submissions.
    lock: threading.Lock = field(default_factory=threading.Lock)
    counter: int = 0


_STATE = _GuardState()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_id(state: _GuardState) -> str:
    state.counter += 1
    return f"broker-write-refusal-{state.counter:06d}"


# --- write grants (Phase F 2.2 / 2.3) ---------------------------------------- #

def grant_write(route_id: str, generation: int, *, environment: str = "",
                account: str = "") -> WriteGrant:
    """Issue this process a write grant for one specific authority.

    Deliberately requires the caller to state the generation explicitly instead of
    reading it here: the caller must have obtained it from the registry, so a grant
    can never be minted against a generation this process merely assumed.
    """
    if not route_id.strip():
        raise ValueError("route_id is required for a write grant")
    if int(generation) < 1:
        raise ValueError(f"generation must be >= 1, got {generation}")
    grant = WriteGrant(route_id=route_id, generation=int(generation),
                       environment=environment, account=account, granted_at=_now())
    with _STATE.lock:
        _STATE.grant = grant
    return grant


def active_grant() -> WriteGrant | None:
    with _STATE.lock:
        return _STATE.grant


def revoke_grant(reason: str = "") -> None:
    """Drop this process's grant. Used by a cutover and by shutdown paths."""
    with _STATE.lock:
        _STATE.grant = None
        _STATE.revocation_reason = reason


def _record_refusal(state: _GuardState, reason_code: str, operation: str,
                    caller: str, symbol: str, quantity: float | None,
                    detail: str) -> str:
    record = RefusalRecord(refusal_id=_next_id(state), at=_now(),
                          reason_code=reason_code, operation=operation,
                          caller=caller, symbol=symbol, quantity=quantity,
                          detail=detail)
    state.refusals.append(record)
    return record.refusal_id


def check_grant(*, operation: str, caller: str, state_reader,
                account: str = "", environment: str = "",
                symbol: str = "", quantity: float | None = None,
                freeze_reader=None,
                detail: str = "") -> None:
    """Re-verify this process's grant against the authoritative state.

    Called at every real submission, not once at start-up: the point of a
    generation is that a grant stops being valid when a cutover happens, and a
    process that only checked at start-up would keep writing across it.

    `state_reader` is injected rather than imported so the caller decides which
    registry is authoritative — and so a test can supply a moving generation
    without a database. `freeze_reader` is the same arrangement for the switch
    freeze (task 2.6 step 1).
    """
    with _STATE.lock:
        grant = _STATE.grant
    if grant is None:
        with _STATE.lock:
            refusal_id = _record_refusal(
                _STATE, REASON_NO_GRANT, operation, caller, symbol, quantity,
                detail or "this process holds no write grant")
        raise BrokerWriteProhibited(
            f"broker {operation} refused: no write grant (caller={caller}, "
            f"refusal_id={refusal_id})",
            reason_code=REASON_NO_GRANT, refusal_id=refusal_id)

    current = state_reader()

    def _refuse(code: str, message: str) -> None:
        note = f"{message} ({detail})" if detail else message
        with _STATE.lock:
            refusal_id = _record_refusal(
                _STATE, code, operation, caller, symbol, quantity, note)
        raise BrokerWriteProhibited(
            f"broker {operation} refused: {note} (caller={caller}, "
            f"refusal_id={refusal_id})",
            reason_code=code, refusal_id=refusal_id)

    if current is None:
        _refuse(REASON_NO_GRANT,
                "the route registry reports no active route")
    # Freeze is checked BEFORE the generation comparison on purpose: during step 2
    # of a switch the generation is still the old one, so a grant that is still
    # valid by generation would otherwise sail through a cutover that is in
    # progress. Freezing first is what makes "drain" a real interval rather than
    # a check that only takes effect once the generation has already moved.
    if freeze_reader is not None:
        freeze = freeze_reader()
        if freeze is not None and getattr(freeze, "frozen", False):
            _refuse(REASON_FROZEN,
                    f"submissions are frozen for route switch "
                    f"{getattr(freeze, 'switch_token', '')!r} "
                    f"(from route {getattr(freeze, 'from_route', '')!r} "
                    f"generation {getattr(freeze, 'from_generation', '')})")
    if current.generation != grant.generation:
        _refuse(REASON_GENERATION_STALE,
                f"grant generation {grant.generation} is behind the active "
                f"generation {current.generation} (route "
                f"{current.route_id!r})")
    if current.route_id != grant.route_id:
        _refuse(REASON_GENERATION_STALE,
                f"grant was issued for route {grant.route_id!r} but the active "
                f"route is {current.route_id!r}")
    if grant.environment and current.environment \
            and grant.environment != current.environment:
        _refuse(REASON_ENVIRONMENT_MISMATCH,
                f"grant environment {grant.environment!r} does not match the "
                f"active environment {current.environment!r}")
    # The account check is the one that makes a paper grant unusable against a
    # live connection. A blank on either side means "unstated", not "matches" —
    # otherwise an unnamed grant would satisfy any broker.
    if grant.account and account and grant.account != account:
        _refuse(REASON_ACCOUNT_MISMATCH,
                f"grant account {grant.account!r} does not match the connected "
                f"broker account {account!r}")
    if grant.account and not account:
        _refuse(REASON_ACCOUNT_MISMATCH,
                f"grant account {grant.account!r} was never matched against a "
                "connected broker account")


def reset_for_tests() -> None:
    """Clear the process guard and any grant. Tests only — production never
    un-installs them, which is the point."""
    with _STATE.lock:
        _STATE.mode = UNSET
        _STATE.reason_code = ""
        _STATE.refusals = []
        _STATE.counter = 0
        _STATE.grant = None
        _STATE.revocation_reason = ""


def install_prohibition(reason_code: str) -> None:
    """Forbid broker writes in this process for the rest of its life.

    Idempotent: a second call keeps the FIRST reason code, because the first
    prohibition is the one that was in force when the attempt happened. A
    re-prohibit with a different reason would rewrite history.
    """
    if reason_code not in {REASON_SHADOW_RUN, REASON_ISOLATED, REASON_EXPLICIT}:
        raise ValueError(f"unknown broker write prohibition reason: {reason_code!r}")
    with _STATE.lock:
        if _STATE.mode == UNSET:
            _STATE.mode = "prohibited"
            _STATE.reason_code = reason_code


def prohibit_broker_writes(*, reason_code: str = REASON_SHADOW_RUN) -> None:
    """Public entry point for shadow and isolated runners."""
    install_prohibition(reason_code)


def is_prohibited() -> bool:
    """True only when a prohibition is installed. ``UNSET`` is NOT prohibited."""
    with _STATE.lock:
        return _STATE.mode == "prohibited"


def guard_installed() -> bool:
    """True once the guard has been installed either way.

    A shadow run asserts this: without it there is no evidence the capability
    existed, which the acceptance criteria treat as a failed start.
    """
    with _STATE.lock:
        return _STATE.mode != UNSET


def assert_broker_writes_prohibited(*, operation: str, caller: str) -> None:
    """Refuse to start a shadow or isolated run without the prohibition.

    The asymmetry is deliberate: ``is_prohibited()`` answers "may this process
    write?" while this answers "is the capability present?". A runner that
    checks only the former treats "never installed" as permission.
    """
    if not guard_installed():
        raise BrokerWriteProhibited(
            "shadow/isolated run requires the broker write prohibition to be "
            "installed before it starts; the capability is missing",
            reason_code="guard_missing",
            refusal_id="",
        )
    if not is_prohibited():
        raise BrokerWriteProhibited(
            "shadow/isolated run requires broker writes to be prohibited, but "
            f"the current mode allows them (mode={_STATE.mode!r})",
            reason_code="guard_not_prohibiting",
            refusal_id="",
        )


def check_broker_write(*, operation: str, caller: str, symbol: str = "",
                       quantity: float | None = None,
                       detail: str = "") -> None:
    """The submit-layer gate. Raise if this process may not write, else return.

    Called from the broker's own submit path, not from a caller, so a new call
    site inherits the prohibition by construction.
    """
    with _STATE.lock:
        if _STATE.mode != "prohibited":
            return
        reason = _STATE.reason_code
        record = RefusalRecord(
            refusal_id=_next_id(_STATE),
            at=_now(),
            reason_code=reason,
            operation=operation,
            caller=caller,
            symbol=symbol,
            quantity=quantity,
            detail=detail,
        )
        _STATE.refusals.append(record)
    raise BrokerWriteProhibited(
        f"broker {operation} refused: {reason} (caller={caller}"
        + (f" symbol={symbol}" if symbol else "")
        + f", refusal_id={record.refusal_id})",
        reason_code=reason,
        refusal_id=record.refusal_id,
    )


def refusals() -> list[RefusalRecord]:
    """Every refusal this process recorded, oldest first."""
    with _STATE.lock:
        return list(_STATE.refusals)


def refusal_rows() -> list[dict[str, Any]]:
    """Refusals as plain rows, for evidence export."""
    return [record.as_row() for record in refusals()]


def prohibited_from_environment() -> bool:
    """Read the prohibition from the environment.

    The env var exists so a *resident* process (the scheduler) can be started
    into a prohibiting state without a code change. It never enables writes:
    only an explicit grant can lift a prohibition, and there is no env var for
    that.
    """
    return os.environ.get("ATS_BROKER_WRITE_PROHIBITION", "").strip().lower() in {
        "1", "true", "yes", "prohibit", "prohibited", "shadow",
    }


def install_from_environment() -> bool:
    """Install the prohibition if the environment asks for it.

    Returns whether a prohibition is now in force, so a caller can refuse to
    continue when the environment was ambiguous.
    """
    if prohibited_from_environment():
        install_prohibition(
            REASON_ISOLATED
            if os.environ.get("ATS_RUN_MODE", "").strip().lower() == "isolated"
            else REASON_SHADOW_RUN)
        return True
    return is_prohibited()
