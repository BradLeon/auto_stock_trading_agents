"""Central candidate-to-asset admission gate for unstructured documents."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping

from . import fiscal, source_cache
from .document_types import CarrierFormat, DocumentSemantic, semantic_type

_UNRESOLVED = {"", "TODO", "TBD", "UNKNOWN", "N/A", "NONE", "LATEST"}


@dataclass(frozen=True)
class CandidateDocument:
    expected_entity: str
    claimed_entity: str
    target_period: str
    claimed_period: str
    expected_semantic: str | DocumentSemantic
    claimed_semantic: str | DocumentSemantic
    text: str
    source: str
    source_url: str = ""
    external_id: str = ""
    title: str = ""
    published_at: str = ""
    carrier_format: str | CarrierFormat = CarrierFormat.PLAIN_TEXT
    completeness: str = "full"
    min_chars: int = 1
    allow_partial: bool = False
    related_entities: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    discovered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def content_hash(self) -> str:
        return hashlib.sha256((self.text or "").strip().encode("utf-8")).hexdigest()

    @property
    def candidate_id(self) -> str:
        identity = self.external_id or self.source_url or self.title
        # One physical filing can legitimately serve several business roles (for
        # example SK hynix's 6-K is both earnings release and interim regulatory
        # report). Role is therefore part of candidate identity even when accession
        # and bytes are identical.
        raw = "|".join((self.source, identity, str(self.expected_semantic),
                        self.content_hash))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class ValidationIssue:
    category: str
    code: str
    detail: str = ""


@dataclass(frozen=True)
class ValidationResult:
    status: str
    issues: tuple[ValidationIssue, ...]
    checks: Mapping[str, bool]

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


ValidationExtension = Callable[[CandidateDocument], Iterable[ValidationIssue]]


def _unresolved(value: object) -> bool:
    return str(value or "").strip().upper() in _UNRESOLVED


def _period(value: str) -> tuple[int, int] | None:
    if _unresolved(value):
        return None
    year, quarter = fiscal.parse_label(value)
    return (year, quarter) if year is not None and quarter is not None else None


def mentions_entity(text: str, symbol: str, company_name: str = "") -> bool:
    """Conservatively verify issuer identity near the document opening."""
    head = re.sub(r"https?://\S+|\[[^\]]*\]\([^)]*\)", " ", (text or "")[:40000]).lower()
    # Inline-XBRL can put its issuer cover page well after taxonomy contexts.  The
    # first 40k characters are still an opening-document check, while 8k was too
    # short to identify COHR's valid 10-K.
    head = re.sub(r"\s+", " ", head)

    def hit(needle: str) -> bool:
        return re.search(rf"(?<![a-z0-9_-]){re.escape(needle)}(?![a-z0-9_-])", head) is not None

    ticker = (symbol or "").lower()
    if ticker and (hit(ticker) or hit(ticker.split(".")[0])):
        return True
    names = [company_name]
    try:
        from ..config import entity_meta

        meta = entity_meta(symbol)
        names.extend([meta.get("name", ""), *(meta.get("aliases", []) or [])])
    except Exception:  # pragma: no cover - identity checking must stay fail-closed
        pass
    normalized_names = []
    for value in names:
        name = re.sub(r"\s+", " ", re.sub(
            r"[^a-z0-9 ]+", " ", str(value or "").lower())).strip()
        if name and name not in normalized_names:
            normalized_names.append(name)
    if any(len(name.split()) > 1 and name in head for name in normalized_names):
        return True
    generic = {
        "inc", "corp", "ltd", "plc", "the", "and", "group", "co", "holding",
        "holdings", "technologies", "technology", "corporation", "company",
        "international", "electronics", "electronic", "semiconductor",
        "semiconductors", "advanced", "micro", "devices", "device", "systems",
        "system", "solutions", "digital", "global", "industries", "materials",
        "products", "research", "manufacturing", "instruments", "labs",
        "laboratories", "microelectronics", "limited", "sa", "nv", "ag",
    }
    return any(
        hit(token)
        for name in normalized_names
        for token in name.split()
        if len(token) >= 3 and token not in generic
    )


def validate_candidate(
    candidate: CandidateDocument,
    *,
    extensions: Iterable[ValidationExtension] = (),
) -> ValidationResult:
    """Apply every strong check and return all failures, never just the first."""
    from ..config import canonical_entity

    issues: list[ValidationIssue] = []
    checks: dict[str, bool] = {}

    expected_entity = canonical_entity(candidate.expected_entity).upper().strip()
    claimed_entity = canonical_entity(candidate.claimed_entity).upper().strip()
    identity_ok = True
    if _unresolved(expected_entity) or _unresolved(claimed_entity):
        issues.append(ValidationIssue("identity", "identity_unresolved",
                                      "expected or claimed entity is empty/placeholder"))
        identity_ok = False
    elif expected_entity != claimed_entity:
        issues.append(ValidationIssue(
            "identity", "identity_mismatch", f"{claimed_entity} != {expected_entity}"))
        identity_ok = False
    checks["identity"] = identity_ok

    target_period = _period(candidate.target_period)
    claimed_period = _period(candidate.claimed_period)
    period_ok = True
    if target_period is None or claimed_period is None:
        issues.append(ValidationIssue("period", "period_unresolved",
                                      "target or claimed fiscal year/quarter is unresolved"))
        period_ok = False
    elif target_period != claimed_period:
        issues.append(ValidationIssue(
            "period", "period_mismatch", f"{claimed_period} != {target_period}"))
        period_ok = False
    checks["period"] = period_ok

    type_ok = True
    try:
        expected_type = semantic_type(candidate.expected_semantic)
        claimed_type = semantic_type(candidate.claimed_semantic)
        if expected_type is not claimed_type:
            issues.append(ValidationIssue(
                "type", "type_mismatch", f"{claimed_type.value} != {expected_type.value}"))
            type_ok = False
    except (KeyError, ValueError):
        issues.append(ValidationIssue("type", "type_unresolved",
                                      "expected or claimed document semantic is unknown"))
        type_ok = False
    checks["type"] = type_ok

    body = (candidate.text or "").strip()
    completeness = (candidate.completeness or "").strip().lower()
    completeness_ok = True
    if not body:
        issues.append(ValidationIssue("completeness", "completeness_empty"))
        completeness_ok = False
    elif len(body) < candidate.min_chars:
        issues.append(ValidationIssue(
            "completeness", "completeness_too_short",
            f"{len(body)} < {candidate.min_chars}"))
        completeness_ok = False
    if completeness in {"", "unknown"}:
        issues.append(ValidationIssue("completeness", "completeness_unresolved"))
        completeness_ok = False
    elif completeness in {"partial", "teaser"} and not candidate.allow_partial:
        issues.append(ValidationIssue(
            "completeness", f"completeness_{completeness}"))
        completeness_ok = False
    elif completeness not in {"full", "partial", "teaser"}:
        issues.append(ValidationIssue("completeness", "completeness_unresolved",
                                      f"unknown status {completeness!r}"))
        completeness_ok = False
    checks["completeness"] = completeness_ok

    for extension in extensions:
        for issue in extension(candidate):
            issues.append(issue)
            checks[f"source:{issue.code}"] = False

    return ValidationResult(
        status="accepted" if not issues else "quarantined",
        issues=tuple(issues),
        checks=checks,
    )


@dataclass(frozen=True)
class AdmissionOutcome:
    candidate_id: str
    validation: ValidationResult
    document: object | None = None
    quarantine_path: Path | None = None


def _write_quarantine(candidate: CandidateDocument) -> Path | None:
    root = source_cache.root()
    if root is None or not candidate.text:
        return None
    folder = root / ".quarantine" / source_cache._slug(candidate.source or "unknown")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{candidate.candidate_id}.txt"
    if not path.exists():
        path.write_text(candidate.text, encoding="utf-8")
    return path


def admit(
    candidate: CandidateDocument,
    *,
    extensions: Iterable[ValidationExtension] = (),
    store=None,
) -> AdmissionOutcome:
    """Validate, audit the decision, and persist only accepted bodies to assets."""
    from .stores.unstructured import get_data_ingestion_store
    from . import document_assets

    store = store or get_data_ingestion_store()
    validation = validate_candidate(candidate, extensions=extensions)
    if not validation.accepted:
        path = _write_quarantine(candidate)
        store.save_document_candidate(candidate, validation, raw_path=str(path or ""))
        return AdmissionOutcome(candidate.candidate_id, validation, quarantine_path=path)

    semantic = semantic_type(candidate.expected_semantic)
    key = candidate.target_period or document_assets.stable_key(
        candidate.external_id or candidate.source_url or candidate.title,
        prefix="candidate",
    )
    document = document_assets.ingest(
        entity=candidate.expected_entity,
        key=key,
        doc_type=semantic.value,
        text=candidate.text,
        source=candidate.source,
        source_url=candidate.source_url,
        external_id=candidate.external_id,
        title=candidate.title,
        published_at=candidate.published_at,
        related_entities=candidate.related_entities,
        completeness=candidate.completeness,
        carrier_format=str(candidate.carrier_format),
        min_chars=candidate.min_chars,
        store=store,
    )
    if document is None:
        issue = ValidationIssue("persistence", "persistence_failed")
        validation = ValidationResult(
            status="quarantined",
            issues=validation.issues + (issue,),
            checks={**validation.checks, "persistence": False},
        )
        path = _write_quarantine(candidate)
        store.save_document_candidate(candidate, validation, raw_path=str(path or ""))
        return AdmissionOutcome(candidate.candidate_id, validation, quarantine_path=path)
    store.save_document_candidate(candidate, validation, document_id=document.document_id)
    return AdmissionOutcome(candidate.candidate_id, validation, document=document)


def result_json(result: ValidationResult) -> str:
    """Stable audit representation used by SQLite and quality reports."""
    return json.dumps(
        {
            "status": result.status,
            "checks": dict(result.checks),
            "issues": [issue.__dict__ for issue in result.issues],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
