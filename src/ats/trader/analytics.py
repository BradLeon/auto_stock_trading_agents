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


def trade_stats(fills: list[dict]) -> dict:
    """Win rate + profit factor from closing fills carrying realized P&L."""
    closed = [f["realized_pnl"] for f in fills
              if f.get("realized_pnl") is not None and f["realized_pnl"] != 0]
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


def summarize(history: list[PerformanceRecord], fills: list[dict],
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
        **trade_stats(fills),
        "benchmarks": {},
    }
    for name, closes in (benchmark or {}).items():
        b = benchmark_return_pct(closes)
        out["benchmarks"][name] = {
            "return_pct": b,
            "alpha_pct": round(ret - b, 2) if (ret is not None and b is not None) else None,
        }
    return out
