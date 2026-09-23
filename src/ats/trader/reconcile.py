"""Post-close reconciliation: make the trade record match what the broker actually did.

`place_orders` polls the order status for 3 seconds and then returns — it has to,
because it runs inside the Feishu approval-resume path and blocking longer would
stall the callback. Anything that settles later (a late fill, a DAY order the
exchange cancels at the close) is therefore frozen mid-flight: 10 of the first 52
rows sat at `submitted` with no fill price, and `realized_pnl` was never written for
any row at all.

This module is the asynchronous other half. It runs read-only after the close,
pulls executions + completed orders, and backfills the outcome onto `trades`.

Attribution is the second job. `reqExecutions` returns the ACCOUNT's executions, so
it also carries orders placed by hand in TWS. Each fill is tagged `origin`:

  matches a local order (order_ref / perm_id / order_id+date)
                                         -> system  (confidence = match method)
  non-`ats:` order_ref set in TWS        -> manual  (a deliberate human order)
  otherwise                              -> unattributed + audit exception

The third class is the §11.1 ruling (task 2.4): "unattributable" is NOT the
same as "manual", and collapsing the two — the old behaviour — disguised
unknown fills as deliberate human trades. An unattributed fill stays in the
ledger with an explicit exception row; it never enters system performance.

The session-date scoping on the order_id rule is not optional: IBKR's orderId
is a per-client sequence reset by a TWS restart, so an unscoped join will
eventually match a completely unrelated order from another day.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger("ats.reconcile")

_OPEN_STATES = ("pending", "submitted")
_MAX_DAY_SKEW = 1        # a fill's date may differ from submit by a day (tz/overnight)


def _as_date(stamp: str | None) -> date | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _match(fill: dict, trades: list[dict]) -> tuple[dict | None, str]:
    """Find the order a fill belongs to. Returns (trade_row, link_confidence)."""
    ref = (fill.get("order_ref") or "").strip()
    if ref.startswith("ats:"):
        for t in trades:
            if (t["order_ref"] or "") == ref:
                return t, "order_ref"
    perm = (fill.get("perm_id") or "").strip()
    if perm:
        for t in trades:
            if (t["perm_id"] or "") == perm:
                return t, "perm_id"
    oid = (fill.get("order_id") or "").strip()
    if oid and oid != "0":
        fdate = _as_date(fill.get("time"))
        for t in trades:
            if (t["order_id"] or "") != oid or t["symbol"] != fill.get("symbol"):
                continue
            tdate = _as_date(t["first_submitted_at"] or t["submitted_at"])
            if fdate and tdate and abs((fdate - tdate).days) > _MAX_DAY_SKEW:
                continue        # orderId reuse across sessions — not the same order
            return t, "order_id+date"
    return None, "none"


def _is_late(fill: dict, trade: dict) -> bool:
    """Task 3.2: the fill's session is AFTER the order's submit day."""
    fdate = _as_date(fill.get("time"))
    tdate = _as_date(trade.get("first_submitted_at") or trade.get("submitted_at"))
    return bool(fdate and tdate and fdate > tdate)


def _apply_cumulative_fills(store, trades: list[dict], rid: int,
                            execs: list[dict]) -> None:
    """Task 3.1: roll a set of executions onto their order, cumulatively.

    Partial fills keep the order in `partial` (never 'filled' by price-sight
    alone); only accumulated shares >= ordered qty closes it as filled. Every
    fill IS broker evidence, so the status basis is 'broker' — this also lets
    a late fill CORRECT an earlier inferred-expired order (task 3.4).
    """
    row = next(t for t in trades if t["rid"] == rid)
    total_shares = sum(float(e.get("shares") or 0) for e in execs)
    if total_shares <= 0:
        return
    priced = [(float(e["price"]), float(e.get("shares") or 0)) for e in execs
              if e.get("price")]
    avg_px = (sum(p * s for p, s in priced) / sum(s for _, s in priced)) if priced else None
    filled_at = max((e.get("time") or "" for e in execs), default="")
    qty = float(row.get("qty") or 0)
    status = "filled" if (qty and total_shares >= qty) else "partial"
    store.conn.execute(
        "UPDATE trades SET avg_fill_price = COALESCE(avg_fill_price, ?), "
        "filled_at = COALESCE(filled_at, ?), filled_qty = ?, status = ?, "
        "terminal_basis = 'broker' WHERE rowid = ?",
        (avg_px, filled_at, total_shares, status, rid))


def reconcile(broker=None, *, store=None, dry_run: bool = False) -> dict:
    """Backfill execution outcomes onto `trades`. Idempotent. Never raises upward.

    Returns a summary dict (also what the CLI prints).
    """
    from ..memory import get_store

    store = store or get_store()
    summary = {"fills_seen": 0, "fills_new": 0, "linked": 0, "manual": 0,
               "unattributed": 0,
               "pnl_backfilled": 0, "status_resolved": 0, "errors": []}

    if broker is None:
        try:
            from .execute import IBKRBroker

            broker = IBKRBroker()
        except Exception as exc:  # noqa: BLE001 - reconcile must not break the cascade
            summary["errors"].append(f"broker unavailable: {exc}")
            log.warning("reconcile: broker unavailable: %s", exc)
            return summary

    try:
        fills = broker.get_fills() or []
        completed = broker.completed_orders() or []
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(f"fetch failed: {exc}")
        log.warning("reconcile: fetch failed: %s", exc)
        return summary

    summary["fills_seen"] = len(fills)
    if not dry_run:
        summary["fills_new"] = store.upsert_fills(fills)

    trades = [dict(r) for r in store.conn.execute(
        "SELECT rowid AS rid, * FROM trades ORDER BY rowid").fetchall()]

    # --- 1. attribute fills, and roll their P&L up onto the owning order --------
    # Cumulative per-order execution accounting (task 3.1): multiple partial
    # fills SUM onto one order — total shares, volume-weighted average price
    # and summed P&L — instead of the old "saw a price → status='filled'"
    # overwrite that made partial fills disappear from the ledger.
    pnl_by_rid: dict[int, float] = {}
    exec_by_rid: dict[int, list[dict]] = {}
    for f in fills:
        row, how = _match(f, trades)
        if row is not None:
            origin = "system"
        elif (f.get("order_ref") or "").strip() and \
                not (f.get("order_ref") or "").startswith("ats:"):
            # A human typed their own orderRef in TWS — a deliberate manual
            # order with evidence, not an unknown (task 2.4).
            origin = "manual"
        else:
            origin = "unattributed"
        entry_id = row.get("client_order_id") if row else None
        if row:
            summary["linked"] += 1
            rp = f.get("realized_pnl")
            if isinstance(rp, (int, float)):
                pnl_by_rid[row["rid"]] = pnl_by_rid.get(row["rid"], 0.0) + float(rp)
            exec_by_rid.setdefault(row["rid"], []).append(f)
        elif origin == "manual":
            summary["manual"] += 1
        else:
            # §11.1: an unattributable fill is recorded AND flagged, never
            # silently absorbed into either class (task 2.4).
            summary["unattributed"] += 1
            if not dry_run:
                store.record_ledger_exception(
                    kind="unattributed_fill", subject_key=f"fill:{f.get('exec_id')}",
                    symbol=f.get("symbol"),
                    basis="no order_ref/perm_id/order_id+date evidence links this "
                          "fill to a local order, and no human orderRef is present",
                    detail_json=json.dumps(
                        {"exec_id": f.get("exec_id"), "symbol": f.get("symbol"),
                         "side": f.get("side"), "shares": f.get("shares"),
                         "price": f.get("price"), "time": f.get("time"),
                         "order_ref": f.get("order_ref") or "",
                         "perm_id": f.get("perm_id") or ""},
                        ensure_ascii=False))
        if not dry_run:
            # entry_id is how the episode reducer later joins a fill back to the
            # pre-registered plan (JournalEntry) that proposed it.
            if row is not None:
                # Task 2.2: a matched fill inherits its order's FULL decision
                # chain — the fill becomes as traceable as the order it fills.
                late = _is_late(f, row)
                store.conn.execute(
                    "UPDATE fills SET origin = ?, link_confidence = ?, entry_id = ?, "
                    "cycle_id = ?, revision_no = ?, decision_hash = ?, approval_id = ?, "
                    "late_backfill = ? "
                    "WHERE exec_id = ?",
                    (origin, how, entry_id, row.get("cycle_id"),
                     row.get("revision_no"), row.get("decision_hash") or "",
                     row.get("approval_id") or "",
                     1 if late else None, f.get("exec_id")))
                if late:
                    summary.setdefault("late_backfilled", 0)
                    summary["late_backfilled"] += 1
            else:
                store.conn.execute(
                    "UPDATE fills SET origin = ?, link_confidence = ?, entry_id = ? "
                    "WHERE exec_id = ?", (origin, how, entry_id, f.get("exec_id")))

    if not dry_run:
        for rid, execs in exec_by_rid.items():
            _apply_cumulative_fills(store, trades, rid, execs)
    for rid, pnl in pnl_by_rid.items():
        if not dry_run:
            store.conn.execute("UPDATE trades SET realized_pnl = ? WHERE rowid = ?", (pnl, rid))
        summary["pnl_backfilled"] += 1

    # --- 2. resolve orders still stuck mid-flight -------------------------------
    # A broker-reported terminal state (cancelled/rejected/filled/...) is the
    # AUTHORITATIVE basis (task 3.3): it also applies to partially-filled
    # orders, and it overrides any earlier inference.
    by_perm = {c["perm_id"]: c for c in completed if c.get("perm_id")}
    by_ref = {c["order_ref"]: c for c in completed if c.get("order_ref")}
    by_oid = {c["order_id"]: c for c in completed if c.get("order_id")}
    for t in trades:
        if t["status"] not in (*_OPEN_STATES, "partial"):
            continue
        c = (by_ref.get(t["order_ref"] or "") or by_perm.get(t["perm_id"] or "")
             or by_oid.get(t["order_id"] or ""))
        if not c or c["symbol"] != t["symbol"]:
            continue
        if not dry_run:
            store.conn.execute(
                "UPDATE trades SET status = ?, avg_fill_price = COALESCE(avg_fill_price, ?), "
                "terminal_basis = 'broker' "
                "WHERE rowid = ?", (c["status"], c.get("avg_fill_price"), t["rid"]))
        summary["status_resolved"] += 1

    # --- 3. a DAY order nobody ever resolved is expired, not in-flight ----------
    # Task 3.4: this is an INFERENCE, not broker evidence — it carries an
    # explicit basis so downstream consumers can distinguish it, and a later
    # broker fill (section 1) will correct it back to partial/filled.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    stale = [t for t in trades
             if t["status"] in _OPEN_STATES
             and (t["first_submitted_at"] or t["submitted_at"] or "")[:10] < cutoff]
    for t in stale:
        if not dry_run:
            store.conn.execute(
                "UPDATE trades SET status = 'expired', terminal_basis = 'inferred', "
                "error = COALESCE(NULLIF(error,''), ?) "
                "WHERE rowid = ?",
                ("未在当日成交，DAY 单已失效（对账推定）", t["rid"]))
        summary["status_resolved"] += 1

    if not dry_run:
        from ..execution.ledger import verify_attempt_counts

        # Task 3.9: every replay re-checks the attempt sequence against the
        # attributed broker history — retries must stay explainable.
        verify_attempt_counts(store)
        store.conn.commit()
        store.set_meta("last_reconcile_at", datetime.now(timezone.utc).isoformat())
    return summary


def render(summary: dict) -> str:
    lines = ["=== 对账 (reconcile) ==="]
    lines.append(f"  券商返回成交      {summary['fills_seen']}（新增 {summary['fills_new']}）")
    lines.append(f"  归属系统单        {summary['linked']}")
    lines.append(f"  归属手工单        {summary['manual']}")
    lines.append(f"  无法归因          {summary.get('unattributed', 0)}（已登记异常）")
    lines.append(f"  回填盈亏的订单     {summary['pnl_backfilled']}")
    lines.append(f"  解决在途状态       {summary['status_resolved']}")
    for e in summary["errors"]:
        lines.append(f"  ⚠️ {e}")
    return "\n".join(lines)


def run(dry_run: bool = False) -> int:
    s = reconcile(dry_run=dry_run)
    print(render(s))
    return 1 if s["errors"] else 0
