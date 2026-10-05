"""Shadow trade ledger, physically separate from the real one (task 3.7).

A shadow order that lands in the real `trades` table defeats the assertion it was
meant to support. It inflates positions, corrupts the cash reconciliation, and —
worse — makes "no unapproved order was placed during the shadow period"
unfalsifiable, because the shadow run's own writes are in the ledger the claim is
about.

So the isolation is physical, and the one thing that does NOT happen is
redirection: `trades` writes during a shadow run are **refused**, not quietly
sent elsewhere. Redirecting would make a bug that writes to the production ledger
look like it worked, and the next run outside the shadow window would write to
production for real.

The shadow ledger holds everything needed to rebuild attribution — order identity,
the decision and approval chain, quantities, the broker submit attempt — because a
ledger that cannot survive a process restart cannot support a cutover decision
made weeks later.
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

DEFAULT_PATH = "var/shadow/orders.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_order_intents (
    intent_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    revision_no INTEGER NOT NULL DEFAULT 0,
    sequence INTEGER NOT NULL DEFAULT 0,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    quantity REAL NOT NULL,
    decision_hash TEXT NOT NULL DEFAULT '',
    approval_id TEXT NOT NULL DEFAULT '',
    route_id TEXT NOT NULL DEFAULT '',
    route_generation INTEGER NOT NULL DEFAULT 0,
    submitted INTEGER NOT NULL DEFAULT 0,
    submit_refusal_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shadow_intents_run
    ON shadow_order_intents(run_id, cycle_id);

-- The broker submit attempts a shadow run made. Present even when the write was
-- refused: "the shadow run reached a submit call and was stopped" is the fact
-- task 3.8's acceptance evidence needs, and it is invisible without a row.
CREATE TABLE IF NOT EXISTS shadow_submit_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    refusal_code TEXT NOT NULL DEFAULT '',
    refusal_id TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TRIGGER IF NOT EXISTS shadow_intents_no_update
BEFORE UPDATE ON shadow_order_intents
BEGIN SELECT RAISE(ABORT, 'shadow order intents are append-only'); END;
CREATE TRIGGER IF NOT EXISTS shadow_intents_no_delete
BEFORE DELETE ON shadow_order_intents
BEGIN SELECT RAISE(ABORT, 'shadow order intents are append-only'); END;
"""


class ShadowLedgerWriteRefused(RuntimeError):
    """A shadow run attempted to write the real trade ledger."""

    reason_code = "shadow_run_refused_trades_write"


def default_shadow_ledger_path() -> str:
    """Where shadow intents live.

    Its own env var rather than a share of `ATS_SHADOW_DB_PATH`: Phase E's shadow
    database holds workflow runs and triggers, and mixing an order ledger into it
    would put trade intents behind the same access rule as scheduling state.
    """
    return os.environ.get("ATS_SHADOW_ORDER_DB",
                          str(REPO_ROOT / DEFAULT_PATH))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: str | Path | None = None) -> sqlite3.Connection:
    target = Path(path or default_shadow_ledger_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)
    return conn


@dataclass(frozen=True)
class ShadowIntent:
    """One order a shadow run would have placed."""

    intent_id: str
    run_id: str
    cycle_id: str
    symbol: str
    action: str
    quantity: float
    revision_no: int = 0
    sequence: int = 0
    decision_hash: str = ""
    approval_id: str = ""
    route_id: str = ""
    route_generation: int = 0

    def as_row(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id, "run_id": self.run_id,
            "cycle_id": self.cycle_id, "symbol": self.symbol,
            "action": self.action, "quantity": self.quantity,
            "revision_no": self.revision_no, "sequence": self.sequence,
            "decision_hash": self.decision_hash, "approval_id": self.approval_id,
            "route_id": self.route_id, "route_generation": self.route_generation,
        }


def record_intent(intent: ShadowIntent, *, submitted: bool = False,
                  submit_refusal_id: str = "",
                  path: str | Path | None = None) -> str:
    """Record an order the shadow run would have placed.

    The causal fields are kept so attribution can be rebuilt after the fact: which
    decision revision, which approval, which route and generation. A shadow ledger
    holding only "symbol, quantity" cannot answer "was this order authorised",
    which is the only reason to keep it.
    """
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO shadow_order_intents (intent_id, run_id, cycle_id,"
            " revision_no, sequence, symbol, action, quantity, decision_hash,"
            " approval_id, route_id, route_generation, submitted,"
            " submit_refusal_id, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (intent.intent_id, intent.run_id, intent.cycle_id,
             intent.revision_no, intent.sequence, intent.symbol, intent.action,
             intent.quantity, intent.decision_hash, intent.approval_id,
             intent.route_id, intent.route_generation, int(submitted),
             submit_refusal_id, _now()))
    return intent.intent_id


def record_submit_attempt(*, intent_id: str, accepted: bool,
                         refusal_code: str = "", refusal_id: str = "",
                         detail: dict[str, Any] | None = None,
                         path: str | Path | None = None) -> int:
    """Record that the shadow run reached a broker submit call and was stopped.

    Recorded whether or not it was accepted. A refused attempt is the evidence
    that the process-level prohibition actually held, so a shadow run that never
    reached a submit cannot be used to claim it works.
    """
    with _connect(path) as conn:
        cursor = conn.execute(
            "INSERT INTO shadow_submit_attempts (intent_id, attempted_at,"
            " accepted, refusal_code, refusal_id, detail_json)"
            " VALUES (?,?,?,?,?,?)",
            (intent_id, _now(), int(accepted), refusal_code, refusal_id,
             json.dumps(detail or {}, ensure_ascii=False, sort_keys=True,
                        default=str)))
        return int(cursor.lastrowid)


def intents(*, run_id: str | None = None, cycle_id: str | None = None,
            path: str | Path | None = None) -> list[dict[str, Any]]:
    clauses, params = [], []
    if run_id:
        clauses.append("run_id=?")
        params.append(run_id)
    if cycle_id:
        clauses.append("cycle_id=?")
        params.append(cycle_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(path) as conn:
        return [dict(row) for row in conn.execute(
            f"SELECT * FROM shadow_order_intents{where}"
            f" ORDER BY cycle_id, revision_no, sequence", params).fetchall()]


def submit_attempts(path: str | Path | None = None) -> list[dict[str, Any]]:
    with _connect(path) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM shadow_submit_attempts ORDER BY attempt_id").fetchall()]


def rebuild_attribution(cycle_id: str, path: str | Path | None = None
                        ) -> dict[str, Any]:
    """Rebuild a cycle's shadow attribution from the ledger alone.

    Deliberately independent of any live store: if rebuilding needs the process
    that produced it, the ledger is not evidence. Everything comes from the rows.
    """
    rows = [row for row in intents(cycle_id=cycle_id, path=path)]
    attempts = submit_attempts(path=path)
    by_intent: dict[str, list[dict[str, Any]]] = {}
    for attempt in attempts:
        by_intent.setdefault(attempt["intent_id"], []).append(attempt)

    orders = []
    for row in rows:
        related = by_intent.get(row["intent_id"], [])
        orders.append({
            "intent_id": row["intent_id"],
            "symbol": row["symbol"], "action": row["action"],
            "quantity": row["quantity"],
            "revision_no": row["revision_no"], "sequence": row["sequence"],
            "decision_hash": row["decision_hash"],
            "approval_id": row["approval_id"],
            "route_id": row["route_id"],
            "route_generation": row["route_generation"],
            "reached_broker": any(a["accepted"] for a in related),
            "refusal_codes": sorted({a["refusal_code"] for a in related
                                     if a["refusal_code"]}),
        })

    return {
        "cycle_id": cycle_id,
        "order_count": len(orders),
        "orders": orders,
        "submitted_count": sum(1 for o in orders if o["reached_broker"]),
        "refused_count": sum(1 for o in orders if o["refusal_codes"]),
        "every_order_authorized": all(
            bool(o["decision_hash"] and o["approval_id"]) for o in orders),
        "any_reached_broker": any(o["reached_broker"] for o in orders),
    }


# --------------------------------------------------------------------------- #
# the refusal
# --------------------------------------------------------------------------- #

def assert_real_ledger_not_written(store: Any, *, run_id: str = "",
                                   path: str | Path | None = None) -> dict[str, Any]:
    """Fail if the real `trades` table gained rows attributable to this run.

    Used as an independent check after a shadow run. The prohibition prevents the
    write; this verifies it, because a prevention that is never verified is a
    prevention nobody can rely on.
    """
    conn = getattr(store, "conn", store)
    rows = conn.execute(
        "SELECT cycle_id, source, context FROM trades").fetchall()
    leaked = []
    for row in rows:
        cycle = row[0] if not isinstance(row, dict) else row.get("cycle_id", "")
        source = row[1] if not isinstance(row, dict) else row.get("source", "")
        context = row[2] if not isinstance(row, dict) else row.get("context", "")
        if run_id and run_id in f"{cycle} {source} {context}":
            leaked.append({"cycle_id": cycle, "source": source, "context": context})
        elif not run_id and str(source).startswith("shadow"):
            leaked.append({"cycle_id": cycle, "source": source,
                           "context": context})

    if leaked:
        raise ShadowLedgerWriteRefused(
            f"the real trades ledger gained {len(leaked)} row(s) from shadow run "
            f"{run_id or '(unnamed)'}: {leaked[:5]}; a shadow run must never write "
            f"there, and the write was neither prevented nor redirected")
    return {"trades_rows_checked": len(rows), "leaked_rows": 0}


def refuse_trades_write(*, run_id: str = "", detail: str = "",
                        path: str | Path | None = None) -> None:
    """Refuse a real-ledger write from a shadow run, loudly.

    Never redirects. Redirecting would make a mistaken write look like it worked,
    and the mistake would then repeat for real outside the shadow window. Refusing
    turns the bug into an immediate, attributable failure.
    """
    raise ShadowLedgerWriteRefused(
        f"shadow run {run_id or '(unnamed)'} attempted to write the real trades "
        f"ledger ({detail or 'no detail'}). Shadow order intents belong in the "
        f"shadow ledger; this write was refused rather than redirected, so the "
        f"mistake surfaces here instead of becoming real state later.")


def shadow_attestation(*, run_id: str, store: Any = None,
                       path: str | Path | None = None) -> dict[str, Any]:
    """The claim a shadow run makes, plus the evidence for it.

    Every claim is paired with the observation that supports it. A shadow run that
    made no submit attempt cannot claim the prohibition held — it never tested it.
    """
    rows = intents(run_id=run_id, path=path)
    attempts = submit_attempts(path=path)
    mine = [a for a in attempts
            if any(row["intent_id"] == a["intent_id"] for row in rows)]
    accepted = [a for a in mine if a["accepted"]]

    attestation: dict[str, Any] = {
        "run_id": run_id,
        "intent_count": len(rows),
        "submit_attempts": len(mine),
        "accepted_attempts": len(accepted),
        "no_order_reached_broker": not accepted,
        # Stated rather than implied: an absence of attempts is not a pass.
        "prohibition_exercised": bool(mine),
    }
    if store is not None:
        attestation["real_ledger"] = assert_real_ledger_not_written(
            store, run_id=run_id, path=path)
    return attestation
