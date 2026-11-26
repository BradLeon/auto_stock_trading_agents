"""Registration filings (S-1, S-1/A, 424B4) of a newly listed issuer.

Two ways in, one admission path:

* a file the operator already has on disk (`ingest_file`);
* a one-off backfill of the issuer's filings from SEC EDGAR (`backfill`).

Only issuers declared under `registration_issuers` in config/data/unstructured.yaml are handled. The daily SEC filing
discovery never looks for these forms, and the documents carry their own semantic (`registration_statement`), so they
are never read as earnings material and never open an earnings event.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ....config import REPO_ROOT, canonical_entity
from ... import admission, sec_rate
from ... import sec as extractor
from ...document_types import DocumentSemantic
from ...persistent_queue import require_queue_worker
from ...runtime.repository import platform_data_db_path
from ...sources import sec_submissions
from ...stores.unstructured import (
    get_platform_unstructured_repository,
    get_platform_unstructured_store,
)
from .document_ingest import _SCHEMA, _known_ciks, _publish_document, _record_row, _rows
from .sec import SECSourceUnavailable, SECTransport

FORMS = ("S-1", "S-1/A", "424B4")          # also the order of two filings of the same day: the later one wins
LOCAL_SOURCE_ID = "local_registration_file"
SEC_SOURCE_ID = "sec_registration_filing"
SOURCE_IDS = (SEC_SOURCE_ID, LOCAL_SOURCE_ID)
LOCAL_PROVIDER = "本地提供"
MIN_BODY_CHARS = 20_000                    # a prospectus runs to hundreds of thousands of characters
EXHIBITS_ONLY_MAX_CHARS = 100_000
_SNAPSHOT_ID = "registration_filing"
_TICKER_CACHE = REPO_ROOT / "var/cache/sec_company_tickers.json"
_COVER_CHARS = 12_000
_PROSPECTUS_COVER_CHARS = 5_000
_INLINE_TAGS = "span|font|b|i|u|em|strong|sup|sub|a|small|big|ix:nonnumeric|ix:nonfraction"
_FILED_ON = re.compile(
    r"As\s+filed\s+with\s+the\s+(?:U\.?\s?S\.?\s+)?Securities\s+and\s+Exchange\s+Commission\s+on\s+"
    r"([A-Za-z]+)\s+(\d{1,2})\s*,\s*(\d{4})", re.IGNORECASE)
_REGISTRANT_MARKER = re.compile(r"\(\s*Exact\s+name\s+of\s+registrant", re.IGNORECASE)
_RULE_424B4 = re.compile(r"424\s*\(\s*b\s*\)\s*\(\s*4\s*\)", re.IGNORECASE)
_AMENDMENT = re.compile(r"Amendment\s+No\.?\s*\d+", re.IGNORECASE)
_FORM_S1 = re.compile(r"\bForm\s*S\s*-\s*1\b", re.IGNORECASE)
_EXHIBITS_ONLY = re.compile(r"sole(?:ly)?\s+(?:for\s+the\s+)?(?:purpose\s+of\s+filing|to\s+file)\b.{0,300}?exhibit",
                            re.IGNORECASE | re.DOTALL)


def issuers() -> dict[str, dict[str, Any]]:
    """Issuers whose registration filings are handled, with the legal name their cover must show."""
    import yaml

    registry = yaml.safe_load((REPO_ROOT / "config/data/unstructured.yaml").read_text(encoding="utf-8")) or {}
    return {str(symbol).upper(): dict(row or {})
            for symbol, row in (registry.get("registration_issuers") or {}).items()}


def _issuer(entity: str) -> tuple[str, str]:
    symbol = canonical_entity(entity).upper()
    declared = issuers().get(symbol)
    if not declared or not str(declared.get("registrant_name") or "").strip():
        raise ValueError(f"registration_issuer_not_declared:{symbol}")
    return symbol, str(declared["registrant_name"]).strip()


def filing_text(raw: str) -> str:
    """Readable text of a registration filing.

    Unlike the periodic-filing extractor, inline tags are removed without leaving a space (these documents split words
    and numbers across styled spans) and HTML entities are decoded.
    """
    body = re.sub(r"(?is)<(script|style|ix:header)\b.*?</\1>", " ", raw or "")
    body = re.sub(rf"(?is)</?(?:{_INLINE_TAGS})(?=[\s/>])[^>]*>", "", body)
    body = html.unescape(re.sub(r"(?s)<[^>]+>", " ", body)).replace("\u200b", "")
    return re.sub(r"\s+", " ", body).strip()


def _normal_name(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()


def _long_date(month: str, day: str, year: str) -> str:
    try:
        stamp = datetime.strptime(f"{month.title()} {day} {year}", "%B %d %Y").replace(tzinfo=UTC)
        return date(stamp.year, stamp.month, stamp.day).isoformat()
    except ValueError:
        return ""


@dataclass(frozen=True)
class Filing:
    """What a file says about itself: its body, its cover and, for a full submission, its SEC header."""

    text: str
    registrant: str          # the name found on the cover; "" when none was found
    registrant_ok: bool
    form_ok: bool
    cover_date: str
    declared_type: str       # <TYPE> of the SGML wrapper, when there is one
    header_date: str         # "FILED AS OF DATE" of a full submission
    accession: str
    complete: bool


def read_filing(raw: str, *, form: str, registrant_name: str) -> Filing:
    head_raw = raw[:20_000]
    accession = re.search(r"ACCESSION NUMBER:\s*(\d{10}-\d{2}-\d{6})", head_raw)
    filed = re.search(r"FILED AS OF DATE:\s*(\d{4})(\d{2})(\d{2})", head_raw)
    body, declared_type = raw, ""
    if re.search(r"<DOCUMENT>", raw, re.IGNORECASE):
        documents = extractor._submission_documents(raw)
        chosen = [item for item in documents if item[0] == form] or documents[:1]
        if chosen:
            declared_type = chosen[0][0]
            body = max(chosen, key=lambda item: len(item[3]))[3]
    text = filing_text(body)
    expected = _normal_name(registrant_name)
    if form == "424B4":
        cover = text[:_PROSPECTUS_COVER_CHARS]
        found = f" {expected} " in f" {_normal_name(cover)} "
        registrant, registrant_ok = (registrant_name if found else ""), found
        form_ok = bool(_RULE_424B4.search(cover))
    else:
        cover = text[:_COVER_CHARS]
        marker = _REGISTRANT_MARKER.search(cover)
        registrant, registrant_ok = "", False
        if marker:
            parts = re.split(r"(?i)\bACT\s+OF\s+1933\b", cover[:marker.start()])
            registrant = parts[-1].strip(" _-–—")[-120:]
            name = _normal_name(registrant)
            # Between "...Act of 1933" and "(Exact name of registrant" there is only the name.
            registrant_ok = name == expected if len(parts) > 1 else f" {name}".endswith(f" {expected}")
        amended = bool(_AMENDMENT.search(cover[:marker.start()] if marker else cover[:3000]))
        form_ok = bool(_FORM_S1.search(cover)) and amended == (form == "S-1/A")
    if declared_type and declared_type != form:
        form_ok = False
    stamp = _FILED_ON.search(text[:_COVER_CHARS])
    tail = raw[-2000:].lower()
    return Filing(
        text=text, registrant=registrant, registrant_ok=registrant_ok, form_ok=form_ok,
        cover_date=_long_date(*stamp.groups()) if stamp else "",
        declared_type=declared_type,
        header_date="-".join(filed.groups()) if filed else "",
        accession=accession.group(1) if accession else "",
        complete=any(mark in tail for mark in ("</html>", "</document>", "</sec-document>")),
    )


def _exhibits_only(filing: Filing, form: str) -> bool:
    """An amendment filed only to add exhibits carries no prospectus; it must not become the latest filing."""
    return (form == "S-1/A" and filing.registrant_ok and filing.form_ok and filing.complete
            and len(filing.text) < EXHIBITS_ONLY_MAX_CHARS and bool(_EXHIBITS_ONLY.search(filing.text)))


def _issues(filing: Filing, *, form: str, registrant_name: str) -> list[admission.ValidationIssue]:
    issues = []
    if not filing.registrant_ok:
        found = filing.registrant or "no registrant name on the cover"
        issues.append(admission.ValidationIssue("identity", "identity_mismatch", f"{found} != {registrant_name}"))
    if not filing.form_ok:
        issues.append(admission.ValidationIssue(
            "type", "form_type_mismatch", f"cover or wrapper ({filing.declared_type or '-'}) is not {form}"))
    if not filing.complete:
        issues.append(admission.ValidationIssue("completeness", "completeness_truncated"))
    return issues


# --------------------------------------------------------------------------- #
# Version chain
# --------------------------------------------------------------------------- #
def _held(conn: Any, entity: str) -> list[dict[str, Any]]:
    """Accepted registration filings of `entity`, oldest first; one entry per document."""
    try:
        rows = conn.execute(
            "SELECT r.source_id,r.natural_key,r.raw_json,r.document_id FROM data_fixed_source_rows r "
            "JOIN data_documents d ON d.document_id=r.document_id AND d.ok=1 "
            "WHERE r.source_id IN (?,?) AND r.entity=? AND r.status='accepted' ORDER BY r.observed_at",
            (*SOURCE_IDS, entity)).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        return []                                          # nothing was ever ingested into this store
    documents: dict[str, dict[str, Any]] = {}
    for source_id, natural_key, raw_json, document_id in rows:
        row = json.loads(raw_json)
        documents[document_id] = {
            "entity": entity, "document_id": document_id, "source_id": source_id, "natural_key": natural_key,
            "form_type": row["form_type"], "filing_date": row["filing_date"],
            "accession_number": row.get("accession_number", ""), "provided_by": row.get("provided_by", ""),
            "raw_sha256": row.get("raw_sha256", ""), "source_url": row.get("filing_url", ""),
        }
    return sorted(documents.values(), key=lambda item: (
        item["filing_date"], FORMS.index(item["form_type"]), item["accession_number"]))


def registration_filings(entity: str, *, as_of: date | None = None, repository: Any = None) -> list[dict[str, Any]]:
    """The issuer's registration filings filed on or before `as_of`, oldest first, each naming what it supersedes."""
    repo = repository or get_platform_unstructured_repository()
    try:
        chain = _held(repo.conn, canonical_entity(entity).upper())
    finally:
        if repository is None:
            repo.close()
    if as_of is not None:
        chain = [item for item in chain if item["filing_date"] <= as_of.isoformat()]
    return [{**item, "supersedes": chain[i - 1]["document_id"] if i else ""} for i, item in enumerate(chain)]


def latest_registration_filing(entity: str, *, as_of: date | None = None,
                               repository: Any = None) -> dict[str, Any] | None:
    chain = registration_filings(entity, as_of=as_of, repository=repository)
    return chain[-1] if chain else None


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #
@contextmanager
def _writer(source_id: str, store: Any, now: datetime) -> Iterator[Any]:
    """The target store, with the same production rules as the other document jobs: queue lease and managed root."""
    store_path = getattr(store, "path", None)
    production = store is None or bool(store_path and Path(store_path).resolve() == platform_data_db_path().resolve())
    if production:
        require_queue_worker(source_id)
    previous_root = os.environ.get("ATS_DOCS_ROOT")
    own = store is None
    store = store or get_platform_unstructured_store()
    if production:
        os.environ["ATS_DOCS_ROOT"] = str(REPO_ROOT / "var/data/fixed_documents")
    try:
        store.conn.executescript(_SCHEMA)
        source = _rows()[source_id]
        store.register_data_source(SimpleNamespace(
            id=source_id, label=source["provider"], adapter=source["adapter"],
            cadence=source["cadence"], entity=""), at=now)
        yield store
    finally:
        if previous_root is None:
            os.environ.pop("ATS_DOCS_ROOT", None)
        else:
            os.environ["ATS_DOCS_ROOT"] = previous_root
        if own:
            store.close()


def _admit(store: Any, *, source_id: str, entity: str, form: str, filing: Filing, natural_key: str,
           external_id: str, filing_date: str, source_url: str, row: dict[str, Any],
           issues: list[admission.ValidationIssue], now: datetime) -> dict[str, Any]:
    row = {**row, "symbol": entity, "form_type": form, "filing_date": filing_date,
           "company_name": filing.registrant, "cover_filing_date": filing.cover_date,
           "document_semantic": DocumentSemantic.REGISTRATION_STATEMENT.value,
           "body_sha256": hashlib.sha256(filing.text.encode("utf-8")).hexdigest()}
    candidate = admission.CandidateDocument(
        expected_entity=entity, claimed_entity=entity, target_period="", claimed_period="",
        expected_semantic=DocumentSemantic.REGISTRATION_STATEMENT.value,
        claimed_semantic=DocumentSemantic.REGISTRATION_STATEMENT.value,
        text=filing.text, source=source_id, source_url=source_url, external_id=external_id,
        title=f"{entity} {form} registration statement filed {filing_date or 'unknown'}",
        published_at=filing_date, carrier_format="html", min_chars=MIN_BODY_CHARS,
        metadata={key: row.get(key, "") for key in ("accession_number", "form_type", "provided_by", "raw_sha256")})
    status, document_id = _publish_document(store, candidate, source_issues=issues)
    reasons = [issue.code for issue in issues]
    if status == "quarantined" and len(filing.text) < MIN_BODY_CHARS:
        reasons.append("body_too_short")
    _record_row(store, source_id=source_id, snapshot_id=_SNAPSHOT_ID, entity=entity, natural_key=natural_key,
                row=row, status="accepted" if status == "no_change" else status, document_id=document_id,
                publication_hash=candidate.content_hash, reason=",".join(reasons), now=now)
    return {"status": "succeeded" if status == "accepted" else status, "document_id": document_id,
            "form_type": form, "filing_date": filing_date, "registrant": filing.registrant,
            "reason_codes": reasons, "reason_details": [issue.detail for issue in issues if issue.detail]}


def ingest_file(entity: str, form: str, path: str | Path, *, filing_date: str = "", accession: str = "",
                store: Any = None, now: datetime | None = None) -> dict[str, Any]:
    """Admit a registration filing the operator has on disk (HTML, or a full SEC submission text file)."""
    symbol, registrant_name = _issuer(entity)
    form = form.strip().upper()
    if form not in FORMS:
        raise ValueError(f"registration_form_not_supported:{form}")
    if filing_date:
        filing_date = date.fromisoformat(filing_date).isoformat()
    if accession and not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
        raise ValueError("registration_accession_invalid")
    path = Path(path).expanduser()
    payload = path.read_bytes()
    raw_sha256 = hashlib.sha256(payload).hexdigest()
    now = now or datetime.now(UTC)
    packet = {"source_id": LOCAL_SOURCE_ID, "entity": symbol, "form_type": form,
              "provided_by": LOCAL_PROVIDER, "raw_sha256": raw_sha256, "raw_bytes": len(payload)}
    with _writer(LOCAL_SOURCE_ID, store, now) as target:
        held = _held(target.conn, symbol)
        for item in held:
            if item["raw_sha256"] == raw_sha256 and item["form_type"] == form:
                return {**packet, "status": "no_change", "reason_codes": ["same_file_already_held"],
                        "document_id": item["document_id"], "filing_date": item["filing_date"]}
        filing = read_filing(payload.decode("utf-8", errors="replace"), form=form, registrant_name=registrant_name)
        issues = _issues(filing, form=form, registrant_name=registrant_name)
        stated = {value for value in (filing_date, filing.cover_date, filing.header_date) if value}
        if not stated:
            issues.append(admission.ValidationIssue("period", "filing_date_unresolved"))
        elif len(stated) > 1:
            issues.append(admission.ValidationIssue("period", "filing_date_mismatch", ", ".join(sorted(stated))))
        if accession and filing.accession and accession != filing.accession:
            issues.append(admission.ValidationIssue(
                "identity", "accession_mismatch", f"{filing.accession} != {accession}"))
        accession = accession or filing.accession
        resolved = filing.header_date or filing.cover_date or filing_date
        if not issues:
            for item in held:
                if (accession and item["accession_number"] == accession) or (
                        (item["form_type"], item["filing_date"]) == (form, resolved)):
                    return {**packet, "status": "no_change", "reason_codes": ["same_filing_already_held"],
                            "document_id": item["document_id"], "filing_date": resolved}
        run_id = target.begin_ingestion(LOCAL_SOURCE_ID, kind="registration_filing", at=now)
        try:
            result = _admit(
                target, source_id=LOCAL_SOURCE_ID, entity=symbol, form=form, filing=filing,
                natural_key=accession or raw_sha256, external_id=f"local:{raw_sha256}:{form}",
                filing_date=resolved, source_url="", issues=issues, now=now,
                row={"accession_number": accession, "provided_by": LOCAL_PROVIDER, "raw_sha256": raw_sha256,
                     "raw_bytes": len(payload), "file_name": path.name})
        except Exception as exc:
            target.finish_ingestion(run_id, status="failed", discovered=1,
                                    reason_codes={type(exc).__name__: 1}, at=now)
            raise
        target.finish_ingestion(
            run_id, status=result["status"], discovered=1, accepted=int(result["status"] == "succeeded"),
            quarantined=int(result["status"] == "quarantined"),
            reason_codes={code: 1 for code in result["reason_codes"]}, at=now)
    return {**packet, **result}


def _backfill_one(target: Any, row: dict[str, Any], *, symbol: str, registrant_name: str,
                  transport: Any, now: datetime) -> dict[str, Any]:
    accession, form, filed, url = row["accession_number"], row["form_type"], row["filing_date"], row["filing_url"]
    if not re.search(r"\.(?:html?|txt)$", url, re.IGNORECASE):
        return {"status": "failed", "reason_codes": ["registration_primary_document_missing"]}
    body, errors = transport.for_filing(row)(url, stage="primary_document")
    if not body:
        return {"status": "failed", "reason_codes": sorted({error.error_type for error in errors}) or ["empty_body"]}
    filing = read_filing(body, form=form, registrant_name=registrant_name)
    if _exhibits_only(filing, form):
        _record_row(target, source_id=SEC_SOURCE_ID, snapshot_id=_SNAPSHOT_ID, entity=symbol, natural_key=accession,
                    row=row, status="not_applicable", reason="registration_amendment_exhibits_only", now=now)
        return {"status": "not_applicable", "reason_codes": ["registration_amendment_exhibits_only"]}
    payload = body.encode("utf-8")
    # The filing date is the one SEC's listing states; a cover dated the evening before is only recorded.
    return _admit(
        target, source_id=SEC_SOURCE_ID, entity=symbol, form=form, filing=filing, natural_key=accession,
        external_id=f"sec:{accession}:registration_statement", filing_date=filed, source_url=url, now=now,
        issues=_issues(filing, form=form, registrant_name=registrant_name),
        row={**row, "provided_by": "SEC EDGAR", "raw_sha256": hashlib.sha256(payload).hexdigest(),
             "raw_bytes": len(payload)})


def backfill(entity: str, *, store: Any = None, now: datetime | None = None,
             http: Callable[..., tuple[int, bytes]] | None = None,
             wait: Callable[[], None] | None = None, transport: Any = None) -> dict[str, Any]:
    """One-off collection of a declared issuer's registration filings from SEC EDGAR."""
    symbol, registrant_name = _issuer(entity)
    now = now or datetime.now(UTC)
    with _writer(SEC_SOURCE_ID, store, now) as target:
        budget = _rows()[SEC_SOURCE_ID]["request_budget"]
        run_id = target.begin_ingestion(SEC_SOURCE_ID, kind="registration_filing", at=now)
        outcomes: list[dict[str, Any]] = []
        failure = ""
        try:
            requests = sec_submissions._Requests(
                int(budget["max_index_requests_per_run"]), timeout=float(budget["timeout_seconds"]),
                http=http or sec_submissions._http_get, wait=wait or sec_rate.wait)
            cik = sec_submissions.resolve_ciks(
                [symbol], known=_known_ciks(target), requests=requests,
                cache=_TICKER_CACHE).get(symbol)
            if not cik:
                raise ValueError("registration_cik_unresolved")
            listing = requests.json(f"{sec_submissions.SUBMISSIONS}/CIK{int(cik):010d}.json")
            if _normal_name(listing.get("name")) != _normal_name(registrant_name):
                # The ticker resolved to somebody else: stop before reading any of their filings.
                raise ValueError(f"registration_cik_name_mismatch:{listing.get('name')}")
            rows = sorted(sec_submissions.recent_filings(symbol, cik, listing, since=date.min, forms=FORMS),
                          key=lambda item: (item["filing_date"], FORMS.index(item["form_type"]),
                                            item["accession_number"]))
            fetched = 0
            for row in rows:
                accession, form, filed = row["accession_number"], row["form_type"], row["filing_date"]
                held = _held(target.conn, symbol)
                same = [item for item in held if item["accession_number"] == accession] or [
                    item for item in held if item["source_id"] == LOCAL_SOURCE_ID and
                    (item["form_type"], item["filing_date"]) == (form, filed)]
                if same:
                    result = {"status": "no_change", "document_id": same[0]["document_id"],
                              "reason_codes": ["same_filing_already_held"]}
                elif target.conn.execute(
                        "SELECT 1 FROM data_fixed_source_rows WHERE source_id=? AND natural_key=? AND "
                        "status='not_applicable'", (SEC_SOURCE_ID, accession)).fetchone():
                    result = {"status": "not_applicable", "reason_codes": ["registration_amendment_exhibits_only"]}
                elif fetched >= int(budget["max_documents_per_run"]):
                    result = {"status": "deferred", "reason_codes": ["document_budget"]}
                else:
                    transport = transport or SECTransport(budget)
                    fetched += 1
                    result = _backfill_one(target, row, symbol=symbol, registrant_name=registrant_name,
                                           transport=transport, now=now)
                outcomes.append({**result, "accession_number": accession, "form_type": form, "filing_date": filed})
        except (SECSourceUnavailable, ValueError) as exc:
            failure = str(exc)
        counts = {state: sum(item["status"] == state for item in outcomes) for state in
                  ("succeeded", "no_change", "quarantined", "not_applicable", "failed", "deferred")}
        if failure:
            status = "failed"
        elif counts["failed"] or counts["deferred"]:
            status = "partial" if counts["succeeded"] else "failed"
        elif counts["quarantined"]:
            status = "partial" if counts["succeeded"] else "quarantined"
        else:
            status = "succeeded" if counts["succeeded"] else "no_change"
        reasons: dict[str, int] = {}
        for code in [failure] if failure else [c for item in outcomes for c in item.get("reason_codes", [])]:
            reasons[code] = reasons.get(code, 0) + 1
        target.finish_ingestion(run_id, status=status, discovered=len(outcomes), accepted=counts["succeeded"],
                                quarantined=counts["quarantined"], reason_codes=reasons, at=now)
        held = _held(target.conn, symbol)
    return {"source_id": SEC_SOURCE_ID, "entity": symbol, "status": status, "reason": failure, "counts": counts,
            "filings": outcomes, "latest": held[-1] if held else None}
