"""US macro indicator histories (FRED + market closes + CNN fear & greed) as governed observations.

Until 2026-10-16 the Macro review pulled these from the providers while it ran (`data/macro.py`
`fetch()` / `fetch_series()`), which broke ARCHITECTURE §3/§12.1 (indicators are persisted structured
data; an Agent must not call a Provider) and left nothing to say which vintage a review used.
The refresh controller now runs this adapter on a schedule, every point lands as an immutable
observation under entity `US_MACRO`, and the review reads `products.macro_series`.

The series set is exactly what the review already used: the history-backed indicator framework
(`data.macro.series_spec()`), plus the few spot values `fetch()` reported that are not in that set
(fed_funds, ndx, gold, fear_greed). Provider code lives in `data/macro.py`; this file only maps
provider output to records, so a change of provider is one place.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from ..structured import (
    AdapterArtifact,
    AdapterBatch,
    FetchRequest,
    IngestionStatus,
    NativeRecord,
)

log = logging.getLogger("ats.data.sources.macro_series")

ENTITY = "US_MACRO"
SOURCE_ID = "macro_series_feeds"
DATASET_ID = "macro_series"

# Series the review reads beyond `data.macro.series_spec()`: key -> (provider, code, label, unit, freq).
EXTRA_SERIES = {
    "fed_funds": ("fred", "FEDFUNDS", "联邦基金利率（月均）", "pct", "monthly"),
    "ndx": ("yf", "^IXIC", "纳斯达克综合", "index", "daily"),
    "gold": ("yf", "GC=F", "黄金", "price", "daily"),
    "fear_greed": ("cnn", "fear_and_greed", "CNN 恐惧贪婪指数", "index", "daily"),
}
# Unit strings per spec unit; claims and payrolls are head-counts, not indices.
_UNIT = {"pct": "percent", "index": "index", "level": "index", "price": "usd"}
_COUNT_KEYS = {"initial_claims": "persons", "continuing_claims": "persons", "payrolls": "thousand persons"}
_PERIOD_BASIS = {"daily": "day", "weekly": "week", "monthly": "month", "quarterly": "quarter"}


def metric_id(key: str) -> str:
    return f"macro.{key}.value"


def all_series() -> dict[str, tuple[str, str, str, str, str]]:
    """key -> (provider, code, label, unit, freq) for every stored series."""
    from .. import macro

    out: dict = {}
    for key, (code, label, unit, freq) in macro._SERIES_SPEC.items():
        out[key] = ("fred", code, label, unit, freq)
    for key, (code, label, unit) in macro._YF_SERIES_SPEC.items():
        out[key] = ("yf", code, label, unit, "daily")
    out.update(EXTRA_SERIES)
    return out


def _unit(key: str, unit: str) -> tuple[str, str]:
    if key in _COUNT_KEYS:
        return _COUNT_KEYS[key], ""
    return _UNIT.get(unit, unit), ("USD" if unit == "price" else "")


def _records(key: str, spec: tuple, series, since: date | None) -> list[NativeRecord]:
    _provider, code, _label, unit, freq = spec
    unit_text, currency = _unit(key, unit)
    out = []
    for moment, value in series.dropna().items():
        day = moment.date() if hasattr(moment, "date") else moment
        if since and day < since:
            continue
        period = day.isoformat()
        out.append(NativeRecord(
            entity_id=ENTITY, provider_field=metric_id(key), period=period, value=float(value),
            unit=unit_text, currency=currency, period_basis=_PERIOD_BASIS.get(freq, "day"),
            period_start=period, period_end=period, raw={"series": key, "code": code}))
    return out


def _fetch_fear_greed():
    import pandas as pd

    from .. import macro

    import httpx

    r = httpx.get(macro._CNN_FG_URL, timeout=20, follow_redirects=True, headers={
        "User-Agent": macro._BROWSER_UA, "Accept": "application/json, text/plain, */*",
        "Referer": "https://edition.cnn.com/markets/fear-and-greed"})
    r.raise_for_status()
    body = r.json()
    points = (body.get("fear_and_greed_historical") or {}).get("data") or []
    rows = {pd.Timestamp(int(p["x"]), unit="ms").normalize(): float(p["y"]) for p in points if "x" in p and "y" in p}
    current = body.get("fear_and_greed") or {}
    if "score" in current:
        rows[pd.Timestamp(datetime.now(timezone.utc).date())] = float(current["score"])
    return pd.Series(rows).sort_index()


class MacroSeriesAdapter:
    source_id = SOURCE_ID
    dataset_id = DATASET_ID

    def __init__(self, *, fred=None, yf_download=None, fear_greed=None, clock=None):
        self._fred, self._yf, self._fg = fred, yf_download, fear_greed
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _fred_client(self):
        if self._fred is not None:
            return self._fred
        from .. import macro

        return macro._fred_client()

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        from ..base import safe_fetch

        fetched_at = self.clock().astimezone(timezone.utc)
        scope = request.query_scope or {}
        since = date.fromisoformat(str(scope["since"])) if scope.get("since") else None
        if since is None and scope.get("lookback_days"):
            # The scheduled run asks for a rolling window; the first load passes an explicit `since`.
            since = fetched_at.date() - timedelta(days=int(scope["lookback_days"]))
        wanted = set(scope.get("series") or [])
        specs = {k: v for k, v in all_series().items() if not wanted or k in wanted}

        records: list[NativeRecord] = []
        gaps: dict[str, str] = {}
        raw: dict[str, list] = {}

        fred = self._fred_client()
        for key, spec in specs.items():
            if spec[0] != "fred":
                continue
            if fred is None:
                gaps[key] = "fred_key_missing"
                continue
            series = safe_fetch(lambda c=spec[1]: fred.get_series(
                c, observation_start=since.isoformat() if since else None).dropna(), source=f"fred:{spec[1]}")
            if series is None or not len(series):
                gaps[key] = "provider_empty"
                continue
            rows = _records(key, spec, series, since)
            records.extend(rows)
            raw[key] = [[r.period, r.value] for r in rows]

        yf_specs = {k: v for k, v in specs.items() if v[0] == "yf"}
        if yf_specs:
            codes = [spec[1] for spec in yf_specs.values()]

            def download():
                if self._yf is not None:
                    return self._yf(codes, since)
                import yfinance as yf

                kwargs = {"start": since.isoformat()} if since else {"period": "11y"}
                return yf.download(codes, interval="1d", auto_adjust=True, progress=False,
                                   group_by="column", **kwargs)

            frame = safe_fetch(download, source=f"yf:{','.join(codes)}")
            close = None if frame is None or getattr(frame, "empty", True) else (
                frame["Close"] if "Close" in frame else frame)
            for key, spec in yf_specs.items():
                try:
                    series = close[spec[1]].dropna() if close is not None and spec[1] in close else None
                except Exception:  # noqa: BLE001 - yfinance column shape varies by version
                    series = None
                if series is None or not len(series):
                    gaps[key] = "provider_empty"
                    continue
                rows = _records(key, spec, series, since)
                records.extend(rows)
                raw[key] = [[r.period, r.value] for r in rows]

        if "fear_greed" in specs:
            series = safe_fetch(self._fg or _fetch_fear_greed, source="cnn:fear_greed", attempts=3)
            if series is None or not len(series):
                gaps["fear_greed"] = "provider_empty"
            else:
                rows = _records("fear_greed", specs["fear_greed"], series, since)
                records.extend(rows)
                raw["fear_greed"] = [[r.period, r.value] for r in rows]

        status = (IngestionStatus.SUCCEEDED if records else IngestionStatus.ZERO_MATCH)
        coverage = {"series_requested": len(specs), "series_with_data": len(raw), "gaps": gaps,
                    "records": len(records), "since": since.isoformat() if since else ""}
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id, status=status,
            fetched_at=fetched_at, records=records,
            artifacts=[AdapterArtifact(
                payload=json.dumps(raw, sort_keys=True, separators=(",", ":")),
                query_scope={**scope}, source_url="fred+yfinance+cnn", source_version="",
                media_type="application/json", retention="full_response",
                metadata={"coverage": coverage})],
            provider_metadata={"coverage": coverage})
