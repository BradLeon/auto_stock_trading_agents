"""期初建账 and the daily position snapshot.

A Day-1 baseline makes the account's state at one moment the opening balance: the broker's positions, cash and net
liquidation are stored, and every audit exception open at that moment is sealed (status `waived`, original row and
detail kept) because it describes history the Clerk can no longer act on. Nothing is deleted; fills, trades,
episodes and the exceptions themselves stay readable.

Each Clerk run then stores the positions it saw. Yesterday's snapshot plus the fills between the two snapshots must
equal today's snapshot — that is the check that tells a lost fill from a quiet day (`position_changes`).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .ids import model_id, run_id

log = logging.getLogger("ats.clerk")

BASELINE_KEY = "ledger_baseline_date"
BASELINE_NOTE_KEY = "ledger_baseline_note"
SNAPSHOT_KIND = "positions_snapshot"
SNAPSHOT_VERSION = "v1"


def baseline_date(store) -> str:
    return store.get_meta(BASELINE_KEY)


def snapshot_payload(portfolio) -> dict:
    from ..journal.episodes import position_key

    return {
        "as_of": portfolio.as_of.isoformat(), "account_id": portfolio.account_id,
        "net_liquidation": portfolio.net_liquidation, "cash": portfolio.cash,
        "daily_pnl": portfolio.daily_pnl, "realized_pnl": portfolio.realized_pnl,
        "positions": [{
            "key": position_key(p), "symbol": p.symbol, "sec_type": p.sec_type, "qty": p.qty,
            "avg_cost": p.avg_cost, "market_price": p.market_price, "market_value": p.market_value,
            "unrealized_pnl": p.unrealized_pnl, "weight": p.weight, "currency": p.currency,
            "multiplier": p.multiplier, "expiry": p.expiry, "strike": p.strike, "right": p.right,
        } for p in portfolio.positions],
    }


def put_snapshot(store, payload: dict, *, period: str, commit: bool = True) -> str:
    mid = model_id(SNAPSHOT_KIND, period, SNAPSHOT_VERSION)
    now = datetime.now(timezone.utc).isoformat()
    store.conn.execute(
        "INSERT OR REPLACE INTO ledger_read_models (model_id, kind, period, as_of, method_version, "
        "source_facts_hash, payload, rebuilt_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (mid, SNAPSHOT_KIND, period, payload["as_of"], SNAPSHOT_VERSION, "",
         json.dumps(payload, ensure_ascii=False, default=str), now))
    if commit:
        store.conn.commit()
    return mid


def save_run_snapshot(store, portfolio) -> bool:
    """The positions one Clerk run saw. A reused run never reaches its steps, so it never writes a second one."""
    if portfolio is None:
        return False
    payload = snapshot_payload(portfolio)
    put_snapshot(store, payload, period=payload["as_of"])
    return True


def snapshot_in_run(store, started_at: str, finished_at: str | None) -> dict | None:
    """The snapshot saved while the run was executing (None for a run that never saved one)."""
    row = store.conn.execute(
        "SELECT payload FROM ledger_read_models WHERE kind = ? AND rebuilt_at >= ? "
        "AND rebuilt_at <= COALESCE(?, '9999') ORDER BY rebuilt_at DESC LIMIT 1",
        (SNAPSHOT_KIND, started_at, finished_at)).fetchone()
    return json.loads(row["payload"]) if row else None


def snapshot_before(store, as_of: str) -> dict | None:
    row = store.conn.execute(
        "SELECT payload FROM ledger_read_models WHERE kind = ? AND as_of < ? ORDER BY as_of DESC LIMIT 1",
        (SNAPSHOT_KIND, as_of)).fetchone()
    return json.loads(row["payload"]) if row else None


def latest_snapshot(store) -> dict | None:
    row = store.conn.execute(
        "SELECT payload FROM ledger_read_models WHERE kind = ? ORDER BY as_of DESC LIMIT 1",
        (SNAPSHOT_KIND,)).fetchone()
    return json.loads(row["payload"]) if row else None


def _ts(stamp) -> datetime:
    value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def position_changes(store, previous: dict | None, current: dict) -> dict:
    """Previous positions + the fills recorded between the two snapshots vs the positions the broker shows now."""
    if previous is None:
        return {"baseline": True, "rows": [], "mismatched": []}
    start, end = _ts(previous["as_of"]), _ts(current["as_of"])
    net: dict[str, float] = {}
    for fill in store.conn.execute(
            "SELECT symbol, instrument_key, side, shares, time, captured_at FROM fills "
            "WHERE COALESCE(time, captured_at, '') != ''"):
        moment = _ts(fill["time"] or fill["captured_at"])
        if not start < moment <= end:
            continue
        key = fill["instrument_key"] or fill["symbol"]
        net[key] = net.get(key, 0.0) + (fill["shares"] if fill["side"] == "BOT" else -fill["shares"])
    before = {p["key"]: p for p in previous["positions"]}
    now = {p["key"]: p for p in current["positions"]}
    rows = []
    for key in sorted(set(before) | set(now) | set(net)):
        had, bought, has = (before[key]["qty"] if key in before else 0.0), net.get(key, 0.0), \
            (now[key]["qty"] if key in now else 0.0)
        label = (now.get(key) or before.get(key) or {"symbol": key})
        rows.append({"key": key, "symbol": label["symbol"], "sec_type": label.get("sec_type", "STK"),
                     "before": had, "traded": bought, "expected": had + bought, "actual": has,
                     "difference": has - (had + bought)})
    return {"baseline": False, "rows": rows, "since": previous["as_of"],
            "mismatched": [r for r in rows if abs(r["difference"]) > 1e-6]}


def establish_baseline(*, store, broker, as_of: str, operator: str, reason: str, apply: bool) -> dict:
    """Open the books at `as_of` (YYYY-MM-DD) from the broker's current state; `apply=False` only reports the plan."""
    from ..workflow.isolation import assert_write_destination

    assert_write_destination(store, what="Day-1 baseline")
    current = baseline_date(store)
    if current and current != as_of:
        raise ValueError(f"a baseline already exists at {current}; refusing to move it to {as_of}")
    portfolio = broker.get_portfolio()
    if portfolio is None:
        raise RuntimeError("broker portfolio unavailable; the opening positions cannot be recorded")
    open_rows = [dict(r) for r in store.conn.execute(
        "SELECT exception_id, kind FROM ledger_exceptions WHERE status = 'open'")]
    by_kind: dict[str, int] = {}
    for row in open_rows:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
    to_void = [r["run_id"] for r in store.conn.execute(
        "SELECT run_id FROM clerk_runs WHERE kind = 'full' AND status = 'completed' AND as_of = ?", (as_of,))]
    plan = {"as_of": as_of, "already": current == as_of, "positions": len(portfolio.positions),
            "net_liquidation": portfolio.net_liquidation, "sealed": by_kind, "voided_runs": to_void}
    if not apply or current == as_of:
        return {**plan, "applied": False}

    now = datetime.now(timezone.utc).isoformat()
    note = f"期初建账封存（{as_of}，{operator}）：{reason}"
    put_snapshot(store, snapshot_payload(portfolio), period=f"baseline:{as_of}", commit=False)
    store.conn.execute(
        "UPDATE ledger_exceptions SET status = 'waived', resolved_at = ?, updated_at = ?, "
        "basis = CASE WHEN COALESCE(basis, '') = '' THEN ? ELSE basis || ' ｜ ' || ? END WHERE status = 'open'",
        (now, now, note, note))
    for rid in to_void:
        store.conn.execute(
            "UPDATE clerk_runs SET status = 'voided', error = ? WHERE run_id = ?",
            (f"作废：{as_of} 期初建账之前的运行，建账后的第一次运行需要重新执行", rid))
    store.conn.execute(
        "INSERT OR REPLACE INTO clerk_runs (run_id, kind, window_start, window_end, as_of, status, counts_json, "
        "error, started_at, finished_at) VALUES (?, 'baseline', ?, ?, ?, 'completed', ?, NULL, ?, ?)",
        (run_id("baseline", as_of, as_of, as_of), as_of, as_of, as_of,
         json.dumps(plan, ensure_ascii=False), now, now))
    store.set_meta(BASELINE_NOTE_KEY, json.dumps({"operator": operator, "reason": reason, "at": now}, ensure_ascii=False))
    store.set_meta(BASELINE_KEY, as_of)   # commits
    return {**plan, "applied": True}
