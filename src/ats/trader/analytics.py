"""Portfolio performance analytics — pure functions over the stored history + fills.

Returns / max drawdown from the NetLiq series; win rate / profit factor from the
per-trade realized P&L in fills; benchmark comparison vs an index return.
"""

from __future__ import annotations

from ..schemas.memory import PerformanceRecord


def total_return_pct(history: list[PerformanceRecord]) -> float | None:
    """Cumulative return over the window (last vs first NetLiq)."""
    navs = [h.net_liquidation for h in history if h.net_liquidation > 0]
    if len(navs) < 2 or navs[0] == 0:
        return None
    return round((navs[-1] / navs[0] - 1) * 100, 2)


def max_drawdown_pct(history: list[PerformanceRecord]) -> float | None:
    """Worst peak-to-trough decline of NetLiq over the window (negative %)."""
    navs = [h.net_liquidation for h in history if h.net_liquidation > 0]
    if len(navs) < 2:
        return None
    peak, worst = navs[0], 0.0
    for nav in navs:
        peak = max(peak, nav)
        worst = min(worst, nav / peak - 1)
    return round(worst * 100, 2)


def episode_stats(episodes: list) -> dict:
    """Win rate + profit factor from CLOSED trade episodes — one number per round
    trip, not per fill.

    Replaces the old fill-level `trade_stats`, which counted every partial exit of a
    scaled-out position as a separate "trade" (a position trimmed in 3 pieces read as
    3 trades, all with the same sign — a coin that only ever lands on one side looks
    like 3 flips). An episode's `realized_pnl` is already the fill-level sum, so this
    reports the SAME total P&L, just grouped by round trip instead of by execution.
    """
    closed = [e.realized_pnl for e in episodes
             if e.status == "closed" and e.realized_pnl is not None and e.realized_pnl != 0]
    if not closed:
        return {"win_rate": None, "profit_factor": None, "closed_trades": 0}
    wins = [p for p in closed if p > 0]
    losses = [p for p in closed if p < 0]
    gross_loss = abs(sum(losses))
    return {
        "win_rate": round(len(wins) / len(closed), 3),
        "profit_factor": round(sum(wins) / gross_loss, 2) if gross_loss else None,
        "closed_trades": len(closed),
    }


def benchmark_return_pct(closes: list[float]) -> float | None:
    """Index return over the same window from a close series."""
    if len(closes) < 2 or closes[0] == 0:
        return None
    return round((closes[-1] / closes[0] - 1) * 100, 2)


def summarize(history: list[PerformanceRecord], episodes: list,
              benchmark: dict[str, list[float]] | None = None) -> dict:
    """Full analytics dict. benchmark = {name: close_series} over the same window."""
    ret = total_return_pct(history)
    out = {
        "window_days": len(history),
        "start_nav": history[0].net_liquidation if history else None,
        "end_nav": history[-1].net_liquidation if history else None,
        "total_return_pct": ret,
        "cumulative_pnl": history[-1].cumulative_pnl if history else None,
        "max_drawdown_pct": max_drawdown_pct(history),
        **episode_stats(episodes),
        "benchmarks": {},
    }
    for name, closes in (benchmark or {}).items():
        b = benchmark_return_pct(closes)
        out["benchmarks"][name] = {
            "return_pct": b,
            "alpha_pct": round(ret - b, 2) if (ret is not None and b is not None) else None,
        }
    return out
