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
    schema_version: str = "internal-state-v1"
    owner: str = "ats.execution.state_api"
    section_as_of: dict[str, str | None] = Field(default_factory=dict)
    section_status: dict[str, str] = Field(default_factory=dict)
    portfolio: dict = Field(default_factory=dict)
    trades: list[dict] = Field(default_factory=list)
    fills: list[dict] = Field(default_factory=list)
    performance: dict = Field(default_factory=dict)
    attribution: dict | None = None
    # Cycles whose approval stands but sent nothing (rehearsal or live lock). Kept apart from
    # trades, fills and performance: they are neither executions nor results.
    approved_not_executed: list[dict] = Field(default_factory=list)
    # The risk role's latest portfolio posture (state, directive, breaches), so the Chief proposes
    # inside it instead of finding out at the risk gate. None when the risk role has not reviewed yet.
    risk_posture: dict | None = None
    completeness: Completeness = Field(default_factory=Completeness)


def live_account_state(broker) -> dict:
    """Today's book from the broker: positions (options with greeks), cash, exposure, margin.

    The ledger tables record what happened; they are not the account. A consumer that
    needs the book reads it here, at the moment it decides, with the broker's own as-of.
    """
    from ..data.runtime.broker import portfolio_snapshot

    return portfolio_snapshot(broker)


def _risk_posture(store) -> dict | None:
    review = store.latest_risk_review()
    if review is None:
        return None
    directive = review.directive
    derisk_only = (not directive.can_increase_risk) if directive else review.risk_state == "derisk"
    block = review.regime_block(1200)
    if derisk_only and not directive:
        block = "**⛔ de-risk 态：只允许减仓决策，禁止任何新买。**\n" + block
    return {"as_of": review.as_of.isoformat(timespec="seconds"), "risk_state": review.risk_state,
            "directive_state": directive.state if directive else None,
            "can_increase_risk": not derisk_only, "block": block}


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
    # Project only account fields. Legacy decision metadata (e.g. a null
    # invalidation_source) is not part of this read contract. Never rewrite
    # historical records or suppress their reconciliation gaps.
    from pydantic import AwareDatetime, FiniteFloat

    class AccountSnapshot(BaseModel):
        as_of: AwareDatetime
        net_liquidation: FiniteFloat
        daily_pnl: FiniteFloat
        cumulative_pnl: FiniteFloat
        account_id: str | None = None

    row = store.conn.execute(
        "SELECT payload FROM performance ORDER BY rowid DESC LIMIT 1").fetchone()
    perf_row = AccountSnapshot.model_validate_json(row["payload"]) if row else None
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
    class EpisodePerformance(BaseModel):
        status: Literal["open", "closed"]
        realized_pnl: FiniteFloat | None = None

    # Analytics consumes these two factual columns only, not the legacy
    # episode decision/invalidation metadata. Invalid factual values still fail.
    episodes = [EpisodePerformance.model_validate(dict(r)) for r in store.conn.execute(
        "SELECT status, realized_pnl FROM trade_episodes ORDER BY opened_at DESC LIMIT 10000")]
    performance = analytics.summarize(history, episodes)
    # What the numbers above are computed over, so a reader can judge them: the NAV
    # snapshots are sparse and not cash-flow adjusted; round trips are the ledger's.
    span = store.conn.execute(
        "SELECT COUNT(*) n, MIN(as_of) first, MAX(as_of) last FROM performance").fetchone()
    # Realized P&L is in each contract's own currency; no FX history is kept, so
    # non-USD round trips are totalled per currency and left out of the USD figures.
    currency = {r["k"]: r["currency"] for r in store.conn.execute(
        "SELECT COALESCE(NULLIF(instrument_key, ''), symbol) k, MAX(currency) currency "
        "FROM fills GROUP BY k")}
    closed = [(r["realized_pnl"], currency.get(r["k"]) or "USD") for r in store.conn.execute(
        "SELECT realized_pnl, COALESCE(NULLIF(instrument_key, ''), symbol) k "
        "FROM trade_episodes WHERE status = 'closed'")]
    usd = [p or 0 for p, ccy in closed if ccy == "USD"]
    gross_loss = -sum(p for p in usd if p < 0)
    other: dict[str, float] = {}
    for p, ccy in closed:
        if ccy != "USD":
            other[ccy] = round(other.get(ccy, 0.0) + (p or 0), 2)
    performance.update(
        nav_points=span["n"], nav_first_as_of=span["first"], nav_last_as_of=span["last"],
        closed_episodes=len(closed),
        closed_wins=sum(1 for p, _ in closed if p and p > 0),
        closed_losses=sum(1 for p, _ in closed if p and p < 0),
        closed_flat=sum(1 for p, _ in closed if not p),
        realized_total=round(sum(usd), 2),
        profit_factor=round(sum(p for p in usd if p > 0) / gross_loss, 2) if gross_loss else None,
        realized_other_currencies=other)

    attribution = None
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    row = store.conn.execute(
        "SELECT payload FROM ledger_read_models WHERE kind='attribution' "
        "AND period = ? ORDER BY rebuilt_at DESC LIMIT 1", (period,)).fetchone()
    if row is not None:
        attribution = json.loads(row["payload"])

    held_back = _rows_to_dicts(store.conn.execute(
        "SELECT cycle_id, updated_at, final_outcome FROM decision_cycles "
        "WHERE status = 'approved_not_executed' ORDER BY rowid DESC LIMIT 20").fetchall())
    risk_posture = _risk_posture(store)
    queried_at = datetime.now(timezone.utc)
    portfolio_stamp = (getattr(perf_row, "as_of", None) if perf_row else None)
    portfolio_stamp = (portfolio_stamp.isoformat() if isinstance(portfolio_stamp, datetime)
                       else str(portfolio_stamp) if portfolio_stamp else None)
    performance_stamp = store.conn.execute("SELECT MAX(as_of) stamp FROM performance").fetchone()["stamp"]
    return InternalState(as_of=queried_at, portfolio=portfolio,
                         trades=trades, fills=fills, performance=performance,
                         attribution=attribution, approved_not_executed=held_back,
                         risk_posture=risk_posture, completeness=comp,
                         section_as_of={"portfolio": portfolio_stamp,
                                        "performance": performance_stamp,
                                        "trades": comp.last_reconcile_at,
                                        "fills": comp.last_reconcile_at},
                         section_status={"portfolio": "complete" if portfolio_stamp else "unavailable",
                                         "performance": "complete" if performance_stamp else "unavailable",
                                         "trades": comp.status if comp.last_reconcile_at else "unreconciled",
                                         "fills": comp.status if comp.last_reconcile_at else "unreconciled",
                                         "attribution": "complete" if attribution is not None else "no_coverage"})


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
