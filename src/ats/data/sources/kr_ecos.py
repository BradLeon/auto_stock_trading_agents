"""Bank of Korea ECOS — monthly export value index by commodity.

The most on-mechanism third-party series available for this theme. Korean semiconductor
exports are memory-dominated and SK hynix plus Samsung are most of them, so this
measures what those two actually shipped — as opposed to what they said about
themselves. Published monthly by the central bank, which has no position in the trade.

Contrast with Taiwan's aggregate IC exports (tw_mof), which the adjudicator correctly
judged non-attributable: Taiwan's exports are foundry and packaging, not memory. Same
statistic class, different mechanism — the country matters here, not just the HS code.

Series 403Y001 = 수출금액지수 (export value index, 2020=100). Item `3091AA` = 반도체.
It is an INDEX, not a dollar amount: levels are meaningless on their own, which is
exactly why the observations this produces are yoy/mom changes rather than levels.

Credentials: the documented `sample` key works and is capped at 10 rows per request —
irrelevant here because we query one item and page. Set `KR_ECOS_API_KEY` in `.env` to
use a registered key instead (free, instant, https://ecos.bok.or.kr/api/).
"""

from __future__ import annotations

import logging
from datetime import date

from ...schemas.chain import SeriesPoint

log = logging.getLogger("ats.data.sources.kr_ecos")

BASE = "https://ecos.bok.or.kr/api/StatisticSearch"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ats-bot)"}
PAGE = 10                       # the sample key's hard cap; a real key allows more


def _months_back(n: int) -> tuple[str, str]:
    today = date.today()
    total = today.year * 12 + (today.month - 1) - n
    return f"{total // 12}{total % 12 + 1:02d}", f"{today.year}{today.month:02d}"


def fetch(*, lookback_months: int = 6, stat: str = "403Y001", item: str = "3091AA",
          **_) -> list[SeriesPoint]:
    """Monthly index, newest last. Fetches an extra 12 months so year-on-year is
    computable for every point the caller asked for."""
    import httpx

    from ...config import get_config

    key = getattr(get_config().secrets, "kr_ecos_api_key", "") or "sample"
    start, end = _months_back(lookback_months + 13)

    raw: dict[str, float] = {}
    unit = ""
    for offset in range(0, 400, PAGE):
        url = (f"{BASE}/{key}/json/kr/{offset + 1}/{offset + PAGE}/"
               f"{stat}/M/{start}/{end}/{item}")
        body = httpx.get(url, headers=HEADERS, timeout=30).json()
        rows = body.get("StatisticSearch", {}).get("row", [])
        if not rows:
            break
        for r in rows:
            try:
                raw[str(r["TIME"])] = float(r["DATA_VALUE"])
            except (KeyError, TypeError, ValueError):
                continue
            unit = unit or r.get("UNIT_NAME", "")

    periods = sorted(raw)
    out: list[SeriesPoint] = []
    for yyyymm in periods[-lookback_months:]:
        year, month = int(yyyymm[:4]), int(yyyymm[4:])
        prev_m = f"{year - 1}12" if month == 1 else f"{year}{month - 1:02d}"
        prev_y = f"{year - 1}{month:02d}"
        out.append(SeriesPoint(
            period=f"{year}-{month:02d}", value=raw[yyyymm], unit=unit,
            yoy=_change(raw[yyyymm], raw.get(prev_y)),
            mom=_change(raw[yyyymm], raw.get(prev_m))))
    log.info("kr_ecos: %d monthly points, latest %s", len(out),
             out[-1].period if out else "n/a")
    return out


def _change(now: float, before: float | None) -> float | None:
    if not before:
        return None
    return (now - before) / before
