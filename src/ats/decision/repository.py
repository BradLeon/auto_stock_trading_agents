"""Append-only decision audit repository (design D1/D2, tasks 2.3–2.7).

Storage rules this module enforces, so no caller can bypass them:

- **Revisions are immutable.** There is no update path; a changed proposal is a
  NEW revision with a new `revision_no` and hash. Re-submitting byte-identical
  content returns the existing revision (a replay is not a new fact).
- **`revision_no` is gapless and unique per cycle.** Allocated under
  `BEGIN IMMEDIATE`, so concurrent writers serialize instead of colliding.
- **Transitions are compare-and-set and journaled.** Every status change
  appends one `cycle_events` row under an idempotency key that excludes the
  request moment (same principle as `TriggerContext.idempotency_key`). A
  replayed transition returns the event it already produced.
- **The store is the audit truth.** Recovery asks these tables what already
  happened (`has_revision` / `has_transition` / `has_approval`), never a
  LangGraph checkpoint.

The repository owns transactions for its multi-write operations; callers must
not wrap repository calls in their own transactions.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..agent.task_projection import canonical_json
from .hashing import decision_hash
from .state import CycleStatus, REVISION_SOURCE_CHIEF, is_terminal


class DecisionAuditError(Exception):
    """Base class for decision-audit repository failures."""


class RevisionImmutableError(DecisionAuditError):
    """An attempt to rewrite a persisted revision. Revisions never change."""


class TransitionConflictError(DecisionAuditError):
    """A compare-and-set transition lost, or targeted a terminal cycle."""


class InvalidRiskReviewError(DecisionAuditError):
    """A review without its binding quad (revision hash, ruleset, snapshots)."""


def approval_idempotency_key(cycle_id: str, revision_no: int, decision_hash: str,
                             channel: str) -> str:
    """Design D3: the callback dedup key is derived from the process, revision,
    hash and approval channel — never from the request moment — so a replayed
    callback across processes/restarts derives the SAME key."""
    body = f"approval|{cycle_id}|r{revision_no}|{decision_hash}|{channel}"
    return hashlib.sha1(body.encode()).hexdigest()[:32]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def transition_idempotency_key(cycle_id: str, from_status: str, to_status: str,
                               revision_no: int | None = None) -> str:
    """Key style of `TriggerContext.idempotency_key`: sha1 over identity fields.

    Deliberately excludes the request moment — a retried or crash-recovered
    transition must derive the SAME key, or deduplication is worthless.
    """
    body = f"cycle|{cycle_id}|{from_status}|{to_status}|{revision_no or ''}"
    return hashlib.sha1(body.encode()).hexdigest()[:32]


class DecisionAuditRepository:
    """Facade over the five §12.3 tables on a `TradingMemory` connection."""

    def __init__(self, store) -> None:
        self.store = store
        self.conn: sqlite3.Connection = store.conn

    # --- transactions ----------------------------------------------------- #
    def _begin(self) -> None:
        self.conn.execute("BEGIN IMMEDIATE")

    # --- cycles ------------------------------------------------------------ #
    def create_cycle(self, *, cycle_id: str, trigger_source: str,
                     trigger_id: str = "", research_snapshot: Any = None,
                     status: CycleStatus = CycleStatus.DRAFT,
                     created_at: str | None = None) -> sqlite3.Row:
        """Idempotent by primary key: replaying creation returns the cycle."""
        stamp = created_at or _now()
        self.conn.execute(
            "INSERT OR IGNORE INTO decision_cycles (cycle_id, trigger_source, "
            "trigger_id, research_snapshot, status, current_revision_no, "
            "created_at, updated_at) VALUES (?,?,?,?,?,NULL,?,?)",
            (cycle_id, trigger_source, trigger_id,
             canonical_json(research_snapshot) if research_snapshot is not None
             else None,
             CycleStatus(status).value, stamp, stamp))
        self.conn.commit()
        row = self.get_cycle(cycle_id)
        assert row is not None
        return row

    def get_cycle(self, cycle_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM decision_cycles WHERE cycle_id = ?",
            (cycle_id,)).fetchone()

    # --- revisions (2.3, 2.4) ---------------------------------------------- #
    def append_revision(self, *, cycle_id: str,
                        orders: Sequence[Mapping[str, Any]],
                        rationale: Any = None, input_refs: Any = None,
                        model_version: str = "", prompt_version: str = "",
                        parent_revision_no: int | None = None,
                        revision_source: str = REVISION_SOURCE_CHIEF,
                        created_at: str | None = None,
                        actor: str = "chief") -> sqlite3.Row:
        """Allocate the next gapless `revision_no` and insert immutably.

        Replaying identical content is a no-op that returns the existing
        revision; anything else becomes a new revision and advances the cycle's
        current-revision pointer (forward only).
        """
        stamp = created_at or _now()
        content_hash = decision_hash(
            orders=orders, rationale=rationale, input_refs=input_refs)
        existing = self.get_revision_by_hash(cycle_id, content_hash)
        if existing is not None:
            return existing
        self._begin()
        try:
            row = self.conn.execute(
                "SELECT MAX(revision_no) FROM decision_revisions "
                "WHERE cycle_id = ?", (cycle_id,)).fetchone()
            revision_no = (row[0] or 0) + 1
            self.conn.execute(
                "INSERT INTO decision_revisions (cycle_id, revision_no, "
                "decision_hash, parent_revision_no, orders_json, rationale, "
                "input_refs, model_version, prompt_version, revision_source, "
                "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (cycle_id, revision_no, content_hash, parent_revision_no,
                 canonical_json(list(orders)),
                 canonical_json(rationale) if isinstance(rationale, Mapping)
                 else (rationale or ""),
                 canonical_json(input_refs) if input_refs is not None else None,
                 model_version, prompt_version, revision_source, stamp))
            # The pointer is navigation state, not revision content: it moves
            # FORWARD only, and monotonically.
            self.conn.execute(
                "UPDATE decision_cycles SET current_revision_no = MAX("
                "COALESCE(current_revision_no, 0), ?), updated_at = ? "
                "WHERE cycle_id = ?", (revision_no, stamp, cycle_id))
            self.conn.execute(
                "INSERT INTO cycle_events (cycle_id, event_type, actor, "
                "from_status, to_status, idempotency_key, payload, created_at) "
                "VALUES (?, 'revision_appended', ?, NULL, NULL, ?, ?, ?)",
                (cycle_id, actor,
                 transition_idempotency_key(
                     cycle_id, "", f"revision:{revision_no}", revision_no),
                 canonical_json({"revision_no": revision_no,
                                 "decision_hash": content_hash}), stamp))
            self.conn.commit()
        except sqlite3.IntegrityError:
            # Lost a race on (cycle_id, revision_no) or on the content-hash
            # index: re-read whatever won and surface it as the fact.
            self.conn.rollback()
            winner = self.get_revision_by_hash(cycle_id, content_hash)
            if winner is not None:
                return winner
            raise
        except Exception:
            self.conn.rollback()
            raise
        return self.get_revision_by_hash(cycle_id, content_hash)  # type: ignore[return-value]

    def get_revision(self, cycle_id: str, revision_no: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM decision_revisions WHERE cycle_id = ? AND revision_no = ?",
            (cycle_id, revision_no)).fetchone()

    def get_revision_by_hash(self, cycle_id: str,
                             decision_hash: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM decision_revisions WHERE cycle_id = ? AND decision_hash = ?",
            (cycle_id, decision_hash)).fetchone()

    def list_revisions(self, cycle_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM decision_revisions WHERE cycle_id = ? ORDER BY revision_no",
            (cycle_id,)).fetchall()

    def latest_revision(self, cycle_id: str) -> sqlite3.Row | None:
        """The revision a callback/authorization must currently bind to."""
        return self.conn.execute(
            "SELECT * FROM decision_revisions WHERE cycle_id = ? "
            "ORDER BY revision_no DESC LIMIT 1", (cycle_id,)).fetchone()

    def effective_review(self, cycle_id: str, revision_no: int,
                         decision_hash: str) -> sqlite3.Row | None:
        """The review ONLY if it binds exactly this revision (task 6.2).

        Any substantive field change after the review produces a NEW revision
        with a new hash — the old review no longer binds and is invalid for it.
        """
        return self.conn.execute(
            "SELECT * FROM decision_risk_reviews WHERE cycle_id = ? AND "
            "revision_no = ? AND decision_hash = ?",
            (cycle_id, revision_no, decision_hash)).fetchone()

    def effective_approval(self, cycle_id: str, revision_no: int,
                           decision_hash: str) -> sqlite3.Row | None:
        """The approval ONLY if it is an approval bound to exactly this revision."""
        return self.conn.execute(
            "SELECT * FROM boss_approvals WHERE cycle_id = ? AND revision_no = ? "
            "AND decision_hash = ? AND decision = 'approved'",
            (cycle_id, revision_no, decision_hash)).fetchone()

    def validate_callback(self, cycle_id: str, *, revision_no: int | None = None,
                          decision_hash: str | None = None,
                          channel: str = "") -> tuple[bool, str, bool]:
        """Callback admission guard (task 6.4). Returns `(ok, reason, deduped)`.

        - terminal cycle → reject;
        - callback binding a superseded revision → reject;
        - review no longer effective for the current revision → reject
          (only enforced when the cycle has reviews at all — the degraded
          no-portfolio path has none and relies on the execution gate);
        - approval already recorded for (revision, hash, channel) → dedup,
          i.e. the original result is the answer, not an error.
        Cycles without an audit row (legacy) are allowed through unchanged.
        """
        cycle = self.get_cycle(cycle_id)
        if cycle is None:
            return True, "no audit row (legacy cycle)", False
        if is_terminal(cycle["status"]):
            # A late/duplicate callback on a finished cycle: the original
            # result stands — reported as dedup, not as an error.
            return False, f"cycle already terminal ({cycle['status']})", True
        rev = self.latest_revision(cycle_id)
        if rev is None:
            return True, "no revision yet", False
        key = approval_idempotency_key(cycle_id, rev["revision_no"],
                                       rev["decision_hash"], channel)
        if self.has_approval(key) is not None:
            return False, "already handled", True
        if decision_hash and decision_hash != rev["decision_hash"]:
            return False, "callback targets a superseded revision", False
        if revision_no is not None and revision_no != rev["revision_no"]:
            return False, "callback targets a superseded revision", False
        if self.effective_review(cycle_id, rev["revision_no"],
                                 rev["decision_hash"]) is None:
            has_any = self.conn.execute(
                "SELECT 1 FROM decision_risk_reviews WHERE cycle_id = ? LIMIT 1",
                (cycle_id,)).fetchone()
            if has_any is not None:
                return False, "risk review no longer effective", False
        return True, "ok", False

    def update_revision(self, *args: Any, **kwargs: Any) -> None:
        """Explicit refusal: persisted revisions are never rewritten (2.4).

        A changed proposal must arrive as a new revision; silently updating a
        row would break the hash binding that authorizations depend on.
        """
        raise RevisionImmutableError(
            "decision_revisions is append-only; propose a new revision instead")

    # --- transitions (2.5) -------------------------------------------------- #
    def transition(self, cycle_id: str, *, to_status: CycleStatus | str,
                   actor: str, from_status: CycleStatus | str | None = None,
                   revision_no: int | None = None, idempotency_key: str | None = None,
                   payload: Any = None, error: str | None = None,
                   created_at: str | None = None) -> tuple[sqlite3.Row, bool]:
        """Compare-and-set the cycle status and append one journaled event.

        Returns `(event_row, changed)`. `changed=False` means this exact
        transition was already applied — the stored event is returned, and no
        second row is written.
        """
        to_value = CycleStatus(to_status).value
        from_value = (CycleStatus(from_status).value
                      if from_status is not None else None)
        key = idempotency_key or transition_idempotency_key(
            cycle_id, from_value or "", to_value, revision_no)
        stamp = created_at or _now()
        prior = self.conn.execute(
            "SELECT * FROM cycle_events WHERE cycle_id = ? AND idempotency_key = ?",
            (cycle_id, key)).fetchone()
        if prior is not None:
            return prior, False                      # replay: no second event

        cycle = self.get_cycle(cycle_id)
        if cycle is None:
            raise TransitionConflictError(f"unknown cycle {cycle_id!r}")
        current = cycle["status"]
        if current == to_value:
            # Already there (someone transitioned it under a different key):
            # journal the fact for this caller's key, change nothing.
            changed = False
        elif is_terminal(current):
            raise TransitionConflictError(
                f"cycle {cycle_id!r} is terminal ({current}); "
                f"cannot transition to {to_value!r}")
        else:
            expected_from = from_value if from_value is not None else current
            if current != expected_from:
                raise TransitionConflictError(
                    f"expected status {expected_from!r}, found {current!r} "
                    f"for cycle {cycle_id!r}")
            changed = True
            cur = self.conn.execute(
                "UPDATE decision_cycles SET status = ?, updated_at = ? "
                "WHERE cycle_id = ? AND status = ?", (to_value, stamp,
                                                      cycle_id, current))
            if cur.rowcount != 1:                    # pragma: no cover - serialized
                raise TransitionConflictError(
                    f"compare-and-set lost for cycle {cycle_id!r}")
            if revision_no is not None:
                self.conn.execute(
                    "UPDATE decision_cycles SET current_revision_no = MAX("
                    "COALESCE(current_revision_no, 0), ?) WHERE cycle_id = ?",
                    (revision_no, cycle_id))
        self.conn.execute(
            "INSERT INTO cycle_events (cycle_id, event_type, actor, from_status, "
            "to_status, idempotency_key, payload, error, created_at) "
            "VALUES (?, 'status_change', ?, ?, ?, ?, ?, ?, ?)",
            (cycle_id, actor, current, to_value, key,
             canonical_json(payload) if payload is not None else None,
             error, stamp))
        self.conn.commit()
        event = self.conn.execute(
            "SELECT * FROM cycle_events WHERE cycle_id = ? AND idempotency_key = ?",
            (cycle_id, key)).fetchone()
        assert event is not None
        return event, changed

    # --- reviews / approvals (storage only; policy lives in groups 4 & 6) ---- #
    def record_review(self, *, review_id: str, cycle_id: str, revision_no: int,
                      decision_hash: str, ruleset_version: str,
                      portfolio_snapshot_id: str, market_as_of: str,
                      verdict: str, violations: list[dict[str, Any]] | None = None,
                      allowed_boundary: Mapping[str, Any] | None = None,
                      before_metrics: Mapping[str, Any] | None = None,
                      after_metrics: Mapping[str, Any] | None = None,
                      notes: str = "", created_at: str | None = None) -> sqlite3.Row:
        # §5.3/§10.2: a review binds ONE revision under ONE ruleset at ONE
        # snapshot instant. Any missing binding makes the row unverifiable, so
        # it is invalid by construction, not merely sparse.
        missing = [name for name, value in (
            ("decision_hash", decision_hash),
            ("ruleset_version", ruleset_version),
            ("portfolio_snapshot_id", portfolio_snapshot_id),
            ("market_as_of", market_as_of)) if not value]
        if missing:
            raise InvalidRiskReviewError(
                f"invalid risk review: missing binding(s) {', '.join(missing)}")
        self.conn.execute(
            "INSERT OR IGNORE INTO decision_risk_reviews (review_id, cycle_id, "
            "revision_no, decision_hash, ruleset_version, portfolio_snapshot_id, "
            "market_as_of, verdict, violations_json, allowed_boundary_json, "
            "before_metrics_json, after_metrics_json, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (review_id, cycle_id, revision_no, decision_hash, ruleset_version,
             portfolio_snapshot_id, market_as_of, verdict,
             canonical_json(violations or []),
             canonical_json(allowed_boundary) if allowed_boundary else None,
             canonical_json(before_metrics) if before_metrics else None,
             canonical_json(after_metrics) if after_metrics else None,
             notes, created_at or _now()))
        self.conn.commit()
        return self.conn.execute(
            "SELECT * FROM decision_risk_reviews WHERE review_id = ?",
            (review_id,)).fetchone()                 # type: ignore[return-value]

    def record_approval(self, *, approval_id: str, cycle_id: str, revision_no: int,
                        decision_hash: str, decision: str, reviewer: str,
                        comment: str = "", channel: str = "",
                        idempotency_key: str,
                        created_at: str | None = None) -> sqlite3.Row:
        """Idempotent on `idempotency_key`: a replayed callback gets the original."""
        self.conn.execute(
            "INSERT OR IGNORE INTO boss_approvals (approval_id, cycle_id, "
            "revision_no, decision_hash, decision, reviewer, comment, channel, "
            "idempotency_key, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (approval_id, cycle_id, revision_no, decision_hash, decision,
             reviewer, comment, channel, idempotency_key, created_at or _now()))
        self.conn.commit()
        return self.conn.execute(
            "SELECT * FROM boss_approvals WHERE idempotency_key = ?",
            (idempotency_key,)).fetchone()           # type: ignore[return-value]

    # --- chain read (2.6) ----------------------------------------------------- #
    def read_chain(self, cycle_id: str) -> dict[str, Any]:
        """The complete causal chain of one cycle, from storage alone.

        Answers "which revision was approved and which was executed" without
        touching any checkpoint: revisions, their reviews, the approvals bound
        to each revision hash, and the event journal in order.
        """
        cycle = self.get_cycle(cycle_id)
        revisions = [dict(r) for r in self.list_revisions(cycle_id)]
        reviews = [dict(r) for r in self.conn.execute(
            "SELECT * FROM decision_risk_reviews WHERE cycle_id = ? "
            "ORDER BY created_at, review_id", (cycle_id,)).fetchall()]
        approvals = [dict(r) for r in self.conn.execute(
            "SELECT * FROM boss_approvals WHERE cycle_id = ? "
            "ORDER BY created_at, approval_id", (cycle_id,)).fetchall()]
        events = [dict(r) for r in self.conn.execute(
            "SELECT * FROM cycle_events WHERE cycle_id = ? ORDER BY event_id",
            (cycle_id,)).fetchall()]
        executed = [a for a in approvals if a["decision"] == "approved"]
        return {
            "cycle": dict(cycle) if cycle else None,
            "revisions": revisions,
            "reviews": reviews,
            "approvals": approvals,
            "events": events,
            "approved_revision_hashes": sorted({a["decision_hash"]
                                                for a in approvals
                                                if a["decision"] == "approved"}),
            "has_execution_approval": bool(executed),
        }

    # --- legacy mirror (design D2, task 3.5) ---------------------------------- #
    def mirror_revision_to_legacy(self, cycle_id: str, revision_no: int, *,
                                  as_of: str) -> None:
        """Double-write a revision into the legacy `cycles`/`decisions` tables.

        Design D2: the revision record is authoritative, but the legacy read
        entry points (`last_chief_run`, `recent_decisions`, reports) keep
        working during the transition. The legacy write is a MIRROR, not a
        second truth — authorization decisions never read it, and the
        transition-period `decisions` rows carry no approval semantics.

        Replay-safe: an identical (cycle, symbol, action, notional) row is not
        duplicated. `legacy_unknown` revisions are skipped — they have no
        recoverable content to mirror.
        """
        rev = self.get_revision(cycle_id, revision_no)
        if rev is None:
            raise DecisionAuditError(f"unknown revision {cycle_id}/{revision_no}")
        if rev["revision_source"] == "legacy_unknown" or not rev["orders_json"]:
            return
        import json as _json
        orders = _json.loads(rev["orders_json"])
        self.conn.execute(
            "INSERT OR IGNORE INTO cycles (cycle_id, as_of, approval_status, "
            "manager_summary) VALUES (?,?,NULL,'')", (cycle_id, as_of))
        for order in orders:
            exists = self.conn.execute(
                "SELECT 1 FROM decisions WHERE cycle_id = ? AND symbol = ? AND "
                "action = ? AND notional_usd IS ?",
                (cycle_id, order.get("symbol"), order.get("action"),
                 order.get("notional_usd"))).fetchone()
            if exists:
                continue
            self.conn.execute(
                "INSERT INTO decisions (cycle_id, symbol, action, notional_usd, "
                "limit_price, conviction, rationale) VALUES (?,?,?,?,?,?,?)",
                (cycle_id, order.get("symbol"), order.get("action"),
                 order.get("notional_usd"), order.get("limit_price"),
                 order.get("conviction"), order.get("rationale")))
        self.conn.commit()

    # --- recovery facts (2.7) ------------------------------------------------- #
    def has_transition(self, cycle_id: str, idempotency_key: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM cycle_events WHERE cycle_id = ? AND idempotency_key = ?",
            (cycle_id, idempotency_key)).fetchone() is not None

    def has_revision(self, cycle_id: str, decision_hash: str) -> bool:
        return self.get_revision_by_hash(cycle_id, decision_hash) is not None

    def has_approval(self, idempotency_key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM boss_approvals WHERE idempotency_key = ?",
            (idempotency_key,)).fetchone()
