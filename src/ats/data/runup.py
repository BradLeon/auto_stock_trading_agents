"""Pre-earnings price setup: run-up vs sector/benchmark + distance to 52w high.

Reuses market_data.fetch_snapshot (1y daily history). 20-day excess return is the
PEAD "抢跑" signal; distance-to-high gauges how priced-for-perfection the move is.
Returns a dict; fields are None if history is unavailable.
"""

from __future__ import annotations

from ..schemas.market import Ticker
from . import market_data


def _ret_20d(closes: list[float]) -> float | None:
    if len(closes) < 21:
        return None
    return (closes[-1] / closes[-21] - 1) * 100


def _closes(symbol: str) -> list[float]:
    snap = market_data.fetch_snapshot(Ticker(symbol=symbol))
    return [b.close for b in snap.history]


def compute(symbol: str, sector_etf: str = "SMH", benchmark: str = "QQQ") -> dict:
    out = {"pre_earnings_close": None, "run_up_vs_sector_pct": None,
           "run_up_vs_bench_pct": None, "dist_to_ath_pct": None}

    sym_closes = _closes(symbol)
    if not sym_closes:
        return out

    out["pre_earnings_close"] = round(sym_closes[-1], 2)
    out["dist_to_ath_pct"] = round((sym_closes[-1] / max(sym_closes) - 1) * 100, 2)

    sym_ret = _ret_20d(sym_closes)
    if sym_ret is None:
        return out

    sec_ret = _ret_20d(_closes(sector_etf)) if sector_etf else None
    bench_ret = _ret_20d(_closes(benchmark)) if benchmark else None
    if sec_ret is not None:
        out["run_up_vs_sector_pct"] = round(sym_ret - sec_ret, 2)
    if bench_ret is not None:
        out["run_up_vs_bench_pct"] = round(sym_ret - bench_ret, 2)
    return out
