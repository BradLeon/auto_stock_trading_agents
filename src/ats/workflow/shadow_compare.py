"""Six-surface shadow comparison with declared tolerance (Phase F task 3.3).

A shadow run that only compared literal equality would report a difference for
every sentence a role rewrite rephrased, and the conclusion "the new path
diverges" would be indistinguishable from "the roles were restructured". Both are
`diverged`, so a byte-equality default turns a known, intended change into noise
that operators learn to accept — and once they accept everything, the comparison
stops being evidence.

So every surface declares **what counts as a difference**. Three verdict kinds:

- `matched` — the two paths agree under that surface's rule.
- `diverged` — they do not, and the report says by how much, against which
  declared allowance.
- `not-compared` — comparable data was absent. Never folded into `matched`: the
  difference between "the same" and "not looked at" is the entire content of a
  comparison's credibility.

Three of the six surfaces have no legitimate reason to differ and are compared
exactly (risk verdicts, approvals, execution attribution). Three are LLM or role
outputs where legitimate change exists, and those compare against a declared
allowance. The split is not a matter of taste — it is the difference between a
comparison whose `matched` verdict means something and one where it does not.

`SCHEDULE_OMISSION` deserves its own note. Both paths can miss the same trigger,
so agreement between them is not evidence the trigger was handled — it is
evidence both dropped it. Judging omissions therefore requires an **independent
expected set**, never the union or intersection of what the two paths did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

MATCHED = "matched"
DIVERGED = "diverged"
NOT_COMPARED = "not_compared"

# The six surfaces, in the order they appear in a report.
INPUT_SNAPSHOT = "input_snapshot"
SCHEDULE_OMISSION = "schedule_omission"
ANALYST_OUTPUT = "analyst_output"
RISK_VERDICT = "risk_verdict"
APPROVAL_CHAIN = "approval_chain"
TRADE_ATTRIBUTION = "trade_attribution"

SIX_SURFACES: tuple[str, ...] = (
    INPUT_SNAPSHOT, SCHEDULE_OMISSION, ANALYST_OUTPUT,
    RISK_VERDICT, APPROVAL_CHAIN, TRADE_ATTRIBUTION,
)

# Surfaces compared exactly. Any difference in these is a real difference: a risk
# verdict either passed the ruleset or it did not, and an approval either happened
# or it did not.
EXACT_SURFACES: frozenset[str] = frozenset({
    INPUT_SNAPSHOT, RISK_VERDICT, APPROVAL_CHAIN, TRADE_ATTRIBUTION,
})

# Surfaces where a legitimate change exists (LLM prose, restructured roles), so
# equality is judged against a declared allowance rather than byte identity.
TOLERATED_SURFACES: frozenset[str] = frozenset({
    SCHEDULE_OMISSION, ANALYST_OUTPUT,
})


@dataclass(frozen=True)
class Tolerance:
    """What counts as a difference on a surface that tolerates legitimate change.

    `metric` is compared rather than the raw values, so the allowance is stated in
    the terms the surface is judged in: a share of items for sets, absolute
    difference for numbers, containment for required elements.
    """

    metric: str
    allowance: float
    note: str = ""

    def evaluate(self, left: Any, right: Any) -> tuple[float, bool]:
        """Return (observed divergence, within allowance)."""
        if self.metric == "jaccard_distance":
            return _jaccard_distance(left, right), _jaccard_distance(left, right) <= self.allowance
        if self.metric == "absolute":
            delta = abs(_as_float(left) - _as_float(right))
            return delta, delta <= self.allowance
        if self.metric == "subset_violation":
            missing = [item for item in (right or []) if item not in (left or [])]
            ratio = (len(missing) / len(right)) if right else 0.0
            return ratio, ratio <= self.allowance
        raise ValueError(f"unknown tolerance metric {self.metric!r}")


# Default allowances. Deliberately not zero: a restructured role rephrases, and an
# allowance of zero would make the report a rephrasing detector. They are also not
# generous — a role rewrite that changes most of what it says should show up.
DEFAULT_TOLERANCES: dict[str, Tolerance] = {
    # A missed trigger is a missed trigger; the tolerance exists only for the
    # identity of the trigger set itself (retries of the same logical trigger).
    SCHEDULE_OMISSION: Tolerance(
        metric="jaccard_distance", allowance=0.0,
        note="every expected trigger must appear on both paths; any omission is "
             "a difference"),
    # Prose and role restructuring: the same conclusion reached in different
    # words is agreement. 0.34 is roughly "a third of the content may be
    # re-expressed" — enough for a rewrite, too little for a changed view.
    ANALYST_OUTPUT: Tolerance(
        metric="jaccard_distance", allowance=0.34,
        note="LLM and role-restructuring differences are legitimate; the share of "
             "changed elements must stay within the declared allowance"),
}

# Authority allowed to accept a difference on each tolerated surface (task 3.4).
DEFAULT_ACCEPTANCE_AUTHORITY: dict[str, str] = {
    ANALYST_OUTPUT: "chief_owner",
    SCHEDULE_OMISSION: "workflow_owner",
}


@dataclass
class SurfaceResult:
    """One surface's conclusion, with its differences named."""

    surface: str
    verdict: str
    reason: str = ""
    details: list[dict[str, Any]] = field(default_factory=list)
    observed_divergence: float | None = None
    allowance: float | None = None
    tolerance_note: str = ""

    @property
    def diverged(self) -> bool:
        return self.verdict == DIVERGED

    @property
    def not_compared(self) -> bool:
        return self.verdict == NOT_COMPARED

    def as_row(self) -> dict[str, Any]:
        return {
            "surface": self.surface, "verdict": self.verdict, "reason": self.reason,
            "details": list(self.details),
            "observed_divergence": self.observed_divergence,
            "allowance": self.allowance, "tolerance_note": self.tolerance_note,
        }


def _not_compared(surface: str, reason: str) -> SurfaceResult:
    return SurfaceResult(surface, NOT_COMPARED, reason=reason)


def _not_compared(surface: str, reason: str) -> SurfaceResult:
    return SurfaceResult(surface, NOT_COMPARED, reason=reason)


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _elements(value: Any) -> set[str]:
    """Coerce a comparable value into a set of identity strings."""
    if value is None:
        return set()
    if isinstance(value, (set, frozenset)):
        return {str(item) for item in value}
    if isinstance(value, (list, tuple)):
        return {str(item) for item in value}
    if isinstance(value, dict):
        return {f"{key}={value[key]}" for key in sorted(value)}
    return {str(value)}


def _jaccard_distance(left: Any, right: Any) -> float:
    lhs, rhs = _elements(left), _elements(right)
    if not lhs and not rhs:
        return 0.0
    union = lhs | rhs
    if not union:
        return 0.0
    return len(lhs ^ rhs) / len(union)


# --------------------------------------------------------------------------- #
# per-surface comparators
# --------------------------------------------------------------------------- #

def compare_input_snapshot(left: Any, right: Any) -> SurfaceResult:
    """Exact. If the two runs saw different inputs, nothing else is comparable."""
    if left is None or right is None:
        return _not_compared(INPUT_SNAPSHOT, "one side has no input snapshot")
    if left == right:
        return SurfaceResult(INPUT_SNAPSHOT, MATCHED, reason="identical packets")
    return SurfaceResult(
        INPUT_SNAPSHOT, DIVERGED,
        reason="the two runs were not given the same inputs, so no downstream "
               "difference can be attributed to the path change",
        details=[{"left": sorted(_elements(left)), "right": sorted(_elements(right))}])


def compare_schedule_omission(*, expected: Iterable[str], old_run: Iterable[str],
                              new_run: Iterable[str]) -> SurfaceResult:
    """Omissions against an INDEPENDENT expected set.

    Two ways this goes wrong, both silent:

    - Comparing the two runs to each other. If both missed a trigger they agree,
      and agreement would read as "handled". The expected set is what makes a
      shared miss visible.
    - Taking the union of what ran. A trigger neither path ran is invisible in a
      union by construction.
    """
    expected_set = {str(item) for item in expected or ()}
    old_set = {str(item) for item in old_run or ()}
    new_set = {str(item) for item in new_run or ()}

    if not expected_set:
        return _not_compared(
            SCHEDULE_OMISSION,
            "no independent expected trigger set was supplied; both paths "
            "omitting the same trigger would otherwise read as agreement")

    old_missing = sorted(expected_set - old_set)
    new_missing = sorted(expected_set - new_set)
    details = [
        {"trigger": trigger, "path": "old", "omitted": True}
        for trigger in old_missing
    ] + [
        {"trigger": trigger, "path": "new", "omitted": True}
        for trigger in new_missing
    ]
    extra_old = sorted(old_set - expected_set)
    extra_new = sorted(new_set - expected_set)
    details.extend(
        [{"trigger": trigger, "path": "old", "unexpected": True}
         for trigger in extra_old])
    details.extend(
        [{"trigger": trigger, "path": "new", "unexpected": True}
         for trigger in extra_new])

    if not details:
        return SurfaceResult(SCHEDULE_OMISSION, MATCHED,
                             reason=f"both paths covered all {len(expected_set)} "
                                    f"expected triggers")

    # Whether the two paths agreed on the omission is stated explicitly, because
    # "both missed it" is the case a naive comparison hides entirely.
    shared = sorted(set(old_missing) & set(new_missing))
    reason = (f"{len(old_missing)} omitted by the old path, "
              f"{len(new_missing)} by the new")
    if shared:
        reason += (f"; {len(shared)} were missed by BOTH — the paths agreeing on "
                   f"an omission is not evidence it was handled")
    return SurfaceResult(SCHEDULE_OMISSION, DIVERGED, reason=reason, details=details)


def compare_analyst_output(left: Any, right: Any,
                           *, tolerance: Tolerance | None = None) -> SurfaceResult:
    """Compared against the declared allowance, not byte equality."""
    tol = tolerance or DEFAULT_TOLERANCES[ANALYST_OUTPUT]
    if left is None or right is None:
        return _not_compared(ANALYST_OUTPUT, "one side produced no analyst output")
    divergence, within = tol.evaluate(left, right)
    result = SurfaceResult(
        ANALYST_OUTPUT, MATCHED if within else DIVERGED,
        reason=("within the declared allowance for legitimate change"
                if within else
                "exceeds the declared allowance for legitimate change; this is "
                "not accepted by default"),
        observed_divergence=divergence, allowance=tol.allowance,
        tolerance_note=tol.note)
    if not within:
        result.details = [{"left_only": sorted(_elements(left) - _elements(right)),
                           "right_only": sorted(_elements(right) - _elements(left))}]
    return result


def compare_risk_verdict(left: Any, right: Any) -> SurfaceResult:
    """Exact, including counterproposals.

    A risk verdict either passed the ruleset or it did not. Tolerance here would
    be a licence to trade on a rule that was not enforced.
    """
    if left is None or right is None:
        return _not_compared(RISK_VERDICT, "one side produced no risk verdict")
    if _verdict_equal(left, right):
        return SurfaceResult(RISK_VERDICT, MATCHED, reason="identical verdict and "
                                                         "counterproposal")
    return SurfaceResult(
        RISK_VERDICT, DIVERGED,
        reason="the two paths reached different risk conclusions; this must be "
               "accepted explicitly or the difference fixed and re-run",
        details=[{"left": _describe_verdict(left), "right": _describe_verdict(right)}])


def compare_approval_chain(left: Any, right: Any) -> SurfaceResult:
    """Exact, and it includes the round number.

    Comparing only "approved / not approved" would hide a difference in *how many
    rounds* it took, which is exactly the multi-round Chief–Risk loop's behaviour.
    """
    if left is None or right is None:
        return _not_compared(APPROVAL_CHAIN, "one side has no approval chain")
    if _verdict_equal(left, right):
        return SurfaceResult(APPROVAL_CHAIN, MATCHED,
                             reason="same outcome and same round")
    return SurfaceResult(
        APPROVAL_CHAIN, DIVERGED,
        reason="the approval outcomes or round numbers differ",
        details=[{"left": _describe_verdict(left), "right": _describe_verdict(right)}])


def compare_trade_attribution(left: Any, right: Any) -> SurfaceResult:
    """Exact. An order's attribution is a fact, not a phrasing."""
    if left is None or right is None:
        return _not_compared(TRADE_ATTRIBUTION,
                             "one side has no trade attribution")
    # Compared whole, NOT through `_verdict_equal`. That helper projects a dict
    # onto (outcome, counterproposal, round), which is right for a verdict and
    # wrong here: an attribution record has none of those keys, so both sides
    # would project to the same empty triple and a misattributed order would
    # compare as matched.
    if left == right:
        return SurfaceResult(TRADE_ATTRIBUTION, MATCHED, reason="identical attribution")
    return SurfaceResult(
        TRADE_ATTRIBUTION, DIVERGED,
        reason="the two paths attributed orders differently",
        details=[{"left_only": sorted(_elements(left) - _elements(right)),
                   "right_only": sorted(_elements(right) - _elements(left))}])


def _verdict_equal(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return _normalise_verdict(left) == _normalise_verdict(right)
    return left == right


def _normalise_verdict(value: dict) -> tuple:
    """Comparand for a verdict dict: outcome, counterproposal and round.

    `round_no` is included because a narrower approval after a counterproposal is a
    different approval outcome even though both say "approved".
    """
    outcome = str(value.get("verdict") or value.get("status") or value.get("action") or "")
    counter = _normalise_counterproposal(value.get("counterproposal"))
    round_no = value.get("round_no", value.get("cycle", value.get("rounds")))
    return outcome, counter, round_no


def _normalise_counterproposal(value: Any) -> Any:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, dict):
        return tuple(sorted((str(k), _normalise_counterproposal(v))
                            for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_normalise_counterproposal(item) for item in value)
    return str(value)


def _describe_verdict(value: Any) -> str:
    if isinstance(value, dict):
        return (f"{value.get('verdict') or value.get('status') or '?'}"
                f" round={value.get('round_no', '?')}"
                f" counterproposal={'yes' if value.get('counterproposal') else 'no'}")
    return str(value)


# --------------------------------------------------------------------------- #
# the whole comparison
# --------------------------------------------------------------------------- #

@dataclass
class ComparisonResult:
    """All six surfaces plus the verdict. Never a summary count.

    A summary would hide exactly the thing the report exists to show: three
    surfaces matched, one diverged, two were not compared. `as_row` keeps the
    per-surface list rather than a tally for that reason.
    """

    run_id: str
    surfaces: dict[str, SurfaceResult] = field(default_factory=dict)
    packet_left: str = ""
    packet_right: str = ""

    def verdict_for(self, surface: str) -> str:
        result = self.surfaces.get(surface)
        return result.verdict if result else NOT_COMPARED

    @property
    def diverged(self) -> list[str]:
        return [s for s, r in self.surfaces.items() if r.diverged]

    @property
    def not_compared(self) -> list[str]:
        return [s for s, r in self.surfaces.items() if r.not_compared]

    @property
    def matched(self) -> list[str]:
        return [s for s, r in self.surfaces.items() if r.verdict == MATCHED]

    def usable_as_evidence(self, *, required: Iterable[str] = ()) -> tuple[bool, list[str]]:
        """Whether the required surfaces have real conclusions.

        `not-compared` on a required surface is not evidence of agreement, so it
        fails here rather than being left for a later check to notice.
        """
        problems: list[str] = []
        for surface in required:
            if surface not in self.surfaces:
                problems.append(f"{surface}: not compared at all")
            elif self.surfaces[surface].not_compared:
                problems.append(
                    f"{surface}: {self.surfaces[surface].reason}")
        return (not problems, problems)

    def as_row(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "packet_left": self.packet_left,
            "packet_right": self.packet_right,
            "verdicts": {s: r.verdict for s, r in sorted(self.surfaces.items())},
            "surfaces": [r.as_row() for _, r in sorted(self.surfaces.items())],
            "diverged": self.diverged,
            "not_compared": self.not_compared,
            "matched": self.matched,
        }


def compare_all(*, run_id: str,
                left: dict[str, Any], right: dict[str, Any],
                expected_triggers: Iterable[str] | None = None,
                analyst_tolerance: Tolerance | None = None,
                required: Iterable[str] | None = None) -> ComparisonResult:
    """Compare all six surfaces for one batch.

    `left` / `right` are keyed by surface; a surface absent from the dict is
    recorded as `not-compared` with the reason, never skipped. `expected_triggers`
    is passed through rather than derived — see `compare_schedule_omission`.
    """
    result = ComparisonResult(run_id=run_id,
                              packet_left=str(left.get("_packet_left", "")),
                              packet_right=str(right.get("_packet_right", "")))

    result.surfaces[INPUT_SNAPSHOT] = compare_input_snapshot(
        left.get("_packet"), right.get("_packet"))

    if expected_triggers is None:
        result.surfaces[SCHEDULE_OMISSION] = _not_compared(
            SCHEDULE_OMISSION,
            "no independent expected trigger set was supplied")
    else:
        result.surfaces[SCHEDULE_OMISSION] = compare_schedule_omission(
            expected=expected_triggers,
            old_run=left.get(SCHEDULE_OMISSION, ()),
            new_run=right.get(SCHEDULE_OMISSION, ()))

    result.surfaces[ANALYST_OUTPUT] = compare_analyst_output(
        left.get(ANALYST_OUTPUT), right.get(ANALYST_OUTPUT),
        tolerance=analyst_tolerance)

    result.surfaces[RISK_VERDICT] = compare_risk_verdict(
        left.get(RISK_VERDICT), right.get(RISK_VERDICT))
    result.surfaces[APPROVAL_CHAIN] = compare_approval_chain(
        left.get(APPROVAL_CHAIN), right.get(APPROVAL_CHAIN))
    result.surfaces[TRADE_ATTRIBUTION] = compare_trade_attribution(
        left.get(TRADE_ATTRIBUTION), right.get(TRADE_ATTRIBUTION))

    if required is not None:
        result.usable_as_evidence(required=required)
    return result
