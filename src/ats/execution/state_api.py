"""Internal State API (§11.1 end, Phase C group 6).

The ONE read path for trade history, performance and attribution for the next
Chief / Risk / context round. Consumers MUST come through here and get an
as-of stamp plus a completeness marker; raw-table reads are retired (task 6.7).

The API is part of the Clerk's deterministic layer: internally it aggregates
the ledger, but what it publishes carries the completeness verdict —
unreconciled windows, unattributed fills and broken links are counted, and a
degraded state is NEVER presented as complete (task 6.3).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

log = logging.getLogger("ats.state_api")

# Exception kinds that make the ledger incomplete for downstream consumers.
_GAP_KINDS = ("reconciliation_gap", "unattributed_fill", "broken_link")


class Completeness(BaseModel):
    """Task 6.2: the gap counts that qualify every number in the state."""

    status: Literal["complete", "degraded"] = "complete"
    unreconciled_windows: int = 0
    unattributed_fills: int = 0
    broken_links: int = 0
    last_reconcile_at: str | None = None


class InternalState(BaseModel):
    """The §11.1 internal state published to the next round."""

    as_of: datetime
    portfolio: dict = Field(default_factory=dict)
    trades: list[dict] = Field(default_factory=list)
    fills: list[dict] = Field(default_factory=list)
    performance: dict = Field(default_factory=dict)
    attribution: dict | None = None
    completeness: Completeness = Field(default_factory=Completeness)


def completeness(store) -> Completeness:
    """Count the open gaps by kind; ANY open gap ⇒ degraded (task 6.3)."""
    counts = {k: 0 for k in _GAP_KINDS}
    for r in store.conn.execute(
        "SELECT kind, COUNT(*) n FROM ledger_exceptions "
        "WHERE status = 'open' GROUP BY kind"):
        if r["kind"] in counts:
            counts[r["kind"]] = r["n"]
    last = store.conn.execute(
        "SELECT value FROM journal_meta WHERE key='last_reconcile_at'").fetchone()
    return Completeness(
        status="degraded" if any(counts.values()) else "complete",
        unreconciled_windows=counts["reconciliation_gap"],
        unattributed_fills=counts["unattributed_fill"],
        broken_links=counts["broken_link"],
        last_reconcile_at=last["value"] if last else None)


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def get_internal_state(store, *, symbol: str = "", trade_limit: int = 8,
                       fill_limit: int = 5) -> InternalState:
    """Assemble the full state. `symbol` narrows the trade history (context
    reports); an empty symbol returns the latest across the book."""
    from ..trader import analytics

    comp = completeness(store)
    perf_row = store.last_performance()
    portfolio = {}
    if perf_row is not None:
        portfolio = {"net_liquidation": perf_row.net_liquidation,
                     "daily_pnl": perf_row.daily_pnl,
                     "cumulative_pnl": perf_row.cumulative_pnl,
                     "account_id": getattr(perf_row, "account_id", "") or ""}

    if symbol:
        trades = _rows_to_dicts(store.recent_trades(symbol, limit=trade_limit))
    else:
        trades = _rows_to_dicts(store.conn.execute(
            "SELECT * FROM trades ORDER BY submitted_at DESC LIMIT ?",
            (trade_limit,)).fetchall())
    fills = _rows_to_dicts(store.recent_fills(limit=fill_limit))

    from types import SimpleNamespace

    history = [SimpleNamespace(net_liquidation=float(r["net_liquidation"] or 0),
                               cumulative_pnl=r["cumulative_pnl"])
               for r in store.conn.execute(
                   "SELECT net_liquidation, cumulative_pnl FROM performance "
                   "ORDER BY as_of").fetchall()]
    episodes = store.list_episodes(limit=10_000)
    performance = analytics.summarize(history, episodes)

    attribution = None
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    row = store.conn.execute(
        "SELECT payload FROM ledger_read_models WHERE kind='attribution' "
        "AND period = ? ORDER BY rebuilt_at DESC LIMIT 1", (period,)).fetchone()
    if row is not None:
        attribution = json.loads(row["payload"])

    return InternalState(as_of=datetime.now(timezone.utc), portfolio=portfolio,
                         trades=trades, fills=fills, performance=performance,
                         attribution=attribution, completeness=comp)


# --------------------------------------------------------------------------- #
# Consumer-facing reads — same shapes as the retired direct reads, so the
# migration (tasks 6.4–6.6) is a drop-in and the double-read comparison works.
# --------------------------------------------------------------------------- #

def recent_trades(store, symbol: str = "", limit: int = 8) -> list[dict]:
    """Chief/context trade history, via the Internal State layer (task 6.4)."""
    if symbol:
        return _rows_to_dicts(store.recent_trades(symbol, limit=limit))
    return _rows_to_dicts(store.conn.execute(
        "SELECT * FROM trades ORDER BY submitted_at DESC LIMIT ?",
        (limit,)).fetchall())


def recent_fills(store, limit: int = 5) -> list[dict]:
    """Chief context fills, via the Internal State layer (task 6.4)."""
    return _rows_to_dicts(store.recent_fills(limit=limit))


def performance_history(store, *, limit: int = 250) -> list:
    """Risk performance history, via the Internal State layer (task 6.5)."""
    return store.performance_history(limit=limit)
