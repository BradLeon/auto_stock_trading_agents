"""Consumer zero, data reconciliation, and tombstone consistency (12.1–12.7).

Three questions, deliberately kept apart because each has a different failure:

- **Is anyone still reading through the old implementation?** (zero)
- **Do the two paths agree on everything the old one was used for?** (reconcile)
- **Does a tombstone say retired only when nothing can read it?** (consistency)

**Why "zero" is almost never zero.** Five of the ten consumers still reach their
data through `ats.execution.state_api` / the product layer's internals rather
than the governed read API, so the honest reading today is "five still do". A
retirement entry may only leave `pending` once its consumers reach zero — and
the load-bearing part is that **reaching zero is not the same as switching**.
A role can be moved onto the governed surface and still be served by the legacy
route until that route is cut, which is why a separate reconciliation criterion
exists.

**Why reconciliation demands every use, not one metric.** Reconciling one number
proves one number. The old implementation existed to serve several purposes; a
purpose left unreconciled is a purpose that can drift without anyone noticing,
and it surfaces much later as a report that disagrees with itself. So the
criterion enumerates the uses and refuses a partial one.

**Why a tombstone is checked against behaviour.** `status: retired` is a claim.
A claim that nothing reads the data any more is checkable, and this module checks
it: if a retired identifier is still reachable, the registry is wrong — which is
worse than the entry having stayed pending, because it tells the next reader the
work is done.

**What this module does not do.** It does not switch anything, and it does not
modify the registry. It produces verdicts and, for entries whose criteria are now
met, the exact wording that would mark them retired. Applying that wording is a
deliberate, separate step.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence

from . import legacy_retirement as retirement

# Verdict states. `partial` is separate from `pending` on purpose: "we looked and
# part of the answer is knowable" is a different statement from "not yet", and
# collapsing them hides the work that did land.
ZERO = "zero"
NOT_ZERO = "not_zero"
PARTIAL = "partial"
RECONCILED = "reconciled"
NOT_RECONCILED = "not_reconciled"
CONSISTENT = "consistent"
INCONSISTENT = "inconsistent"
WINDOW_OK = "window_ok"
WINDOW_REFUSED = "window_refused"
REFUNDABLE = "refundable"


@dataclass
class ZeroVerdict:
    """Whether one registered consumer still reaches the legacy implementation."""

    identifier: str
    consumer: str
    verdict: str
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def blocks_exit(self) -> bool:
        return self.verdict != ZERO

    def as_row(self) -> dict[str, Any]:
        return {"identifier": self.identifier, "consumer": self.consumer,
                "verdict": self.verdict, "reason": self.reason,
                "evidence": dict(self.evidence)}


@dataclass
class ReconcileVerdict:
    """Whether old and new agree on *every* use the old one served."""

    identifier: str
    verdict: str
    uses: dict[str, dict[str, Any]] = field(default_factory=dict)
    reason: str = ""
    # Carried so a report can show "3 of 7" without re-deriving the denominator.
    required_uses: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return self.verdict == RECONCILED

    def as_row(self) -> dict[str, Any]:
        return {"identifier": self.identifier, "verdict": self.verdict,
                "uses": dict(self.uses), "reason": self.reason,
                "required_uses": list(self.required_uses)}


@dataclass
class TombstoneVerdict:
    """Does a retired entry still have readers?"""

    identifier: str
    verdict: str
    registered_status: str = ""
    readable_by: tuple[str, ...] = ()
    reason: str = ""

    def as_row(self) -> dict[str, Any]:
        return {"identifier": self.identifier, "verdict": self.verdict,
                "registered_status": self.registered_status,
                "readable_by": list(self.readable_by), "reason": self.reason}


# --- gates ------------------------------------------------------------------

G0_VERIFICATION = Path(__file__).resolve().parents[3] / "openspec" / "changes" / \
    "retire-legacy-and-migration-tooling" / "verification.md"
_OPEN_VERDICTS = {"通过", "接受缺口", "不适用"}


@dataclass
class GateStatus:
    """What the manual verification (G0) says today — read from verification.md, never written."""

    gate: str
    is_open: bool
    reason: str
    total: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    not_open: tuple[str, ...] = ()
    concluded: bool = False

    def as_row(self) -> dict[str, Any]:
        return {"gate": self.gate, "open": self.is_open, "reason": self.reason, "total": self.total,
                "counts": dict(self.counts), "not_open": list(self.not_open), "concluded": self.concluded}


def read_g0_gate(path: Path | str | None = None) -> GateStatus:
    """G0 is open only when every item has a verdict that lets the retirement go ahead AND the conclusion is signed.

    Open verdicts are 通过 / 接受缺口 / 不适用. 未通过 and an empty verdict both block: "not yet looked at" is not a pass.
    """
    target = Path(path) if path else G0_VERIFICATION
    if not target.exists():
        return GateStatus("G0", False, f"verification record not found: {target}")
    text = target.read_text(encoding="utf-8")
    rows = [(m.group(1), line.split("|")[3].strip()) for line in text.splitlines()
            if (m := re.match(r"\| ([A-Z]\d{2}) \|", line)) and len(line.split("|")) > 4]
    counts: dict[str, int] = {}
    for _id, verdict in rows:
        counts[verdict or "空"] = counts.get(verdict or "空", 0) + 1
    not_open = tuple(item for item, verdict in rows if verdict not in _OPEN_VERDICTS)
    conclusion = re.search(r"\*\*G0 结论：\*\*\s*(.*)", text)
    concluded = bool(conclusion) and conclusion.group(1).strip().startswith("通过") \
        and "不通过" not in conclusion.group(1)
    if not rows:
        return GateStatus("G0", False, "no verification rows found", 0, counts, (), concluded)
    if not_open:
        return GateStatus("G0", False, f"{len(not_open)} item(s) without an open verdict: {list(not_open)}",
                          len(rows), counts, not_open, concluded)
    if not concluded:
        return GateStatus("G0", False, "every item has a verdict but the G0 conclusion is not signed",
                          len(rows), counts, (), False)
    return GateStatus("G0", True, "all items verified and the G0 conclusion is signed", len(rows), counts, (), True)


def evaluate_gate(gate: str, *, g0: GateStatus | None = None) -> tuple[bool, str]:
    """(open?, why-not). An entry with no gate is not gated; an external gate is never opened from here."""
    if not gate:
        return True, ""
    if gate == "G0":
        status = g0 or read_g0_gate()
        return status.is_open, ("" if status.is_open else f"gate G0 not met: {status.reason}")
    return False, f"external gate not met: {gate} (owned by another change, never opened from here)"


@dataclass
class EntryDecision:
    """What may be said about one registry entry today."""

    identifier: str
    status: str
    may_exit: bool
    reason: str
    missing: tuple[str, ...] = ()
    proposed_status: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        return {"identifier": self.identifier, "status": self.status,
                "may_exit": self.may_exit, "reason": self.reason,
                "missing": list(self.missing),
                "proposed_status": self.proposed_status,
                "evidence": dict(self.evidence)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# The retirement registry names its consumers as **module paths**
# (`agents.fundamental`, `trader.execute`, `memory.store.save_trades`); the intake
# scanner takes **role ids** (`fundamental`, `trader`).
#
# Only *role-shaped* consumers can be checked by the scanner. The registry also
# names plenty that are not roles at all — `chain.*`, `memory.store.*`,
# `runtime.*`, even `tests/*` — and those are cleared by the access reader, not
# by scanning a module's imports. Guessing a role from a path is refused: the last
# dotted segment is the module, not the role (`agents.risk_officer.review` would
# give "review"), so a guess could scan the wrong role and read as zero — the
# exact false clean this module exists to prevent.
MODULE_TO_ROLE: dict[str, str] = {
    "agents.chief.assemble": "chief",
    "agents.chief.decide": "chief",
    "agents.fundamental": "fundamental",
    "agents.fundamental.entry": "fundamental",
    "agents.fundamental.routine": "fundamental",
    "agents.fundamental.event": "fundamental",
    "agents.layer.layer_review": "layer",
    "agents.information.entry": "information",
    "agents.sector.review": "sector",
    "agents.macro.review": "macro",
    "agents.technical.review": "technical",
    "agents.risk_officer.review": "risk",
    "trader.execute": "trader",
    "execution.clerk": "clerk",
}


def role_for(module_path: str) -> str:
    """The role id behind a registered module path.

    Raises rather than guessing. An unmapped path means the registry named a
    consumer this module cannot verify, and scanning a wrong role (or treating it
    as zero) would let an entry exit on evidence that was never collected.
    """
    key = str(module_path or "").strip()
    if key in MODULE_TO_ROLE:
        return MODULE_TO_ROLE[key]
    raise KeyError(
        f"no role id is registered for module path {module_path!r}. Add it to "
        "MODULE_TO_ROLE rather than guessing: the last dotted segment is the "
        "module, not the role (e.g. 'agents.risk_officer.review' → 'risk')")


# --- 12.1 consumer zero ------------------------------------------------------

def verify_consumer_zero(identifier: str, consumers: Sequence[str], *,
                         access_reader: Callable[[str], dict[str, Any]]
                         | None = None,
                         scan: Callable[[str], Any] | None = None,
                         comparison_path_still_live: bool = False,
                         ) -> list[ZeroVerdict]:
    """12.1: does anyone still reach the legacy implementation?

    `comparison_path_still_live` keeps an entry pending for a reason that has
    nothing to do with its consumers: the old/new comparison that proves the two
    paths agree is itself still reading both. Until it comes down, "zero readers
    of the old path" would be measured with the old path still wired up — a
    clean result obtained by an instrument that is part of what it measures.
    """
    verdicts: list[ZeroVerdict] = []

    for consumer in consumers:
        if access_reader is not None:
            reading = access_reader(consumer)
            legacy = bool(reading.get("legacy_reads"))
            verdict = NOT_ZERO if legacy else ZERO
            verdicts.append(ZeroVerdict(
                identifier=identifier, consumer=consumer, verdict=verdict,
                reason=reading.get("reason") or (
                    f"still reaches {reading['legacy_reads']}" if legacy
                    else "no legacy read recorded"),
                evidence=reading))
            continue

        if scan is not None:
            try:
                role = role_for(consumer)
            except KeyError as exc:
                verdicts.append(ZeroVerdict(
                    identifier=identifier, consumer=consumer, verdict=PARTIAL,
                    reason=str(exc)))
                continue
            record = scan(role)
            kinds = sorted({getattr(v, "kind", "") for v in getattr(record, "violations", ())})
            # Reaching the governed surface is what zero means here. A retained
            # but unreachable read (`disabled_bypass`) still counts as a finding
            # against the consumer, but it is reported separately so the
            # criterion being evaluated stays clear.
            governed = any(m.startswith("ats.data.consumer_api")
                           for m in getattr(record, "call_path", ())) or any(
                           m.startswith("ats.data.products")
                           for m in getattr(record, "call_path", ()))
            verdict = ZERO if governed and "governed_read_surface_not_reached" not in kinds \
                else NOT_ZERO
            verdicts.append(ZeroVerdict(
                identifier=identifier, consumer=consumer, verdict=verdict,
                reason=("reaches the governed read surface" if governed and
                        verdict == ZERO else
                        "the role's own modules reach neither the governed read API "
                        "nor the product layer"),
                evidence={"violations": kinds,
                          "call_path": list(getattr(record, "call_path", ()))[:6]}))
            continue

        verdicts.append(ZeroVerdict(
            identifier=identifier, consumer=consumer, verdict=PARTIAL,
            reason="no access reader and no scanner were supplied, so whether "
                    "this consumer still reads the legacy path is unknown. "
                    "Unknown is not zero"))

    if comparison_path_still_live:
        for verdict in verdicts:
            verdict.evidence["comparison_path_still_live"] = True
            if verdict.verdict == ZERO:
                verdict.verdict = PARTIAL
                verdict.reason = (
                    "the old/new comparison that proves agreement is itself still "
                    "reading the legacy path; zero readers cannot be asserted while "
                    "the measuring instrument is still connected")

    return verdicts


# --- 12.2 reconciliation -----------------------------------------------------

def reconcile(identifier: str, uses: dict[str, dict[str, Any]], *,
              required_uses: Sequence[str],
              retired_data_readable: bool = False) -> ReconcileVerdict:
    """12.2: do old and new agree on **every** use the old one served?

    `required_uses` is the enumeration of what the old implementation was for.
    Reconciling a subset is refused outright rather than reported as partial
    success, because a partial pass reads as a pass in any summary line that
    lists counts.
    """
    result = ReconcileVerdict(identifier=identifier, verdict=NOT_RECONCILED,
                             required_uses=tuple(required_uses))
    missing = [u for u in required_uses if u not in uses]
    agreed: list[str] = []
    disagreed: list[str] = []

    for use in required_uses:
        if use in missing:
            continue
        row = uses[use]
        result.uses[use] = dict(row)
        if row.get("agrees") is True:
            agreed.append(use)
        else:
            disagreed.append(use)

    if retired_data_readable:
        result.verdict = NOT_RECONCILED
        result.reason = (
            "a use reads data that has already been retired, so agreement cannot "
            "be established — the old path's own input is gone")
        return result

    if missing:
        result.reason = (
            f"only {len(agreed)} of {len(required_uses)} uses were reconciled; "
            f"not reconciled: {missing}. Reconciling one metric proves one metric — "
            "a purpose left uncompared can drift without anyone noticing")
        return result

    if disagreed:
        result.reason = (f"these uses disagree between old and new: {disagreed}")
        return result

    result.verdict = RECONCILED
    result.reason = f"all {len(required_uses)} uses agree"
    return result


# --- 12.3 rollback window ----------------------------------------------------

def verify_rollback_window(*, drilled: bool, drill_reference: str = "",
                           stop_conditions: Sequence[str] = (),
                           triggered: Sequence[str] = ()) -> str:
    """12.3: may this boundary be marked retired?

    Two requirements, both about evidence rather than opinion. An **undrilled**
    boundary is refused outright — an untested way back is not a way back — and a
    boundary whose stop conditions fired during the window stays pending even
    though the rollback itself worked, because the window was not clean.
    """
    if not drilled or not str(drill_reference or "").strip():
        return WINDOW_REFUSED
    if triggered:
        return WINDOW_REFUSED
    return WINDOW_OK


# --- 12.4 tombstone vs behaviour ---------------------------------------------

def check_tombstone_behaviour(identifier: str, registered_status: str, *,
                              readable_by: Sequence[str] = (),
                              ) -> TombstoneVerdict:
    """12.4: a tombstone that says retired must actually be unreadable.

    This is the one check that can fail an entry the registry already considers
    done — which is the point. A `retired` entry that still has readers is worse
    than a `pending` one: it tells the next reader the work is finished.
    """
    verdict = TombstoneVerdict(identifier=identifier,
                               verdict=CONSISTENT,
                               registered_status=registered_status,
                               readable_by=tuple(readable_by))
    if registered_status == "retired" and readable_by:
        verdict.verdict = INCONSISTENT
        verdict.reason = (
            f"registered as retired but still readable by {list(readable_by)}. "
            "A retired claim that is false is worse than a pending one: it tells "
            "the next reader the work is done")
    return verdict


# --- 12.5 fallback target pre-check ------------------------------------------

def precheck_fallback_target(target_route: str, *,
                             is_retired: Callable[[str], bool],
                             is_available: Callable[[str], bool] | None = None,
                             ) -> tuple[bool, str]:
    """12.5: refuse a fallback target that is retired — **before** calling it.

    Ordering matters more than the verdict. Calling a route that cannot serve
    and reporting afterwards is how an outage becomes a different outage; the
    caller gets `(False, reason)` and does not make the call at all.
    """
    if not str(target_route or "").strip():
        return False, "no fallback target was named"
    if is_retired(target_route):
        return False, (
            f"fallback target {target_route!r} is registered as retired; a tombstone "
            "is not a destination. The rollback is not attempted — calling a route "
            "that cannot serve turns one outage into a different one")
    if is_available is not None and not is_available(target_route):
        return False, (
            f"fallback target {target_route!r} is not retired but is not available "
            "either; the rollback is not attempted")
    return True, f"fallback target {target_route!r} is registered and not retired"


# --- 12.6 per-entry decision -------------------------------------------------

def decide_entry(identifier: str, *,
                 zero_verdicts: Sequence[ZeroVerdict] = (),
                 reconcile_verdict: ReconcileVerdict | None = None,
                 window: str = WINDOW_REFUSED,
                 tombstone: TombstoneVerdict | None = None,
                 required_uses: Sequence[str] = (),
                 unmet_dependencies: Sequence[str] = (),
                 gate: str = "",
                 gate_open: bool = True,
                 gate_reason: str = "") -> EntryDecision:
    """12.6: what may be said about one entry today.

    Reports `retired` only when **all** criteria hold. Otherwise it stays
    `pending` and states what is missing — the spec's "只登记不退出", and the
    reason an entry must not be quietly promoted on partial evidence.
    """
    missing: list[str] = []

    if unmet_dependencies:
        missing.append(f"depends on entries that have not retired: {list(unmet_dependencies)}")
    if gate and not gate_open:
        missing.append(gate_reason or f"gate {gate} not met")

    not_zero = [v.consumer for v in zero_verdicts if v.blocks_exit]
    partial = [v.consumer for v in zero_verdicts if v.verdict == PARTIAL]
    if not_zero:
        missing.append(f"consumers still on the legacy path: {not_zero}")
    if partial:
        missing.append(f"consumer state unknown (not verified zero): {partial}")

    if reconcile_verdict is not None:
        if reconcile_verdict.identifier == identifier and not reconcile_verdict.complete:
            missing.append(f"reconciliation incomplete: {reconcile_verdict.reason}")

    if window != WINDOW_OK:
        missing.append(
            "rollback not drilled" if not window or window == WINDOW_REFUSED
            else f"rollback window: {window}")

    if tombstone is not None and tombstone.verdict == INCONSISTENT:
        missing.append(f"tombstone inconsistency: {tombstone.reason}")

    may_exit = not missing
    return EntryDecision(
        identifier=identifier,
        status="retired" if may_exit else "pending",
        may_exit=may_exit,
        reason=("every criterion holds" if may_exit else
                "; ".join(missing)),
        missing=tuple(missing),
        proposed_status="retired" if may_exit else "pending",
        evidence={"required_uses": list(required_uses),
                  "unmet_dependencies": list(unmet_dependencies),
                  "gate": gate, "gate_open": gate_open,
                  "zero": [v.as_row() for v in zero_verdicts],
                  "reconcile": reconcile_verdict.as_row()
                  if reconcile_verdict else None,
                  "window": window,
                  "tombstone": tombstone.as_row() if tombstone else None,
                  "decided_at": _now()})


def render_report(decisions: Sequence[EntryDecision],
                  zero: Sequence[ZeroVerdict] = (),
                  reconcile: Sequence[ReconcileVerdict] = ()) -> str:
    """Operator-facing. Names every entry that stays pending and what it waits on."""
    lines = ["# 消费者清零与对账核验（11.8）", ""]
    may_exit = [d for d in decisions if d.may_exit]
    pending = [d for d in decisions if not d.may_exit]
    lines.append(f"{len(decisions)} 条登记 · 可退出 **{len(may_exit)}** · "
                 f"保持 pending **{len(pending)}**")
    lines += ["", "| 条目 | 结论 | 可退出 |", "|---|---|---|"]
    for d in decisions:
        lines.append(f"| `{d.identifier}` | {d.status} | {'是' if d.may_exit else '**否**'} |")

    if zero:
        lines += ["", "## 逐消费方清零", ""]
        lines += ["| 条目 | 消费方 | 结论 | 说明 |", "|---|---|---|---|"]
        for v in zero:
            lines.append(f"| `{v.identifier}` | `{v.consumer}` | {v.verdict} | "
                         f"{v.reason} |")

    if reconcile:
        lines += ["", "## 数据对账", ""]
        lines += ["| 条目 | 结论 | 已对账 | 全部用途 |", "|---|---|---|---|"]
        for r in reconcile:
            total = sum(1 for u in r.uses.values() if u.get("agrees") is True)
            lines.append(f"| `{r.identifier}` | {r.verdict} | {total} | "
                         f"{len(r.required_uses)} |")

    if pending:
        lines += ["", "## 保持 pending 的条目及其所缺条件", ""]
        for d in pending:
            lines += [f"### `{d.identifier}`", ""]
            lines.append(f"- {d.reason}")
            lines.append("")

    lines += ["", "**缺项只阻断受影响范围**：未满足清零/对账条件的条目保持 pending，"
             "不得为使其可退出而降低判据标准；已满足的条目亦不得据此放行其它范围。"]
    return "\n".join(lines)
