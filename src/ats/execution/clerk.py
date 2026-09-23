"""Clerk — the deterministic orchestration service (§11, Phase C group 4).

Clerk CHAINS the existing deterministic computations (reconcile, marks,
episodes, predictions, performance) into one idempotent pipeline keyed by
「运行种类 + 对账窗口 + 数据截止时点」. It never rewrites domain facts the
services already wrote — it coordinates, reconciles, compensates and
publishes. It is NOT an agent: no LLM is consulted anywhere in here.

Each run leaves a trail in `clerk_runs`; re-running the same window returns
the first run's outcome instead of producing new facts (design D13: during
the dual-track period the legacy cron jobs keep running, and the window
idempotency key is what stops double booking).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .ids import run_id

log = logging.getLogger("ats.clerk")

# The deterministic pipeline, in dependency order. `order attempt` sequences
# are covered inside reconcile's replay (task 3.9).
DEFAULT_STEPS = ("reconcile", "marks", "episodes", "predictions", "performance")

# Domain-fact columns the Clerk must never alter (task 4.3): the trader wrote
# them at event time; Clerk may only add reconciliation/derivation fields.
_PROTECTED_TRADE_COLUMNS = (
    "cycle_id", "symbol", "action", "qty", "order_type", "submitted_at",
    "client_order_id", "revision_no", "decision_hash", "approval_id", "rationale",
)


def _portfolio_positions(broker):
    """Best-effort live positions for marks/episodes; None when TWS is down."""
    if broker is None:
        try:
            from ..trader.execute import IBKRBroker as _B

            broker = _B()
        except Exception as exc:  # noqa: BLE001 - degradation, not failure
            log.warning("clerk: no broker (%s); marks/episodes degrade", exc)
            return None
    try:
        return broker.get_portfolio()
    except Exception as exc:  # noqa: BLE001
        log.warning("clerk: portfolio unavailable (%s)", exc)
        return None


def _step_reconcile(store, *, broker, dry_run):
    from ..trader import reconcile

    return reconcile.reconcile(broker=broker, store=store, dry_run=dry_run)


def _step_marks(store, *, broker, dry_run):
    from ..journal import marks

    portfolio = _portfolio_positions(broker)
    prices = ({p.symbol: p.market_price for p in portfolio.positions}
              if portfolio else {})
    return marks.mark_all(market_prices=prices)


def _step_episodes(store, *, broker, dry_run):
    from ..journal import episodes

    return episodes.rebuild_all(portfolio=_portfolio_positions(broker))


def _step_predictions(store, *, broker, dry_run):
    from ..journal import predictions

    return predictions.score_open_predictions(store=store)


def _step_performance(store, *, broker, dry_run):
    from ..trader import performance

    rec = performance.record_snapshot(cycle_id="")
    return {"recorded": rec is not None}


_STEPS = {
    "reconcile": _step_reconcile,
    "marks": _step_marks,
    "episodes": _step_episodes,
    "predictions": _step_predictions,
    "performance": _step_performance,
}


def _register_missed_windows(store, *, broker_available: bool,
                             window_end: str) -> list[str]:
    """Task 3.7 in action: windows with local fills that NO reconcile has ever
    covered, where the broker can no longer return the data, become explicit
    `reconciliation_gap` exceptions instead of silently looking reconciled."""
    if broker_available:
        return []
    from .ledger import register_reconciliation_gap

    last = store.conn.execute(
        "SELECT value FROM journal_meta WHERE key='last_reconcile_at'").fetchone()
    cutoff = (last["value"][:10] if last else "")
    rows = store.conn.execute(
        "SELECT DISTINCT DATE(COALESCE(time, captured_at)) d FROM fills "
        "WHERE COALESCE(time, captured_at, '') != '' "
        "AND (? = '' OR DATE(COALESCE(time, captured_at)) > ?) "
        "AND DATE(COALESCE(time, captured_at)) <= ?",
        (cutoff, cutoff, window_end)).fetchall()
    return [register_reconciliation_gap(
                store, window_start=r["d"], window_end=r["d"],
                reason="session never reconciled and broker no longer returns it",
                detail="registered by clerk_run after a broker-unavailable run")
            for r in rows]


def clerk_run(*, store=None, broker=None, steps: tuple[str, ...] = DEFAULT_STEPS,
              window_start: str | None = None, window_end: str | None = None,
              as_of: str | None = None, dry_run: bool = False) -> dict:
    """Run the deterministic ledger pipeline for one window. Idempotent.

    The run id is derived from kind + window + as-of — NEVER from the request
    moment — so the scheduler, a manual rerun and a crash recovery all derive
    the SAME id for the same window and cannot double-book.
    """
    from ..memory import get_store

    store = store or get_store()
    now = datetime.now(timezone.utc)
    as_of_day = as_of or now.date().isoformat()
    window_start = window_start or as_of_day
    window_end = window_end or as_of_day
    rid = run_id("full", window_start, window_end, as_of_day)

    prior = store.conn.execute(
        "SELECT status FROM clerk_runs WHERE run_id = ?", (rid,)).fetchone()
    if prior is not None and prior["status"] == "completed":
        return {"run_id": rid, "reused": True, "status": "completed", "steps": {}}

    store.conn.execute(
        "INSERT OR REPLACE INTO clerk_runs (run_id, kind, window_start, window_end, "
        "as_of, status, started_at) VALUES (?, 'full', ?, ?, ?, 'running', ?)",
        (rid, window_start, window_end, as_of_day, now.isoformat()))
    store.conn.commit()

    results: dict = {}
    errors: list[str] = []
    for step in steps:
        impl = _STEPS.get(step)
        if impl is None:
            errors.append(f"{step}: unknown step")
            continue
        try:
            results[step] = impl(store, broker=broker, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - one failed step must not
            # poison the others; the run trail records exactly what happened.
            log.exception("clerk step %s failed", step)
            errors.append(f"{step}: {exc}")

    # Broker availability, judged from the reconcile step's own summary (it
    # reports "broker unavailable"/"IBKR" failures there instead of raising).
    # This decides whether uncovered windows can still be reconciled later
    # (3.7) or must be registered as permanent gaps now.
    rec_summary = results.get("reconcile") or {}
    rec_errors = " ".join(rec_summary.get("errors", [])) if isinstance(rec_summary, dict) else ""
    all_errors = errors + [rec_errors]
    broker_available = not any(
        ("IBKR" in e) or ("broker unavailable" in e) for e in all_errors)
    gaps = _register_missed_windows(store, broker_available=broker_available,
                                    window_end=window_end)
    status = "completed" if not errors else "failed"
    store.conn.execute(
        "UPDATE clerk_runs SET status = ?, counts_json = ?, error = ?, "
        "finished_at = ? WHERE run_id = ?",
        (status, json.dumps(results, ensure_ascii=False, default=str),
         "; ".join(errors) or None, datetime.now(timezone.utc).isoformat(), rid))
    store.conn.commit()
    return {"run_id": rid, "reused": False, "status": status, "steps": results,
            "errors": errors, "gaps_registered": len(gaps)}


def protected_trade_snapshot(store) -> list[tuple]:
    """The (task 4.3) domain-fact projection of `trades` — byte-for-byte the
    values the trader wrote. Clerk runs must not change any of it."""
    cols = ", ".join(_PROTECTED_TRADE_COLUMNS)
    return [tuple(r) for r in store.conn.execute(f"SELECT {cols} FROM trades")]


def source_facts_hash(store) -> str:
    """Task 4.5: a fingerprint of the immutable facts a derived read model is
    built from (orders' execution outcome + marks' inputs). Same facts → same
    hash → a rebuild can be skipped instead of silently recomputed."""
    import hashlib

    from ..agent.task_projection import canonical_json

    rows = store.conn.execute(
        "SELECT client_order_id, status, avg_fill_price, filled_qty, "
        "realized_pnl, terminal_basis FROM trades "
        "ORDER BY client_order_id").fetchall()
    body = canonical_json([tuple(r) for r in rows])
    return hashlib.sha256(body.encode()).hexdigest()[:32]


def read_model_hit(store, *, kind: str, period: str, method_version: str,
                   facts_hash: str) -> bool:
    """True when a read model for (kind, period, version) already exists AND
    was built from exactly these facts — the skip-recompute condition."""
    row = store.conn.execute(
        "SELECT source_facts_hash FROM ledger_read_models "
        "WHERE kind = ? AND period = ? AND method_version = ?",
        (kind, period, method_version)).fetchone()
    return row is not None and row["source_facts_hash"] == facts_hash
