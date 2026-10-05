"""Replayable shadow input packets (Phase F task 3.1, step 1 of the code freeze).

A shadow run that compares two code paths is only meaningful if both were handed
the *same* inputs. Same data vintage is not that: `read_input` returns runtime
values for `MARKET_DATA` and `BROKER_STATE`, and `assemble.build` stamps
`datetime.now()`. Two paths given the same published snapshot will still see
different prices, different account state, and a different wall clock — so a
difference in the risk verdict cannot be attributed to the path change.

A packet fixes those and stores a hash per surface. Two properties make it usable
as evidence rather than as decoration:

- **Hash per surface, not one hash over everything.** "The packets differ" is not
  actionable; "the account state differs" is. One combined hash would also make a
  pure-research packet change whenever a broker tick arrives, which is exactly the
  false positive task 3.2's applicability matrix exists to avoid.
- **The logical evaluation time is fixed, not captured.** A packet records the
  instant the run *should* behave as if it were, and every read is filtered to it.
  That is what makes a replay repeatable rather than merely recorded.

This module wraps `read_input` rather than modifying it, and that is a hard
constraint rather than a preference: `consumer_api.py` is on the qualification
fingerprint surface for all ten consumers, so editing it invalidates every
recorded evidence row (see `workflow.assurance_surface`). The wrapper is the only
place that can change without retiring evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

# The explicit "this surface does not apply to this batch class" marker. A string
# rather than a missing key, so a research-only packet and an incomplete one are
# distinguishable by inspection. Declared before the dataclass because
# `__post_init__` compares against it.
NOT_APPLICABLE = "not_applicable"

# Surfaces a packet may fix. Named by what they are rather than by which consumer
# reads them, so the applicability matrix (task 3.2) can talk about surfaces.
PERSISTENT_REFS = "persistent_refs"        # published documents / facts / chains
PROJECTION_HASH = "projection_hash"        # rendered projection content
MARKET_RUNTIME = "market_runtime"          # live quotes and bars
ACCOUNT_STATE = "account_state"            # broker holdings and cash
HISTORY_STATE = "history_state"            # prior decisions and executions
LOGICAL_EVAL_TIME = "logical_eval_time"    # the instant the run behaves as
RULESET_VERSION = "ruleset_version"        # risk configuration
MODEL_CONFIG = "model_config"              # model and prompt configuration

# Every surface, in the order a packet declares them.
ALL_SURFACES: tuple[str, ...] = (
    PERSISTENT_REFS, PROJECTION_HASH, MARKET_RUNTIME, ACCOUNT_STATE,
    HISTORY_STATE, LOGICAL_EVAL_TIME, RULESET_VERSION, MODEL_CONFIG,
)

# A packet is only usable as evidence when the logical evaluation time is fixed.
# Everything else can be marked not-applicable per batch class (task 3.2); a
# missing clock cannot, because without it every read drifts.
SURFACES_REQUIRED_FOR_EVIDENCE: tuple[str, ...] = (LOGICAL_EVAL_TIME,)


def _hash(value: Any) -> str:
    """Content hash of any JSON-able value.

    Sorted keys so the hash is over content rather than over dict ordering, which
    differs between two processes that built the same value by different routes.
    """
    payload = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class IncompletePacketError(ValueError):
    """The packet cannot serve as evidence, and the reason says which surface."""


@dataclass
class ShadowInputPacket:
    """One replayable set of inputs, hashed per surface.

    `surfaces` maps a surface name to either a hash (fixed) or the sentinel
    `NOT_APPLICABLE`. The distinction is explicit rather than inferred from
    absence: a research-only batch legitimately does not fix broker state, and
    recording that as "absent" would be indistinguishable from having forgotten.
    """

    run_id: str
    consumer_id: str
    batch_class: str
    surfaces: dict[str, str] = field(default_factory=dict)
    declared_at: str = ""
    scope: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.declared_at:
            self.declared_at = datetime.now(timezone.utc).isoformat()
        unknown = set(self.surfaces) - set(ALL_SURFACES)
        if unknown:
            raise ValueError(
                f"unknown shadow input surface(s): {sorted(unknown)}; "
                f"known surfaces are {list(ALL_SURFACES)}")

    # --- content ------------------------------------------------------------ #

    def fixed_surfaces(self) -> dict[str, str]:
        return {name: value for name, value in self.surfaces.items()
                if value != NOT_APPLICABLE}

    def not_applicable(self) -> tuple[str, ...]:
        return tuple(sorted(name for name, value in self.surfaces.items()
                            if value == NOT_APPLICABLE))

    def unset_surfaces(self) -> tuple[str, ...]:
        declared = set(self.surfaces)
        return tuple(name for name in ALL_SURFACES if name not in declared)

    def packet_hash(self) -> str:
        """One hash over the whole packet — identity, not diagnosis.

        Kept separate from the per-surface hashes on purpose: this one answers
        "are these the same inputs?", the others answer "what differs?".
        """
        return _hash({"run_id": self.run_id, "consumer_id": self.consumer_id,
                      "batch_class": self.batch_class,
                      "surfaces": dict(sorted(self.surfaces.items())),
                      "declared_at": self.declared_at,
                      "scope": self.scope})

    def as_row(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "consumer_id": self.consumer_id,
            "batch_class": self.batch_class, "packet_hash": self.packet_hash(),
            "surfaces": dict(sorted(self.surfaces.items())),
            "declared_at": self.declared_at, "scope": self.scope,
            "not_applicable": list(self.not_applicable()),
            "unset": list(self.unset_surfaces()),
        }

    # --- validity ----------------------------------------------------------- #

    def evidence_problems(self) -> list[str]:
        """Why this packet may not be cited as evidence. Empty means usable.

        Fail-closed in the same direction as everything else in Phase F: a surface
        that was never declared is reported rather than assumed equivalent to one
        that was deliberately marked not-applicable.
        """
        problems: list[str] = []
        for surface in SURFACES_REQUIRED_FOR_EVIDENCE:
            if surface not in self.surfaces:
                problems.append(f"{surface}: not declared")
            elif self.surfaces[surface] == NOT_APPLICABLE:
                problems.append(
                    f"{surface}: marked not-applicable, but it is required for "
                    f"evidence in every batch class")
        clock = self.surfaces.get(LOGICAL_EVAL_TIME)
        if clock and clock != NOT_APPLICABLE:
            try:
                datetime.fromisoformat(str(clock).replace("Z", "+00:00"))
            except ValueError:
                problems.append(
                    f"{LOGICAL_EVAL_TIME}: {clock!r} is not a timestamp; a packet "
                    f"whose clock cannot be parsed cannot be replayed")
        return problems

    def assert_usable_as_evidence(self) -> None:
        problems = self.evidence_problems()
        if problems:
            raise IncompletePacketError(
                f"shadow input packet {self.run_id} cannot be cited as evidence:\n"
                + "\n".join(f"  - {problem}" for problem in problems))


# --------------------------------------------------------------------------- #
# building packets from real reads
# --------------------------------------------------------------------------- #

def capture_persistent_surface(inputs: Iterable[Any]) -> str:
    """Hash the published refs and source stamps of persistent inputs.

    Takes `ConsumerInput` objects (or anything with `input_refs` / `source_as_of`)
    rather than re-reading, so the packet describes reads that actually happened
    instead of a second, possibly different, set of them.
    """
    refs: list[str] = []
    stamps: list[str] = []
    for item in inputs:
        refs.extend(str(ref) for ref in getattr(item, "input_refs", []) or [])
        stamps.extend(str(stamp) for stamp in getattr(item, "source_as_of", []) or [])
    return _hash({"refs": sorted(set(refs)), "as_of": sorted(set(stamps))})


def capture_projection_surface(payloads: Iterable[Any]) -> str:
    """Hash rendered projection content."""
    return _hash(sorted((_jsonable(p) for p in payloads), key=repr))


def capture_market_surface(quotes: Any) -> str:
    """Hash live market data. Separate from projections so a tick is attributable."""
    return _hash(_jsonable(quotes))


def capture_account_surface(account_state: Any) -> str:
    """Hash broker holdings and cash.

    Its own surface because this is the case the requirement names: same published
    vintage, different account state, therefore a different packet.
    """
    return _hash(_jsonable(account_state))


def capture_history_surface(history: Any) -> str:
    return _hash(_jsonable(history))


def capture_ruleset(config: Any) -> str:
    return _hash(_jsonable(config))


def capture_model_config(config: Any) -> str:
    return _hash(_jsonable(config))


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def build_packet(*, run_id: str, consumer_id: str, batch_class: str,
                 logical_eval_time: str | None = None,
                 persistent_refs: str | None = None,
                 projection_hash: str | None = None,
                 market_runtime: str | None = None,
                 account_state: str | None = None,
                 history_state: str | None = None,
                 ruleset_version: str | None = None,
                 model_config: str | None = None,
                 scope: dict[str, Any] | None = None) -> ShadowInputPacket:
    """Assemble a packet, leaving unfixed surfaces unset rather than empty.

    An unset surface and one marked not-applicable mean different things, so this
    does not fill the gaps: task 3.2's matrix decides which is which per batch
    class, and this function only records what the caller actually fixed.
    """
    surfaces: dict[str, str] = {}
    supplied = {
        PERSISTENT_REFS: persistent_refs, PROJECTION_HASH: projection_hash,
        MARKET_RUNTIME: market_runtime, ACCOUNT_STATE: account_state,
        HISTORY_STATE: history_state, LOGICAL_EVAL_TIME: logical_eval_time,
        RULESET_VERSION: ruleset_version, MODEL_CONFIG: model_config,
    }
    for surface, value in supplied.items():
        if value is not None:
            surfaces[surface] = value

    return ShadowInputPacket(run_id=run_id, consumer_id=consumer_id,
                             batch_class=batch_class, surfaces=surfaces,
                             scope=dict(scope or {}))


def mark_not_applicable(packet: ShadowInputPacket, *surfaces: str) -> ShadowInputPacket:
    """Declare surfaces that do not apply to this batch class.

    Returns a new packet rather than mutating: a packet that has been declared is
    evidence, and evidence should not change after the fact. This is the same
    append-only reasoning the shadow report applies to conclusions.

    Refuses to override a surface that was actually fixed. Silently replacing a
    captured market snapshot with "not applicable" would discard real observation
    data and make the packet claim it never touched runtime data — the opposite of
    what the declaration is for. The caller has to say which it meant.
    """
    unknown = set(surfaces) - set(ALL_SURFACES)
    if unknown:
        raise ValueError(f"unknown surface(s): {sorted(unknown)}")
    already_fixed = sorted(
        surface for surface in surfaces
        if surface in packet.surfaces and packet.surfaces[surface] != NOT_APPLICABLE)
    if already_fixed:
        raise ValueError(
            f"surface(s) {already_fixed} were fixed on this packet; marking them "
            f"not-applicable would discard captured data. Build a packet without "
            f"them if they genuinely do not apply to this batch class.")

    merged = dict(packet.surfaces)
    for surface in surfaces:
        merged[surface] = NOT_APPLICABLE
    return ShadowInputPacket(run_id=packet.run_id, consumer_id=packet.consumer_id,
                             batch_class=packet.batch_class, surfaces=merged,
                             declared_at=packet.declared_at, scope=dict(packet.scope))


# --------------------------------------------------------------------------- #
# comparing two packets
# --------------------------------------------------------------------------- #

def diff_packets(left: ShadowInputPacket, right: ShadowInputPacket) -> dict[str, Any]:
    """Which surfaces differ, which are fixed on both, which are unfixed.

    Reported per surface because the question a shadow report has to answer is
    "may this difference be attributed to the path change?", and that is a
    per-surface judgement: a differing account snapshot invalidates a risk-verdict
    comparison but says nothing about an input-snapshot comparison.
    """
    names = sorted(set(left.surfaces) | set(right.surfaces))
    rows = []
    for name in names:
        lhs = left.surfaces.get(name)
        rhs = right.surfaces.get(name)
        if lhs is None or rhs is None:
            verdict = "unfixed_on_one_side"
        elif lhs == rhs:
            verdict = "equal"
        else:
            verdict = "differs"
        rows.append({"surface": name, "left": lhs, "right": rhs, "verdict": verdict})

    return {
        "identical": left.packet_hash() == right.packet_hash(),
        "left_hash": left.packet_hash(), "right_hash": right.packet_hash(),
        "surfaces": rows,
        "differing": [r["surface"] for r in rows if r["verdict"] == "differs"],
        "unfixed": [r["surface"] for r in rows
                    if r["verdict"] == "unfixed_on_one_side"],
    }
