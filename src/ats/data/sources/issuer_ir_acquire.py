"""File acquirers for ``issuer_ir_files``: fetch an issuer's new IR file into its drop folder.

An acquirer only *places files*; parsing, checks and ingestion stay in ``IssuerIRFilesAdapter``.
Every acquirer is cheap on a quiet day (one listing request), downloads only what it has not
already fetched, writes a ``<file>.meta.json`` sidecar (source URL, accession / page, filing date)
and reports what it did.  Requests are paced and capped by the source's request budget.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import fnmatch
import hashlib
import json
from pathlib import Path
import time
from typing import Callable

ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
SUBMISSIONS = "https://data.sec.gov/submissions"
REQUEST_INTERVAL_SECONDS = 1.0       # internal budget: 1 request/second for SEC (their limit is 10)
REQUESTS_PER_RUN = 6                 # config/data/structured.yaml issuer_ir_files.internal_request_budget


class RequestBudgetExhausted(RuntimeError):
    pass


class _Client:
    """Paced, counted HTTP access; ``http`` is injectable for tests: ``http(url) -> bytes``."""

    def __init__(self, http: Callable[[str], bytes] | None, *, budget: int, interval: float, sleep=time.sleep,
                 headers: dict | None = None):
        self._http = http
        self.budget = budget
        self.interval = interval
        self._sleep = sleep
        self._headers = headers
        self.requests = 0

    def get(self, url: str) -> bytes:
        if self.requests >= self.budget:
            raise RequestBudgetExhausted(f"request budget of {self.budget} per run reached")
        if self.requests and self.interval:
            self._sleep(self.interval)
        self.requests += 1
        if self._http is not None:
            return self._http(url)
        import httpx
        response = httpx.get(url, headers=self._headers, timeout=30, follow_redirects=True)
        response.raise_for_status()
        return response.content

    def get_json(self, url: str):
        return json.loads(self.get(url).decode("utf-8"))


def _state_path(drop: Path) -> Path:
    return drop / ".acquisition_state.json"


def _load_state(drop: Path) -> dict:
    try:
        return json.loads(_state_path(drop).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(drop: Path, state: dict) -> None:
    drop.mkdir(parents=True, exist_ok=True)
    _state_path(drop).write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def _write_download(drop: Path, name: str, payload: bytes, meta: dict) -> Path:
    drop.mkdir(parents=True, exist_ok=True)
    path = drop / name
    path.write_bytes(payload)
    meta = {**meta, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
            "downloaded_at": datetime.now(timezone.utc).isoformat()}
    path.with_name(path.name + ".meta.json").write_text(json.dumps(meta, indent=1, sort_keys=True), encoding="utf-8")
    return path


def _sec_headers() -> dict:
    from .. import sec
    headers = sec._headers()
    if not str(headers.get("User-Agent", "")).strip():
        raise PermissionError("SEC_EDGAR_USER_AGENT is not set in .env: EDGAR refuses requests without it")
    return headers


# --------------------------------------------------------------------------- #
# SEC 6-K exhibits (ASML: the quarterly-results 6-K's "Financial Statements US GAAP" exhibit)
# --------------------------------------------------------------------------- #
def acquire_sec_6k_exhibits(config, drop: Path, request, fetched_at: datetime, *,
                            http: Callable[[str], bytes] | None = None, sleep=time.sleep) -> dict:
    """Download exhibits matching ``exhibit_patterns`` from not-yet-seen recent 6-Ks.

    Quiet day: one request (the submissions listing).  A 6-K is looked at once: its index is read,
    the matching exhibits are saved, and the accession is remembered -- except a 6-K filed within the
    last two days that has no matching exhibit yet, which is re-checked on the next run.
    """
    settings = config.raw.get("acquisition") or {}
    cik = str(settings["cik"]).zfill(10)
    forms = {str(item) for item in (settings.get("forms") or ["6-K"])}
    patterns = [str(item) for item in settings["exhibit_patterns"]]
    lookback = timedelta(days=int(settings.get("lookback_days", 120)))
    max_new = int(settings.get("max_new_filings_per_run", 3))
    today = fetched_at.astimezone(timezone.utc).date()

    client = _Client(http, budget=REQUESTS_PER_RUN, interval=REQUEST_INTERVAL_SECONDS if http is None else 0.0,
                     sleep=sleep, headers=None if http is not None else _sec_headers())
    state = _load_state(drop)
    seen: dict = state.setdefault("sec_6k", {})
    report = {"kind": "sec_6k_exhibits", "cik": cik, "downloaded": [], "checked": [], "skipped_seen": 0}
    try:
        listing = client.get_json(f"{SUBMISSIONS}/CIK{cik}.json")
        recent = (listing.get("filings") or {}).get("recent") or {}
        rows = list(zip(recent.get("form", []), recent.get("accessionNumber", []),
                        recent.get("filingDate", []), recent.get("primaryDocument", [])))
        pending = []
        for form, accession, filed, primary in rows:
            if form not in forms:
                continue
            if date.fromisoformat(filed) < today - lookback:
                continue
            if accession in seen:
                report["skipped_seen"] += 1
                continue
            pending.append((filed, accession, form))
        for filed, accession, form in sorted(pending, reverse=True)[:max_new]:
            nodash = accession.replace("-", "")
            base = f"{ARCHIVES}/{int(cik)}/{nodash}"
            index = client.get_json(f"{base}/index.json")
            names = [item["name"] for item in (index.get("directory") or {}).get("item", [])]
            wanted = [name for name in names if any(fnmatch.fnmatch(name, pattern) for pattern in patterns)]
            saved = []
            for name in wanted:
                payload = client.get(f"{base}/{name}")
                path = _write_download(drop, f"{accession}_{name}", payload, {
                    "source_url": f"{base}/{name}", "accession": accession, "form": form, "filed": filed,
                    "exhibit": name, "cik": cik})
                saved.append(path.name)
            if saved:
                seen[accession] = {"status": "downloaded", "files": saved, "filed": filed}
                report["downloaded"].extend(saved)
            elif (today - date.fromisoformat(filed)).days > 2:
                seen[accession] = {"status": "no_matching_exhibit", "filed": filed}
            report["checked"].append(accession)
    except RequestBudgetExhausted as exc:
        report["budget_exhausted"] = str(exc)
    finally:
        _save_state(drop, state)
    report["requests"] = client.requests
    return report


ACQUIRERS: dict[str, Callable] = {
    "sec_6k_exhibits": acquire_sec_6k_exhibits,
}
