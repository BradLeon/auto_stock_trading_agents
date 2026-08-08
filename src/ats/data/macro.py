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


# Series the indicator framework needs the FULL HISTORY of, not just the last
# print: every axis, z-score and Δ in docs/MACRO_ANALYST.md §4 is defined on the
# series, and `fred.get_series()` already returns all of it — the old code just
# threw it away in `_latest()`. Keeping it in memory costs nothing and avoids
# introducing a macro persistence layer (docs/DATA_SOURCES.md keeps raw macro
# out of the DB on purpose).
#
#   key -> (fred_code, 中文标签, unit, 观测频率)
# `unit` decides how a change reads: pct → basis points, price/index → percent.
_SERIES_SPEC: dict[str, tuple[str, str, str, str]] = {
    # ── 利率 / 信用（日频）─────────────────────────────
    "ust_10y":        ("DGS10",         "10y 名义",       "pct",   "daily"),
    "ust_2y":         ("DGS2",          "2y 国债",        "pct",   "daily"),
    "real_10y":       ("DFII10",        "10y 实际收益率", "pct",   "daily"),
    "breakeven_10y":  ("T10YIE",        "10y 通胀补偿",   "pct",   "daily"),
    "policy_rate":    ("DFF",           "有效联邦基金",   "pct",   "daily"),
    "hy_oas":         ("BAMLH0A0HYM2",  "高收益利差",     "pct",   "daily"),
    "ig_oas":         ("BAMLC0A0CM",    "投资级利差",     "pct",   "daily"),
    "vix":            ("VIXCLS",        "VIX",            "index", "daily"),
    # ── 就业（周频/月频）──────────────────────────────
    "initial_claims": ("ICSA",          "初请失业金",     "index", "weekly"),
    "continuing_claims": ("CCSA",       "续请失业金",     "index", "weekly"),
    "unemployment":   ("UNRATE",        "失业率",         "pct",   "monthly"),
    # PAYEMS is a level in thousands; its one-month raw difference is the
    # headline non-farm-payroll change.  Keeping the full series here makes the
    # print auditable and comparable with the previous review.
    "payrolls":       ("PAYEMS",         "非农就业（千人）", "level", "monthly"),
    # ── 通胀 / 增长（月频）────────────────────────────
    "headline_cpi":   ("CPIAUCSL",       "CPI 指数",        "index", "monthly"),
    "core_pce":       ("PCEPILFE",      "核心 PCE 指数",  "index", "monthly"),
    # CFNAI oscillates around zero and changes sign — a percent change on it is
    # division noise, so it reports absolute differences (`level`).
    "cfnai":          ("CFNAI",         "CFNAI 活动指数", "level", "monthly"),
    # ── 大宗（周频）───────────────────────────────────
    "gasoline":       ("GASREGW",       "汽油零售价",     "price", "weekly"),
    # ── 生产率（季频，仅展示，不参与象限判定，§3.3）────
    "productivity":   ("OPHNFB",        "非农生产率",     "index", "quarterly"),
    "unit_labor_cost": ("ULCNFB",       "单位劳动成本",   "index", "quarterly"),
}

# yfinance-sourced series that also need history (FRED has no clean daily feed
# for these two). Batched into one download — the rate-limit mitigation used
# throughout data/ (see sector_snapshot.py).
_YF_SERIES_SPEC: dict[str, tuple[str, str, str]] = {
    "oil_wti": ("CL=F",       "WTI 原油", "price"),
    "dxy":     ("DX-Y.NYB",   "美元指数", "index"),
    # Needed by the credit-equity divergence alert (§6.5): the warning is
    # "credit is widening while equities have not noticed yet", so it takes both.
    "spx":     ("^GSPC",      "标普500",  "index"),
}


def series_spec() -> dict[str, tuple[str, str, str, str]]:
    """key -> (source_code, label, unit, freq) for every history-backed series."""
    out = dict(_SERIES_SPEC)
    for key, (sym, label, unit) in _YF_SERIES_SPEC.items():
        out[key] = (sym, label, unit, "daily")
    return out


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


def fetch_series(years: int = 11) -> dict:
    """Full histories for the indicator framework: `{key: pandas.Series}`.

    Deliberately separate from `fetch()` rather than folded into it: `fetch()` is
    consumed by five downstream call sites and mocked in tests, and the weekly
    cadence makes the duplicated FRED round-trips irrelevant next to the
    regression risk of changing its contract.

    `years` covers the longest window any indicator needs (10y percentile + a
    year of slack). A dead feed yields a missing key, never an exception —
    `data/base.py` policy: a source degrades the cycle, it never aborts it.
    """
    out: dict = {}
    fred = _fred_client()
    if fred is None:
        log.warning("FRED (no api key): indicator histories unavailable")
    else:
        for key, (code, _label, _unit, _freq) in _SERIES_SPEC.items():
            s = safe_fetch(lambda c=code: fred.get_series(c).dropna(),
                           source=f"fred:{code}")
            if s is not None and len(s):
                out[key] = s
    _add_yf_series(out, years=years)
    return out


def _add_yf_series(out: dict, *, years: int) -> None:
    symbols = [sym for sym, _l, _u in _YF_SERIES_SPEC.values()]

    def _download():
        import yfinance as yf

        return yf.download(symbols, period=f"{years}y", interval="1d",
                           auto_adjust=True, progress=False, group_by="column")

    df = safe_fetch(_download, source=f"yf:{','.join(symbols)}")
    if df is None or getattr(df, "empty", True):
        return
    close = df["Close"] if "Close" in df else df
    for key, (sym, _label, _unit) in _YF_SERIES_SPEC.items():
        try:
            s = close[sym].dropna() if sym in close else None
        except Exception:  # noqa: BLE001 - shape varies with yfinance versions
            s = None
        if s is not None and len(s):
            out[key] = s


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
