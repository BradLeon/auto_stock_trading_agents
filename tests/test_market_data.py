"""Phase 3: market data adapter — indicators + graceful degradation (no network)."""

import pandas as pd

from ats.data import indicators, market_data
from ats.schemas.market import Ticker


def _synthetic_df(n=260):
    # Monotone-ish series so indicators are well-defined.
    close = pd.Series([100 + i * 0.5 for i in range(n)])
    return pd.DataFrame({
        "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": [1_000_000] * n,
    })


def test_indicators_present_with_enough_history():
    out = indicators.compute_indicators(_synthetic_df())
    for key in ("sma_20", "sma_50", "sma_200", "rsi_14", "macd", "macd_hist", "atr_14"):
        assert key in out
    assert 0 <= out["rsi_14"] <= 100


def test_indicators_skip_when_history_short():
    out = indicators.compute_indicators(_synthetic_df(10))
    assert "sma_200" not in out  # not enough bars
    assert "sma_20" not in out


def test_snapshot_degrades_on_fetch_failure(monkeypatch):
    # Force the downloader to raise -> snapshot returns with no history, no crash.
    monkeypatch.setattr(market_data, "_download",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network down")))
    snap = market_data.fetch_snapshot(Ticker(symbol="NVDA"))
    assert snap.ticker.symbol == "NVDA"
    assert snap.history == []
    assert snap.last_price is None
