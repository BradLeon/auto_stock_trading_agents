"""Per-consumer disposition of the gaps intake verification found (task 7.7).

Verification produces findings. This module decides what each finding MEANS, and
the rules are mostly about refusing to convert an inconvenient fact into a
convenient policy:

- **An accepted optional input stays accepted.** `sec_edgar_filing_body` is
  optional and non-blocking by an explicit, versioned policy decision. A
  verification run that happened to find it missing does not get to overturn that
  decision by reclassifying it — that is how an exception quietly becomes a
  permanent hole.
- **A source used by the latest final is never downgraded by report age.** Age
  says nothing about whether the CURRENT data is usable; a daily-refreshed source
  with a three-day-old report is current, and calling it stale would be a rule
  that fires on schedule rather than on fact.
- **Time gaps and broken history stay explicitly `partial`.** Not silently
  complete, and not a hard failure either — the range genuinely lacks something,
  and saying so is the honest state.
- **A legacy-path defect is registered, not fixed.** Fixing it would mean
  changing code the new path is supposed to be isolated from, in the same change
  that is verifying the isolation.

Two things this must NOT do, both of which look helpful:

- **It must not auto-release a consumer whose history is incomplete.** "Most of
  the history is fine" is not a qualification, and letting a gap become a pass on
  a majority threshold converts "we checked" into "we approved".
- **It must not let one consumer's history block an unrelated accepted path.**
  A broken lineage for `fundamental` says nothing about `technical`, and a
  disposition that halted the portfolio over one consumer's history would make
  this mechanism unusable and get it switched off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from . import intake_verification as iv


class DispositionError(RuntimeError):
    """A disposition cannot be formed as stated."""


# Disposition outcomes. `partial` is the load-bearing one: it is neither a pass
# nor a failure, and collapsing it into either is the mistake this vocabulary
# exists to prevent.
DISPOSITION_OK = "ok"
DISPOSITION_PARTIAL = "partial"
DISPOSITION_PENDING = "pending"
DISPOSITION_REGISTERED_ONLY = "registered_only"

# Inputs accepted as optional by an explicit, versioned policy. A verification
# run does not get to re-decide this: the decision was made with its reasons
# recorded, and reopening it needs that record, not a fresh observation.
ACCEPTED_OPTIONAL_INPUTS: frozenset[str] = frozenset({"sec_edgar_filing_body"})

# Sources whose CURRENT final version is what downstream reads. Age-based
# downgrade is refused for these because a refreshed source with an older report
# is current — the report is a publication artefact, not the data.
FINAL_VERSION_SOURCES: frozenset[str] = frozenset({
    "company_financials",
    "defeatbeta_sec_filing_index",
    "defeatbeta_earnings_transcript",
})


@dataclass
class ConsumerDisposition:
    """What happens to one consumer's scope, and why."""

    consumer_id: str
    disposition: str
    reasons: list[str] = field(default_factory=list)
    partial_ranges: list[str] = field(default_factory=list)
    registered_only: list[str] = field(default_factory=list)
    blocks_cutover: bool = False
    auto_released: bool = False
    blocks_unrelated: tuple[str, ...] = ()

    def as_row(self) -> dict[str, Any]:
        return {
            "consumer_id": self.consumer_id,
            "disposition": self.disposition,
            "reasons": list(self.reasons),
            "partial_ranges": list(self.partial_ranges),
            "registered_only": list(self.registered_only),
            "blocks_cutover": self.blocks_cutover,
            "auto_released": self.auto_released,
            "blocks_unrelated": list(self.blocks_unrelated),
        }


def dispose_consumer(*, consumer_id: str,
                     optional_inputs: Sequence[str] = (),
                     coverage_gaps: Sequence[str] = (),
                     time_missing: Sequence[str] = (),
                     history_broken: Sequence[str] = (),
                     legacy_defects: Sequence[str] = (),
                     stale_by_report_age: Sequence[str] = (),
                     ) -> ConsumerDisposition:
    """Form one consumer's disposition from what the verification found.

    Every parameter is an OBSERVED gap, not a conclusion — this function is where
    observations become policy, which is why the policy rules are stated here
    rather than left to the caller. A caller that could pass "approved" would make
    every rule below optional.
    """
    if consumer_id not in iv.TEN_CONSUMERS:
        raise DispositionError(
            f"unknown consumer {consumer_id!r}; expected one of "
            + ", ".join(iv.TEN_CONSUMERS))

    disposition = ConsumerDisposition(consumer_id=consumer_id,
                                      disposition=DISPOSITION_OK)
    reasons = disposition.reasons

    # 1. An accepted optional input stays accepted.
    for name in optional_inputs:
        if name not in ACCEPTED_OPTIONAL_INPUTS:
            disposition.disposition = DISPOSITION_PARTIAL
            reasons.append(
                f"{name} is absent but was never accepted as optional; an input "
                "with no recorded acceptance decision cannot be treated as one "
                "because this run happened not to see it")
            disposition.partial_ranges.append(name)

    # 2. A confirmed coverage gap keeps its existing policy — recorded, not
    #    silently promoted to a blocker or waived.
    for gap in coverage_gaps:
        if disposition.disposition == DISPOSITION_OK:
            disposition.disposition = DISPOSITION_PARTIAL
        reasons.append(
            f"confirmed coverage gap: {gap} — recorded and left as-is; a "
            "verification run does not get to resolve it by deciding it does not "
            "matter")
        disposition.partial_ranges.append(gap)

    # 3. Age-based downgrade is refused for sources read at their final version.
    for name in stale_by_report_age:
        if name in FINAL_VERSION_SOURCES:
            reasons.append(
                f"refused the age-based downgrade of {name}: it is read at its "
                "latest final version, so the report's age says nothing about "
                "whether the current data is usable — a rule that fires on "
                "schedule rather than on fact")
            continue
        if disposition.disposition == DISPOSITION_OK:
            disposition.disposition = DISPOSITION_PENDING
        reasons.append(
            f"{name} was flagged stale by report age; recorded as pending rather "
            "than resolved, because this run has no way to tell an age problem "
            "from a real one")
        disposition.partial_ranges.append(name)

    # 4. Time gaps and broken history stay explicitly partial.
    for name in time_missing:
        if disposition.disposition == DISPOSITION_OK:
            disposition.disposition = DISPOSITION_PARTIAL
        reasons.append(f"time is missing for {name}; marked partial explicitly — "
                       "an absent timestamp is not a present-and-valid one")
        disposition.partial_ranges.append(name)

    for name in history_broken:
        if disposition.disposition == DISPOSITION_OK:
            disposition.disposition = DISPOSITION_PARTIAL
        reasons.append(
            f"history is broken for {name}; marked partial explicitly. Historical "
            "incompleteness does NOT auto-release this scope: 'most of the "
            "history is fine' is not a qualification")
        disposition.partial_ranges.append(name)
        disposition.blocks_cutover = True

    # 5. Legacy-path defects are registered, never fixed here.
    for defect in legacy_defects:
        reasons.append(
            f"legacy path defect registered, not fixed: {defect}. Repairing it "
            "would change the code the new path is supposed to be isolated from, "
            "in the very change that verifies the isolation")
        disposition.registered_only.append(defect)

    # A registered legacy defect does not block a path whose verification passed:
    # the whole premise of the new path is that it does not inherit the defect.
    if legacy_defects and not (time_missing or history_broken
                               or coverage_gaps or optional_inputs):
        if disposition.disposition == DISPOSITION_OK:
            reasons.append(
                "the new path is verified as isolated from the registered legacy "
                "defect, so the defect is not a reason to hold this scope")

    disposition.blocks_unrelated = ()
    return disposition


def consumer_dispositions(*, consumers: Iterable[str] | None = None,
                          optional_inputs: Sequence[str] = (),
                          coverage_gaps: Sequence[str] = (),
                          time_missing: Sequence[str] = (),
                          history_broken: Sequence[str] = (),
                          legacy_defects: Sequence[str] = (),
                          stale_by_report_age: Sequence[str] = (),
                          ) -> list[ConsumerDisposition]:
    """Dispositions for the declared consumers (all ten by default).

    A gap named here applies to every consumer examined, because these inputs
    describe the DATA rather than one consumer's use of it. Per-consumer
    narrowing would need per-consumer evidence, and inventing that is how a
    general gap turns into a specific consumer's excuse.
    """
    targets = list(consumers) if consumers else list(iv.TEN_CONSUMERS)
    return [
        dispose_consumer(
            consumer_id=consumer_id,
            optional_inputs=optional_inputs, coverage_gaps=coverage_gaps,
            time_missing=time_missing, history_broken=history_broken,
            legacy_defects=legacy_defects, stale_by_report_age=stale_by_report_age)
        for consumer_id in targets
    ]


def assert_no_auto_release(report: ConsumerDisposition) -> None:
    """Refuse a disposition that released a scope with incomplete history.

    There is no path that sets `auto_released`, and that is deliberate: it makes
    the forbidden outcome unrepresentable rather than merely discouraged. The
    assertion exists so that a future edit which introduces the flag has to delete
    this function to do it.
    """
    if report.auto_released:
        raise DispositionError(
            f"{report.consumer_id} was auto-released despite "
            f"{report.partial_ranges}; historical incompleteness must not become "
            "a qualification")


def assert_scope_isolation(reports: Sequence[ConsumerDisposition]) -> None:
    """One consumer's gap must not block an unrelated accepted path.

    The spec's "缺项只阻断受影响范围" in its disposition form: a broken lineage for
    one consumer says nothing about another, and a disposition that halted the
    portfolio over it would be so unusable that it gets switched off — which is
    the opposite outcome.
    """
    blocking = [r.consumer_id for r in reports if r.blocks_cutover]
    clean = [r.consumer_id for r in reports
             if r.disposition == DISPOSITION_OK and not r.blocks_cutover]
    if blocking and clean:
        offenders = [r for r in reports if r.blocks_unrelated]
        if offenders:
            detail = "; ".join(f"{r.consumer_id} blocked {r.blocks_unrelated}"
                               for r in offenders)
            raise DispositionError(
                f"a blocked scope must not hold unrelated accepted paths, but "
                f"{detail}. {len(blocking)} blocked / {len(clean)} clean — reduce "
                "the blocking scope to the affected consumer.")


def render_dispositions(reports: Sequence[ConsumerDisposition]) -> str:
    """Operator-facing summary: the disposition, then why."""
    lines = ["# 逐消费者处置（F.0.5）", "",
             "| 消费者 | 处置 | 阻断切流 | 部分范围 | 仅登记 |",
             "|---|---|---|---|---|"]
    for report in sorted(reports, key=lambda r: r.consumer_id):
        lines.append(
            f"| `{report.consumer_id}` | {report.disposition} | "
            f"{'是' if report.blocks_cutover else '否'} | "
            f"{', '.join(report.partial_ranges) or '—'} | "
            f"{', '.join(report.registered_only) or '—'} |")

    # Rendered whenever any reason exists, NOT only when the disposition changed.
    # The refusals are the interesting output: "we declined to apply this rule"
    # is a decision somebody has to see, and a report that printed `ok` with no
    # reasoning would hide every one of them.
    with_reasons = [r for r in reports if r.reasons]
    if with_reasons:
        lines += ["", "## 理由", ""]
        for report in sorted(with_reasons, key=lambda r: r.consumer_id):
            lines.append(f"### `{report.consumer_id}`（{report.disposition}）")
            for reason in report.reasons:
                lines.append(f"- {reason}")

    lines += ["",
              "**缺项只阻断受影响范围**：不得据此放行其他消费者，也不得为使其可切而"
              "降低证据标准。**历史不完整不自动放行**——「大部分历史是好的」不是资格。"]
    return "\n".join(lines)