"""Performance & attribution rebuild (§15.4, Phase C group 5).

Derived read models are REBUILT from the immutable original records — orders,
fills, marks, fees, decision and approval records — never edited in place.
Same method version + same facts ⇒ same payload (deterministic); the method
version and rebuild time are recorded so any two results are comparable.

The rebuild writes ONLY `ledger_read_models`. If it fails, nothing partial is
published: the original records stay untouched and no half-built model is
presented as complete.

First-version attribution reuses the EXISTING deterministic formulas in
:mod:`ats.trader.analytics` (design Open Question: no new methodology here —
a richer attribution model should be a NEW method version, not a rewrite).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .clerk import read_model_hit, source_facts_hash
from .ids import model_id

log = logging.getLogger("ats.clerk.rebuild")

METHOD_VERSION = "clerk-v1"

KINDS = ("performance", "attribution")


def _month_window(period: str) -> tuple[str, str]:
    """`YYYY-MM` → [start, end) ISO prefixes for record filtering."""
    year, month = period.split("-")
    m = int(month)
    if m == 12:
        nxt = f"{int(year) + 1}-01"
    else:
        nxt = f"{year}-{m + 1:02d}"
    return period, nxt


def _system_trades_in_period(store, period: str) -> list:
    start, end = _month_window(period)
    rows = store.conn.execute(
        "SELECT symbol, action, qty, status, avg_fill_price, realized_pnl, "
        "submitted_at, cycle_id, revision_no, decision_hash, approval_id "
        "FROM trades WHERE COALESCE(submitted_at,'') >= ? "
        "AND COALESCE(submitted_at,'') < ? "
        "AND COALESCE(cycle_id,'') != '' "
        "AND COALESCE(decision_hash,'') != '' "
        "AND COALESCE(approval_id,'') != '' "
        "ORDER BY submitted_at", (start, end)).fetchall()
    return [dict(r) for r in rows]


def rebuild_performance(store, *, period: str,
                        method_version: str = METHOD_VERSION) -> dict:
    """Rebuild the performance read model for a period (task 5.1).

    Returns {"status": "skipped|rebuilt|failed", ...}. Never raises upward;
    never touches original records; never publishes a partial payload.
    """
    from ..trader import analytics
    from ..memory import get_store

    store = store or get_store()
    now = datetime.now(timezone.utc).isoformat()
    mid = model_id("performance", period, method_version)
    facts = source_facts_hash(store)

    if read_model_hit(store, kind="performance", period=period,
                      method_version=method_version, facts_hash=facts):
        return {"status": "skipped", "model_id": mid,
                "source_facts_hash": facts}

    try:
        trades = _system_trades_in_period(store, period)
        # Only SYSTEM trades enter the system performance (task 2.5): the
        # trade rows selected above are chain-complete system orders; manual
        # and unattributed fills never appear here.
        from types import SimpleNamespace

        history = [SimpleNamespace(
            net_liquidation=float(r["net_liquidation"] or 0),
            cumulative_pnl=r["cumulative_pnl"])
            for r in store.conn.execute(
                "SELECT net_liquidation, cumulative_pnl FROM performance "
                "ORDER BY as_of").fetchall()]
        episodes = store.list_episodes(limit=10_000)
        summary = analytics.summarize(history, episodes)
        summary["system_trades"] = len(trades)
        summary["system_realized_pnl"] = round(
            sum(float(t["realized_pnl"] or 0) for t in trades), 2)
    except Exception as exc:  # noqa: BLE001 - task 5.5: fail cleanly
        log.exception("performance rebuild failed for %s", period)
        return {"status": "failed", "model_id": mid, "error": str(exc)}

    store.conn.execute(
        "INSERT OR REPLACE INTO ledger_read_models (model_id, kind, period, "
        "as_of, method_version, source_facts_hash, payload, rebuilt_at) "
        "VALUES (?, 'performance', ?, ?, ?, ?, ?, ?)",
        (mid, period, now, method_version, facts,
         _dumps(summary), now))
    store.conn.commit()
    return {"status": "rebuilt", "model_id": mid,
            "source_facts_hash": facts, "payload": summary}


def rebuild_attribution(store, *, period: str,
                        method_version: str = METHOD_VERSION) -> dict:
    """Rebuild the per-symbol attribution read model (task 5.2).

    First version: realized P&L per symbol from chain-complete SYSTEM orders,
    plus the analytics formulas' win-rate/profit-factor block for the same
    population. Deterministic by construction; same facts ⇒ same payload.
    """
    from ..memory import get_store

    store = store or get_store()
    now = datetime.now(timezone.utc).isoformat()
    mid = model_id("attribution", period, method_version)
    facts = source_facts_hash(store)

    if read_model_hit(store, kind="attribution", period=period,
                      method_version=method_version, facts_hash=facts):
        return {"status": "skipped", "model_id": mid,
                "source_facts_hash": facts}

    try:
        trades = _system_trades_in_period(store, period)
        by_symbol: dict[str, dict] = {}
        for t in trades:
            slot = by_symbol.setdefault(
                t["symbol"], {"realized_pnl": 0.0, "trades": 0})
            slot["realized_pnl"] = round(
                slot["realized_pnl"] + float(t["realized_pnl"] or 0), 2)
            slot["trades"] += 1
        payload = {
            "by_symbol": dict(sorted(by_symbol.items())),
            "total_realized_pnl": round(
                sum(s["realized_pnl"] for s in by_symbol.values()), 2),
        }
    except Exception as exc:  # noqa: BLE001 - task 5.5: fail cleanly
        log.exception("attribution rebuild failed for %s", period)
        return {"status": "failed", "model_id": mid, "error": str(exc)}

    store.conn.execute(
        "INSERT OR REPLACE INTO ledger_read_models (model_id, kind, period, "
        "as_of, method_version, source_facts_hash, payload, rebuilt_at) "
        "VALUES (?, 'attribution', ?, ?, ?, ?, ?, ?)",
        (mid, period, now, method_version, facts,
         _dumps(payload), now))
    store.conn.commit()
    return {"status": "rebuilt", "model_id": mid,
            "source_facts_hash": facts, "payload": payload}


def _dumps(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, default=str)


def list_open_exceptions(store) -> list:
    """Task 5.6/6.x support: the open audit exceptions (gaps, unattributed,
    broken links) for CLI display and Internal State completeness."""
    return [dict(r) for r in store.conn.execute(
        "SELECT kind, subject_key, symbol, window_start, window_end, basis, "
        "first_seen_at, last_seen_at FROM ledger_exceptions "
        "WHERE status = 'open' ORDER BY first_seen_at")]
