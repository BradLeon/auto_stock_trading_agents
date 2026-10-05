"""Per-batch applicability matrix for shadow comparisons (Phase F task 3.2).

Requiring every packet to fix every surface would be wrong in both directions. A
pure research batch reads nothing but published snapshots, so demanding a broker
account snapshot would add observation cost with nothing to attribute. And a
decision or trading batch that does not fix runtime quotes and account state is
not evidence — which is the failure task 3.1's packet design exists to prevent.

So the requirement is per **batch class**, and the classes are:

- `research_read` — Layer / Information / Sector / Fundamental / Macro. Persistent
  refs and projection hashes must be fixed; runtime surfaces are declared
  not-applicable rather than omitted.
- `decision` — Chief, Risk. Everything in the research class plus runtime market
  data, account state, history state, the ruleset and the logical evaluation time.
  Without those, a differing risk verdict cannot be attributed to the path change.
- `trading` — Trader, Clerk. Everything `decision` requires, and it is the only
  class for which the approval-authorization surface must also be fixed.

Two failure modes the matrix has to catch, and they are not symmetric:

- **Over-demanding** a research batch for surfaces it cannot have. The cost is a
  batch that can never run, so the shadow comparison silently never happens.
- **Under-demanding** a trading batch. The cost is a comparison that produces a
  clean result for the wrong reason.

So both directions are errors, with different messages, rather than one being
tolerated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .shadow_inputs import (
    ACCOUNT_STATE,
    ALL_SURFACES,
    HISTORY_STATE,
    LOGICAL_EVAL_TIME,
    MARKET_RUNTIME,
    MODEL_CONFIG,
    NOT_APPLICABLE,
    PERSISTENT_REFS,
    PROJECTION_HASH,
    RULESET_VERSION,
    ShadowInputPacket,
)

RESEARCH_READ = "research_read"
DECISION = "decision"
TRADING = "trading"

BATCH_CLASSES: tuple[str, ...] = (RESEARCH_READ, DECISION, TRADING)

# What each class must fix, and what it may declare not-applicable.
#
# Written as explicit per-class sets rather than derived from a rule like "trading
# is research plus the rest": an explicit table can be read by whoever adds a
# batch class later, and it makes an omission visible as a missing row instead of
# as a silently wrong rule.
_REQUIRED_BY_CLASS: dict[str, tuple[str, ...]] = {
    RESEARCH_READ: (PERSISTENT_REFS, PROJECTION_HASH, LOGICAL_EVAL_TIME),
    DECISION: (PERSISTENT_REFS, PROJECTION_HASH, LOGICAL_EVAL_TIME,
               MARKET_RUNTIME, ACCOUNT_STATE, HISTORY_STATE, RULESET_VERSION,
               MODEL_CONFIG),
    TRADING: (PERSISTENT_REFS, PROJECTION_HASH, LOGICAL_EVAL_TIME,
              MARKET_RUNTIME, ACCOUNT_STATE, HISTORY_STATE, RULESET_VERSION,
              MODEL_CONFIG),
}

# Surfaces a research batch is expected to declare not-applicable rather than
# leave unset. Declared, because "this batch does not touch broker state" is a
# positive statement about the comparison — and its absence is the ambiguity task
# 3.5's `not-compared` reporting exists to surface.
_EXPECTED_NA_BY_CLASS: dict[str, tuple[str, ...]] = {
    # The spec puts it as "只要求固定持久化数据引用与投影 hash" and "运行时面 SHALL
    # 记为不适用". The ruleset and model configuration are configuration *inputs*
    # read at evaluation time, so they belong on the not-applicable side for a
    # batch that never reaches an evaluation — which is what a research read is.
    RESEARCH_READ: (MARKET_RUNTIME, ACCOUNT_STATE, HISTORY_STATE,
                     RULESET_VERSION, MODEL_CONFIG),
    DECISION: (),
    TRADING: (),
}

# Consumers whose scope of work is research-only, used to check that the class and
# the consumer agree. A mismatch means the matrix was applied to the wrong batch.
RESEARCH_CONSUMERS: frozenset[str] = frozenset({
    "layer", "information", "sector", "fundamental", "macro", "technical",
})
DECISION_CONSUMERS: frozenset[str] = frozenset({"chief", "risk"})
TRADING_CONSUMERS: frozenset[str] = frozenset({"trader", "clerk"})

_EXPECTED_CLASS_BY_CONSUMER: dict[str, str] = {
    **{name: RESEARCH_READ for name in RESEARCH_CONSUMERS},
    **{name: DECISION for name in DECISION_CONSUMERS},
    **{name: TRADING for name in TRADING_CONSUMERS},
}


class MatrixError(ValueError):
    """The packet does not satisfy the matrix for its batch class."""


@dataclass(frozen=True)
class MatrixVerdict:
    satisfied: bool
    problems: tuple[str, ...]
    required: tuple[str, ...]
    not_applicable: tuple[str, ...]
    missing: tuple[str, ...]

    def as_row(self) -> dict:
        return {
            "satisfied": self.satisfied, "problems": list(self.problems),
            "required": list(self.required),
            "not_applicable": list(self.not_applicable),
            "missing": list(self.missing),
        }


def required_for(batch_class: str) -> tuple[str, ...]:
    if batch_class not in _REQUIRED_BY_CLASS:
        raise MatrixError(
            f"unknown batch class {batch_class!r}; known classes are "
            f"{list(BATCH_CLASSES)}")
    return _REQUIRED_BY_CLASS[batch_class]


def expected_not_applicable(batch_class: str) -> tuple[str, ...]:
    required_for(batch_class)  # validates the class
    return _EXPECTED_NA_BY_CLASS[batch_class]


def class_for_consumer(consumer_id: str) -> str:
    """The batch class a consumer's work belongs to."""
    return _EXPECTED_CLASS_BY_CONSUMER.get(consumer_id, DECISION)


def check_packet(packet: ShadowInputPacket, *, batch_class: str | None = None
                 ) -> MatrixVerdict:
    """Does `packet` satisfy the matrix for its batch class?

    Both directions are checked. A missing required surface and an
    unexpected not-applicable are different failures with different fixes, and
    conflating them is how "the shadow batch never ran" stays invisible.
    """
    effective_class = batch_class or packet.batch_class
    required = required_for(effective_class)
    expected_na = set(expected_not_applicable(effective_class))

    missing = tuple(s for s in required if s not in packet.surfaces)
    wrongly_na = tuple(
        sorted(s for s in required if packet.surfaces.get(s) == NOT_APPLICABLE))
    undeclared_na = tuple(
        sorted(s for s in packet.not_applicable()
               if s not in expected_na and s not in required))
    undeclared = packet.unset_surfaces()

    problems: list[str] = []

    for surface in missing:
        problems.append(
            f"{surface}: required for a {effective_class} batch but not declared; "
            f"the comparison cannot attribute a difference in this surface")
    for surface in wrongly_na:
        problems.append(
            f"{surface}: required for a {effective_class} batch but marked "
            f"not-applicable")
    for surface in undeclared_na:
        problems.append(
            f"{surface}: marked not-applicable, but a {effective_class} batch is "
            f"expected to fix it; if it genuinely does not apply, the matrix "
            f"should say so")
    for surface in undeclared:
        problems.append(
            f"{surface}: neither fixed nor declared not-applicable; an unset "
            f"surface is indistinguishable from an oversight")

    # The other direction. A surface the matrix does not require but the batch
    # class is supposed to exclude must not be quietly fixed: a research batch
    # carrying a live market snapshot means it DID touch runtime data, which
    # contradicts its own declaration and makes the comparison's scope a lie.
    # Tolerating extra fixed surfaces would let a mislabelled batch pass as a
    # clean research comparison.
    for surface in expected_na:
        if surface in packet.surfaces and packet.surfaces[surface] != NOT_APPLICABLE:
            problems.append(
                f"{surface}: a {effective_class} batch declares this surface "
                f"not-applicable, yet this packet fixed it — either the batch was "
                f"mislabelled or the matrix is wrong")

    # The clock is required for every class, so this only fires when the caller
    # checked a class whose matrix omits it — kept explicit because the packet's
    # own evidence check already insists on it.
    problems.extend(packet.evidence_problems())

    return MatrixVerdict(not problems, tuple(problems), required,
                         packet.not_applicable(), missing)


def assert_satisfies_matrix(packet: ShadowInputPacket, *,
                            batch_class: str | None = None) -> None:
    verdict = check_packet(packet, batch_class=batch_class)
    if not verdict.satisfied:
        raise MatrixError(
            f"shadow input packet {packet.run_id} does not satisfy the "
            f"{packet.batch_class} matrix:\n"
            + "\n".join(f"  - {problem}" for problem in verdict.problems))


def assert_class_matches_consumer(packet: ShadowInputPacket) -> None:
    """A batch class that contradicts the consumer's role is a wiring mistake.

    Worth asserting separately: a Macro run labelled `trading` would demand broker
    snapshots it cannot produce, and the resulting error would read as a matrix
    problem rather than as a mislabelled batch.
    """
    expected = _EXPECTED_CLASS_BY_CONSUMER.get(packet.consumer_id)
    if expected is None:
        return
    if packet.batch_class != expected:
        raise MatrixError(
            f"consumer {packet.consumer_id!r} belongs to the {expected!r} batch "
            f"class but this packet declares {packet.batch_class!r}")


def unmet_surfaces(packets: Iterable[ShadowInputPacket]) -> dict[str, tuple[str, ...]]:
    """Across a set of packets, which required surfaces are still unfixed anywhere.

    Used when assembling a batch: the batch's requirements are the union, so a
    surface one packet fixes and another does not is a gap for the batch even
    though it is satisfied per packet.
    """
    packets = list(packets)
    if not packets:
        return {}
    unmet: dict[str, tuple[str, ...]] = {}
    for batch_class in sorted({p.batch_class for p in packets}):
        required = required_for(batch_class)
        missing = tuple(
            surface for surface in required
            if any(surface not in p.surfaces for p in packets
                   if p.batch_class == batch_class))
        if missing:
            unmet[batch_class] = missing
    return unmet
