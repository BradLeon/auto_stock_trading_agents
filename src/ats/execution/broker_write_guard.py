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


def reset_for_tests() -> None:
    """Clear the process guard. Tests only — production never un-installs it."""
    with _STATE.lock:
        _STATE.mode = UNSET
        _STATE.reason_code = ""
        _STATE.refusals = []
        _STATE.counter = 0


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
