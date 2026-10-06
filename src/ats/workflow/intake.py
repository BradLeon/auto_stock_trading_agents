"""Code-freeze check and versioned prerequisite intake (tasks 6.1–6.3).

Phase F is about to take evidence. Taking it against code that is still moving
makes the evidence a description of a system that no longer exists, so the freeze
check runs FIRST and refuses to let intake proceed on a dirty surface.

- **6.1 — the freeze check.** Compares the fingerprint-constrained files against the
  baseline the evidence will be taken against, and reports each difference with the
  consumers it would invalidate. Not a warning: intake is blocked while it fails,
  because the alternative is evidence that silently describes the wrong code.
- **6.2 — the versioned intake matrix.** One row per prerequisite (the A–E
  programmes and the dataflow programme), each with its implementation, command,
  evidence reference, acceptance result, gaps, owner and dependencies. The rule
  that matters: **acceptance is by verified entry point, not by the static
  manifest.** A manifest entry naming a command that no longer runs is NOT accepted,
  which is the failure mode a static checklist produces.
- **6.3 — reuse is registered, not re-collected.** An item with verifiable material
  and cache evidence records the reference and its timestamp; re-running a full
  collection to "confirm" a capability that already has evidence is both slow and a
  way to accidentally produce fresh evidence with a different scope.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from ..config import REPO_ROOT

# Intake verdicts. `accepted` requires verified entry points; `reused` is a distinct
# outcome because it changes what the batch cost and what a later reader must check.
ACCEPTED = "accepted"
REUSED = "reused"
NOT_ACCEPTED = "not_accepted"
PARTIAL = "partial"

ACCEPTED_VERDICTS: frozenset[str] = frozenset({ACCEPTED, REUSED})


class IntakeError(RuntimeError):
    """Intake cannot proceed on the current tree."""


# --------------------------------------------------------------------------- #
# 6.1 — the freeze check
# --------------------------------------------------------------------------- #

@dataclass
class FreezeDifference:
    """One changed fingerprint-constrained file, and who it would retire."""

    path: str
    consumers: tuple[str, ...]
    against: str = ""

    def as_row(self) -> dict[str, Any]:
        return {"path": self.path, "consumers": list(self.consumers),
                "against": self.against}


@dataclass
class FreezeReport:
    clean: bool
    baseline: str
    differences: list[FreezeDifference] = field(default_factory=list)
    fingerprint_paths: tuple[str, ...] = ()
    unregistered: list[str] = field(default_factory=list)

    @property
    def invalidated_consumers(self) -> tuple[str, ...]:
        out: set[str] = set()
        for difference in self.differences:
            out.update(difference.consumers)
        return tuple(sorted(out))

    def as_row(self) -> dict[str, Any]:
        return {
            "clean": self.clean, "baseline": self.baseline,
            "differences": [d.as_row() for d in self.differences],
            "fingerprint_paths": list(self.fingerprint_paths),
            "unregistered": list(self.unregistered),
            "invalidated_consumers": list(self.invalidated_consumers),
        }


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(["git", *args], cwd=str(cwd or REPO_ROOT),
                               capture_output=True, text=True)
    return completed.stdout.strip()


def freeze_check(baseline: str, *, declared_changes: Iterable[str] = (),
                 path: str | Path | None = None) -> FreezeReport:
    """Compare the constrained surface against `baseline`.

    `declared_changes` is what Phase F itself changed on purpose. Those are not
    violations — they are the changes that must be *inside* the evidence window,
    so a row saying "declared" is different from a row saying "unregistered".

    A change that is neither declared nor expected is what blocks intake: somebody
    changed the surface without saying why, and every evidence row about it is now
    about unknown code.
    """
    from .assurance_surface import load_surface

    surface = load_surface()
    tracked = set(surface.all_paths())
    declared = set(declared_changes)

    changed = [line for line in _git("diff", "--name-only", baseline, "HEAD").splitlines()
               if line]
    constrained = sorted(path_ for path_ in changed if path_ in tracked)

    differences = [
        FreezeDifference(path_, surface.consumers_touched_by(path_), against=baseline)
        for path_ in constrained
    ]
    unregistered = sorted(set(constrained) - declared)

    return FreezeReport(
        clean=not unregistered,
        baseline=baseline,
        differences=differences,
        fingerprint_paths=tuple(sorted(tracked)),
        unregistered=unregistered,
    )


def assert_frozen(baseline: str, *, declared_changes: Iterable[str] = (),
                  path: str | Path | None = None) -> FreezeReport:
    """Block intake when the constrained surface moved without being declared."""
    report = freeze_check(baseline, declared_changes=declared_changes, path=path)
    if not report.clean:
        raise IntakeError(
            f"the evidence fingerprint surface moved since {baseline} in "
            f"{len(report.unregistered)} file(s) without being declared:\n"
            + "\n".join(
                f"  {item} — would invalidate evidence for: "
                f"{', '.join(report.invalidated_consumers) or 'every consumer'}"
                for item in report.unregistered)
            + "\nEither declare them (they are part of the evidence window) or "
              "revert them. Taking evidence now would describe code that is about "
              "to change.")
    return report


# --------------------------------------------------------------------------- #
# 6.2 — the versioned intake matrix
# --------------------------------------------------------------------------- #

# The prerequisites Phase F must receive. Ordered so the report reads in dependency
# order rather than alphabetically.
PREREQUISITES: tuple[str, ...] = (
    "phase_a_contracts_and_baseline",
    "phase_b_decision_audit_and_trade_safety",
    "phase_c_clerk_and_trade_ledger",
    "phase_d_analyst_roles_and_chief_inputs",
    "phase_e_dispatcher_and_calendar",
    "dataflow_managed_refresh_chain",
)

MATRIX_VERSION = "phase-f-intake-v1"


@dataclass
class IntakeRow:
    """One prerequisite's acceptance record.

    `entry_points_verified` is the load-bearing field: acceptance is decided by
    running or inspecting the actual entry point, not by the presence of a name in
    a manifest.
    """

    prerequisite: str
    implementation_ref: str = ""
    command: str = ""
    entry_points_verified: bool = False
    verification_method: str = ""
    evidence_ref: str = ""
    evidence_at: str = ""
    verdict: str = NOT_ACCEPTED
    gaps: list[str] = field(default_factory=list)
    owner: str = ""
    depends_on: list[str] = field(default_factory=list)
    reused_material: bool = False

    def as_row(self) -> dict[str, Any]:
        return {
            "prerequisite": self.prerequisite,
            "implementation_ref": self.implementation_ref,
            "command": self.command,
            "entry_points_verified": self.entry_points_verified,
            "verification_method": self.verification_method,
            "evidence_ref": self.evidence_ref,
            "evidence_at": self.evidence_at,
            "verdict": self.verdict,
            "gaps": list(self.gaps),
            "owner": self.owner,
            "depends_on": list(self.depends_on),
            "reused_material": self.reused_material,
        }


def assess_row(row: IntakeRow, *, entry_point_runnable: Callable[[str], bool] | None = None
               ) -> IntakeRow:
    """Decide one row's verdict from what was verified, not what was declared.

    The case that matters: a manifest lists `ats collect --full` but that command
    no longer parses. Treating the manifest entry as evidence would accept a
    prerequisite on the strength of a name.
    """
    gaps = list(row.gaps)

    if row.command and entry_point_runnable is not None:
        row.entry_points_verified = bool(entry_point_runnable(row.command))
        if not row.entry_points_verified:
            gaps.append(
                f"the registered entry point {row.command!r} is not runnable; a "
                f"static manifest entry is not evidence that it still works")

    if not row.evidence_ref:
        gaps.append("no evidence reference recorded")

    if row.entry_points_verified and row.evidence_ref and not gaps:
        # 6.3: reuse is a distinct acceptance, and it records the reference and time
        # rather than re-running the collection.
        row.verdict = REUSED if row.reused_material else ACCEPTED
    elif row.entry_points_verified and row.evidence_ref and gaps == [
            item for item in gaps if item.startswith("the registered entry point")]:
        row.verdict = PARTIAL
    else:
        row.verdict = NOT_ACCEPTED

    row.gaps = gaps
    return row


@dataclass
class IntakeMatrix:
    """A versioned set of rows plus the verdict derived from them."""

    version: str
    baseline: str
    rows: list[IntakeRow] = field(default_factory=list)
    freeze: FreezeReport | None = None
    declared_at: str = ""

    def __post_init__(self) -> None:
        if not self.declared_at:
            self.declared_at = datetime.now(timezone.utc).isoformat()

    def row(self, prerequisite: str) -> IntakeRow:
        for row in self.rows:
            if row.prerequisite == prerequisite:
                return row
        raise IntakeError(f"no intake row for {prerequisite!r}")

    @property
    def accepted(self) -> tuple[str, ...]:
        return tuple(r.prerequisite for r in self.rows
                     if r.verdict in ACCEPTED_VERDICTS)

    @property
    def blocked(self) -> tuple[str, ...]:
        return tuple(r.prerequisite for r in self.rows
                     if r.verdict not in ACCEPTED_VERDICTS)

    def assert_no_unmet_dependency(self, prerequisite: str) -> None:
        """Refuse to accept a row whose dependency was not accepted.

        A prerequisite accepted on its own merits but resting on something not
        accepted is not accepted — the gap is inherited.
        """
        for dependency in self.row(prerequisite).depends_on:
            if dependency not in self.accepted:
                raise IntakeError(
                    f"{prerequisite} depends on {dependency}, which is "
                    f"{self.row(dependency).verdict}; a prerequisite cannot be "
                    f"accepted while its dependency is not")

    def as_row(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "baseline": self.baseline,
            "declared_at": self.declared_at,
            "rows": [r.as_row() for r in self.rows],
            "accepted": list(self.accepted),
            "blocked": list(self.blocked),
            "freeze": self.freeze.as_row() if self.freeze else None,
        }

    def render(self) -> str:
        lines = [f"# 前置接收矩阵 {self.version}（基线 {self.baseline}）", ""]
        lines.append("| 前置项 | 结论 | 入口已核验 | 证据引用 | 缺口 | 责任方 |")
        lines.append("|---|---|---|---|---|---|")
        for row in self.rows:
            lines.append(
                f"| `{row.prerequisite}` | {row.verdict} | "
                f"{'是' if row.entry_points_verified else '否'} | "
                f"{row.evidence_ref or '—'} | "
                f"{'；'.join(row.gaps) if row.gaps else '—'} | "
                f"{row.owner or '—'} |")
        if self.blocked:
            lines += ["", "未接收项只能阻断受影响范围，不得据此放行其他项。"]
        return "\n".join(lines)


def build_matrix(*, baseline: str, freeze: FreezeReport | None = None,
                 rows: list[IntakeRow] | None = None,
                 entry_point_runnable: Callable[[str], bool] | None = None,
                 owners: dict[str, str] | None = None,
                 ) -> IntakeMatrix:
    """Assemble the matrix, assessing each row from what was verified."""
    declared_owners = dict(owners or {})
    materialised = []
    for row in (rows or []):
        row.owner = row.owner or declared_owners.get(row.prerequisite, "")
        materialised.append(assess_row(row, entry_point_runnable=entry_point_runnable))
    return IntakeMatrix(version=MATRIX_VERSION, baseline=baseline, rows=materialised,
                        freeze=freeze)


def assert_intake_ready(matrix: IntakeMatrix) -> IntakeMatrix:
    """Intake must start from a frozen surface and a clean freeze check.

    Two reasons, and the second is easy to overlook: evidence taken on a moving
    surface describes code that will have changed, and evidence taken while the
    surface is *unregistered* cannot be attributed at all.
    """
    if matrix.freeze is not None and not matrix.freeze.clean:
        raise IntakeError(
            f"the fingerprint surface moved since {matrix.baseline}: "
            f"{matrix.freeze.unregistered}")
    return matrix


# --------------------------------------------------------------------------- #
# 6.4 / 6.5 — controlled evidence registration
# --------------------------------------------------------------------------- #

@dataclass
class EvidenceItem:
    """One piece of existing evidence offered for registration.

    Deliberately mirrors what `assurance.record_evidence` requires, plus the two
    things this module adds: the fingerprint paths it was validated against, and the
    scope it covers. Without the first, "validated on some version" is unfalsifiable;
    without the second, a narrow pass can be registered as a broad one.
    """

    evidence_type: str
    outcome: str
    command_summary: str
    result_summary: str
    as_of: datetime
    dependency_paths: list[str] = field(default_factory=list)
    prerequisite_event_ids: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    # The report this came from, if any. Recorded so a summary can never be
    # mistaken for item-level evidence.
    source_report: str = ""


@dataclass
class RegistrationOutcome:
    """What one item's registration did, and what it refused to do."""

    consumer_id: str
    evidence_type: str
    registered: bool
    event_id: str = ""
    reason_code: str = ""
    reasons: list[str] = field(default_factory=list)
    drifted_paths: list[str] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        return {
            "consumer_id": self.consumer_id,
            "evidence_type": self.evidence_type,
            "registered": self.registered, "event_id": self.event_id,
            "reason_code": self.reason_code, "reasons": list(self.reasons),
            "drifted_paths": list(self.drifted_paths),
        }


def register_existing_evidence(*, consumer_id: str, items: list[EvidenceItem],
                                actor: str = "", db_path: str | Path | None = None,
                                coverage_path: str | Path | None = None
                                ) -> list[RegistrationOutcome]:
    """Register existing per-item evidence for one consumer, append-only.

    Three things this refuses, and each corresponds to a way a summary becomes a
    production proof by accident:

    1. **A summary report is not evidence.** An item whose only support is
       `source_report` is rejected: `assurance` requires per-type evidence, and a
       "passed" summary is exactly what the requirement forbids converting.
    2. **A drifted fingerprint is rejected**, naming the paths. Registering it would
       produce evidence that describes code that no longer exists.
    3. **A wrong fingerprint path is rejected outright** — an empty path list makes
       the evidence unfalsifiable later.

    What registration never does: change a route, produce a risk verdict, or create
    an approval. It appends an evidence row and nothing else.
    """
    from ..data.assurance import record_evidence

    import yaml

    manifest_path = coverage_path or (REPO_ROOT / "config" / "data"
                                      / "target_dataflow_coverage.yaml")
    manifest = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))
    contract = next((c for c in manifest.get("consumers", [])
                     if c.get("id") == consumer_id), None)
    if contract is None:
        raise IntakeError(
            f"consumer {consumer_id!r} is not declared in the coverage manifest; "
            f"evidence cannot be registered for an undeclared consumer")

    domain_id = str(contract["domain"])
    required = set(contract["required_evidence"])
    outcomes: list[RegistrationOutcome] = []

    for item in items:
        if item.evidence_type not in required:
            outcomes.append(RegistrationOutcome(
                consumer_id, item.evidence_type, False,
                reason_code="evidence_type_not_required",
                reasons=[f"{consumer_id} does not require {item.evidence_type!r}; "
                         f"required: {sorted(required)}"]))
            continue

        if not item.dependency_paths:
            outcomes.append(RegistrationOutcome(
                consumer_id, item.evidence_type, False,
                reason_code="fingerprint_paths_required",
                reasons=["no code/config fingerprint path was supplied; evidence "
                         "without one cannot be re-checked against later changes"]))
            continue

        drifted = _drifted_paths(item.dependency_paths)
        if drifted:
            outcomes.append(RegistrationOutcome(
                consumer_id, item.evidence_type, False,
                reason_code="fingerprint_drift",
                reasons=[f"{path} changed since this evidence was taken" for path
                         in drifted],
                drifted_paths=drifted))
            continue

        # `details` is deliberately narrow: `assurance._evidence_details` accepts
        # only bounded machine-proof summaries (`inputs`, `rollback`) and rejects
        # anything else — a summary report or an operator name does not belong
        # there, and putting it there would mean loosening a sanitiser whose whole
        # job is to keep source bodies and accounts out of the ledger.
        details = dict(item.details)

        command_summary = item.command_summary
        result_summary = item.result_summary
        registration_note = "existing-evidence intake (F.0.2)"
        if actor:
            registration_note += f"; registered_by={actor}"
        if item.source_report:
            registration_note += f"; source_report={item.source_report}"
            if not result_summary.strip():
                outcomes.append(RegistrationOutcome(
                    consumer_id, item.evidence_type, False,
                    reason_code="summary_report_is_not_evidence",
                    reasons=[f"{item.source_report} is a summary; a summary's pass "
                             f"is not per-item evidence and cannot be converted "
                             f"into a production proof"]))
                continue
        result_summary = (f"{result_summary} [{registration_note}]"
                          if result_summary else result_summary)

        kwargs = dict(
            domain_id=domain_id, consumer_id=consumer_id,
            evidence_type=item.evidence_type, outcome=item.outcome,
            scope=_consumer_scope(consumer_id, contract), as_of=item.as_of,
            command_summary=command_summary,
            result_summary=result_summary,
            prerequisite_event_ids=list(item.prerequisite_event_ids),
            dependency_paths=list(item.dependency_paths),
            details=details, db_path=db_path,
        )
        if coverage_path is not None:
            kwargs["coverage_path"] = coverage_path

        try:
            event_id = record_evidence(**kwargs)
        except (ValueError, AssertionError) as exc:
            outcomes.append(RegistrationOutcome(
                consumer_id, item.evidence_type, False,
                reason_code="evidence_rejected", reasons=[str(exc)]))
            continue

        outcomes.append(RegistrationOutcome(
            consumer_id, item.evidence_type, True, event_id=event_id))

    return outcomes


def _consumer_scope(consumer_id: str, contract: dict[str, Any]) -> dict[str, Any]:
    """The exact scope evidence for a consumer is registered under.

    Derived from the manifest rather than supplied by the caller: a caller-chosen
    scope is how a narrow pass gets registered as a broad one.
    """
    return {"consumer": consumer_id,
            "products": sorted(contract.get("products", ()))}


def _drifted_paths(paths: Iterable[str], baseline: str | None = None) -> list[str]:
    """Constrained paths whose content differs from `baseline` (or from HEAD).

    Only fingerprint-constrained paths can drift in a way that matters; a change to
    an unconstrained file does not invalidate evidence.
    """
    from .assurance_surface import load_surface

    surface = load_surface()
    constrained = set(surface.all_paths())
    reference = baseline or "HEAD"
    drifted = []
    for path in paths:
        if path not in constrained:
            continue
        if _git("diff", "--name-only", reference, "--", path).strip():
            drifted.append(path)
    return drifted


# --------------------------------------------------------------------------- #
# 6.6 — the per-consumer report
# --------------------------------------------------------------------------- #

@dataclass
class ConsumerReport:
    """One consumer's eligibility, with the minimum re-verification scope."""

    consumer_id: str
    domain_id: str
    status: str
    reasons: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    matrix_row: str = ""

    @property
    def eligible(self) -> bool:
        return self.status == "eligible"

    def as_row(self) -> dict[str, Any]:
        return {
            "consumer_id": self.consumer_id, "domain_id": self.domain_id,
            "status": self.status, "reasons": list(self.reasons),
            "missing_evidence": list(self.missing_evidence),
            "event_ids": list(self.event_ids), "matrix_row": self.matrix_row,
        }


def consumer_reports(*, matrix: IntakeMatrix | None = None,
                     consumers: Iterable[str] | None = None,
                     now: datetime | None = None,
                     db_path: str | Path | None = None) -> list[ConsumerReport]:
    """Query eligibility per consumer after registration.

    Recomputable: the same inputs give the same answer, because eligibility is a
    query over the append-only ledger rather than a stored conclusion. That is the
    property that makes the report auditable — a reader can re-run it and get the
    same answer, or a different one, and the difference means the ledger changed.
    """
    from ..data.assurance import qualification

    import yaml

    manifest = yaml.safe_load(
        (REPO_ROOT / "config" / "data" / "target_dataflow_coverage.yaml").read_text(
            encoding="utf-8"))
    ids = list(consumers) if consumers else [c["id"] for c in manifest["consumers"]]

    reports = []
    for consumer_id in ids:
        contract = next((c for c in manifest["consumers"]
                         if c["id"] == consumer_id), None)
        if contract is None:
            continue
        scope = _consumer_scope(consumer_id, contract)
        result = qualification(
            domain_id=str(contract["domain"]), consumer_id=consumer_id,
            contract_version=str(contract["contract_version"]), scope=scope,
            now=now, db_path=db_path)
        reasons = [str(r) for r in result.get("reasons", [])]
        reports.append(ConsumerReport(
            consumer_id=consumer_id, domain_id=str(contract["domain"]),
            status=str(result.get("status")),
            reasons=reasons,
            missing_evidence=_missing_types(contract, reasons),
            event_ids=[str(e.get("event_id")) for e in result.get("events", ())
                       if isinstance(e, dict)],
            matrix_row=(matrix.row(consumer_id).verdict
                        if matrix and any(r.prerequisite == consumer_id
                                          for r in matrix.rows) else ""),
        ))
    return reports


def _missing_types(contract: dict[str, Any], reasons: list[str]) -> list[str]:
    """Which evidence types the reasons implicate, as a minimum re-verification scope.

    A reason string names its type (`rollback_unverified:x`), so the minimum scope is
    derived rather than guessed — an operator can act on it without reading the
    policy.
    """
    required = set(contract.get("required_evidence", ()))
    missing = set()
    for reason in reasons:
        head = reason.split(":", 1)[0].split("_unverified", 1)[0]
        for evidence_type in required:
            if evidence_type in head:
                missing.add(evidence_type)
    return sorted(missing) if missing else sorted(required)


def render_reports(reports: list[ConsumerReport]) -> str:
    """Operator-facing summary. Counts per status, plus the blocking reason each."""
    lines = ["# 逐消费者资格报告（F.0.2）", "",
             "| 消费者 | 结论 | 缺失证据 | 原因 |", "|---|---|---|---|"]
    for report in reports:
        lines.append(
            f"| `{report.consumer_id}` | {report.status} | "
            f"{', '.join(report.missing_evidence) or '—'} | "
            f"{'; '.join(report.reasons[:2]) or '—'} |")
    blocked = [r for r in reports if not r.eligible]
    if blocked:
        lines += ["", f"{len(blocked)} 个消费者未取得资格。**缺项只阻断受影响范围**——"
                  "不得据此放行其他消费者，也不得为使其可切而降低证据标准。"]
    return "\n".join(lines)
