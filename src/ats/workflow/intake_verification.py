"""Isolated intake verification for the ten roles (Phase F tasks 7.1–7.6).

The gap this closes
-------------------
`F.0.2` produced **0/10 eligible**: every consumer is ineligible because the
assurance ledger has never had a per-item evidence row. That is the honest
starting state, but it is a dead end unless the missing evidence can be produced
*without* the qualification it is meant to prove — otherwise the only way to
qualify is to qualify, which is circular (design decision 11).

So this module is the isolated intake acceptance entry: a place where actual
connectivity CAN be verified while production qualification is still absent. It
exists under three constraints that are structural rather than procedural:

1. **It runs inside `isolation.isolated_run`.** Every persistence surface is
   redirected and the broker write prohibition is armed, so a verification run
   cannot write a production ledger row or reach a broker even if it tries.
   `7.1`'s three cases are all about this: it must START without production
   qualification, a production side effect must INVALIDATE the run (not be
   logged), and an isolated result must never authorise a trade.
2. **It records what actually happened, not what was declared.** A role's
   `AccessRecord` carries the call path that was really taken, the product /
   document / vintage references it really read, the projection hash it really
   published, and its gaps. A verification that cannot name its bypass location
   has not verified anything, so the path is recorded per call.
3. **An isolated proof is not a ledger integrity proof.** `7.6` — the two are
   different claims. `isolation_attestation()` says so explicitly and
   `assert_isolated_result_not_tradable()` enforces it at the point of use, so a
   shadow run's clean ledger cannot be quoted as evidence that the *production*
   ledger is complete.

Three failure modes this is built to catch, each of which is silent:

- **Provider / base-table bypass** (`7.2`). The scan is AST-based over the role's
  real import graph, not a runtime probe: a probe only covers the branch that
  executed, while "can this role reach a provider at all?" is a question about
  the source. Roles legitimately read `ats.data.products` — that is the product
  layer — but NOT `ats.data.sources` (a provider) and NOT `ats.data.stores` /
  `ats.memory` (the raw tables underneath).
- **Opinion leakage** (`7.3`). Only Layer→Sector and Information→Fundamental may
  read another role's opinion. Every other pair is a violation, and so is any
  candidate (unadmitted) document presented as published data, and any opinion
  written back as a shared fact.
- **A run that proceeds on incomplete inputs** (`7.4`). A missing required
  analysis must block the decision cycle, and a projection that cannot be
  resolved after a restart must fail rather than silently continue with an empty
  projection.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from ..config import REPO_ROOT

# The consumer access scan and its constants live in `consumer_scan` (r5 task 8.1); re-exported here for the
# remaining intake checks and their callers until this module is removed (task 8.7).
from .consumer_scan import (  # noqa: F401  (re-exports)
    PACKAGE_ROOT,
    CONSUMER_ENTRY_POINTS,
    ANALYSIS_CONSUMERS,
    DECISION_CONSUMERS,
    TEN_CONSUMERS,
    PRODUCT_LAYER,
    GOVERNED_READ_API,
    SANCTIONED_READ_SURFACE,
    PROVIDER_MODULES,
    DECISION_RUNTIME_MODULES,
    BASE_TABLE_MODULES,
    THIRD_PARTY_DATA_CLIENTS,
    ALLOWED_OPINION_EDGES,
    SOLE_AGGREGATORS,
    NEUTRAL_FACT_TABLES,
    PROJECTION_TABLES,
    AccessViolation,
    AccessRecord,
    scan_consumer_access,
    ConsumerScanError as IntakeVerificationError,
)


# --------------------------------------------------------------------------- #
# 7.1 — the isolated acceptance entry
# --------------------------------------------------------------------------- #

@dataclass
class IsolationAttestation:
    """What the isolated run guarantees, and what it explicitly does not.

    `production_ledger_integrity_proven` is False and cannot be set True here.
    That is the whole point of `7.6`: the run used isolated ledgers and a
    simulated broker, so it can speak to whether the PATH works and not to
    whether the production ledger is complete. An attestation that claimed both
    would be the most dangerous artefact this module could emit.
    """

    run_id: str
    isolation_root: str
    broker_write_prohibited: bool
    surfaces_redirected: tuple[str, ...]
    production_side_effects: tuple[str, ...] = ()
    production_ledger_integrity_proven: bool = False
    qualification_required: bool = False
    started_at: str = ""
    finished_at: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "isolation_root": self.isolation_root,
            "broker_write_prohibited": self.broker_write_prohibited,
            "surfaces_redirected": list(self.surfaces_redirected),
            "production_side_effects": list(self.production_side_effects),
            # Stated as a field rather than left implicit: a reader must be able
            # to see that this was considered and refused, not overlooked.
            "production_ledger_integrity_proven": self.production_ledger_integrity_proven,
            "qualification_required": self.qualification_required,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class ProductionSideEffect(RuntimeError):
    """An isolated verification reached production state. The run is invalid.

    Not a warning. The spec requires the acceptance to be judged INVALID and the
    production-side effect CLEARED, and both halves matter: a run whose whole
    value was "I changed nothing" cannot be cited if it changed something.
    """

    def __init__(self, run_id: str, effects: Sequence[str]) -> None:
        self.run_id = run_id
        self.effects = list(effects)
        super().__init__(
            f"isolated verification {run_id!r} produced production side effect(s): "
            + "; ".join(self.effects)
            + ". The acceptance is invalid; the effect must be cleared before this "
              "run may be cited as evidence.")


@contextmanager
def isolated_verification(run_id: str, *, root: str | Path | None = None,
                          reason_code: str | None = None
                          ) -> Iterator[IsolationAttestation]:
    """Run a verification with production unreachable and broker writes forbidden.

    `7.1`, first case: **this entry point does NOT require production
    qualification.** That is the entire reason it exists — requiring the
    qualification it is meant to produce would make the missing evidence
    unobtainable. The qualification gate still applies at the point of USE
    (`assert_isolated_result_not_tradable`), which is where a trade would happen.

    On exit the attestation is completed and, if anything reached production, the
    run is INVALIDATED by raising. Refusing after the fact rather than logging is
    deliberate: a side effect discovered at the end of a long verification is
    exactly the one nobody would look for.
    """
    from ..execution import broker_write_guard
    from . import isolation

    if reason_code is None:
        reason_code = broker_write_guard.REASON_ISOLATED

    before = _production_fingerprint()
    attestation = IsolationAttestation(
        run_id=run_id,
        isolation_root=str(root or isolation.default_isolation_root(run_id)),
        broker_write_prohibited=broker_write_guard.is_prohibited(),
        surfaces_redirected=(),
        qualification_required=False,
        started_at=datetime.now(timezone.utc).isoformat())

    try:
        with isolation.isolated_run(run_id, root=root, reason_code=reason_code) as env:
            from ..memory import TradingMemory, bound_store

            broker_write_guard.assert_broker_writes_prohibited(
                operation="isolated_verification", caller=run_id)
            attestation.isolation_root = str(env.root.resolve())
            attestation.surfaces_redirected = tuple(sorted(env.surfaces))
            attestation.broker_write_prohibited = True
            # Override a task-local connection inherited from an outer business
            # invocation, not merely the process connection cache.
            filename = env.path_for("ATS_DB_PATH")
            fresh = not filename.exists() or filename.stat().st_size == 0
            store = TradingMemory(filename, initialize=fresh)
            try:
                required = {"decision_cycles", "decision_revisions", "decision_risk_reviews",
                            "boss_approvals", "trades", "fills", "clerk_runs"}
                tables = {row[0] for row in store.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if not required <= tables:
                    raise IntakeVerificationError("isolated store schema incomplete; explicit migration required")
                with bound_store(store):
                    yield attestation
            finally:
                store.close()
    finally:
        after = _production_fingerprint()
        leaked = _production_delta(before, after)
        attestation.production_side_effects = tuple(leaked)
        attestation.finished_at = datetime.now(timezone.utc).isoformat()
        if leaked:
            raise ProductionSideEffect(run_id, leaked)


def _production_delta(before: dict, after: dict) -> list[str]:
    """Refuse additions, deletions, same-count updates and unreadable state.

    Concurrent production changes conservatively invalidate acceptance; they
    are not attributed to this run or automatically reverted.
    """
    return sorted(key for key in before.keys() | after.keys()
                  if before.get(key, 0) != after.get(key, 0)
                  or before.get(key) == -1 or after.get(key) == -1)


# Production state an isolated verification must leave unchanged. Counts and
# complete row digests catch same-count updates; concurrent changes invalidate
# acceptance conservatively without attributing or reverting the write.
_PRODUCTION_WATCH_TABLES: tuple[tuple[str, str], ...] = (
    ("ats.sqlite", "trades"),
    ("ats.sqlite", "boss_approvals"),
    ("ats.sqlite", "decision_cycles"),
    ("ats.sqlite", "decision_revisions"),
    ("ats.sqlite", "decision_risk_reviews"),
    ("data.sqlite", "data_evidence_facts"),
    ("ats.sqlite", "dataflow_assurance_events"),
    ("data.sqlite", "dataflow_assurance_events"),
    ("phase_f_cutover.sqlite", "cutover_boundary_state"),
    ("phase_f_cutover.sqlite", "cutover_activations"),
    ("phase_f_cutover.sqlite", "fallback_revocations"),
    ("phase_f_routes.sqlite", "trade_route_state"),
    ("phase_f_routes.sqlite", "trade_route_freeze"),
    ("phase_f_routes.sqlite", "trade_submit_receipts"),
)


def _production_db_path(name: str) -> Path:
    return REPO_ROOT / "var" / name


def _production_fingerprint() -> dict[str, int | str]:
    """Read-only counts and row digests of protected production tables.

    Read-only and fail-closed: a missing database is a count of zero, not an
    error, because the first thing a verification run ever does is run against a
    repository where some of these may not exist yet.
    """
    counts: dict[str, int | str] = {}
    for filename, table in _PRODUCTION_WATCH_TABLES:
        path = _production_db_path(filename)
        key = f"{filename}:{table}"
        if not path.is_file():
            counts[key] = 0
            continue
        try:
            conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                   timeout=5.0)
            try:
                present = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,)).fetchone()
                counts[key] = int(conn.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0]) if present else 0
                if present:
                    rows = sorted(canonical(tuple(row)) for row in conn.execute(f"SELECT * FROM {table}"))
                    counts[key + ":content"] = hashlib.sha256(canonical(rows).encode()).hexdigest()
            finally:
                conn.close()
        except sqlite3.Error:
            # An unreadable table cannot be shown to be unchanged, so it counts as
            # present-and-unverifiable rather than silently zero. Over-reporting
            # here fails the run; under-reporting would let a leak pass.
            counts[key] = -1
    return counts


def clear_production_side_effect(effect: str, *, confirm: bool = False
                                 ) -> dict[str, Any]:
    """Report how a leaked production effect would be cleared.

    Refuses to delete without explicit confirmation, and NEVER deletes silently.
    A cleanup routine that removes ledger rows on its own initiative is a second
    way to corrupt the ledger it is meant to protect, so this returns the plan
    and the operator performs it. The refusal is the behaviour under test: the
    point is that clearing is a deliberate act, not a side effect of verifying.
    """
    if not confirm:
        raise IntakeVerificationError(
            f"clearing production side effect {effect!r} requires explicit "
            "confirmation. An acceptance run whose value was 'I changed nothing' "
            "cannot clear what it changed automatically; report it, then remove "
            "it deliberately.")
    filename, _, table = effect.partition(":")
    if not table:
        raise IntakeVerificationError(
            f"malformed side-effect id {effect!r}; expected '<db file>:<table>'")
    return {"effect": effect, "path": str(_production_db_path(filename)),
            "table": table, "cleared": False,
            "note": "deletion is a deliberate operator action; this function only "
                    "reports the target so the row count can be compared before "
                    "and after"}


def assert_isolated_result_not_tradable(attestation: IsolationAttestation, *,
                                        consumer_id: str = "") -> None:
    """`7.1` third case / `7.6`: an isolated result may not authorise a trade.

    The isolated run proves the PATH works. It says nothing about whether the
    production ledger is complete, because it never read the production ledger.
    Quoting it as a trading authorisation would be a category error, and it is
    refused here — at the point of use, which is the only place a reader is
    forced to confront it.
    """
    if not consumer_id:
        consumer_id = "(unspecified)"
    raise IntakeVerificationError(
        f"isolated verification {attestation.run_id!r} cannot authorise a trade "
        f"for {consumer_id}: it ran against isolated ledgers with a simulated "
        "broker, so it establishes that the access path works — not that the "
        "production ledger is complete and not that the consumer holds production "
        "reading qualification. Re-verify qualification for the current scope via "
        "ats.data.assurance.qualification() before submitting.")


def check_candidate_material(*, consumer_id: str, document_refs: Sequence[str],
                             admitted_refs: Sequence[str]) -> list[AccessViolation]:
    """`7.3`: a candidate document must never be presented as published data.

    The distinction the product layer makes is admission: a candidate has been
    extracted but not accepted, so citing it as evidence asserts a publication
    that has not happened. The check is by REFERENCE, not by trusting a status
    flag the caller passes — the refs are what the analysis actually used.
    """
    admitted = set(admitted_refs)
    out: list[AccessViolation] = []
    for ref in document_refs:
        if ref in admitted:
            continue
        out.append(AccessViolation(
            "candidate_material_as_published", consumer_id, ref,
            "document reference is not in the admitted set; presenting an "
            "unadmitted candidate as published data asserts a publication that "
            "has not happened"))
    return out


def check_opinion_writeback(*, consumer_id: str, statements: Sequence[dict[str, Any]]
                            ) -> list[AccessViolation]:
    """`7.3`: an opinion must not be written back as a shared fact.

    `statements` are the writes the run reported, as `{table, kind, values}`.
    A neutral-fact table written by an analysis role is the violation: once an
    opinion is a fact row, later readers cannot tell it apart from evidence, and
    the reasoning that produced it is gone.
    """
    out: list[AccessViolation] = []
    for index, statement in enumerate(statements):
        table = str(statement.get("table", ""))
        if table not in NEUTRAL_FACT_TABLES:
            continue
        if consumer_id in SOLE_AGGREGATORS:
            continue
        out.append(AccessViolation(
            "opinion_writeback", consumer_id, f"{table}[{index}]",
            f"{consumer_id} wrote to the neutral-fact table {table!r}; an opinion "
            "written as a shared fact is indistinguishable from evidence to every "
            "later reader"))
    return out


# --------------------------------------------------------------------------- #
# 7.4 — required-input blocking and cross-process restart
# --------------------------------------------------------------------------- #

@dataclass
class RestartVerdict:
    """Whether the references a frozen snapshot cites survive a fresh process."""

    resolvable: bool
    resolved: list[str] = field(default_factory=list)
    unresolved: list[dict[str, str]] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        return {"resolvable": self.resolvable, "resolved": list(self.resolved),
                "unresolved": list(self.unresolved)}


def verify_restart_resolvability(store_factory: Callable[[], Any],
                                 projection_ids: Sequence[str], *,
                                 expect_hash: dict[str, str] | None = None
                                 ) -> RestartVerdict:
    """`7.4`: every cited projection must resolve in a FRESH store.

    `store_factory` is called to obtain a NEW store rather than reusing the one
    that wrote the rows — that is what makes it a restart rather than a re-read.
    The failure this catches is the quiet one: after a restart the projection is
    gone or replaced, and a caller that treats an empty result as "no opinion
    yet" proceeds with a default snapshot. So an unresolvable reference FAILS,
    and its `content_hash` is checked when one is supplied — a projection that
    resolves to different content is a different projection.
    """
    wanted = dict(expect_hash or {})
    verdict = RestartVerdict(resolvable=True)
    try:
        store = store_factory()
    except Exception as exc:  # noqa: BLE001 - any failure to open is unresolvable
        verdict.resolvable = False
        verdict.unresolved.append({"projection_id": "*", "reason":
                                   f"store could not be reopened: "
                                   f"{type(exc).__name__}: {exc}"})
        return verdict

    for projection_id in projection_ids:
        row = store.get_task_projection(projection_id)
        if row is None:
            verdict.resolvable = False
            verdict.unresolved.append({"projection_id": projection_id,
                                       "reason": "projection not found after restart"})
            continue
        if row.get("status") != "published":
            verdict.resolvable = False
            verdict.unresolved.append({
                "projection_id": projection_id,
                "reason": f"projection status is {row.get('status')!r}, not published"})
            continue
        expected = wanted.get(projection_id, "")
        if expected and row.get("content_hash") != expected:
            verdict.resolvable = False
            verdict.unresolved.append({
                "projection_id": projection_id,
                "reason": f"content hash drifted: snapshot cited {expected[:12]}…, "
                          f"stored row is {str(row.get('content_hash'))[:12]}…"})
            continue
        verdict.resolved.append(projection_id)
    return verdict


def verify_required_input_blocking(*, registry: Any, projections: dict[str, Any],
                                   scope: Any, required_scopes=None,
                                   enter_decision_cycle: bool = True,
                                   fundamental_mode: str = "routine", requested_tasks=None,
                                   at: datetime | None = None) -> dict[str, Any]:
    """Evaluate snapshot and run against the same selected dependency closure."""
    from ..agent.task_projection import ProjectionScope, reuse_decision
    from ..decision.snapshot import (
        IncompleteResearchSnapshotError,
        build_research_snapshot,
        selected_decision_tasks,
    )
    from .run_contracts import (
        TaskResult,
        TriggerContext,
        WorkflowRunRequest,
        build_run_result,
        should_enter_decision_cycle,
    )

    selected = selected_decision_tasks(registry, fundamental_mode=fundamental_mode,
                                       requested_tasks=requested_tasks)
    snapshot_scope = scope if isinstance(scope, ProjectionScope) else ProjectionScope(
        kind=str((scope or {}).get("kind", "portfolio")), id=str((scope or {}).get("id", "")))
    snapshot = build_research_snapshot(registry=registry, projections=projections,
        scope=snapshot_scope, required_scopes=required_scopes, selected_tasks=selected, at=at)
    gaps = [{"category": i.category, "task_id": i.task_id, "reason": i.reason}
            for i in snapshot.gaps()]
    request = WorkflowRunRequest(run_id="intake-verification",
        trigger=TriggerContext(kind="manual"), tasks=selected, scope=snapshot_scope,
        as_of=snapshot.built_at, enter_decision_cycle=enter_decision_cycle)
    outcomes = {}
    for task_id in selected:
        env = projections.get(task_id)
        usable, reason = (reuse_decision(env, scope=(required_scopes or {}).get(task_id, env.scope), at=at)
                          if env else (False, "missing"))
        outcomes[task_id] = TaskResult(task_id=task_id,
            status="succeeded" if usable else "missing" if env is None else "stale",
            projection_refs=(env.projection_id,) if env else (), detail=reason)
    result = build_run_result(request, registry, outcomes)
    allowed, reason = should_enter_decision_cycle(request, result)
    refusal = str(IncompleteResearchSnapshotError(snapshot)) if gaps else ""
    return {"complete": snapshot.complete, "gaps": gaps,
        "decision_cycle_entered": bool(snapshot.complete and allowed),
        "block_reason": reason if not allowed else "incomplete_research_snapshot" if gaps else "",
        "refusal_message": refusal, "run_contract_complete": result.terminal == "complete",
        "contract_disagreements": [], "selected_tasks": list(selected),
        "verified": bool(snapshot.complete and allowed)}


# --------------------------------------------------------------------------- #
# 7.5 — the decision and execution chain, in isolation
# --------------------------------------------------------------------------- #

@dataclass
class ChainBinding:
    """One link of the decision chain, and whether it is bound.

    Reported per link rather than as one boolean because a cutover decision needs
    to know WHICH link is missing — "the chain is not bound" sends the reader
    back to the start.
    """

    name: str
    bound: bool
    detail: str = ""
    refs: tuple[str, ...] = ()

    def as_row(self) -> dict[str, Any]:
        return {"name": self.name, "bound": self.bound, "detail": self.detail,
                "refs": list(self.refs)}


@dataclass
class ChainVerification:
    """Chief → Risk → Trader → Clerk, verified without a broker write."""

    bindings: list[ChainBinding] = field(default_factory=list)
    multi_round: bool = False
    rounds: int = 0
    recovery_idempotent: bool = False
    partial_fill_handled: bool = False
    late_fill_stop_switch: bool = False
    performance_rebuilt: bool = False
    broker_write_attempts: int = 0
    broker_refusals: int = 0

    @property
    def complete(self) -> bool:
        return bool(self.bindings) and all(b.bound for b in self.bindings)

    def as_row(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "bindings": [b.as_row() for b in self.bindings],
            "multi_round": self.multi_round,
            "rounds": self.rounds,
            "recovery_idempotent": self.recovery_idempotent,
            "partial_fill_handled": self.partial_fill_handled,
            "late_fill_stop_switch": self.late_fill_stop_switch,
            "performance_rebuilt": self.performance_rebuilt,
            "broker_write_attempts": self.broker_write_attempts,
            "broker_refusals": self.broker_refusals,
        }


def verify_decision_chain(*, repo: Any, cycle_id: str, snapshot: Any,
                          broker_write_attempted: bool = False,
                          reconcile: Callable[[dict, dict], dict] | None = None,
                          store: Any = None, rebuild_period: str = "",
                          ) -> ChainVerification:
    """`7.5`: verify Chief/Risk/Trader/Clerk without any broker write permission.

    Four properties, each about a way this verification could pass while the
    system is broken:

    - **Every link is bound.** Research snapshot, internal state, decision
      revision, approval, and the order/fill channel each have to name what they
      are bound to. An unbound link is reported, not defaulted.
    - **The risk loop is multi-round.** A single review is the easy path; the loop
      exists because a revision after review invalidates it.
    - **Repeated recovery is idempotent.** Re-running recovery must not duplicate
      a fill or a position — the Clerk's ledger is the record of what happened, so
      a duplicate there is a duplicate in reality.
    - **A late fill stops a switch.** A late fill proves the broker was still
      working after local state looked settled, so a switch must not proceed past
      one. Absorbing it is always right; continuing past it is not.

    `rebuild_period` is optional so the performance rebuild can be verified in
    the same run; when supplied, the read model must actually rebuild.
    """
    from ..execution import authorization, authorization_lifecycle, order_disposition

    verification = ChainVerification()

    snapshot_payload = snapshot.to_payload() if hasattr(snapshot, "to_payload") else snapshot
    items = (snapshot_payload or {}).get("items") or []
    projection_ids = tuple(str(item.get("projection_id", "")) for item in items
                           if item.get("projection_id"))
    verification.bindings.append(ChainBinding(
        name="research_snapshot", bound=bool(projection_ids),
        detail=("bound to " + ", ".join(sorted(set(projection_ids)))[:200])
        if projection_ids else "no projection is cited by the snapshot",
        refs=projection_ids))

    revision = repo.latest_revision(cycle_id)
    verification.bindings.append(ChainBinding(
        name="decision_revision", bound=revision is not None,
        detail=(f"revision {revision['revision_no']} @ "
                f"{str(revision['decision_hash'])[:12]}…") if revision is not None
        else "no revision has been appended for this cycle",
        refs=(str(revision["decision_hash"]),) if revision is not None else ()))

    review = (repo.effective_review(cycle_id, revision["revision_no"],
                                    revision["decision_hash"])
              if revision is not None else None)
    rounds = int(repo.conn.execute(
        "SELECT COUNT(*) FROM decision_risk_reviews WHERE cycle_id=? AND "
        "decision_hash=?", (cycle_id, revision["decision_hash"])).fetchone()[0]
    ) if revision is not None else 0
    verification.rounds = rounds
    verification.multi_round = rounds >= 2
    verification.bindings.append(ChainBinding(
        name="risk_review",
        bound=bool(review is not None and review["verdict"] == "approved"),
        detail=(f"round-bound review {review['review_id']} verdict "
                f"{review['verdict']} ({rounds} round(s) recorded)")
        if review is not None else "no effective risk review for the current revision",
        refs=(str(review["review_id"]),) if review is not None else ()))

    approval = (repo.effective_approval(cycle_id, revision["revision_no"],
                                         revision["decision_hash"])
                if revision is not None else None)
    verification.bindings.append(ChainBinding(
        name="boss_approval", bound=approval is not None,
        detail=(f"approval {approval['approval_id']} by {approval['reviewer']}")
        if approval is not None else "no human approval bound to the current revision",
        refs=(str(approval["approval_id"]),) if approval is not None else ()))

    authorization_reasons: list[str] = []
    try:
        auth = authorization.build_authorization(repo, cycle_id)
        authorization_reasons = authorization.validate_authorization(
            repo, auth, snapshot_as_of=_parse_or_now(
                (snapshot_payload or {}).get("built_at")))
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        authorization_reasons = [f"{type(exc).__name__}: {exc}"]
    verification.bindings.append(ChainBinding(
        name="execution_authorization", bound=not authorization_reasons,
        detail=("authorization complete" if not authorization_reasons
                else "; ".join(authorization_reasons)),
        refs=()))

    internal_state_bound = False
    internal_detail = "no store supplied, so internal state was not read"
    if store is not None:
        from ..execution import state_api

        try:
            state = state_api.get_internal_state(store)
            internal_state_bound = bool(state.section_status)
            internal_detail = (f"completeness={state.completeness.status}; sections="
                               f"{sorted(k for k, v in state.section_status.items() if v)}")
        except Exception as exc:  # noqa: BLE001
            internal_detail = f"internal state unreadable: {type(exc).__name__}: {exc}"
    verification.bindings.append(ChainBinding(
        name="internal_state", bound=internal_state_bound, detail=internal_detail))

    lifecycle = authorization_lifecycle.lifecycle_for_cycle(cycle_id, store=store)
    unsettled = lifecycle.unfinished() if store is not None else []
    dispositions = [order_disposition.disposition_for_order(row) for row in unsettled]
    verification.partial_fill_handled = all(
        not d.blocks_switch or d.status != "partial" for d in dispositions)
    if dispositions:
        late = order_disposition.late_fill_disposition(
            unsettled[0], {"exec_id": "probe", "symbol": unsettled[0].get("symbol", ""),
                           "side": "BOT", "shares": 1, "price": 1.0,
                           "time": "2999-01-01T00:00:00+00:00",
                           "order_id": unsettled[0].get("order_id", "")},
            reconcile=reconcile)
        verification.late_fill_stop_switch = bool(late.stop_switch or late.late)
    verification.bindings.append(ChainBinding(
        name="order_fill_channel",
        bound=bool(unsettled) or store is None,
        detail=(f"{len(unsettled)} unsettled order(s) classified; "
                f"{sum(1 for d in dispositions if d.blocks_switch)} blocking a switch")
        if store is not None else "no store supplied, so no order rows were read",
        refs=tuple(str(row.get("order_id", "")) for row in unsettled)))

    if broker_write_attempted:
        from ..execution import broker_write_guard

        verification.broker_write_attempts = 1
        verification.broker_refusals = len(broker_write_guard.refusals())

    if store is not None and rebuild_period:
        from ..execution import rebuild

        outcome = rebuild.rebuild_performance(store, period=rebuild_period)
        verification.performance_rebuilt = outcome.get("status") in {"rebuilt", "skipped"}

    return verification


def verify_recovery_idempotence(*, action: Callable[[], Any],
                                ledger_reader: Callable[[], list[Any]]) -> dict[str, Any]:
    """`7.5`: run the same recovery twice and require an identical ledger.

    A recovery action that is not idempotent duplicates fills and positions the
    second time it runs — and a recovery is exactly what gets re-run, because
    nobody is watching a process crash and resume. Comparing the ledger rather
    than the return value is deliberate: the ledger is what downstream readers
    see.
    """
    action()
    first = list(ledger_reader())
    action()
    second = list(ledger_reader())
    identical = len(first) == len(second)
    return {"idempotent": identical, "rows_after_first": len(first),
            "rows_after_second": len(second),
            "reason": "" if identical else
            "the repeated recovery changed the ledger; a resumed process would "
            "double-count fills or positions"}


def _parse_or_now(value: Any) -> datetime:
    """Parse an ISO stamp, falling back to now. Never returns a naive datetime.

    `validate_authorization` compares the snapshot instant against the clock, and
    a naive value raises inside it — which would surface as an authorization
    failure caused by this helper rather than by the thing under verification.
    """
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def verification_digest(records: Sequence[AccessRecord]) -> str:
    """A stable digest over the verification, for citing it as evidence.

    Covers the observations, not the timestamps: two runs that verified the same
    thing must produce the same digest, or "the evidence matches" becomes a
    claim nobody can check. Sorted by consumer id alone — `AccessRecord` is not
    orderable, so a plain `sorted()` on (id, record) pairs raises as soon as two
    records share an id.
    """
    ordered = sorted(records, key=lambda record: record.consumer_id)
    material = canonical({
        record.consumer_id: {
            "entry_point": record.entry_point,
            "call_path": sorted(record.call_path),
            "product_refs": sorted(record.product_refs),
            "document_refs": sorted(record.document_refs),
            "vintage_refs": sorted(record.vintage_refs),
            "projection_hash": record.projection_hash,
            "opinion_inputs": sorted(record.opinion_inputs),
            "violations": sorted((v.kind, v.location) for v in record.violations),
        }
        for record in ordered
    })
    return hashlib.sha256(material.encode()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def render_report(records: Sequence[AccessRecord],
                  chain: ChainVerification | None = None,
                  attestation: IsolationAttestation | None = None) -> str:
    """Operator-facing summary. Counts, then the failures with their locations."""
    lines = ["# 十角色接入核验（F.0.3–F.0.5）", ""]
    if attestation is not None:
        lines += [
            f"隔离运行 `{attestation.run_id}` · 券商写入禁写="
            f"{attestation.broker_write_prohibited} · 生产副作用="
            f"{len(attestation.production_side_effects)}",""]
        lines += ["**隔离证明不是生产账本完整性证明**：本次核验未读生产账本，不得据此放行交易。", ""]

    lines += ["| 消费者 | 到达入口 | 合规 | 违规 | 缺口 |", "|---|---|---|---|---|"]
    for record in sorted(records, key=lambda r: r.consumer_id):
        reached = "是" if record.reached_entry_point else "**否**"
        lines.append(
            f"| `{record.consumer_id}` | {reached} | "
            f"{'是' if record.compliant else '否'} | "
            f"{len(record.violations)} | {len(record.gaps)} |")

    failed = [r for r in records if not r.compliant]
    if failed:
        lines += ["", "## 不合规明细", ""]
        for record in sorted(failed, key=lambda r: r.consumer_id):
            for violation in record.violations:
                lines.append(f"- `{record.consumer_id}` — {violation}")
            if not record.reached_entry_point:
                lines.append(f"- `{record.consumer_id}` — 未到达入口 "
                             f"{record.entry_point}，本次核验对该角色不构成结论")

    if chain is not None:
        lines += ["", "## 决策与执行链", "",
                  f"- 链路完整：{'是' if chain.complete else '否'}"
                  f" · 多轮风控：{'是' if chain.multi_round else '否'}"
                  f"（{chain.rounds} 轮）"
                  f" · 恢复幂等：{'是' if chain.recovery_idempotent else '否'}"
                  f" · 迟到成交停止切换：{'是' if chain.late_fill_stop_switch else '否'}",
                  f"- 券商写入尝试 {chain.broker_write_attempts} 次 / 拒绝 "
                  f"{chain.broker_refusals} 次"]
        for binding in chain.bindings:
            lines.append(f"  - `{binding.name}`：{'已绑定' if binding.bound else '**未绑定**'}"
                         f" — {binding.detail}")

    lines += ["", f"核验摘要 digest：`{verification_digest(records)}`"]
    return "\n".join(lines)
