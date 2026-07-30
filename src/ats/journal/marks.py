"""Deterministic marks on top of a built episode: holding period, R-multiple,
MAE/MFE, and exit-reason classification. No LLM — everything here is arithmetic
over already-stored data (episode facts, the linked plan, and daily price bars).

## Why this is a SEPARATE stage from the reducer (B1)

`build_episodes` only needs fills to reproduce the broker's own realized P&L
deterministically. Everything here needs one more thing — price history or a linked
plan — so it is computed as a second pass over already-persisted episodes, not inside
the reducer itself.

## The `basis_source` gate

A `pre_tracking`-tainted episode's `opened_at` is FABRICATED: the seed leg is dated
one second before the earliest real fill (see `episodes._seed_fill`), which is not
when the position was actually opened. Computing "holding period" or "max adverse
move since opened_at" against a fake timestamp would produce a plausible-looking but
meaningless number — usually a tiny window that badly understates the real history.
So every mark that depends on `opened_at` being real (`holding_days`, `mae_pct`,
`mfe_pct`, `r_multiple`, `exit_reason`) is skipped whenever
`basis_source != "observed_fills"`, and left `None` — an honest gap beats a
confident-looking fabrication.

## exit_reason is a classification, not a narrative

Every branch is a direct comparison against already-recorded facts (a stop/target
price, a setup tag, an approval-divergence flag, a day count) — never free-text
reasoning about "why". That is what keeps it deterministic and testable, unlike the
weekly `invalidation_triggered` check (B3), which reads free text and does need an
LLM, gated through `EpisodeCard.blind()`.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from . import prices
from ..schemas.journal import ExitReason, TradeEpisode

log = logging.getLogger("ats.journal")


def _mae_mfe(symbol: str, direction: str, avg_entry: float,
            start: date, end: date) -> tuple[float | None, float | None]:
    """Max adverse / favorable excursion over [start, end], as % of avg_entry."""
    bars = prices.window(symbol, start, end)
    if not bars or not avg_entry:
        return (None, None)
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    if direction == "long":
        mfe = (max(highs) / avg_entry - 1) * 100
        mae = (min(lows) / avg_entry - 1) * 100
    else:  # short: favorable = price falling, adverse = price rising
        mfe = (avg_entry - min(lows)) / avg_entry * 100
        mae = (avg_entry - max(highs)) / avg_entry * 100
    return (round(mae, 3), round(mfe, 3))


def _hit(direction: str, avg_exit: float, target: float | None,
        stop: float | None) -> ExitReason | None:
    """Direction-aware: for a long, target is above entry and stop is below (and the
    reverse for a short) — so "hit" means crossed in the adverse/favorable direction
    consistent with that side."""
    if direction == "long":
        if target and avg_exit >= target:
            return "target_hit"
        if stop and avg_exit <= stop:
            return "stop_hit"
    else:
        if target and avg_exit <= target:
            return "target_hit"
        if stop and avg_exit >= stop:
            return "stop_hit"
    return None


def classify_exit(episode: TradeEpisode, opening_entry, closing_entry,
                  holding_days: int | None) -> tuple[ExitReason | None, bool | None]:
    """Only called when `opening_entry` is real (decision_gradeable) — there is no
    plan to grade compliance against otherwise, so `drift` would be a false signal,
    not a finding.
    """
    if episode.status != "closed":
        return (None, None)
    if closing_entry is not None:
        if closing_entry.setup == "risk_repair":
            return ("risk_forced", False)
        if closing_entry.approval is not None and closing_entry.approval.diverged:
            return ("boss_override", False)
    if episode.avg_exit is not None:
        hit = _hit(episode.direction, episode.avg_exit,
                  opening_entry.target_price, opening_entry.stop_price)
        if hit:
            return (hit, True)
    if opening_entry.planned_horizon_days and holding_days is not None \
            and holding_days >= opening_entry.planned_horizon_days:
        return ("horizon_reached", True)
    return ("drift", False)


def _closing_entry(store, episode: TradeEpisode):
    """The JournalEntry (if any) behind the leg that closed — or most recently
    trimmed — this episode."""
    legs = store.legs_for_episode(episode.episode_id)
    for leg in reversed(legs):
        if leg.get("entry_id"):
            return store.get_journal_entry(leg["entry_id"])
    return None


def mark_episode(store, episode: TradeEpisode, *, market_price: float | None = None,
                 as_of: date | None = None) -> TradeEpisode:
    """Compute holding_days / r_multiple / mae / mfe / exit_reason for one episode.
    Returns an updated copy; does not persist (the caller decides when to save).
    """
    updates: dict = {}
    today = as_of or datetime.now(timezone.utc).date()
    opening_entry = (store.get_journal_entry(episode.primary_entry_id)
                     if episode.primary_entry_id else None)

    # Everything below needs a REAL opened_at — see module docstring.
    if episode.basis_source == "observed_fills" and episode.avg_entry:
        start = episode.opened_at.date()
        end = episode.closed_at.date() if episode.closed_at else today
        mae, mfe = _mae_mfe(episode.symbol, episode.direction, episode.avg_entry,
                            start, end)
        if mae is not None:
            updates["mae_pct"], updates["mfe_pct"] = mae, mfe

        if episode.status == "closed" and episode.closed_at:
            holding_days = (episode.closed_at - episode.opened_at).days
            updates["holding_days"] = holding_days
            if opening_entry and opening_entry.planned_risk_usd and \
                    episode.realized_pnl is not None:
                updates["r_multiple"] = round(
                    episode.realized_pnl / opening_entry.planned_risk_usd, 3)
                updates["risk_unit_source"] = opening_entry.risk_unit_source
            if opening_entry:
                closing_entry = _closing_entry(store, episode)
                reason, as_planned = classify_exit(episode, opening_entry,
                                                   closing_entry, holding_days)
                updates["exit_reason"], updates["exit_as_planned"] = reason, as_planned
        elif market_price and opening_entry and opening_entry.planned_risk_usd:
            # Open position: mark-to-market R, using the SAME risk unit as entry —
            # never re-derive it from current conditions.
            sign = 1 if episode.direction == "long" else -1
            mtm_pnl = sign * (market_price - episode.avg_entry) * _open_qty(store, episode)
            updates["r_multiple_mtm"] = round(mtm_pnl / opening_entry.planned_risk_usd, 3)

    return episode.model_copy(update=updates) if updates else episode


def _open_qty(store, episode: TradeEpisode) -> float:
    """Remaining open quantity, reconstructed from the episode's own legs."""
    from .episodes import _is_buy

    qty = 0.0
    for leg in store.legs_for_episode(episode.episode_id):
        shares = float(leg.get("shares") or 0.0)
        qty += shares if _is_buy(leg.get("side", "")) else -shares
    return abs(qty)


def mark_all(*, store=None, market_prices: dict[str, float] | None = None) -> dict:
    """Mark every episode. `market_prices`, if given, is {symbol: last price} for
    open-position MTM — this function never calls the broker itself."""
    from ..memory import get_store

    store = store or get_store()
    market_prices = market_prices or {}
    summary = {"marked": 0}
    for ep in store.list_episodes(limit=100_000):
        updated = mark_episode(store, ep, market_price=market_prices.get(ep.symbol))
        if updated is not ep:
            store.save_episode(updated)
            summary["marked"] += 1
    return summary


def run() -> int:
    portfolio = None
    try:
        from ..trader.execute import IBKRBroker

        portfolio = IBKRBroker().get_portfolio()
    except Exception as exc:  # noqa: BLE001 - MTM marks are best-effort
        log.warning("mark_all: no live prices (%s); closed-episode marks only", exc)

    prices_by_symbol = ({p.symbol: p.market_price for p in portfolio.positions}
                        if portfolio else {})
    s = mark_all(market_prices=prices_by_symbol)
    print(f"标记完成：{s['marked']} 个回合已更新")
    return 0
