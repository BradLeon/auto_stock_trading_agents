"""Filing discovery straight from SEC EDGAR, for the companies whose earnings are due now.

The DefeatBeta filing index is a daily copy of EDGAR that shows a filing the next day around 08:35 UTC, sometimes a
day later still. An 8-K with the earnings release is on EDGAR minutes after it is filed. This source reads
`data.sec.gov/submissions/CIK##########.json` for the few entities whose earnings event is within a few days of today
and returns rows in the same shape as the DefeatBeta index, so the SEC body job treats both alike. One request per
entity, every request through the machine-wide SEC pace (`ats.data.sec_rate`).
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .. import sec_rate
from ..pipelines.unstructured.sec import SECSourceUnavailable

SUBMISSIONS = "https://data.sec.gov/submissions"
COMPANY_TICKERS = "https://www.sec.gov/files/company_tickers.json"
FORMS = ("10-K", "10-Q", "8-K", "20-F", "6-K")
_NEW_YORK = ZoneInfo("America/New_York")


def due_entities(now: datetime, *, days_before: int, days_after: int, calendar_store=None) -> list[str]:
    """Entities with a non-cancelled earnings event from `days_after` days ago to `days_before` days ahead (New York)."""
    from ..stores.schedule_calendar import ScheduleCalendarStore

    today = now.astimezone(_NEW_YORK).date()
    first, last = today - timedelta(days=days_after), today + timedelta(days=days_before)
    store = calendar_store or ScheduleCalendarStore()
    entities = set()
    for event in store.latest_events(limit=10_000):
        if event["event_type"] != "earnings" or event["status"] == "cancelled":
            continue
        try:
            when = date.fromisoformat(str(event.get("event_date") or "")[:10])
        except ValueError:
            continue
        entity = str(((event.get("payload") or {}).get("metadata") or {}).get("entity") or "").upper()
        if entity and first <= when <= last:
            entities.add(entity)
    return sorted(entities)


def user_agent() -> str:
    agent = (os.environ.get("SEC_EDGAR_USER_AGENT") or os.environ.get("ATS_SEC_USER_AGENT") or "").strip()
    if "@" not in agent:
        raise ValueError("sec_user_agent_missing")
    return agent


def _http_get(url: str, *, timeout: float) -> tuple[int, bytes]:
    import httpx

    response = httpx.get(url, headers={"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate"},
                         timeout=timeout, follow_redirects=False)
    return response.status_code, response.content


class _Requests:
    """Counted, SEC-paced GETs; a throttling answer ends the run."""

    def __init__(self, budget: int, *, timeout: float, http: Callable[..., tuple[int, bytes]] = _http_get,
                 wait: Callable[[], None] = sec_rate.wait):
        self.remaining = budget
        self.timeout = timeout
        self._http = http
        self._wait = wait

    def json(self, url: str) -> Any:
        if self.remaining <= 0:
            raise SECSourceUnavailable("sec_RequestBudgetExceeded")
        try:
            self._wait()
        except sec_rate.SECRateLimited as exc:
            raise SECSourceUnavailable(f"sec_rate_limited:{exc}") from exc
        self.remaining -= 1
        status, body = self._http(url, timeout=self.timeout)
        if sec_rate.observe(status, body):
            raise SECSourceUnavailable(f"sec_rate_limited:http_{status}")
        if status != 200:
            raise ValueError(f"sec_submissions_http_{status}")
        return json.loads(body.decode("utf-8"))


def _cached_tickers(requests: _Requests, cache: Path, *, max_age_days: int = 7) -> dict[str, str]:
    try:
        if time.time() - cache.stat().st_mtime < max_age_days * 86400:
            return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    payload = requests.json(COMPANY_TICKERS)
    mapping = {str(row["ticker"]).upper(): str(int(row["cik_str"])) for row in payload.values()}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(mapping), encoding="utf-8")
    return mapping


def resolve_ciks(symbols: list[str], *, known: dict[str, str], requests: _Requests, cache: Path) -> dict[str, str]:
    """CIK per symbol: what the filing index already recorded, else SEC's ticker file (cached for a week)."""
    resolved = {symbol: known[symbol] for symbol in symbols if known.get(symbol)}
    if len(resolved) < len(symbols):
        tickers = _cached_tickers(requests, cache)
        resolved.update({symbol: tickers[symbol] for symbol in symbols
                         if symbol not in resolved and symbol in tickers})
    return resolved


def recent_filings(symbol: str, cik: str, payload: dict, *, since: date) -> list[dict[str, Any]]:
    recent = (payload.get("filings") or {}).get("recent") or {}
    columns = ("accessionNumber", "form", "filingDate", "reportDate", "acceptanceDateTime", "primaryDocument")
    rows = list(zip(*(recent.get(name) or [] for name in columns), strict=False))
    output = []
    for accession, form, filed, report, accepted, primary in rows:
        if form not in FORMS or not filed or date.fromisoformat(filed) < since:
            continue
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"
        output.append({"symbol": symbol, "cik": str(int(cik)), "accession_number": accession,
                       "company_name": str(payload.get("name") or ""), "form_type": form,
                       "filing_date": filed, "report_date": report or "", "acceptance_date_time": accepted or "",
                       "filing_url": f"{base}/{primary}" if primary and "/" not in primary else base})
    return output


def discover(symbols: list[str], *, now: datetime, known_ciks: dict[str, str], budget: dict,
             cache: Path, http: Callable[..., tuple[int, bytes]] = _http_get,
             wait: Callable[[], None] = sec_rate.wait) -> tuple[list[dict[str, Any]], list[str]]:
    """Recent filings of `symbols` and the symbols that could not be looked up (no CIK, or a failed request)."""
    requests = _Requests(int(budget["max_requests_per_run"]), timeout=float(budget.get("timeout_seconds", 30)),
                         http=http, wait=wait)
    since = now.astimezone(_NEW_YORK).date() - timedelta(days=int(budget.get("lookback_days", 7)))
    ciks = resolve_ciks(symbols, known=known_ciks, requests=requests, cache=cache)
    rows, missing = [], sorted(set(symbols) - set(ciks))
    for symbol in sorted(ciks):
        try:
            payload = requests.json(f"{SUBMISSIONS}/CIK{int(ciks[symbol]):010d}.json")
        except ValueError:
            missing.append(symbol)
            continue
        rows.extend(recent_filings(symbol, ciks[symbol], payload, since=since))
    return rows, sorted(missing)
