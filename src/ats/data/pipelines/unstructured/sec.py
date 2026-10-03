"""SEC filing acquisition: reuse ats.data.sec, enforce managed transport policy."""
from __future__ import annotations

import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from ... import sec as extractor

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
        self.remaining = int(budget["max_requests_per_run"])
        self.timeout = float(budget.get("timeout_seconds", 30))
        self.user_agent = (os.environ.get("SEC_EDGAR_USER_AGENT") or
                           os.environ.get("ATS_SEC_USER_AGENT") or "").strip()
        if not self.user_agent:
            self.user_agent = extractor._headers().get("User-Agent", "")
        if "@" not in self.user_agent:
            raise ValueError("sec_user_agent_missing")
        self.last_request = 0.0

    def for_filing(self, row: dict):
        def request(url: str, *, stage: str, attempts: int = 3):
            failures = []
            if not _official_url(url, row["accession_number"], str(row["cik"])):
                return "", (extractor.SecFetchFailure(
                    stage, url, "InvalidOfficialURL", "outside accession"),)
            for attempt in range(min(attempts, 2)):
                if self.remaining <= 0:
                    failures.append(extractor.SecFetchFailure(
                        stage, url, "RequestBudgetExceeded", "run request budget exhausted"))
                    break
                self.remaining -= 1
                time.sleep(max(0, 0.15 - (time.monotonic() - self.last_request)))
                self.last_request = time.monotonic()
                try:
                    with httpx.Client(timeout=self.timeout, follow_redirects=False,  # noqa: SIM117
                                      headers={"User-Agent": self.user_agent}) as client:
                        with client.stream("GET", url) as response:
                            response.raise_for_status()
                            if response.is_redirect:
                                raise ValueError("sec_redirect_not_allowed")
                            payload = bytearray()
                            for chunk in response.iter_bytes():
                                payload.extend(chunk)
                                if len(payload) > 12_000_000:
                                    raise ValueError("sec_body_over_budget")
                            return bytes(payload).decode(response.encoding or "utf-8",
                                                         errors="replace"), tuple(failures)
                except (httpx.HTTPError, ValueError) as exc:
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
    return result, semantic
