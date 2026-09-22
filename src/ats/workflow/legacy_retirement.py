"""The one legal route out of a legacy implementation.

Why a registry at all
---------------------
The migration rule is additive: never drop an old table or column while a new write path
is still stabilising. That rule is correct but incomplete — without a register and an
exit criterion, nobody can later answer "which legacy implementation may leave now, and
on what evidence?", so compatibility layers accumulate indefinitely and both the audit
trail and the write boundary get diluted by permanently co-existing paths.

This module fixes the route as: **tombstone → fail-closed read gate → two-phase purge**,
the same pattern the structured data layer already uses for retired sources. It is
deliberately not a second, competing mechanism.

Three properties matter more than the shape of the data:

* A tombstone and an in-use entry for the same identifier is a conflict that fails
  loading — it must never be resolved by silently preferring one side.
* A read that hits a tombstone returns a *distinct* reason code: retired is not
  "not found" and not "no data". Collapsing them lets a caller retry forever against
  something that is deliberately gone.
* Purge is two-phase. An unconfirmed request is a read-only dry run; nothing irreversible
  happens without an explicit confirm, and every confirmed purge leaves an audit record.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config import REPO_ROOT

REGISTRY_PATH = REPO_ROOT / "config" / "workflow" / "legacy_retirement.yaml"

RetirementStatus = Literal["retired", "pending"]
ReadKind = Literal["ok", "retired", "not_found", "no_data"]

# Distinct on purpose: a caller that cannot tell these apart will retry a retired
# implementation indefinitely, or treat "deliberately gone" as "happened to be empty".
REASON_RETIRED = "legacy_retired"
REASON_NOT_FOUND = "not_found"
REASON_NO_DATA = "no_data"


class RetirementConflictError(ValueError):
    """The same identifier is both in use and retired."""

    def __init__(self, identifiers: Sequence[str]) -> None:
        self.identifiers = list(identifiers)
        super().__init__(
            "these identifiers are registered as retired while still present in the "
            f"in-use inventory: {', '.join(self.identifiers)}. A tombstone and an "
            "in-use entry cannot coexist — register the exit or withdraw the tombstone.")


class RetirementTombstone(BaseModel):
    """Everything a later phase needs to know to actually remove something."""

    model_config = ConfigDict(extra="forbid")

    identifier: str
    capability_domain: str
    replaced_by: str
    target_phase: str
    exit_condition: str
    status: RetirementStatus = "pending"
    # Required when `status` is `pending`: what is missing, not just that it is missing.
    missing_condition: str = ""
    consumers: tuple[str, ...] = ()
    consumer_zero_criterion: str = ""
    location: str = ""

    @model_validator(mode="after")
    def _require_the_missing_condition(self) -> RetirementTombstone:
        if self.status == "pending" and not self.missing_condition:
            raise ValueError(
                f"{self.identifier}: a pending retirement must state the condition it "
                "is waiting on — 'not yet' with no reason is how items stay pending "
                "forever")
        return self

    @property
    def may_exit(self) -> bool:
        return self.status == "retired"

    def validate_record(self) -> RetirementTombstone:
        return self


class PurgeAuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str
    action: str
    scope: tuple[str, ...] = ()
    note: str = ""
    data_retained_elsewhere: bool = False
    at: str = ""


class PurgePlan(BaseModel):
    """What a purge would touch. Produced by the dry run; nothing is modified."""

    model_config = ConfigDict(extra="forbid")

    identifier: str
    scope: tuple[str, ...] = ()
    executed: bool = False
    audit: PurgeAuditRecord | None = None


class RetirementRegistry:
    """Tombstones plus the in-use inventory they must not overlap with."""

    def __init__(self, tombstones: Iterable[RetirementTombstone] = (),
                 active: Iterable[str] = ()) -> None:
        self._tombstones: dict[str, RetirementTombstone] = {}
        self._active: set[str] = set()
        self._audit: list[PurgeAuditRecord] = []
        self._purge_scope: dict[str, tuple[str, ...]] = {}
        for tombstone in tombstones:
            self.register(tombstone)
        self._active.update(active)
        self._validate_no_conflicts()

    # --- registration ------------------------------------------------------ #
    def register(self, tombstone: RetirementTombstone) -> None:
        self._tombstones[tombstone.identifier] = tombstone.validate_record()

    def mark_active(self, identifier: str) -> None:
        self._active.add(identifier)

    def declare_purge_scope(self, identifier: str, scope: Sequence[str]) -> None:
        """What a purge of `identifier` would touch — used by the dry run."""
        self._purge_scope[identifier] = tuple(scope)

    def _validate_no_conflicts(self) -> None:
        # Only fully retired identifiers conflict. A pending one is *expected* to still
        # be in use — that is what "pending" means — so checking it here would make the
        # registry unable to hold the very items it exists to track.
        retired = {name for name, item in self._tombstones.items() if item.may_exit}
        overlap = sorted(self._active & retired)
        if overlap:
            raise RetirementConflictError(overlap)

    # --- queries ----------------------------------------------------------- #
    def tombstone(self, identifier: str) -> RetirementTombstone | None:
        return self._tombstones.get(identifier)

    def tombstones(self) -> tuple[RetirementTombstone, ...]:
        return tuple(sorted(self._tombstones.values(), key=lambda item: item.identifier))

    def identifiers(self) -> tuple[str, ...]:
        return tuple(sorted(self._tombstones))

    def active(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    def audit_log(self) -> tuple[PurgeAuditRecord, ...]:
        return tuple(self._audit)

    # --- fail-closed read gate --------------------------------------------- #
    def read_gate(self, identifier: str, *, has_data: bool = True) -> tuple[ReadKind, str]:
        """Decide what a read of `identifier` may see.

        `has_data=False` is how a caller reports an empty result; the gate then says
        `no_data` rather than letting emptiness masquerade as retirement.
        """
        tombstone = self._tombstones.get(identifier)
        if tombstone is not None and tombstone.status == "retired":
            return "retired", REASON_RETIRED
        if identifier not in self._active:
            return "not_found", REASON_NOT_FOUND
        if not has_data:
            return "no_data", REASON_NO_DATA
        return "ok", ""

    def write_gate(self, identifier: str) -> tuple[bool, str]:
        """A retired write path fails; it is never redirected to the replacement.

        Redirecting would make the call succeed while hiding that a retired path is
        still being exercised — exactly the signal this registry exists to produce.
        """
        tombstone = self._tombstones.get(identifier)
        if tombstone is not None and tombstone.status == "retired":
            return False, REASON_RETIRED
        return True, ""

    # --- two-phase purge ---------------------------------------------------- #
    def purge(self, identifier: str, *, confirm: bool = False,
              exported: bool = False, note: str = "",
              scope: Sequence[str] | None = None) -> PurgePlan:
        """Dry run by default; only `confirm=True` executes, and it is recorded.

        `exported` is the "the data lives elsewhere already" declaration. It is kept as
        a first-class field so an audit reader can see that the deletion was deliberate
        and backed up, rather than inferring it from a note.
        """
        affected = tuple(scope) if scope is not None else self._purge_scope.get(
            identifier, (identifier,))
        if not confirm:
            return PurgePlan(identifier=identifier, scope=affected, executed=False)
        record = PurgeAuditRecord(
            identifier=identifier, action="purge", scope=affected, note=note,
            data_retained_elsewhere=exported,
            at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self._audit.append(record)
        self._purge_scope.pop(identifier, None)
        return PurgePlan(identifier=identifier, scope=affected, executed=True,
                         audit=record)


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def load_registry(path: Path | None = None) -> RetirementRegistry:
    """Load tombstones and the in-use inventory, failing on any overlap.

    This mirrors the structured data layer's `retired_sources` handling: the conflict
    check runs at LOAD time, so a contradictory registry cannot be opened at all.
    """
    import yaml

    target = path or REGISTRY_PATH
    if not target.exists():
        return RetirementRegistry()
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return registry_from_mapping(raw)


def registry_from_mapping(payload: Mapping[str, Any]) -> RetirementRegistry:
    tombstones = []
    for row in payload.get("retired") or []:
        tombstones.append(RetirementTombstone(
            identifier=row["identifier"],
            capability_domain=row.get("capability_domain", ""),
            replaced_by=row.get("replaced_by", ""),
            target_phase=row.get("target_phase", ""),
            exit_condition=row.get("exit_condition", ""),
            status=row.get("status", "pending"),
            missing_condition=row.get("missing_condition", ""),
            consumers=_as_tuple(row.get("consumers")),
            consumer_zero_criterion=row.get("consumer_zero_criterion", ""),
            location=row.get("location", "")))
    active = _as_tuple(payload.get("active"))
    registry = RetirementRegistry(tombstones, active)
    for row in payload.get("purge_scope") or []:
        registry.declare_purge_scope(row["identifier"], _as_tuple(row.get("scope")))
    return registry
