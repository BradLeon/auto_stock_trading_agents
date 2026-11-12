"""SEC filing acquisition: reuse ats.data.sec, enforce managed transport policy."""
from __future__ import annotations

import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from ... import sec as extractor
from ... import sec_rate

_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")


class SECSourceUnavailable(RuntimeError):
    """Transport failure or exhausted budget, not bad source content."""

def _official_url(value: str, accession: str, cik: str) -> bool:
    parsed = urlparse(value)
    digits = accession.replace("-", "")
    parts = parsed.path.split("/")
    return (parsed.scheme == "https" and parsed.netloc == "www.sec.gov" and
            parsed.query == "" and parsed.fragment == "" and
            len(parts) >= 6 and parts[:4] == ["", "Archives", "edgar", "data"] and
            parts[4].isdigit() and int(parts[4]) == int(cik) and
            parts[5] == digits and len(parts) == 7 and
            parts[6] not in {"", "index.json"})


def _official_directory(value: str, accession: str, cik: str) -> bool:
    parsed = urlparse(value)
    parts = parsed.path.rstrip("/").split("/")
    return (parsed.scheme == "https" and parsed.netloc == "www.sec.gov" and
            not parsed.query and not parsed.fragment and len(parts) == 6 and
            parts[:4] == ["", "Archives", "edgar", "data"] and
            parts[4].isdigit() and int(parts[4]) == int(cik) and
            parts[5] == accession.replace("-", ""))


def validate_filing(row: dict[str, Any]) -> tuple[str, str]:
    symbol = str(row.get("symbol") or "").upper()
    accession = str(row.get("accession_number") or "")
    cik = str(row.get("cik") or "")
    if not symbol or not _ACCESSION.fullmatch(accession) or not cik.isdigit():
        raise ValueError("filing_identity_invalid")
    if str(row.get("form_type") or "") not in {"10-K", "10-Q", "8-K", "20-F", "6-K"}:
        raise ValueError("filing_form_out_of_scope")
    filing_url = str(row.get("filing_url") or "")
    if not (_official_url(filing_url, accession, cik) or
            _official_directory(filing_url, accession, cik)):
        raise ValueError("filing_official_url_invalid")
    return symbol, accession



class SECTransport:
    """One run's request/size budget; never follows an unvalidated redirect."""

    def __init__(self, budget: dict):
        # `max_requests_per_run` is the budget of SUCCESSFUL requests: a request that failed does not use it. Failures
        # have their own ceiling (`max_failed_requests_per_run`, default the same number), so a run still cannot hammer
        # an unreachable host. A 403/404 is an answer, not a failure of the host: it uses neither (the run is bounded by
        # `max_documents_per_run` anyway).
        self.remaining = int(budget["max_requests_per_run"])
        self.failures_remaining = int(budget.get("max_failed_requests_per_run", self.remaining))
        self.timeout = float(budget.get("timeout_seconds", 30))
        self.user_agent = (os.environ.get("SEC_EDGAR_USER_AGENT") or
                           os.environ.get("ATS_SEC_USER_AGENT") or "").strip()
        if not self.user_agent:
            self.user_agent = extractor._headers().get("User-Agent", "")
        if "@" not in self.user_agent:
            raise ValueError("sec_user_agent_missing")
        self.last_request = 0.0

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0 or self.failures_remaining <= 0

    def for_filing(self, row: dict):
        def request(url: str, *, stage: str, attempts: int = 3):
            failures = []
            if not _official_url(url, row["accession_number"], str(row["cik"])):
                return "", (extractor.SecFetchFailure(
                    stage, url, "InvalidOfficialURL", "outside accession"),)
            for attempt in range(min(attempts, 2)):
                if self.exhausted:
                    failures.append(extractor.SecFetchFailure(
                        stage, url, "RequestBudgetExceeded", "run request budget exhausted"))
                    break
                try:
                    sec_rate.wait()
                except sec_rate.SECRateLimited as exc:
                    # SEC is throttling this machine: stop the run, the filings left are deferred, not failed.
                    self.remaining = 0
                    failures.append(extractor.SecFetchFailure(
                        stage, url, "RequestBudgetExceeded", f"sec rate cooldown: {exc}"))
                    break
                self.last_request = time.monotonic()
                try:
                    with httpx.Client(timeout=self.timeout, follow_redirects=False,  # noqa: SIM117
                                      headers={"User-Agent": self.user_agent}) as client:
                        with client.stream("GET", url) as response:
                            if response.status_code in {403, 429} and \
                                    sec_rate.observe(response.status_code, response.read()):
                                self.remaining = 0
                                failures.append(extractor.SecFetchFailure(
                                    stage, url, "RequestBudgetExceeded",
                                    f"sec throttled the request (http {response.status_code})"))
                                break
                            response.raise_for_status()
                            if response.is_redirect:
                                raise ValueError("sec_redirect_not_allowed")
                            payload = bytearray()
                            for chunk in response.iter_bytes():
                                payload.extend(chunk)
                                if len(payload) > 12_000_000:
                                    raise ValueError("sec_body_over_budget")
                            self.remaining -= 1
                            return bytes(payload).decode(response.encoding or "utf-8",
                                                         errors="replace"), tuple(failures)
                except (httpx.HTTPError, ValueError) as exc:
                    definitive = isinstance(exc, httpx.HTTPStatusError) and \
                        exc.response.status_code in {403, 404}
                    if not definitive:
                        self.failures_remaining -= 1      # a definitive "not there" costs nothing; a flaky host does
                    failures.append(extractor.SecFetchFailure(
                        stage, url, type(exc).__name__, f"attempt {attempt + 1} failed"))
                    if isinstance(exc, ValueError) or (
                            isinstance(exc, httpx.HTTPStatusError) and
                            exc.response.status_code in {403, 404}):
                        break
            return "", tuple(failures)
        return request


def fetch_filing(row: dict, transport: SECTransport):
    """Return an authoritative body with its semantic role, never a directory/cover."""
    validate_filing(row)
    cik, accession, form = str(row["cik"]), row["accession_number"], row["form_type"]
    spent_before = getattr(transport, "remaining", None)
    with extractor.document_transport(transport.for_filing(row)):
        if form in {"8-K", "6-K"}:
            result = extractor.exhibit_result(cik, accession, form_type=form)
            semantic = "company_release"
        else:
            result = extractor.primary_filing_result(
                cik, accession, form, primary_url=row["filing_url"])
            semantic = "regulatory_filing"
    if result.status == "succeeded" and not _official_url(result.url, accession, cik):
        raise ValueError("sec_result_outside_accession")
    if result.status != "succeeded" and spent_before is not None:
        # The budget pays for filings that are admitted. A filing that yields no body (an 8-K without a press-release
        # exhibit, a missing submission) gives its requests back; the run stays bounded by `max_documents_per_run`
        # and, for flaky hosts, by `max_failed_requests_per_run`.
        transport.remaining = spent_before
    return result, semantic
