"""EDGAR exhibit fetch by accession number — the earnings release itself.

Why this exists alongside the transcript: the call is where management NARRATES, the
release is where the numbers are TABULATED. On 2026-08-07 every one of the ledger's 379
`reported_actual` observations came from a transcript, so the most checkable facts in
the system were being extracted from its least precise source — spoken, rounded figures
like "ASP rose by mid 40%". The release carries the same quarter as a table.

For a US filer the earnings release IS Exhibit 99.1 of the 8-K; foreign private issuers
(ASML, TSM, SK hynix) attach it to a 6-K. They are not two artefacts to choose between.

Deterministic by construction: the caller supplies CIK and accession number (from
data.defeatbeta.filings), so there is no search step and no possibility of being handed
another company's document. EDGAR requires a declared User-Agent — `SEC_EDGAR_USER_AGENT`
in `.env`; requests without one are refused, and rightly so.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("ats.data.sec")

ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
SUBMISSIONS = "https://data.sec.gov/submissions"
COMPANY_TICKERS = "https://www.sec.gov/files/company_tickers.json"
MIN_CHARS = 800


@dataclass(frozen=True)
class SecFetchFailure:
    """One exhausted SEC transport stage, retained for source-health reporting."""

    stage: str
    url: str
    error_type: str
    message: str


@dataclass(frozen=True)
class SecFetchResult:
    """A document body plus a non-ambiguous acquisition outcome."""

    text: str = ""
    url: str = ""
    status: str = "missing"
    stage: str = ""
    errors: tuple[SecFetchFailure, ...] = ()
    role: str = ""
    description: str = ""
    declared_type: str = ""


@dataclass(frozen=True)
class SecRecordResult:
    """Validated issuer/event record and the underlying SEC acquisition state."""

    record: dict | None = None
    status: str = "missing"
    stage: str = ""
    errors: tuple[SecFetchFailure, ...] = ()
    discovered: int = 0


@dataclass(frozen=True)
class SecIndexDocument:
    """One declared EDGAR document row, including its human-readable description."""

    declared_type: str
    href: str
    size: int = 0
    description: str = ""


def _headers() -> dict:
    from ..config import get_config

    return {"User-Agent": get_config().secrets.sec_edgar_user_agent,
            "Accept-Encoding": "gzip, deflate"}


def _request_text(url: str, *, stage: str, attempts: int = 3) -> tuple[str, tuple[SecFetchFailure, ...]]:
    """GET text with a small bounded retry budget; never collapses failure to empty."""
    import httpx

    failures: list[SecFetchFailure] = []
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = httpx.get(
                url, headers=_headers(), timeout=30, follow_redirects=True)
            response.raise_for_status()
            return response.text, tuple(failures)
        except Exception as exc:  # noqa: BLE001 - surfaced as structured health below
            failures.append(SecFetchFailure(
                stage=stage, url=url, error_type=type(exc).__name__,
                message=f"attempt {attempt}/{max(1, attempts)}: {exc}",
            ))
    return "", tuple(failures)


def _filing_base(cik: str, accession: str) -> tuple[str, str]:
    accn = (accession or "").replace("-", "")
    if not accn or not str(cik).isdigit():
        return "", ""
    return f"{ARCHIVES}/{int(cik)}/{accn}", accn


def _hyphenated_accession(accession: str) -> str:
    """Return the accession spelling SEC uses for index/submission filenames."""
    raw = (accession or "").strip()
    if re.fullmatch(r"\d{10}-\d{2}-\d{6}", raw):
        return raw
    compact = raw.replace("-", "")
    if re.fullmatch(r"\d{18}", compact):
        return f"{compact[:10]}-{compact[10:12]}-{compact[12:]}"
    return raw


def _index_documents(page: str) -> list[SecIndexDocument]:
    """Parse EDGAR's table without discarding document descriptions."""
    rows: list[SecIndexDocument] = []
    for raw in re.findall(r"<tr[^>]*>(.*?)</tr>", page or "", re.I | re.S):
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cell)).strip()
                 for cell in re.findall(r"<td[^>]*>(.*?)</td>", raw, re.I | re.S)]
        if len(cells) < 4:
            continue
        hrefs = re.findall(r'href=["\']([^"\']+)', raw, re.I)
        href = next((item for item in hrefs if "/ix?doc=" not in item), "")
        size = int(cells[4].replace(",", "")) if len(cells) > 4 and \
            cells[4].replace(",", "").isdigit() else 0
        if href:
            rows.append(SecIndexDocument(
                declared_type=cells[3].upper(), href=href, size=size,
                description=cells[1] if len(cells) > 1 else "",
            ))
    return rows


def _index_rows(page: str) -> list[tuple[str, str, int]]:
    """Compatibility view used by primary-filing selection."""
    return [(item.declared_type, item.href, item.size)
            for item in _index_documents(page)]


def _absolute_sec_url(base: str, href: str) -> str:
    from urllib.parse import urljoin

    return urljoin(f"{base}/", href)


def _submission_documents(raw: str) -> list[tuple[str, str, str, str]]:
    """Return type, filename, description and embedded body from submission SGML."""
    documents = []
    for block in re.findall(r"<DOCUMENT>(.*?)</DOCUMENT>", raw or "", re.I | re.S):
        kind = re.search(r"<TYPE>\s*([^\r\n<]+)", block, re.I)
        filename = re.search(r"<FILENAME>\s*([^\r\n<]+)", block, re.I)
        description = re.search(r"<DESCRIPTION>\s*([^\r\n<]+)", block, re.I)
        body = re.search(r"<TEXT>(.*?)</TEXT>", block, re.I | re.S)
        documents.append((
            kind.group(1).strip().upper() if kind else "",
            filename.group(1).strip() if filename else "",
            description.group(1).strip() if description else "",
            body.group(1) if body else block,
        ))
    return documents


def _classify_document_role(description: str, text: str) -> tuple[str, int]:
    """Classify an EX-99 by disclosure role; size is deliberately not a signal."""
    descriptor = (description or "").lower()
    head = (text or "")[:8000].lower()
    combined = f"{descriptor}\n{head}"
    release_score = 0
    for marker, weight in (
        ("press release", 16), ("pressrelease", 16),
        ("earnings release", 16), ("earningsrelease", 16),
        ("for immediate release", 16),
        ("financial results", 12), ("quarterly results", 10),
        ("reports fiscal", 8), ("announces", 4), ("reported results", 6),
    ):
        if marker in combined:
            release_score += weight
    if re.search(
            r"\breports?\b.{0,100}\b(?:q[1-4]|first|second|third|fourth)\b",
            head[:2000], re.I):
        release_score += 10
    financial_table_hits = sum(marker in head for marker in (
        "balance sheet", "statement of operations", "statements of operations",
        "income statement", "statement of cash flows", "statements of cash flows",
        "statement of financial position", "statements of financial position",
    ))
    presentation_markers = ("presentation", "slides", "investor deck")
    regulatory_markers = (
        "statutory interim report", "interim financial report",
        "condensed financial statements", "consolidated financial statements",
        "operating and financial review", "condensed consolidated",
    )
    # A real press release often contains abbreviated statements below its headline.
    # Explicit release descriptions/headlines therefore win; otherwise a specialist
    # regulatory document or deck is kept out of the release role.
    explicit_release = any(marker in f"{descriptor}\n{head[:1200]}" for marker in (
        "press release", "pressrelease", "earnings release", "earningsrelease",
        "results announcement", "for immediate release"))
    explicit_regulatory = (
        any(marker in descriptor for marker in regulatory_markers)
        or any(marker in head[:1200] for marker in regulatory_markers)
    )
    if release_score >= 10 and explicit_release and not explicit_regulatory:
        return "earnings_release", release_score
    if any(marker in f"{descriptor}\n{head[:1200]}" for marker in presentation_markers):
        return "presentation", -20
    if explicit_regulatory or financial_table_hits >= 2:
        return "regulatory_filing", -15
    if release_score >= 10:
        return "earnings_release", release_score
    return "other", release_score


def _filing_regime_from_forms(forms) -> str:
    """Infer the issuer reporting regime from its actual SEC filing history."""
    normalized = {str(form or "").upper() for form in forms}
    if normalized & {"10-Q", "10-K"}:
        return "domestic"
    if "40-F" in normalized:
        return "foreign_40f"
    if "20-F" in normalized:
        return "foreign_20f"
    if "6-K" in normalized:
        return "foreign"
    return "unknown"


def _direct_6k_financial_report(text: str) -> bool:
    """Recognize a full preliminary/interim financial table carried in a 6-K body."""
    low = (text or "")[:20000].lower()
    hits = sum(marker in low for marker in (
        "preliminary results of operations", "basis: consolidated", "current period",
        "operating profit", "profit for the period", "quarterly results",
        "international financial reporting standards", "k-ifrs",
    ))
    return hits >= 3 and "revenue" in low


def _complete_submission(base: str, accession: str, *, stage: str,
                         attempts: int = 3) -> tuple[str, str, tuple[SecFetchFailure, ...]]:
    # The archive DIRECTORY is the compact accession, but the complete-submission
    # FILENAME retains its hyphens (for example 0000002488-26-000123.txt).
    url = f"{base}/{_hyphenated_accession(accession)}.txt"
    body, failures = _request_text(url, stage=stage, attempts=attempts)
    return body, url, failures


def _filing_index_page(base: str, accession: str) -> tuple[
        str, str, tuple[SecFetchFailure, ...]]:
    """Fetch an EDGAR filing index, accepting both index suffixes seen in archives."""
    errors: list[SecFetchFailure] = []
    stem = _hyphenated_accession(accession)
    last_url = ""
    for suffix in ("-index.html", "-index.htm"):
        last_url = f"{base}/{stem}{suffix}"
        page, failures = _request_text(last_url, stage="filing_index")
        errors.extend(failures)
        if page:
            return page, last_url, tuple(errors)
    return "", last_url, tuple(errors)


def exhibit_result(cik: str, accession: str, *, form_type: str = "") -> SecFetchResult:
    """Fetch an EX-99 via filing index, then the complete submission as fallback.

    An `ex99` exhibit is REQUIRED, never merely preferred. Falling back to the largest
    document in the filing returned SK hynix's bare 6-K cover page — 4.8k of "UNITED
    STATES SECURITIES AND EXCHANGE COMMISSION, Washington D.C." with no financial
    content — because that 6-K carried no exhibit at all. A cover page is not an
    earnings release, and a filing without an exhibit is simply not the filing we want:
    the caller moves on to the next one rather than accepting boilerplate.

    Every EX-99 is classified by its SEC description and content. A filing can carry a
    press release, slides and full statutory statements together; the largest of those
    is commonly the statements, not the company release.
    """
    from .documents import _text, strip_xbrl_boilerplate

    base, _ = _filing_base(cik, accession)
    if not base:
        return SecFetchResult(status="missing", stage="identity")
    errors: list[SecFetchFailure] = []
    # The filing index PAGE, not index.json. The JSON's `type` field is an icon name
    # ("text.gif"); only the page carries the declared document type. Matching on the
    # filename instead is what this replaced, and it failed on the first filer tried:
    # AMD's Exhibit 99.1 is named `q22026991.htm`, which contains no "ex99" at all.
    page, _, index_errors = _filing_index_page(base, accession)
    errors.extend(index_errors)
    index_had_exhibit = False
    if page:
        index_documents = _index_documents(page)
        exhibits = [item for item in index_documents
                    if item.declared_type.startswith("EX-99")]
        index_had_exhibit = bool(exhibits)
        # Some foreign issuers put the actual release directly in the 6-K body and
        # declare no exhibit. It is admissible only after the same role/content,
        # issuer and fiscal-period gates as an EX-99 release.
        direct_6k = [item for item in index_documents
                     if form_type.upper() == "6-K" and item.declared_type == "6-K"]
        candidates = []
        for item in [*exhibits, *direct_6k]:
            url = _absolute_sec_url(base, item.href)
            body, body_errors = _request_text(url, stage="exhibit_document")
            errors.extend(body_errors)
            if body:
                text = strip_xbrl_boilerplate(_text(body))
                if len(text) >= MIN_CHARS:
                    if (item.declared_type == "6-K" and len(text) < 12000
                            and any(marker in text.lower() for marker in (
                                "filed as exhibit", "furnished as exhibit",
                                "index to exhibits"))):
                        continue
                    role, role_score = _classify_document_role(
                        f"{item.description} {item.href}", text)
                    candidates.append((
                        role == "earnings_release",
                        item.declared_type.startswith("EX-99"),
                        role_score, item, text, url))
        release_candidates = [item for item in candidates if item[0]]
        if release_candidates:
            _, _, _, item, text, url = max(
                release_candidates, key=lambda value: (value[1], value[2]))
            return SecFetchResult(
                text=text, url=url, status="succeeded", stage="filing_index",
                errors=tuple(errors), role="earnings_release",
                description=item.description, declared_type=item.declared_type,
            )

    submission, submission_url, submission_errors = _complete_submission(
        base, accession, stage="complete_submission")
    errors.extend(submission_errors)
    if submission:
        embedded = []
        for kind, filename, description, raw in _submission_documents(submission):
            if not (kind.startswith("EX-99") or
                    (form_type.upper() == "6-K" and kind == "6-K")):
                continue
            text = strip_xbrl_boilerplate(_text(raw))
            if len(text) >= MIN_CHARS:
                if (kind == "6-K" and len(text) < 12000
                        and any(marker in text.lower() for marker in (
                            "filed as exhibit", "furnished as exhibit",
                            "index to exhibits"))):
                    continue
                role, score = _classify_document_role(f"{description} {filename}", text)
                if role == "earnings_release":
                    embedded.append((kind.startswith("EX-99"), score, filename,
                                     description, kind, text))
        if embedded:
            _, _, filename, description, kind, text = max(embedded)
            return SecFetchResult(
                text=text, url=_absolute_sec_url(base, filename) if filename else submission_url,
                status="succeeded", stage="complete_submission", errors=tuple(errors),
                role="earnings_release", description=description, declared_type=kind,
            )
        log.info("sec: %s/%s carries no valid EX-99 exhibit", cik, accession)
        return SecFetchResult(
            status="missing", stage="complete_submission", errors=tuple(errors))

    # A reachable index with no declared exhibit is conclusive absence. If it declared
    # one but neither the document nor SGML could be read, this is an outage, not a gap.
    if page and not index_had_exhibit:
        return SecFetchResult(status="missing", stage="filing_index", errors=tuple(errors))
    return SecFetchResult(status="unreachable", stage=(errors[-1].stage if errors else "sec"),
                          errors=tuple(errors))


def exhibit_text(cik: str, accession: str, *, detailed: bool = False,
                 form_type: str = ""):
    """Compatibility API: `(text, url)` by default, structured status when requested."""
    result = exhibit_result(cik, accession, form_type=form_type)
    return result if detailed else (result.text, result.url)


def foreign_regulatory_result(cik: str, accession: str) -> SecFetchResult:
    """Fetch the financial-report/operating-review role from a foreign issuer's 6-K.

    This deliberately selects the regulatory document rather than the press release.
    Both may live under the same accession but remain independent source roles.
    """
    from .documents import _text, strip_xbrl_boilerplate

    base, _ = _filing_base(cik, accession)
    if not base:
        return SecFetchResult(status="missing", stage="identity")
    errors: list[SecFetchFailure] = []
    page, _, index_errors = _filing_index_page(base, accession)
    errors.extend(index_errors)
    had_candidates = False
    if page:
        candidates = []
        for item in _index_documents(page):
            if not (item.declared_type.startswith("EX-99") or item.declared_type == "6-K"):
                continue
            had_candidates = True
            url = _absolute_sec_url(base, item.href)
            body, body_errors = _request_text(url, stage="regulatory_document")
            errors.extend(body_errors)
            if not body:
                continue
            text = strip_xbrl_boilerplate(_text(body))
            if len(text) < MIN_CHARS:
                continue
            role, score = _classify_document_role(f"{item.description} {item.href}", text)
            if role == "regulatory_filing" or (
                    item.declared_type == "6-K" and _direct_6k_financial_report(text)):
                candidates.append((score, item.size, item, text, url))
        if candidates:
            _, _, item, text, url = max(candidates, key=lambda value: (value[0], value[1]))
            return SecFetchResult(
                text=text, url=url, status="succeeded", stage="filing_index",
                errors=tuple(errors), role="regulatory_filing",
                description=item.description, declared_type=item.declared_type,
            )

    submission, submission_url, submission_errors = _complete_submission(
        base, accession, stage="complete_submission")
    errors.extend(submission_errors)
    if submission:
        embedded = []
        for kind, filename, description, raw in _submission_documents(submission):
            if not (kind.startswith("EX-99") or kind == "6-K"):
                continue
            text = strip_xbrl_boilerplate(_text(raw))
            role, score = _classify_document_role(f"{description} {filename}", text)
            if len(text) >= MIN_CHARS and (role == "regulatory_filing" or
                    (kind == "6-K" and _direct_6k_financial_report(text))):
                embedded.append((score, len(text), filename, description, kind, text))
        if embedded:
            _, _, filename, description, kind, text = max(embedded)
            return SecFetchResult(
                text=text, url=_absolute_sec_url(base, filename) if filename else submission_url,
                status="succeeded", stage="complete_submission", errors=tuple(errors),
                role="regulatory_filing", description=description, declared_type=kind,
            )
        return SecFetchResult(status="missing", stage="complete_submission",
                              errors=tuple(errors))
    if page and not had_candidates:
        return SecFetchResult(status="missing", stage="filing_index", errors=tuple(errors))
    return SecFetchResult(status="unreachable", stage=(errors[-1].stage if errors else "sec"),
                          errors=tuple(errors))


def primary_filing_result(cik: str, accession: str, form_type: str, *,
                          primary_url: str = "") -> SecFetchResult:
    """Fetch a declared periodic primary document, never an arbitrary large file."""
    from .documents import _text, strip_xbrl_boilerplate

    wanted = (form_type or "").upper()
    if wanted not in {"10-Q", "10-K", "20-F", "40-F"}:
        return SecFetchResult(status="missing", stage="form_type")
    base, _ = _filing_base(cik, accession)
    if not base:
        return SecFetchResult(status="missing", stage="identity")
    errors: list[SecFetchFailure] = []
    # SEC's submissions feed supplies the exact primaryDocument filename. Use it
    # first when it is demonstrably inside this accession directory; mirrored
    # metadata sometimes exposes only a filing folder, so other URLs are ignored.
    if primary_url.startswith(f"{base}/") and re.search(r"\.html?$", primary_url, re.I):
        body, body_errors = _request_text(primary_url, stage="primary_document")
        errors.extend(body_errors)
        if body:
            text = strip_xbrl_boilerplate(_text(body))
            if len(text) >= MIN_CHARS:
                return SecFetchResult(
                    text=text, url=primary_url, status="succeeded",
                    stage="primary_document", errors=tuple(errors),
                )
    page, _, index_errors = _filing_index_page(base, accession)
    errors.extend(index_errors)
    index_had_primary = False
    if page:
        primaries = [(size, href) for kind, href, size in _index_rows(page)
                     if kind == wanted]
        index_had_primary = bool(primaries)
        for _, href in sorted(primaries, reverse=True):
            url = _absolute_sec_url(base, href)
            body, body_errors = _request_text(url, stage="primary_document")
            errors.extend(body_errors)
            if body:
                text = strip_xbrl_boilerplate(_text(body))
                if len(text) >= MIN_CHARS:
                    return SecFetchResult(
                        text=text, url=url, status="succeeded", stage="filing_index",
                        errors=tuple(errors),
                    )

    submission, submission_url, submission_errors = _complete_submission(
        base, accession, stage="complete_submission")
    errors.extend(submission_errors)
    if submission:
        embedded = []
        for kind, filename, _description, raw in _submission_documents(submission):
            if kind != wanted:
                continue
            text = strip_xbrl_boilerplate(_text(raw))
            if len(text) >= MIN_CHARS:
                embedded.append((len(text), filename, text))
        if embedded:
            _, filename, text = max(embedded)
            return SecFetchResult(
                text=text, url=_absolute_sec_url(base, filename) if filename else submission_url,
                status="succeeded", stage="complete_submission", errors=tuple(errors),
            )
        return SecFetchResult(
            status="missing", stage="complete_submission", errors=tuple(errors))
    if page and not index_had_primary:
        return SecFetchResult(status="missing", stage="filing_index", errors=tuple(errors))
    return SecFetchResult(status="unreachable", stage=(errors[-1].stage if errors else "sec"),
                          errors=tuple(errors))


def _detailed_exhibit(cik: str, accession: str, form_type: str = "") -> SecFetchResult:
    """Honor simple two-tuple monkeypatches while production uses staged status."""
    try:
        result = exhibit_text(cik, accession, detailed=True, form_type=form_type)
    except TypeError:
        result = exhibit_text(cik, accession)
    if isinstance(result, SecFetchResult):
        return result
    text, url = result
    return SecFetchResult(text=text, url=url,
                          status="succeeded" if text else "missing", stage="compatibility")


def _filing_metadata_failure(results) -> SecFetchFailure | None:
    if getattr(results, "status", "") != "unreachable":
        return None
    return SecFetchFailure(
        stage="filing_metadata",
        url=str(getattr(results, "source_uri", "") or ""),
        error_type="MetadataSourceError",
        message=str(getattr(results, "error", "") or "filing metadata unavailable"),
    )


def _company_submissions(cik: str):
    """Fetch and parse SEC's authoritative recent filing-history payload."""
    import json

    if not str(cik).isdigit():
        return {}, "missing", ()
    url = f"{SUBMISSIONS}/CIK{int(cik):010d}.json"
    body, errors = _request_text(url, stage="company_submissions")
    if not body:
        return {}, "unreachable", errors
    try:
        recent = (json.loads(body).get("filings") or {}).get("recent") or {}
    except (json.JSONDecodeError, AttributeError) as exc:
        failure = SecFetchFailure(
            "company_submissions", url, type(exc).__name__, str(exc))
        return {}, "unreachable", (*errors, failure)
    return recent, "succeeded", errors


def _ticker_cik(symbol: str) -> tuple[str, str, tuple[SecFetchFailure, ...]]:
    """Resolve a US-listed ticker to CIK from SEC, independent of third-party mirrors."""
    import json

    body, errors = _request_text(COMPANY_TICKERS, stage="company_tickers")
    if not body:
        return "", "unreachable", errors
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        failure = SecFetchFailure(
            "company_tickers", COMPANY_TICKERS, type(exc).__name__, str(exc))
        return "", "unreachable", (*errors, failure)
    wanted = (symbol or "").upper().strip()
    for item in payload.values() if isinstance(payload, dict) else ():
        if str((item or {}).get("ticker") or "").upper() != wanted:
            continue
        cik = str((item or {}).get("cik_str") or "")
        if cik.isdigit():
            return cik, "succeeded", errors
    return "", "missing", errors


def issuer_filing_regime(cik: str) -> tuple[str, str, tuple[SecFetchFailure, ...]]:
    """Return regime, acquisition status and errors from actual SEC filing history."""
    recent, status, errors = _company_submissions(cik)
    if status != "succeeded":
        return "unknown", status, errors
    regime = _filing_regime_from_forms(recent.get("form") or [])
    return regime, "succeeded" if regime != "unknown" else "missing", errors


def _submission_filing_metadata(cik: str, *, symbol: str, forms: tuple[str, ...],
                                near: str, window_days: int):
    """Discover recent accessions from SEC when the third-party index lags."""
    from datetime import date, timedelta

    from . import defeatbeta

    recent, submissions_status, errors = _company_submissions(cik)
    if submissions_status != "succeeded":
        return [], submissions_status, errors
    try:
        pivot = date.fromisoformat(near[:10])
    except ValueError:
        pivot = None
    lower = pivot - timedelta(days=window_days) if pivot else None
    upper = pivot + timedelta(days=window_days) if pivot else None
    keys = ("accessionNumber", "filingDate", "reportDate", "form", "items",
            "primaryDocument")
    columns = {key: recent.get(key) or [] for key in keys}
    required = (columns["accessionNumber"], columns["filingDate"], columns["form"])
    count = min((len(column) for column in required), default=0)
    values = []
    for index in range(count):
        value = lambda key: columns[key][index] if index < len(columns[key]) else ""  # noqa: E731
        accession, filing_date, report_date, form_type, items, primary = (
            str(value(key) or "") for key in keys)
        if form_type not in forms:
            continue
        try:
            filed = date.fromisoformat(filing_date[:10])
        except ValueError:
            continue
        if lower and not lower <= filed <= upper:
            continue
        base, _ = _filing_base(cik, accession)
        values.append(defeatbeta.Filing(
            symbol=symbol, cik=str(cik), accession=accession, form_type=form_type,
            filing_date=filing_date, url=_absolute_sec_url(base, primary),
            report_date=report_date, items=items, primary_document=primary,
        ))
    values.sort(key=lambda item: item.filing_date, reverse=True)
    return values, "succeeded" if values else "missing", errors


def earnings_release_result(symbol: str, *, near: str = "",
                            period: str = "") -> SecRecordResult:
    """Strict event-bound release plus explicit missing/unreachable acquisition state."""
    from datetime import date

    from ..config import canonical_entity, entity_meta
    from . import defeatbeta, fiscal
    from .admission import mentions_entity

    if not near:
        log.info("sec: refusing unanchored latest-filing lookup for %s", symbol)
        return SecRecordResult(status="missing", stage="event_anchor")
    errors: list[SecFetchFailure] = []
    # SEC is authoritative and first: ticker -> CIK -> submissions metadata. The
    # defeatbeta parquet is only a fallback index when SEC metadata is unavailable.
    cik, _cik_status, cik_errors = _ticker_cik(symbol)
    errors.extend(cik_errors)
    hits = []
    if cik:
        official, official_status, official_errors = _submission_filing_metadata(
            cik, symbol=symbol, forms=defeatbeta.EARNINGS_FORMS,
            near=near, window_days=4)
        errors.extend(official_errors)
        if official_status == "succeeded":
            hits = official
    if not hits:
        mirror_hits = defeatbeta.filings(symbol, near=near)
        metadata_failure = _filing_metadata_failure(mirror_hits)
        if metadata_failure:
            errors.append(metadata_failure)
        else:
            hits = list(mirror_hits)
        # SEC ticker mapping and the mirror can fail independently. If only the
        # ticker map failed, reuse a mirror CIK to retry authoritative submissions.
        mirror_cik = next((item.cik for item in hits if str(item.cik).isdigit()), "")
        if mirror_cik and not cik and any(
                not getattr(item, "primary_document", "") for item in hits):
            official, official_status, official_errors = _submission_filing_metadata(
                mirror_cik, symbol=symbol, forms=defeatbeta.EARNINGS_FORMS,
                near=near, window_days=4)
            errors.extend(official_errors)
            if official_status == "succeeded":
                hits = official
    had_unreachable = bool(errors and not hits)
    for filing in hits:
        if (canonical_entity(filing.symbol).upper() != canonical_entity(symbol).upper()
                or not str(filing.cik).isdigit()):
            log.warning("sec: rejected filing identity/CIK %s %s for %s",
                        filing.symbol, filing.cik, symbol)
            continue
        if (filing.form_type.upper() == "8-K" and getattr(filing, "items", "")
                and "2.02" not in {item.strip() for item in filing.items.split(",")}):
            log.info("sec: skipped non-earnings 8-K %s items=%s",
                     filing.accession, filing.items)
            continue
        fetched = _detailed_exhibit(filing.cik, filing.accession, filing.form_type)
        errors.extend(fetched.errors)
        had_unreachable = had_unreachable or fetched.status == "unreachable"
        if not fetched.text:
            continue
        text, url = fetched.text, fetched.url
        company = entity_meta(symbol).get("name", "")
        if not mentions_entity(text, symbol, company):
            log.warning("sec: %s exhibit %s failed issuer identity", symbol, filing.accession)
            continue
        low = text[:12000].lower()
        earnings_hits = sum(marker in low for marker in (
            "financial results", "quarter ended", "revenue", "net income",
            "earnings per share", "guidance", "preliminary results of operations",
            "operating profit", "profit for the period", "quarterly results",
        ))
        if earnings_hits < 2:
            log.warning("sec: %s exhibit %s lacks earnings semantics", symbol, filing.accession)
            continue
        if period:
            period_ok, period_reason = fiscal.verify_release_period(
                period, text, url, event_date=near)
            if not period_ok:
                log.warning("sec: %s exhibit %s rejected: %s",
                            symbol, filing.accession, period_reason)
                continue
        try:
            filed = date.fromisoformat(filing.filing_date[:10])
        except ValueError:
            filed = None
        record = {
            "label": f"SEC {filing.form_type} earnings release ({filing.filing_date})",
            "text": text, "filed": filed, "source_url": url,
            "accession": filing.accession, "cik": filing.cik,
            "form_type": filing.form_type,
            "document_role": "company_release",
            "claimed_period": period,
        }
        return SecRecordResult(record=record, status="succeeded", stage=fetched.stage,
                               errors=tuple(errors), discovered=len(hits))
    return SecRecordResult(
        status="unreachable" if had_unreachable else "missing",
        stage=errors[-1].stage if errors else ("filing_metadata" if not hits else "exhibit"),
        errors=tuple(errors), discovered=len(hits),
    )


def earnings_release_record(symbol: str, *, near: str = "",
                            period: str = "") -> dict | None:
    """Compatibility wrapper returning only a validated record or None."""
    return earnings_release_result(symbol, near=near, period=period).record


def periodic_filing_result(symbol: str, *, near: str = "",
                           period: str = "") -> SecRecordResult:
    """Fetch the event-bound domestic or foreign periodic regulatory document."""
    from datetime import date

    from ..config import canonical_entity, entity_meta
    from . import defeatbeta, fiscal
    from .admission import mentions_entity

    if not near or not period:
        return SecRecordResult(status="missing", stage="event_anchor")
    _, quarter = fiscal.parse_label(period)
    domestic_forms = ("10-K",) if quarter == 4 else ("10-Q",)
    forms = domestic_forms
    window_days = 45 if quarter == 4 else 14
    regime = "unknown"
    discovery_errors: list[SecFetchFailure] = []
    hits = []

    cik, _cik_status, cik_errors = _ticker_cik(symbol)
    discovery_errors.extend(cik_errors)
    if cik:
        history_regime, _history_status, history_errors = issuer_filing_regime(cik)
        discovery_errors.extend(history_errors)
        regime = history_regime if history_regime != "unknown" else "domestic"
        if regime.startswith("foreign"):
            if quarter == 4:
                forms = (("40-F",) if regime == "foreign_40f" else
                         ("20-F",) if regime == "foreign_20f" else
                         ("20-F", "40-F"))
                window_days = 150
            else:
                forms = ("6-K",)
                window_days = 60
        official, _official_status, official_errors = _submission_filing_metadata(
            cik, symbol=symbol, forms=forms, near=near, window_days=window_days)
        discovery_errors.extend(official_errors)
        hits = official

    if not hits:
        # Only now consult the mirror. It may still supply a CIK/accession while one
        # SEC metadata endpoint is unavailable, but it is not a normal dependency.
        mirror_hits = defeatbeta.filings(
            symbol, forms=forms, near=near, window_days=window_days)
        metadata_failure = _filing_metadata_failure(mirror_hits)
        if metadata_failure:
            discovery_errors.append(metadata_failure)
        else:
            hits = list(mirror_hits)
    if not hits:
        identity_hits = defeatbeta.filings(
            symbol, forms=defeatbeta.EARNINGS_FORMS, near=near, window_days=14)
        identity_failure = _filing_metadata_failure(identity_hits)
        if identity_failure:
            discovery_errors.append(identity_failure)
            identity_hits = []
        fallback_cik = cik or next(
            (item.cik for item in identity_hits if str(item.cik).isdigit()), "")
        if regime == "unknown" and fallback_cik:
            history_regime, _history_status, history_errors = issuer_filing_regime(fallback_cik)
            discovery_errors.extend(history_errors)
            if history_regime != "unknown":
                regime = history_regime
        if regime == "unknown" and any(
                item.form_type.upper() == "6-K" for item in identity_hits):
            # The recent history payload may not include an older 20-F/40-F, but a
            # 6-K identity filing is itself sufficient to rule out domestic 10-Q/K.
            regime = "foreign"
        if regime.startswith("foreign"):
            if quarter == 4:
                forms = (("40-F",) if regime == "foreign_40f" else
                         ("20-F",) if regime == "foreign_20f" else
                         ("20-F", "40-F"))
                window_days = 150
            else:
                forms = ("6-K",)
                # Foreign interim reports are often furnished weeks after the press
                # release, as TSM does; the event remains the lower-bound anchor.
                window_days = 60
        fallback, fallback_status, fallback_errors = _submission_filing_metadata(
            fallback_cik, symbol=symbol, forms=forms, near=near, window_days=window_days)
        discovery_errors.extend(fallback_errors)
        if fallback_status == "unreachable" and not hits:
            return SecRecordResult(status="unreachable", stage="company_submissions",
                                   errors=tuple(discovery_errors),
                                   discovered=len(identity_hits))
        hits = fallback
    if regime == "unknown":
        regime = "domestic"
    try:
        pivot = date.fromisoformat(near[:10])
    except ValueError:
        pivot = None
    if pivot:
        hits.sort(key=lambda item: abs((date.fromisoformat(item.filing_date[:10]) - pivot).days)
                  if item.filing_date else 9999)
    errors: list[SecFetchFailure] = list(discovery_errors)
    had_unreachable = bool(errors and not hits)
    allowed_forms = set(forms)
    for filing in hits:
        if (canonical_entity(filing.symbol).upper() != canonical_entity(symbol).upper()
                or not str(filing.cik).isdigit()
                or filing.form_type.upper() not in allowed_forms):
            log.warning("sec: rejected periodic filing identity/form %s %s for %s",
                        filing.symbol, filing.form_type, symbol)
            continue
        if filing.form_type.upper() == "6-K":
            fetched = foreign_regulatory_result(filing.cik, filing.accession)
        else:
            fetched = primary_filing_result(
                filing.cik, filing.accession, filing.form_type,
                primary_url=filing.url,
            )
        errors.extend(fetched.errors)
        had_unreachable = had_unreachable or fetched.status == "unreachable"
        if not fetched.text:
            continue
        company = entity_meta(symbol).get("name", "")
        if not mentions_entity(fetched.text, symbol, company):
            log.warning("sec: %s filing %s failed issuer identity", symbol, filing.accession)
            continue
        # Inline-XBRL filings can begin with tens of thousands of characters of
        # contexts and taxonomy members before the human-readable cover page. The
        # AMD 2026-Q2 10-Q places "10-Q" at character 20,127, so a 16k prefix check
        # rejected the exact primary document selected by SEC metadata.
        if filing.form_type.upper() != "6-K":
            searchable = fetched.text.lower()
            form_marker = filing.form_type.lower()
            if form_marker not in searchable and "quarterly report" not in searchable and \
                    "annual report" not in searchable:
                log.warning("sec: %s filing %s lacks form semantics", symbol, filing.accession)
                continue
        try:
            filed = date.fromisoformat(filing.filing_date[:10])
        except ValueError:
            filed = None
        record = {
            "label": f"SEC {filing.form_type} filing ({filing.filing_date})",
            "text": fetched.text, "filed": filed, "source_url": fetched.url,
            "accession": filing.accession, "cik": filing.cik,
            "form_type": filing.form_type, "report_date": filing.report_date,
            "filing_regime": regime,
            "document_role": "regulatory_filing",
            # Event binding comes from exact symbol/accession/report-date metadata;
            # prose period regexes are too weak for annual filings and ixbrl tables.
            "claimed_period": period,
        }
        return SecRecordResult(record=record, status="succeeded", stage=fetched.stage,
                               errors=tuple(errors), discovered=len(hits))
    return SecRecordResult(
        status="unreachable" if had_unreachable else "missing",
        stage=errors[-1].stage if errors else ("filing_metadata" if not hits else "primary_document"),
        errors=tuple(errors), discovered=len(hits),
    )


def earnings_release(symbol: str, *, near: str = "", period: str = "") -> tuple[str, str, str]:
    """(text, url, note) for the earnings release nearest `near` (the print date).

    `near` matters: a company files hundreds of 8-Ks, and only the one landing on the
    print date is the release. Without it "latest 8-K" returns director changes and
    shelf registrations — which is exactly the class of error the keyed sources were
    adopted to eliminate, so reintroducing it here would be self-defeating.
    """
    record = earnings_release_record(symbol, near=near, period=period)
    if record is None:
        return "", "", f"未找到 {near or '近期'} 附近的 8-K/6-K 备案"
    return record["text"], record["source_url"], (
        f"{record['form_type']} 财报稿 · 备案日 {record['filed']}"
        f" · accession {record['accession']}")
