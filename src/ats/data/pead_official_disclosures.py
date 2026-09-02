"""Event-bound official earnings-document packages for active PEAD targets.

This module is deliberately an orchestration layer.  SEC retrieval, transcript
normalisation and candidate admission remain their own source-specific contracts;
the package makes their common event binding and independent role results visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from .earnings_events import EarningsEvent, EventResolution, resolve_latest_event

_ROLES = ("earnings_release", "regulatory_filing", "earnings_transcript")
_RELEASE_GRACE_DAYS = 3
_FILING_GRACE_DAYS = 45
_TRANSCRIPT_GRACE_DAYS = 7


@dataclass(frozen=True)
class DisclosureRoleResult:
    role: str
    status: str
    reason_codes: tuple[str, ...] = ()
    document_id: str = ""
    source: str = ""
    source_url: str = ""
    published_at: str = ""
    metadata: Mapping[str, str] | None = None

    def as_dict(self) -> dict:
        return {
            "role": self.role, "status": self.status,
            "reason_codes": list(self.reason_codes), "document_id": self.document_id,
            "source": self.source, "source_url": self.source_url,
            "published_at": self.published_at, "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class OfficialDisclosurePackage:
    entity: str
    event_resolution: EventResolution
    roles: tuple[DisclosureRoleResult, ...]
    checked_at: str

    @property
    def event(self) -> EarningsEvent | None:
        return self.event_resolution.event

    @property
    def complete(self) -> bool:
        return self.event is not None and all(role.status == "accepted" for role in self.roles)

    def as_dict(self) -> dict:
        event = self.event
        return {
            "entity": self.entity,
            "checked_at": self.checked_at,
            "event": {
                "status": self.event_resolution.status,
                "event_id": event.event_id if event else "",
                "report_date": str(event.report_date) if event else "",
                "fiscal_label": event.fiscal_label if event else "",
                "conflicts": [item.__dict__ for item in self.event_resolution.conflicts],
                "unresolved_fields": list(self.event_resolution.unresolved_fields),
            },
            "roles": [role.as_dict() for role in self.roles],
            "complete": self.complete,
        }


def active_pead_targets() -> list[str]:
    """The one authoritative scope for official PEAD-package collection."""
    from ..config import load_pead_global

    return [str(symbol).upper() for symbol in load_pead_global().get("targets", [])]


def _now_date(now: datetime | date | None) -> date:
    if isinstance(now, datetime):
        return now.date()
    return now or datetime.now(timezone.utc).date()


def _missing_status(role: str, event: EarningsEvent, *, now: date, source_status: str) -> str:
    if source_status == "unreachable":
        return "unreachable"
    age_days = max(0, (now - event.report_date).days)
    grace = {
        "earnings_release": _RELEASE_GRACE_DAYS,
        "periodic_filing": _FILING_GRACE_DAYS,
        "regulatory_filing": _FILING_GRACE_DAYS,
        "earnings_transcript": _TRANSCRIPT_GRACE_DAYS,
    }[role]
    return "not_yet_available" if age_days <= grace else "missing"


def _admit_sec_record(symbol: str, event: EarningsEvent, role: str, record: dict, *, store):
    """Persist one SEC record through the central admission contract."""
    from ..config import entity_meta
    from . import admission, documents, fiscal
    from .document_types import infer_carrier_format

    text = str(record.get("text") or "")
    source_url = str(record.get("source_url") or "")
    claimed_period = str(record.get("claimed_period") or "")
    company = entity_meta(symbol).get("name", "")
    filed = record.get("filed")
    published = str(filed or record.get("report_date") or "")[:10]
    candidate = admission.CandidateDocument(
        expected_entity=symbol,
        claimed_entity=symbol if admission.mentions_entity(text, symbol, company) else "",
        target_period=event.fiscal_label,
        claimed_period=claimed_period,
        expected_semantic=("company_release" if role == "earnings_release"
                           else "regulatory_filing"),
        claimed_semantic=str(record.get("document_role") or ""),
        text=text,
        source="sec",
        source_url=source_url,
        external_id=str(record.get("accession") or source_url),
        title=str(record.get("label") or f"SEC {role}"),
        published_at=published,
        carrier_format=infer_carrier_format(source_url),
        completeness="full",
        min_chars=1000,
        related_entities=(symbol,),
        metadata={
            "official_domains": documents._official_domains(symbol),
            "form_type": str(record.get("form_type") or ""),
            "cik": str(record.get("cik") or ""),
            "report_date": str(record.get("report_date") or ""),
            "filing_regime": str(record.get("filing_regime") or ""),
        },
    )
    def release_period_issue(_candidate):
        if role != "earnings_release":
            return ()
        ok, reason = fiscal.verify_release_period(
            event.fiscal_label, text, source_url, event_date=event.report_date)
        if ok:
            return ()
        code = "period_unresolved" if "unresolved" in reason else "period_mismatch"
        return (admission.ValidationIssue("period", code, reason),)

    outcome = admission.admit(
        candidate, extensions=(documents.official_document_issues, release_period_issue),
        store=store)
    if not outcome.validation.accepted or outcome.document is None:
        return DisclosureRoleResult(
            role, "quarantined", outcome.validation.reason_codes, source="sec",
            source_url=source_url, published_at=published,
            metadata={"form_type": str(record.get("form_type") or ""),
                      "accession": str(record.get("accession") or "")},
        )
    store.save_document_alias(
        outcome.document.document_id, source=f"sec_metadata:{role}",
        source_url=source_url, external_id=str(record.get("accession") or source_url),
        title=candidate.title, published_at=published,
        metadata={
            "cik": str(record.get("cik") or ""),
            "form_type": str(record.get("form_type") or ""),
            "report_date": str(record.get("report_date") or ""),
            "filing_regime": str(record.get("filing_regime") or ""),
            "claimed_period": claimed_period,
        },
    )
    return DisclosureRoleResult(
        role, "accepted", document_id=outcome.document.document_id, source="sec",
        source_url=source_url, published_at=published,
        metadata={"form_type": str(record.get("form_type") or ""),
                  "accession": str(record.get("accession") or ""),
                  "cik": str(record.get("cik") or "")},
    )


def _collect_sec_role(symbol: str, event: EarningsEvent, role: str, *, store, fetcher, now: date):
    from . import documents

    result = fetcher(symbol, near=str(event.report_date), period=event.fiscal_label)
    if result.record is None:
        documents._record_sec_run(symbol, role, result, accepted=False, store=store)
        return DisclosureRoleResult(
            role, _missing_status(role, event, now=now, source_status=result.status),
            tuple(f"{failure.stage}:{failure.error_type}" for failure in result.errors),
            source="sec", metadata={"stage": result.stage},
        )
    admitted = _admit_sec_record(symbol, event, role, result.record, store=store)
    documents._record_sec_run(symbol, role, result,
                              accepted=admitted.status == "accepted", store=store)
    return admitted


def _record_transcript_run(symbol: str, outcome: DisclosureRoleResult, *, store) -> None:
    source = type("TranscriptSource", (), {
        "id": f"defeatbeta_transcript:{symbol.upper()}",
        "label": "DefeatBeta earnings transcript",
        "adapter": "defeatbeta.transcript", "cadence": "quarterly",
        "entity": symbol.upper(),
    })()
    store.register_data_source(source, kind="unstructured")
    run_id = store.begin_ingestion(source.id, kind="unstructured")
    store.finish_ingestion(
        run_id, status=("succeeded" if outcome.status == "accepted" else outcome.status),
        discovered=1 if outcome.status in {"accepted", "quarantined"} else 0,
        accepted=1 if outcome.status == "accepted" else 0,
        quarantined=1 if outcome.status == "quarantined" else 0,
        reason_codes=list(outcome.reason_codes), note="; ".join(outcome.reason_codes),
    )


def _collect_transcript(symbol: str, event: EarningsEvent, *, store, fetcher, now: date):
    from . import admission, defeatbeta
    from .document_types import CarrierFormat

    structured = fetcher(
        symbol, fiscal_year=event.fiscal_year, fiscal_quarter=event.fiscal_quarter)
    if structured is None:
        outcome = DisclosureRoleResult(
            "earnings_transcript",
            _missing_status("earnings_transcript", event, now=now, source_status="missing"),
            ("transcript_not_returned",), source="defeatbeta",
        )
        _record_transcript_run(symbol, outcome, store=store)
        return outcome

    candidate = admission.CandidateDocument(
        expected_entity=symbol, claimed_entity=str(structured.symbol),
        target_period=event.fiscal_label, claimed_period=structured.label,
        expected_semantic="earnings_transcript", claimed_semantic="earnings_transcript",
        text=structured.text, source="defeatbeta",
        source_url=f"defeatbeta:{structured.symbol}:{structured.report_date}",
        external_id=f"defeatbeta:{structured.symbol}:{structured.report_date}",
        title=f"{symbol.upper()} {event.fiscal_label} earnings call transcript",
        published_at=str(structured.report_date),
        carrier_format=CarrierFormat.STRUCTURED_TEXT, completeness="full", min_chars=2000,
        related_entities=(symbol,), metadata={"paragraphs": structured.paragraphs},
    )
    outcome = admission.admit(
        candidate, extensions=(defeatbeta.structured_transcript_issues,), store=store)
    if not outcome.validation.accepted or outcome.document is None:
        result = DisclosureRoleResult(
            "earnings_transcript", "quarantined", outcome.validation.reason_codes,
            source="defeatbeta", source_url=candidate.source_url,
            published_at=candidate.published_at,
        )
    else:
        raw_path = defeatbeta.save_structure(structured, outcome.document)
        store.save_document_candidate(
            candidate, outcome.validation, raw_path=str(raw_path or ""),
            document_id=outcome.document.document_id,
        )
        result = DisclosureRoleResult(
            "earnings_transcript", "accepted", document_id=outcome.document.document_id,
            source="defeatbeta", source_url=candidate.source_url,
            published_at=candidate.published_at,
            metadata={"snapshot_updated_at": str(
                getattr(getattr(structured, "snapshot", None), "updated_at", ""))},
        )
    _record_transcript_run(symbol, result, store=store)
    return result


def collect_latest_package(
        symbol: str, *, store=None, now: datetime | date | None = None,
        event_resolver: Callable | None = None, release_fetcher: Callable | None = None,
        filing_fetcher: Callable | None = None, transcript_fetcher: Callable | None = None,
        config_label: str = "") -> OfficialDisclosurePackage:
    """Collect/revalidate one PEAD issuer's latest event-bound disclosure package."""
    from .stores.unstructured import get_data_ingestion_store
    from . import defeatbeta, sec

    store = store or get_data_ingestion_store()
    checked = _now_date(now)
    event_resolver = event_resolver or resolve_latest_event
    resolution = event_resolver(
        symbol, store=store, as_of=checked, config_label=config_label)
    entity = (resolution.event.entity if resolution.event else symbol.upper())
    if not resolution.resolved or resolution.event is None:
        reasons = tuple(["event_" + resolution.status, *resolution.unresolved_fields])
        return OfficialDisclosurePackage(
            entity, resolution,
            tuple(DisclosureRoleResult(role, "quarantined", reasons) for role in _ROLES),
            datetime.combine(checked, datetime.min.time(), timezone.utc).isoformat(),
        )

    event = resolution.event
    release = _collect_sec_role(
        entity, event, "earnings_release", store=store,
        fetcher=release_fetcher or sec.earnings_release_result, now=checked)
    filing = _collect_sec_role(
        entity, event, "periodic_filing", store=store,
        fetcher=filing_fetcher or sec.periodic_filing_result, now=checked)
    # Public role name is regulatory_filing while the historical SEC ingestion id
    # remains periodic_filing for compatibility with the existing health reports.
    filing = DisclosureRoleResult(
        "regulatory_filing", filing.status, filing.reason_codes, filing.document_id,
        filing.source, filing.source_url, filing.published_at, filing.metadata)
    transcript = _collect_transcript(
        entity, event, store=store, fetcher=transcript_fetcher or defeatbeta.fetch,
        now=checked)
    return OfficialDisclosurePackage(
        entity, resolution, (release, filing, transcript),
        datetime.combine(checked, datetime.min.time(), timezone.utc).isoformat(),
    )


def collect_active_packages(*, store=None, now: datetime | date | None = None,
                            symbols: list[str] | None = None, **kwargs) -> list[OfficialDisclosurePackage]:
    """Continue the bounded acceptance sweep when one issuer fails unexpectedly."""
    packages = []
    for symbol in symbols or active_pead_targets():
        try:
            packages.append(collect_latest_package(symbol, store=store, now=now, **kwargs))
        except Exception as exc:  # noqa: BLE001 - one issuer cannot hide the rest
            # Avoid claiming a data state we did not obtain.  This package is an
            # orchestration failure, not a document-source "missing" result.
            from .earnings_events import EventEvidence

            resolution = EventResolution(
                "unresolved", None, (),
                (EventEvidence("entity", reference=f"entity:{symbol.upper()}"),),
                ("orchestration",),
            )
            reasons = ("orchestration_error", type(exc).__name__)
            packages.append(OfficialDisclosurePackage(
                symbol.upper(), resolution,
                tuple(DisclosureRoleResult(role, "quarantined", reasons) for role in _ROLES),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ))
    return packages


def render_acceptance_markdown(packages: list[OfficialDisclosurePackage]) -> str:
    """Human-reviewable companion to the machine-readable package results."""
    lines = [
        "# PEAD 官方披露完整性与准确性验收报告",
        "",
        f"生成时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "## 总览",
        "",
        "| 标的 | 最新事件 | Earnings release | Regulatory filing | Call transcript | 完整 |",
        "|---|---|---|---|---|---|",
    ]
    for package in packages:
        event = package.event
        label = (f"{event.fiscal_label} / {event.report_date}" if event else
                 f"{package.event_resolution.status}: "
                 f"{', '.join(package.event_resolution.unresolved_fields)}")
        roles = {role.role: role.status for role in package.roles}
        lines.append(
            f"| {package.entity} | {label} | {roles.get('earnings_release', '—')} | "
            f"{roles.get('regulatory_filing', '—')} | "
            f"{roles.get('earnings_transcript', '—')} | "
            f"{'yes' if package.complete else 'no'} |")
    for package in packages:
        lines.extend(["", f"## {package.entity}"])
        event = package.event
        if event:
            lines.append(f"- Event: `{event.event_id}`; disclosed `{event.report_date}`; `{event.fiscal_label}`.")
        else:
            lines.append("- Event: unresolved; no document role is treated as accepted.")
        if package.event_resolution.conflicts:
            lines.append("- Conflicts: " + "; ".join(
                f"{item.field} ({item.anchor_source}={item.anchor_value}; "
                f"{item.conflicting_source}={item.conflicting_value})"
                for item in package.event_resolution.conflicts))
        if package.event_resolution.unresolved_fields:
            lines.append("- Event gaps: " + ", ".join(package.event_resolution.unresolved_fields))
        lines.extend(["", "| Role | Status | Provenance / checks |", "|---|---|---|"])
        for role in package.roles:
            meta = ", ".join(f"{key}={value}" for key, value in (role.metadata or {}).items()
                             if value)
            provenance = " · ".join(part for part in (
                role.source, role.published_at, meta, role.source_url,
                ", ".join(role.reason_codes),
            ) if part) or "—"
            lines.append(f"| {role.role} | {role.status} | {provenance} |")
    return "\n".join(lines) + "\n"


def write_acceptance_report(path: str | Path, packages: list[OfficialDisclosurePackage]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_acceptance_markdown(packages), encoding="utf-8")
    return target


__all__ = [
    "DisclosureRoleResult", "OfficialDisclosurePackage", "active_pead_targets",
    "collect_active_packages", "collect_latest_package", "render_acceptance_markdown",
    "write_acceptance_report",
]
