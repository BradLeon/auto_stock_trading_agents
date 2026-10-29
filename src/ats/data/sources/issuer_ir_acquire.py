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

import re
from urllib.parse import urljoin, urlparse

from .issuer_ir_files import AcquisitionError

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

        from .. import sec_rate
        if "sec.gov" in url:
            sec_rate.wait()
        response = httpx.get(url, headers=self._headers, timeout=30, follow_redirects=True)
        if "sec.gov" in url and sec_rate.observe(response.status_code, response.content):
            raise sec_rate.SECRateLimited(f"sec throttled the request (http {response.status_code})")
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


def _store_unique(drop: Path, payload: bytes, filename: str, meta: dict, *, suffix: str,
                  fetched_at: datetime) -> tuple[Path, bool]:
    """Save ``payload`` unless a file with the same sha256 is already in ``drop``.

    Returns (path, newly_saved).  When the content is already there (for example a manual download of
    the same file) nothing is stored twice; the existing copy only gets its source recorded.
    """
    sha256 = hashlib.sha256(payload).hexdigest()
    twin = next((path for path in sorted(drop.glob(f"*{suffix}")) if path.is_file()
                 and hashlib.sha256(path.read_bytes()).hexdigest() == sha256), None) if drop.is_dir() else None
    if twin is None:
        return _write_download(drop, filename, payload, meta), True
    sidecar = twin.with_name(twin.name + ".meta.json")
    if not sidecar.exists():
        sidecar.write_text(json.dumps({**meta, "sha256": sha256, "bytes": len(payload),
                                       "downloaded_at": fetched_at.isoformat(),
                                       "note": "file was already in the folder; URL recorded afterwards"},
                                      indent=1, sort_keys=True), encoding="utf-8")
    return twin, False


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


# --------------------------------------------------------------------------- #
# TSMC investor-relations "Quarterly Results" page (FS.pdf link carries a content hash)
# --------------------------------------------------------------------------- #
_TSMC_FS_LINK = re.compile(
    r'<a\b[^>]*?href="(?P<href>[^"]*/reports/[^"]*/FS\.pdf)"[^>]*>', re.I | re.S)
_TSMC_ACTIVE_TAB = re.compile(r'class="active[^"]*"[^>]*data-currentYear="(?P<year>\d{4})-Q(?P<quarter>[1-4])"', re.I)
_TSMC_TITLE = re.compile(r"<title>\s*TSMC (?P<year>\d{4}) Q(?P<quarter>[1-4]) Quarterly Results", re.I)


def parse_tsmc_results_page(html: str) -> dict:
    """Latest quarter and its Financial Statements link from the (server-rendered) results page."""
    links = {match.group("href") for match in _TSMC_FS_LINK.finditer(html)}
    if len(links) != 1:
        raise AcquisitionError(
            f"TSMC results page: expected one FS.pdf link, found {len(links)} -- the page layout changed")
    quarter = _TSMC_ACTIVE_TAB.search(html) or _TSMC_TITLE.search(html)
    if not quarter:
        raise AcquisitionError("TSMC results page: cannot tell which quarter the page shows")
    return {"href": next(iter(links)), "year": int(quarter.group("year")), "quarter": int(quarter.group("quarter"))}


def acquire_tsmc_quarterly_results(config, drop: Path, request, fetched_at: datetime, *,
                                   http: Callable[[str], bytes] | None = None, sleep=time.sleep) -> dict:
    """Light check of TSMC's results page; download FS.pdf only when its link is new.

    A quiet day costs one request (the page).  The link contains a content hash, so a changed link
    means a new file; a downloaded file whose sha256 is already in the folder is not stored twice
    (the existing copy just gets its source URL recorded).
    """
    settings = config.raw.get("acquisition") or {}
    page_url = str(settings["page_url"])
    host = str(settings.get("allowed_host") or urlparse(page_url).netloc)
    headers = {"User-Agent": str(settings.get("user_agent") or ""), "Accept": "text/html,application/pdf"}
    client = _Client(http, budget=REQUESTS_PER_RUN, interval=REQUEST_INTERVAL_SECONDS if http is None else 0.0,
                     sleep=sleep, headers=headers)
    state = _load_state(drop)
    known: dict = state.setdefault("tsmc_results", {})
    report = {"kind": "tsmc_quarterly_results", "downloaded": [], "page_url": page_url}
    try:
        page = parse_tsmc_results_page(client.get(page_url).decode("utf-8", errors="replace"))
        url = urljoin(page_url, page["href"])
        if urlparse(url).netloc != host or not urlparse(url).path.endswith("/FS.pdf"):
            raise AcquisitionError(f"TSMC results page: unexpected FS.pdf location {url}")
        label = f"{page['year']}Q{page['quarter']}"
        report.update(quarter=label, fs_url=url)
        previous = known.get(url)
        if previous and (drop / previous["file"]).is_file():
            report["status"] = "link_unchanged"
            return report
        payload = client.get(url)
        if not payload.startswith(b"%PDF"):
            raise AcquisitionError(f"TSMC FS.pdf at {url} is not a PDF")
        sha256 = hashlib.sha256(payload).hexdigest()
        meta = {"source_url": url, "page_url": page_url, "quarter": label, "form": "FS.pdf"}
        path, saved = _store_unique(drop, payload, f"FS_{label}_{sha256[:8]}.pdf", meta, suffix=".pdf",
                                    fetched_at=fetched_at)
        known[url] = {"file": path.name, "sha256": sha256, "quarter": label}
        report["file"] = path.name
        if saved:
            report["status"] = "downloaded"
            report["downloaded"].append(path.name)
        else:
            report["status"] = "already_have_this_file"
    finally:
        _save_state(drop, state)
        report["requests"] = client.requests
    return report


# --------------------------------------------------------------------------- #
# Euroland "Interactive Analysis" Excel export (SK hynix quarterly statements, third party)
# --------------------------------------------------------------------------- #
def urlencode_params(settings: dict) -> str:
    from urllib.parse import urlencode
    return urlencode({str(k): str(v) for k, v in (settings.get("params") or {}).items()})


def acquire_euroland_export(config, drop: Path, request, fetched_at: datetime, *,
                            http: Callable[[str], bytes] | None = None, sleep=time.sleep) -> dict:
    """Request the tool's own "Export all the data into an Excel" URL and keep it when its content is new.

    The tool page's export button builds ``<tool>/Excel/?<tool params>&sid=&generalNotesFirst=0&levelZero=<n>&format=xls``
    (levelZero 1 = the Quarterly tab).  The request needs no cookie or login; one request a day is both the
    check and the download, and an unchanged export (same sha256) is not stored again.
    """
    settings = config.raw.get("acquisition") or {}
    url = f"{settings['export_url']}?{urlencode_params(settings)}"
    host = str(settings.get("allowed_host") or urlparse(url).netloc)
    if urlparse(url).netloc != host:
        raise AcquisitionError(f"Euroland export host {urlparse(url).netloc} is not {host}")
    headers = {"User-Agent": str(settings.get("user_agent") or ""), "Referer": str(settings.get("page_url") or "")}
    client = _Client(http, budget=REQUESTS_PER_RUN, interval=0.0, sleep=sleep, headers=headers)
    state = _load_state(drop)
    known: dict = state.setdefault("euroland_export", {})
    report = {"kind": "euroland_export", "export_url": url, "downloaded": []}
    try:
        payload = client.get(url)
        if not payload.startswith(b"\xd0\xcf\x11\xe0"):          # OLE2 / .xls
            raise AcquisitionError(
                f"Euroland export did not return a workbook: {payload[:60]!r} -- the export parameters changed")
        sha256 = hashlib.sha256(payload).hexdigest()
        previous = known.get("sha256")
        if previous == sha256 and (drop / known.get("file", "")).is_file():
            report["status"] = "export_unchanged"
            return report
        meta = {"source_url": url, "page_url": str(settings.get("page_url") or ""),
                "source_nature": "third_party_aggregator", "form": "Data.xls"}
        stamp = fetched_at.astimezone(timezone.utc).strftime("%Y%m%d")
        path, saved = _store_unique(drop, payload, f"Data_{stamp}_{sha256[:8]}.xls", meta, suffix=".xls",
                                    fetched_at=fetched_at)
        known.update(sha256=sha256, file=path.name, checked_at=fetched_at.isoformat())
        report["file"] = path.name
        if saved:
            report["status"] = "downloaded"
            report["downloaded"].append(path.name)
        else:
            report["status"] = "already_have_this_file"
    finally:
        _save_state(drop, state)
        report["requests"] = client.requests
    return report


ACQUIRERS: dict[str, Callable] = {
    "sec_6k_exhibits": acquire_sec_6k_exhibits,
    "tsmc_quarterly_results": acquire_tsmc_quarterly_results,
    "euroland_export": acquire_euroland_export,
}
