"""Macro data: FRED (rates/CPI/jobs) + yfinance (VIX, SPX, NDX) + CNN fear&greed.

Every feed is best-effort: missing FRED key or a dead endpoint records a note and
leaves the field None rather than failing the cycle.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..config import get_config
from ..schemas.macro import MacroData
from .base import safe_fetch

name = "macro"

_FRED_SERIES = {
    "ust_10y": "DGS10",
    "ust_2y": "DGS2",
    "fed_funds": "FEDFUNDS",
    "unemployment": "UNRATE",
}


def _fred_client():
    key = get_config().secrets.fred_api_key
    if not key:
        return None
    try:
        from fredapi import Fred
    except ImportError:
        log.warning("fredapi not installed (pip install fredapi); skipping FRED feeds")
        return None
    return Fred(api_key=key)


def _latest(series) -> float | None:
    s = series.dropna()
    return float(s.iloc[-1]) if len(s) else None


def fetch() -> MacroData:
    data = MacroData(as_of=datetime.now(timezone.utc))
    fred = _fred_client()

    if fred is None:
        data.notes.append("FRED (no api key): rates/CPI/jobs unavailable")
    else:
        for field, code in _FRED_SERIES.items():
            val = safe_fetch(lambda c=code: _latest(fred.get_series(c)), source=f"fred:{code}")
            setattr(data, field, val)
        # CPI YoY from the headline index.
        cpi = safe_fetch(lambda: fred.get_series("CPIAUCSL").dropna(), source="fred:CPIAUCSL")
        if cpi is not None and len(cpi) > 12:
            data.cpi_yoy = round(float((cpi.iloc[-1] / cpi.iloc[-13] - 1) * 100), 2)
        # NFP latest month-over-month change (thousands).
        nfp = safe_fetch(lambda: fred.get_series("PAYEMS").dropna(), source="fred:PAYEMS")
        if nfp is not None and len(nfp) > 1:
            data.nfp_change_k = round(float(nfp.iloc[-1] - nfp.iloc[-2]), 1)

    _add_market_regime(data)
    _add_fear_greed(data)
    return data


def _add_market_regime(data: MacroData) -> None:
    def quote(symbol):
        import yfinance as yf

        df = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=True)
        if df is None or df.empty:
            raise ValueError(f"no data for {symbol}")
        last = float(df["Close"].iloc[-1])
        chg = float((df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100) if len(df) > 1 else None
        return last, chg

    vix = safe_fetch(lambda: quote("^VIX")[0], source="yf:^VIX")
    if vix is not None:
        data.vix = round(vix, 2)
    for field, symbol in (("spx", "^GSPC"), ("ndx", "^IXIC")):
        res = safe_fetch(lambda s=symbol: quote(s), source=f"yf:{symbol}")
        if res is not None:
            last, chg = res
            setattr(data, field, round(last, 2))
            if chg is not None:
                setattr(data, f"{field}_chg_pct", round(chg, 2))


def _add_fear_greed(data: MacroData) -> None:
    def pull():
        import httpx

        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        return int(round(r.json()["fear_and_greed"]["score"]))

    fg = safe_fetch(pull, source="cnn:fear_greed", attempts=2)
    if fg is not None:
        data.fear_greed = fg
    else:
        data.notes.append("fear&greed unavailable")
