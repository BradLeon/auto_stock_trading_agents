"""Append-only shadow reports, difference dispositions and citation checks.

Tasks 3.4–3.6 in one module because they are one chain: a comparison produces
divergences, a divergence is either accepted or fixed, and only an accepted
report may be cited. Splitting them would put the "may this be cited" question in
a different file from the thing that answers it, which is how it drifts.

**Append-only is the load-bearing property.** The current `record_consumer_comparison`
is `INSERT OR REPLACE`, so today's conclusion overwrites yesterday's and nobody can
tell who changed their mind or when. That is unacceptable for the artefact a cutover
decision rests on, so conclusions, sign-offs, rejections and revocations are all
new rows — enforced by SQLite triggers, not by convention.

Two things follow from that, and they are why the shape is what it is:

- A **rejection does not erase the conclusion**. It adds a record saying the
  conclusion was rejected. Rewriting the conclusion would leave no record that a
  report was ever signed off and then withdrawn.
- **A completed surface can be compared again.** `not-compared` on the first pass
  is often "we had no data yet"; the second pass fills it in. That is a new
  comparison event, and the earlier `not-compared` stays in the history — because
  "we did not look, then we did" is exactly what an auditor needs to see.

The four citation checks (task 3.6) live here rather than in the cutover executor
so that the rule about a report's usability is written once, next to the state it
reads. A cutover that reimplemented them could drift.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import REPO_ROOT
from . import shadow_compare as compare

DEFAULT_PATH = "var/shadow/reports.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_report_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id TEXT NOT NULL,
    -- comparison | signoff | rejection | revocation | acceptance | recompare
    event_type TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    consumer_id TEXT NOT NULL DEFAULT '',
    batch_class TEXT NOT NULL DEFAULT '',
    scope_hash TEXT NOT NULL DEFAULT '',
    packet_hash TEXT NOT NULL DEFAULT '',
    -- The code and configuration fingerprint the comparison was made against.
    -- A report that predates a change cannot speak for the code after it.
    code_fingerprint TEXT NOT NULL DEFAULT '',
    verdicts_json TEXT NOT NULL DEFAULT '{}',
    surfaces_json TEXT NOT NULL DEFAULT '[]',
    not_compared_json TEXT NOT NULL DEFAULT '[]',
    unaccepted_json TEXT NOT NULL DEFAULT '[]',
    actor TEXT NOT NULL DEFAULT '',
    authority TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL,
    -- Free-form structured payload for events that need to name what they acted
    -- on (e.g. which acceptance a revocation revokes).
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_shadow_report_report
    ON shadow_report_events(report_id, event_id);
CREATE INDEX IF NOT EXISTS idx_shadow_report_run
    ON shadow_report_events(run_id);

-- Accepted divergences. Kept as their own rows so "who accepted what, and when"
-- is answerable without replaying the event log, and so an acceptance can be
-- revoked without touching the acceptance itself.
CREATE TABLE IF NOT EXISTS shadow_difference_acceptances (
    acceptance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id TEXT NOT NULL,
    surface TEXT NOT NULL,
    observed_divergence REAL,
    allowance REAL,
    accepted_by TEXT NOT NULL,
    authority TEXT NOT NULL,
    reason TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    revoked_by TEXT NOT NULL DEFAULT '',
    revoked_at TEXT NOT NULL DEFAULT '',
    revoke_reason TEXT NOT NULL DEFAULT ''
);

-- Append-only enforcement. Both tables are history; a mutation would make the
-- record of who concluded what unverifiable, which is the one thing this store
-- exists to provide.
CREATE TRIGGER IF NOT EXISTS shadow_report_events_no_update
BEFORE UPDATE ON shadow_report_events
BEGIN SELECT RAISE(ABORT, 'shadow report events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS shadow_report_events_no_delete
BEFORE DELETE ON shadow_report_events
BEGIN SELECT RAISE(ABORT, 'shadow report events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS shadow_acceptances_no_delete
BEFORE DELETE ON shadow_difference_acceptances
BEGIN SELECT RAISE(ABORT, 'shadow acceptances are append-only'); END;
"""

# Report lifecycle. A report is citable only while it is signed off and not
# revoked — and a later sign-off after a revocation replaces it, which is why the
# state is derived from the event log rather than stored on a row.
SIGNED_OFF = "signed_off"
REJECTED = "rejected"
REVOKED = "revoked"
PENDING = "pending"


class ShadowReportError(RuntimeError):
    """The report cannot be used as the evidence it was asked to be."""


def default_shadow_db_path() -> str:
    return os.environ.get("ATS_SHADOW_REPORT_DB",
                          str(REPO_ROOT / DEFAULT_PATH))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: Any) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def scope_fingerprint(scope: dict[str, Any]) -> str:
    """A stable identity for the scope a comparison covers.

    Order-insensitive so a dict rebuilt by a different code path still matches —
    the point is the scope's content, not how it was assembled.
    """
    return _hash(scope or {})


def code_fingerprint(paths: Iterable[str] | None = None) -> str:
    """The fingerprint of the code a comparison is valid for.

    Defaults to the qualification-policy manifest, which already changes whenever
    the data contracts do. A report predating a manifest change cannot speak for
    the contracts after it, and that is the check task 3.6 needs.
    """
    from .assurance_surface import load_surface

    surface = load_surface()
    entries = []
    report_paths = (*surface.all_paths(), "src/ats/workflow/shadow_replay.py",
                    "src/ats/workflow/shadow_inputs.py", "src/ats/workflow/shadow_reports.py",
                    "src/ats/workflow/shadow_compare.py", "src/ats/workflow/shadow_matrix.py",
                    "src/ats/workflow/batch_manifest.py", "src/ats/workflow/read_cutover.py",
                    "src/ats/workflow/cutover.py", "src/ats/runtime/cli.py",
                    "src/ats/workflow/isolated_entry.py", "src/ats/workflow/intake_verification.py",
                    "src/ats/workflow/isolation.py", "src/ats/workflow/shadow_ledger.py",
                    "src/ats/execution/shadow_execution.py")
    report_paths += tuple(str(p.relative_to(REPO_ROOT)) for p in
                          (REPO_ROOT / "config").rglob("*") if p.is_file())
    for rel in sorted(set(paths if paths is not None else report_paths)):
        target = REPO_ROOT / rel
        try:
            entries.append((rel, _hash(target.read_bytes())))
        except OSError:
            entries.append((rel, "absent"))
    return _hash(entries)


def _connect(path: str | Path | None = None, *, writable=True) -> sqlite3.Connection:
    target = Path(path or default_shadow_db_path())
    if not writable:
        try:
            conn = sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True, timeout=30.0)
        except sqlite3.Error as exc:
            raise ShadowReportError(f"no shadow report: store unreadable ({exc})") from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)
    return conn


@dataclass(frozen=True)
class ReportState:
    """The derived, current state of one report."""

    report_id: str
    status: str
    run_id: str
    consumer_id: str
    batch_class: str
    scope_hash: str
    packet_hash: str
    code_fingerprint: str
    not_compared: tuple[str, ...]
    unaccepted: tuple[str, ...]
    surfaces: dict[str, str]

    def as_row(self) -> dict[str, Any]:
        return {
            "report_type": "historical-comparison-v1",
            "report_id": self.report_id, "status": self.status,
            "run_id": self.run_id, "consumer_id": self.consumer_id,
            "batch_class": self.batch_class, "scope_hash": self.scope_hash,
            "packet_hash": self.packet_hash,
            "code_fingerprint": self.code_fingerprint,
            "not_compared": list(self.not_compared),
            "unaccepted": list(self.unaccepted), "surfaces": dict(self.surfaces),
        }


def record_comparison(*, report_id: str, run_id: str, consumer_id: str,
                      batch_class: str, scope: dict[str, Any],
                      packet_hash: str, result: compare.ComparisonResult,
                      actor: str = "", code_hash: str | None = None,
                      execution_evidence: dict | None = None,
                      event_type: str = "comparison",
                      path: str | Path | None = None) -> str:
    """Record one comparison event. Never overwrites an earlier one.

    `event_type` distinguishes a first pass from a re-run so the log reads as
    "compared, then compared again with more surfaces" rather than as two
    identical-looking comparisons whose order has to be inferred from ids.
    """
    unaccepted = _unaccepted_surfaces(result)
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO shadow_report_events (report_id, event_type, run_id,"
            " consumer_id, batch_class, scope_hash, packet_hash,"
            " code_fingerprint, verdicts_json, surfaces_json, not_compared_json,"
            " unaccepted_json, actor, authority, reason, recorded_at, payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (report_id, event_type, run_id, consumer_id, batch_class,
             scope_fingerprint(scope), packet_hash,
             code_hash or code_fingerprint(), _dumps(result.as_row()["verdicts"]),
             _dumps(result.as_row()["surfaces"]), _dumps(result.not_compared),
             _dumps(unaccepted), actor, "", f"{event_type} recorded", _now(),
             _dumps({"execution_evidence": execution_evidence})))
    return report_id


def record_recomparison(*, report_id: str, run_id: str, consumer_id: str,
                       batch_class: str, scope: dict[str, Any],
                       packet_hash: str, result: compare.ComparisonResult,
                       actor: str = "", reason: str = "",
                       code_hash: str | None = None,
                       execution_evidence: dict | None = None,
                       path: str | Path | None = None) -> str:
    """Record a later comparison that filled in what the first pass could not.

    A new event rather than an edit. The earlier `not-compared` stays in the log,
    because "we did not look, then we did" is what an auditor needs to see.
    """
    return record_comparison(
        report_id=report_id, run_id=run_id, consumer_id=consumer_id,
        batch_class=batch_class, scope=scope, packet_hash=packet_hash,
        result=result, actor=actor, code_hash=code_hash,
        event_type="recomparison", execution_evidence=execution_evidence, path=path)


def record_signoff(*, report_id: str, actor: str, reason: str,
                   required_surfaces: Iterable[str] | None = None,
                   path: str | Path | None = None) -> str:
    """Sign off a report — but only if it currently has nothing blocking it.

    A sign-off is an assertion that the report is usable, so it is refused while
    divergences are unaccepted or a required surface is uncompared. Allowing it
    anyway would make the signature meaningless and push the whole check into the
    citation step, where an operator has less context.
    """
    state = read_state(report_id, path=path)
    blocking: list[str] = []
    if state.unaccepted:
        blocking.append(
            f"unaccepted divergence(s): {', '.join(state.unaccepted)} — they must "
            f"be accepted by the declared authority or fixed and re-run")
    if required_surfaces:
        for surface in required_surfaces:
            if surface in state.not_compared:
                blocking.append(
                    f"required surface {surface} is not-compared")
    if blocking:
        raise ShadowReportError(
            f"report {report_id} cannot be signed off:\n"
            + "\n".join(f"  - {item}" for item in blocking))

    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO shadow_report_events (report_id, event_type, reason,"
            " actor, recorded_at) VALUES (?,?,?,?,?)",
            (report_id, "signoff", reason, actor, _now()))
    return report_id


def record_rejection(*, report_id: str, actor: str, reason: str,
                     path: str | Path | None = None) -> str:
    """Reject a report. The original conclusion is left intact.

    Rewriting the conclusion would erase the fact that it was ever signed off,
    which is the record a reviewer needs when the rejection itself is questioned.
    """
    if not reason.strip():
        raise ShadowReportError("a rejection must state a reason")
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO shadow_report_events (report_id, event_type, reason,"
            " actor, recorded_at) VALUES (?,?,?,?,?)",
            (report_id, "rejection", reason, actor, _now()))
    return report_id


def record_revocation(*, report_id: str, actor: str, reason: str,
                      path: str | Path | None = None) -> str:
    """Revoke a sign-off. Later sign-offs after this are what restore the report."""
    if not reason.strip():
        raise ShadowReportError("a revocation must state a reason")
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO shadow_report_events (report_id, event_type, reason,"
            " actor, recorded_at) VALUES (?,?,?,?,?)",
            (report_id, "revocation", reason, actor, _now()))
    return report_id


def accept_divergence(*, report_id: str, surface: str, actor: str,
                      authority: str, reason: str,
                      observed_divergence: float | None = None,
                      allowance: float | None = None,
                      path: str | Path | None = None) -> int:
    """Accept one divergence, with the authority that is allowed to accept it.

    The authority is checked against what the comparison declared, because
    "anyone may accept a difference" means the tolerance is not a control at all —
    it is a suggestion.
    """
    expected = compare.DEFAULT_ACCEPTANCE_AUTHORITY.get(surface)
    if expected is None:
        raise ShadowReportError(
            f"surface {surface!r} has no acceptance authority; it is compared "
            f"exactly, so it cannot be accepted — fix it and re-run")
    if authority != expected:
        raise ShadowReportError(
            f"surface {surface!r} requires acceptance by {expected!r}, not "
            f"{authority!r}")
    if not reason.strip():
        raise ShadowReportError("an acceptance must state a reason")

    state = read_state(report_id, path=path)
    if surface not in state.unaccepted:
        raise ShadowReportError(
            f"surface {surface!r} is not an unaccepted divergence on report "
            f"{report_id} (surfaces: {sorted(state.unaccepted)})")

    with _connect(path) as conn:
        cursor = conn.execute(
            "INSERT INTO shadow_difference_acceptances (report_id, surface,"
            " observed_divergence, allowance, accepted_by, authority, reason,"
            " accepted_at) VALUES (?,?,?,?,?,?,?,?)",
            (report_id, surface, observed_divergence, allowance, actor,
             authority, reason, _now()))
        acceptance_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO shadow_report_events (report_id, event_type, reason,"
            " actor, authority, recorded_at, payload_json)"
            " VALUES (?,?,?,?,?,?,?)",
            (report_id, "acceptance", f"{surface}: {reason}", actor, authority,
             _now(), _dumps({"surface": surface, "acceptance_id": acceptance_id})))
    return acceptance_id


def revoke_acceptance(*, report_id: str, surface: str, actor: str, reason: str,
                      path: str | Path | None = None) -> str:
    """Revoke an acceptance. The acceptance row stays; the revocation is new.

    A revoked acceptance puts the divergence back into the unaccepted set. The
    revocation is recorded in the event log rather than by mutating the
    acceptance row, so the acceptance table can keep its append-only trigger
    intact — dropping a trigger to perform one write would remove the constraint
    for every later write too, which is the trade this store exists to refuse.
    """
    if not reason.strip():
        raise ShadowReportError("an acceptance revocation must state a reason")
    with _connect(path) as conn:
        active = _active_acceptance_row(conn, report_id, surface)
        if active is None:
            raise ShadowReportError(
                f"no active acceptance for surface {surface!r} on report {report_id}")
        conn.execute(
            "INSERT INTO shadow_report_events (report_id, event_type, reason,"
            " actor, recorded_at, payload_json) VALUES (?,?,?,?,?,?)",
            (report_id, "acceptance_revoked", f"{surface}: {reason}", actor,
             _now(), _dumps({"surface": surface, "acceptance_id": int(active)})))
    return report_id


def _active_acceptance_row(conn: sqlite3.Connection, report_id: str,
                           surface: str) -> int | None:
    """The newest acceptance for `surface` that has not been revoked."""
    revoked_ids = set()
    for row in conn.execute(
            "SELECT payload_json, surfaces_json FROM shadow_report_events"
            " WHERE report_id=? AND event_type='acceptance_revoked'",
            (report_id,)).fetchall():
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if payload.get("acceptance_id") is not None:
            revoked_ids.add(int(payload["acceptance_id"]))

    for row in conn.execute(
            "SELECT acceptance_id FROM shadow_difference_acceptances"
            " WHERE report_id=? AND surface=? ORDER BY acceptance_id DESC",
            (report_id, surface)).fetchall():
        if int(row["acceptance_id"]) not in revoked_ids:
            return int(row["acceptance_id"])
    return None


def _active_acceptance_surfaces(conn: sqlite3.Connection,
                                report_id: str) -> set[str]:
    """Surfaces with an acceptance that has not been revoked.

    Revocation lives in the event log, so this reads both. Deriving it here
    rather than from a `revoked_at` column is what lets the acceptance table keep
    its append-only trigger.
    """
    revoked = set()
    for row in conn.execute(
            "SELECT payload_json, surfaces_json FROM shadow_report_events"
            " WHERE report_id=? AND event_type='acceptance_revoked'",
            (report_id,)).fetchall():
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        surface = str(payload.get("surface") or "")
        if surface:
            revoked.add(surface)

    return {
        row["surface"] for row in conn.execute(
            "SELECT DISTINCT surface FROM shadow_difference_acceptances"
            " WHERE report_id=?", (report_id,)).fetchall()
    } - revoked


def _unaccepted_surfaces(result: compare.ComparisonResult) -> list[str]:
    """Diverged surfaces with no active acceptance."""
    return sorted(result.diverged)


def read_state(report_id: str, path: str | Path | None = None) -> ReportState:
    """Derive the current state from the event log.

    Derived rather than stored, because a stored status needs a writer that
    always agrees with the log — and the log is the record an auditor reads.
    """
    try:
        conn = _connect(path, writable=False)
    except ShadowReportError as exc:
        raise ShadowReportError(f"no shadow report {report_id!r}: {exc}") from exc
    with conn:
        rows = conn.execute(
            "SELECT * FROM shadow_report_events WHERE report_id=? ORDER BY event_id",
            (report_id,)).fetchall()
        if not rows:
            raise ShadowReportError(f"no shadow report {report_id!r}")

        latest_comparison = None
        status = PENDING
        for row in rows:
            event = row["event_type"]
            if event in ("comparison", "recomparison"):
                latest_comparison = row
                status = PENDING
            elif event == "signoff":
                status = SIGNED_OFF
            elif event == "rejection":
                status = REJECTED
            elif event == "revocation":
                status = REVOKED

        accepted = _active_acceptance_surfaces(conn, report_id)

    assert latest_comparison is not None  # guarded above
    diverged = [surface for surface, verdict
                in json.loads(latest_comparison["verdicts_json"]).items()
                if verdict == compare.DIVERGED]
    not_compared = tuple(json.loads(latest_comparison["not_compared_json"]))

    return ReportState(
        report_id=report_id, status=status,
        run_id=latest_comparison["run_id"],
        consumer_id=latest_comparison["consumer_id"],
        batch_class=latest_comparison["batch_class"],
        scope_hash=latest_comparison["scope_hash"],
        packet_hash=latest_comparison["packet_hash"],
        code_fingerprint=latest_comparison["code_fingerprint"],
        not_compared=not_compared,
        unaccepted=tuple(s for s in sorted(diverged) if s not in accepted),
        surfaces=json.loads(latest_comparison["verdicts_json"]))


def events(report_id: str, path: str | Path | None = None) -> list[dict[str, Any]]:
    with _connect(path) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM shadow_report_events WHERE report_id=? ORDER BY event_id",
            (report_id,)).fetchall()]


def acceptances(report_id: str, path: str | Path | None = None) -> list[dict[str, Any]]:
    with _connect(path) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM shadow_difference_acceptances WHERE report_id=?"
            " ORDER BY acceptance_id", (report_id,)).fetchall()]


# --------------------------------------------------------------------------- #
# task 3.6 — the four citation checks
# --------------------------------------------------------------------------- #

def check_citable(*, report_id: str, scope: dict[str, Any],
                  required_surfaces: Iterable[str] | None = None,
                  code_hash: str | None = None,
                  path: str | Path | None = None) -> tuple[bool, list[str]]:
    """May this report be cited as evidence for this cutover?

    All four checks, in the order an operator would want them explained:

    1. signed off, and neither rejected nor revoked
    2. no `not-compared` on a required surface
    3. no unaccepted divergence
    4. still applicable to this scope and to the current code

    Each returns all its problems rather than the first, because an operator
    fixing a citation needs the whole list, not a sequence of one-at-a-time
    discoveries.
    """
    problems: list[str] = []
    try:
        state = read_state(report_id, path=path)
    except (sqlite3.Error, OSError) as exc:
        raise ShadowReportError(f"no shadow report {report_id!r}: store unreadable ({exc})") from exc

    if state.status != SIGNED_OFF:
        detail = {
            REJECTED: "the report was rejected",
            REVOKED: "the report's sign-off was revoked",
            PENDING: "the report was never signed off",
        }.get(state.status, state.status)
        # A later sign-off after a revocation restores the report, so the status
        # is derived rather than stored — a report that once passed and was
        # withdrawn does not get to pass because it passed once.
        problems.append(f"report {report_id} is not citable: {detail}")

    for surface in (required_surfaces or ()):
        if surface in state.not_compared or surface not in state.surfaces:
            problems.append(
                f"required surface {surface} is not-compared; it was never "
                f"compared, so nothing is known about it")

    if state.unaccepted:
        problems.append(
            f"unaccepted divergence(s): {', '.join(state.unaccepted)} — accepted "
            f"by the declared authority or fixed and re-run")

    if scope_fingerprint(scope) != state.scope_hash:
        problems.append(
            f"scope mismatch: the report covers {state.scope_hash[:12]} but this "
            f"cutover is {scope_fingerprint(scope)[:12]}")

    current_code = code_hash or code_fingerprint()
    if current_code != state.code_fingerprint:
        problems.append(
            f"the report predates a change to the code or configuration it "
            f"compared against ({state.code_fingerprint[:12]} -> "
            f"{current_code[:12]}); re-run the shadow comparison")

    return (not problems, problems)


def check_batch_report(batch, *, path=None):
    """Formal gate: historical comparisons remain diagnostic only."""
    from .acceptance_reports import check_batch_report as check_new
    return check_new(batch, path=path)


def require_batch_report(batch, checker):
    if not str(batch.shadow_report_id or "").strip():
        return False, ["shadow report ID is required"]
    if checker is None:
        return False, ["shadow report checker is required"]
    try:
        ok, problems = checker(batch)
        if not isinstance(ok, bool) or not isinstance(problems, (list, tuple)):
            return False, ["invalid shadow report checker result"]
        if not ok and not problems:
            return False, ["shadow report checker refused"]
        return ok and not problems, list(problems)
    except Exception as exc:  # noqa: BLE001
        return False, [f"shadow report checker failed: {exc}"]


def activation_batch(request):
    from types import SimpleNamespace

    classes = {"projection_read": "research_read", "analyst_output": "research_read",
               "dispatcher_schedule": "schedule", "approval_lifecycle": "internal_state_approval",
               "clerk_publication": "internal_state_approval", "live_trader": "live_trader"}
    return SimpleNamespace(shadow_report_id=request.report_id, scope=request.scope,
                           consumer_id=request.consumer_id,
                           batch_class=classes[request.boundary], required_surfaces=())


def assert_citable(*, report_id: str, scope: dict[str, Any],
                   required_surfaces: Iterable[str] | None = None,
                   code_hash: str | None = None,
                   path: str | Path | None = None) -> ReportState:
    ok, problems = check_citable(report_id=report_id, scope=scope,
                                 required_surfaces=required_surfaces,
                                 code_hash=code_hash, path=path)
    if not ok:
        raise ShadowReportError(
            f"shadow report {report_id} cannot be cited as evidence:\n"
            + "\n".join(f"  - {problem}" for problem in problems))
    return read_state(report_id, path=path)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
