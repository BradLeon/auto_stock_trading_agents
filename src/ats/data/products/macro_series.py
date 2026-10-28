"""Macro indicator read product: stored series -> the snapshot and histories the Macro review used.

Replaces `data.macro.fetch()` / `fetch_series()` as the review's input (2026-10-16): the numbers
come from observations the refresh controller collected (`sources/macro_series.py`), so every
review can name the data it read and a missing or stale feed is a stated gap instead of a silent
provider failure. The derived fields reproduce `fetch()` exactly (YoY from the index 12 prints
back, NFP change, claims in thousands, % change vs the prior close).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from ...schemas.macro import MacroData

ENTITY = "US_MACRO"
SOURCE_ID = "macro_series_feeds"
DATASET_ID = "macro_series"
# A series with no print for this long is reported stale rather than used silently. Monthly and
# quarterly series are dated by period start and published weeks after it (CFNAI/PCE ~ 3-5 weeks,
# productivity ~ 5-8 weeks), so their limits include the normal release lag.
STALE_DAYS = {"daily": 10, "weekly": 21, "monthly": 100, "quarterly": 220}


@dataclass(frozen=True)
class MacroSeriesSet:
    series: dict                      # key -> pandas.Series (DatetimeIndex), ascending
    known_at: dict = field(default_factory=dict)   # key -> latest known_at (ISO) of the newest point
    gaps: tuple = ()                  # keys with no stored data
    stale: tuple = ()                 # (key, last_period, age_days)
    defined: dict = field(default_factory=dict)   # key -> cadence of every defined series (collected or not)
    as_of: datetime | None = None


def _repository(repository):
    if repository is not None:
        return repository, False
    from ..runtime import get_platform_structured_repository

    return get_platform_structured_repository(), True


def load(*, as_of: datetime | None = None, repository=None, now: datetime | None = None) -> MacroSeriesSet:
    """All stored macro series visible at `as_of` (default: now). Never touches a provider."""
    import pandas as pd

    from ..sources.macro_series import all_series, metric_id

    now = now or datetime.now(timezone.utc)
    repo, owned = _repository(repository)
    try:
        specs = all_series()
        by_metric = {metric_id(key): key for key in specs}
        rows = repo.observations(dataset_id=DATASET_ID, entity_id=ENTITY, source_id=SOURCE_ID,
                                 as_of=as_of, latest_only=True, accepted_only=True, limit=500_000)
    finally:
        if owned:
            repo.close()
    points: dict[str, dict] = {}
    known: dict[str, str] = {}
    for row in rows:
        key = by_metric.get(row["metric_id"])
        if key is None:
            continue
        points.setdefault(key, {})[row["period"]] = float(row["value"])
        known[key] = max(known.get(key, ""), str(row.get("known_at") or ""))
    series = {}
    for key, values in points.items():
        s = pd.Series(values)
        s.index = pd.to_datetime(s.index)
        series[key] = s.sort_index()
    today = (as_of or now).date()
    gaps = tuple(sorted(set(specs) - set(series)))
    stale = []
    for key, s in series.items():
        age = (today - s.index[-1].date()).days
        if age > STALE_DAYS.get(specs[key][4], 10):
            stale.append((key, s.index[-1].date().isoformat(), age))
    return MacroSeriesSet(series=series, known_at=known, gaps=gaps, stale=tuple(sorted(stale)),
                          defined={key: spec[4] for key, spec in specs.items()}, as_of=as_of or now)


def _last(series, key):
    s = series.get(key)
    return float(s.iloc[-1]) if s is not None and len(s) else None


def _yoy(series, key):
    s = series.get(key)
    return round(float((s.iloc[-1] / s.iloc[-13] - 1) * 100), 2) if s is not None and len(s) > 12 else None


def _chg_pct(series, key):
    s = series.get(key)
    return round(float((s.iloc[-1] / s.iloc[-2] - 1) * 100), 2) if s is not None and len(s) > 1 else None


def snapshot(data: MacroSeriesSet, *, now: datetime | None = None) -> MacroData:
    """The latest-values view the review's quant block and theme lines are built from."""
    s = data.series
    out = MacroData(as_of=now or data.as_of or datetime.now(timezone.utc))
    for field_name in ("ust_10y", "ust_2y", "fed_funds", "real_10y", "unemployment", "cfnai",
                       "hy_oas", "ig_oas", "breakeven_10y", "oil_wti", "gold", "dxy"):
        value = _last(s, field_name)
        setattr(out, field_name, round(value, 2) if value is not None and field_name in {
            "oil_wti", "gold", "dxy"} else value)
    out.cpi_yoy = _yoy(s, "headline_cpi")
    out.pce_yoy = _yoy(s, "core_pce")
    payrolls = s.get("payrolls")
    out.nfp_change_k = round(float(payrolls.iloc[-1] - payrolls.iloc[-2]), 1) if payrolls is not None and len(payrolls) > 1 else None
    claims = _last(s, "initial_claims")
    out.jobless_claims_k = round(claims / 1000, 1) if claims is not None else None
    for key in ("vix", "spx", "ndx"):
        value = _last(s, key)
        if value is not None:
            setattr(out, key, round(value, 2))
    out.spx_chg_pct = _chg_pct(s, "spx")
    out.ndx_chg_pct = _chg_pct(s, "ndx")
    fear = _last(s, "fear_greed")
    out.fear_greed = int(round(fear)) if fear is not None else None
    if data.gaps:
        out.notes.append("macro series not collected: " + ", ".join(data.gaps))
    for key, last, age in data.stale:
        out.notes.append(f"stale: {key} last {last} ({age}d old)")
    return out
