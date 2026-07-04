"""Macro data: FRED (rates/CPI/jobs) + yfinance (VIX, SPX, NDX) + CNN fear&greed.

Every feed is best-effort: missing FRED key or a dead endpoint records a note and
leaves the field None rather than failing the cycle.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config import get_config
from ..schemas.macro import MacroData
from .base import safe_fetch

log = logging.getLogger("ats.data.macro")

name = "macro"

_FRED_SERIES = {
    "ust_10y": "DGS10",
    "ust_2y": "DGS2",
    "fed_funds": "FEDFUNDS",
    "real_10y": "DFII10",          # 10y TIPS real yield
    "unemployment": "UNRATE",
    "cfnai": "CFNAI",              # Chicago Fed National Activity Index (broad growth)
    "hy_oas": "BAMLH0A0HYM2",      # ICE BofA US High Yield OAS
    "ig_oas": "BAMLC0A0CM",        # ICE BofA US IG Corporate OAS
    "breakeven_10y": "T10YIE",     # 10y breakeven inflation
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
        # Core PCE YoY (Fed's preferred inflation gauge).
        pce = safe_fetch(lambda: fred.get_series("PCEPILFE").dropna(), source="fred:PCEPILFE")
        if pce is not None and len(pce) > 12:
            data.pce_yoy = round(float((pce.iloc[-1] / pce.iloc[-13] - 1) * 100), 2)
        # NFP latest month-over-month change (thousands).
        nfp = safe_fetch(lambda: fred.get_series("PAYEMS").dropna(), source="fred:PAYEMS")
        if nfp is not None and len(nfp) > 1:
            data.nfp_change_k = round(float(nfp.iloc[-1] - nfp.iloc[-2]), 1)
        # Initial jobless claims (level -> thousands).
        icsa = safe_fetch(lambda: _latest(fred.get_series("ICSA")), source="fred:ICSA")
        if icsa is not None:
            data.jobless_claims_k = round(icsa / 1000, 1)

    _add_market_regime(data)
    _add_commodities(data)
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


def _add_commodities(data: MacroData) -> None:
    def last_close(symbol):
        import yfinance as yf

        df = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=True)
        if df is None or df.empty:
            raise ValueError(f"no data for {symbol}")
        return float(df["Close"].iloc[-1])

    for field, symbol in (("oil_wti", "CL=F"), ("gold", "GC=F"), ("dxy", "DX-Y.NYB")):
        val = safe_fetch(lambda s=symbol: last_close(s), source=f"yf:{symbol}")
        if val is not None:
            setattr(data, field, round(val, 2))


# CNN's fear&greed API (the data behind edition.cnn.com/markets/fear-and-greed).
# It rejects bare/short User-Agents (HTTP 418); a full browser UA + Referer passes.
_CNN_FG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _add_fear_greed(data: MacroData) -> None:
    def pull():
        import httpx

        r = httpx.get(_CNN_FG_URL, timeout=15, follow_redirects=True, headers={
            "User-Agent": _BROWSER_UA, "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9", "Referer": "https://edition.cnn.com/markets/fear-and-greed"})
        r.raise_for_status()
        return int(round(float(r.json()["fear_and_greed"]["score"])))

    fg = safe_fetch(pull, source="cnn:fear_greed", attempts=3)
    if fg is not None:
        data.fear_greed = fg
    else:
        data.notes.append("fear&greed unavailable")
