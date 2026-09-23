"""Ledger-level helpers: origin splitting for performance & internal state.

Clerk-adjacent deterministic logic that both the performance rebuild (task 5.1)
and the Internal State API (task 6.1) need: separating SYSTEM trades — the
only class that may enter system performance — from MANUAL orders (deliberate
human trades, shown separately) and UNATTRIBUTED fills (unknown, flagged,
excluded from system performance but never hidden).

§11.1: unattributable is not ignorable, and it is not manual either.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .ids import exception_id, model_id, run_id  # noqa: F401  (re-exported)

MANUAL_ORIGIN = "manual"
UNATTRIBUTED_ORIGIN = "unattributed"
SYSTEM_ORIGIN = "system"

# Origins that may NEVER enter system performance / attribution.
NON_SYSTEM_ORIGINS = frozenset({MANUAL_ORIGIN, UNATTRIBUTED_ORIGIN})


def split_by_origin(rows: Iterable[Mapping]) -> dict[str, list[Mapping]]:
    """Split ledger rows into system / manual / unattributed buckets.

    Rows without an `origin` field (or with an empty one) land in
    `unattributed`: absent evidence is unknown, not system.
    """
    out: dict[str, list[Mapping]] = {"system": [], "manual": [], "unattributed": []}
    for r in rows:
        origin = (r.get("origin") or "").strip() if hasattr(r, "get") else ""
        out[origin if origin in out else "unattributed"].append(r)
    return out


def system_only(rows: Iterable[Mapping]) -> list[Mapping]:
    """The rows allowed into system performance / attribution (task 2.5).

    ONLY an explicit `origin == "system"` qualifies — a missing origin is
    unknown, and unknown never enters system performance.
    """
    return [r for r in rows if (r.get("origin") or "").strip() == SYSTEM_ORIGIN]


# --------------------------------------------------------------------------- #
# Compensation bookkeeping (tasks 3.7–3.9)
# --------------------------------------------------------------------------- #

def register_reconciliation_gap(store, *, window_start: str, window_end: str,
                                reason: str, detail: str = "") -> str:
    """Task 3.7: a session window that never got reconciled AND whose broker
    data is no longer retrievable. The gap is explicit and counts toward
    Internal State completeness — never filled with other days' data."""
    import json as _json

    return store.record_ledger_exception(
        kind="reconciliation_gap", subject_key=window_start,
        window_start=window_start, window_end=window_end,
        basis=reason,
        detail_json=_json.dumps({"detail": detail}, ensure_ascii=False))


def register_position_cash_discrepancy(
        store, *, as_of: str, symbol: str, local_qty: float | None,
        broker_qty: float | None, local_cash: float | None = None,
        broker_cash: float | None = None) -> str:
    """Task 3.8: broker vs local-ledger divergence, recorded with BOTH sides'
    values. Never silently overwrites either side — the discrepancy row is the
    deliverable; resolving it is a separate, auditable step."""
    import json as _json

    subject = f"position:{symbol}:{as_of}" if symbol != "_CASH" else f"cash:{as_of}"
    return store.record_ledger_exception(
        kind="position_cash_discrepancy", subject_key=subject, symbol=symbol,
        as_of=as_of,
        basis="broker holdings differ from the local ledger derivation",
        detail_json=_json.dumps(
            {"local_qty": local_qty, "broker_qty": broker_qty,
             "local_cash": local_cash, "broker_cash": broker_cash},
            ensure_ascii=False))


def compare_holdings(store, *, as_of: str, broker_positions: list[dict],
                     local_positions: list[dict],
                     broker_cash: float | None = None,
                     local_cash: float | None = None) -> list[str]:
    """Task 3.8 orchestration: compare broker holdings with the local ledger
    and register every difference as an explicit exception. Returns the
    registered subjects (empty = fully consistent)."""
    broker = {p.get("symbol"): float(p.get("qty") or 0) for p in broker_positions}
    local = {p.get("symbol"): float(p.get("qty") or 0) for p in local_positions}
    registered: list[str] = []
    for symbol in sorted(set(broker) | set(local)):
        bq, lq = broker.get(symbol, 0.0), local.get(symbol, 0.0)
        if abs(bq - lq) > 1e-9:
            registered.append(register_position_cash_discrepancy(
                store, as_of=as_of, symbol=symbol, local_qty=lq, broker_qty=bq,
                local_cash=local_cash, broker_cash=broker_cash))
    if broker_cash is not None and local_cash is not None \
            and abs(broker_cash - local_cash) > 1e-6:
        registered.append(register_position_cash_discrepancy(
            store, as_of=as_of, symbol="_CASH", local_qty=None, broker_qty=None,
            local_cash=local_cash, broker_cash=broker_cash))
    return registered


def verify_attempt_counts(store, fills: list[dict] | None = None) -> list[str]:
    """Task 3.9: the order attempt sequence in the replay.

    Each matched intent's DISTINCT broker order_ids are evidence of how many
    separate submissions happened. If the broker saw more distinct order_ids
    than the local attempt counter recorded, the local attempt sequence has
    been lost or trimmed — register `attempt_count_mismatch` so the replay
    cannot silently pretend the retries never happened.

    Reads the attributed fills from the ledger itself (`fills.origin='system'`
    with an entry_id), so it re-verifies the WHOLE attributed history on every
    replay — exactly what an idempotent re-run needs.
    """
    rows = store.conn.execute(
        "SELECT entry_id, order_id, COUNT(DISTINCT order_id) n FROM fills "
        "WHERE COALESCE(origin,'') = 'system' AND COALESCE(entry_id,'') != '' "
        "AND COALESCE(order_id,'') NOT IN ('', '0') "
        "GROUP BY entry_id").fetchall()
    checked: list[str] = []
    for r in rows:
        entry_id = r["entry_id"]
        broker_orders = int(r["n"])
        trow = store.conn.execute(
            "SELECT attempt FROM trades WHERE client_order_id = ?",
            (entry_id,)).fetchone()
        if trow is None:
            continue
        local_attempts = int(trow["attempt"] or 1)
        if broker_orders > local_attempts:
            checked.append(store.record_ledger_exception(
                kind="attempt_count_mismatch", subject_key=f"intent:{entry_id}",
                cycle_id=entry_id.split(":")[0] if ":" in entry_id else None,
                basis="broker shows more distinct order_ids than the local "
                      "attempt counter — the attempt sequence is incomplete",
                detail_json=f'{{"broker_order_ids": {broker_orders}, '
                            f'"local_attempts": {local_attempts}}}'))
    return checked
