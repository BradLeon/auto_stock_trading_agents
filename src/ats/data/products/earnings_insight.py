"""Typed, report-consistent FactSet Earnings Insight data product."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
import json
from typing import Any

from pydantic import BaseModel, Field


DATASET_ID = "sp500_earnings_insight"
SOURCE_ID = "factset_earnings_insight_metrics"
FRESHNESS_DAYS = 10
_FAILURE_STATES = {
    "unreachable", "unauthorized", "not_pdf", "parse_failed",
    "validation_failed",
}


class EarningsInsightEvidence(BaseModel):
    anchor_kind: str
    page_number: int = 0
    char_start: int = 0
    char_end: int = 0
    chart_id: str = ""
    region: tuple[float, ...] = ()
    document_id: str = ""
    version_id: str = ""


class EarningsInsightObservation(BaseModel):
    observation_id: str
    entity_id: str
    metric_id: str
    period: str
    period_basis: str
    estimate_state: str = "not_applicable"
    value: float
    unit: str
    known_at: datetime
    dimensions: dict[str, Any] = Field(default_factory=dict)
    quality_status: str = "accepted"
    evidence: list[EarningsInsightEvidence] = Field(default_factory=list)


class EarningsInsightReport(BaseModel):
    report_date: date | None = None
    document_id: str = ""
    version_id: str = ""
    artifact_id: str = ""
    official_url: str = ""
    known_at: datetime | None = None
    source_name: str = "FactSet Research Systems Inc."
    usage: str = "internal_only"
    retention: str = "licensed_internal_research"
    copyright_notice: str = "Copyright FactSet Research Systems Inc. All rights reserved."


class EarningsInsightPartitionStatus(BaseModel):
    state: str = "registered_no_data"
    release_id: str = ""
    extractor_version: str = ""
    passed: bool = False
    quality: dict[str, Any] = Field(default_factory=dict)


class EarningsInsightStatus(BaseModel):
    state: str = "registered_no_data"
    freshness: str = "unavailable"
    age_days: int | None = None
    index_release: EarningsInsightPartitionStatus = Field(
        default_factory=EarningsInsightPartitionStatus)
    sector_release: EarningsInsightPartitionStatus = Field(
        default_factory=EarningsInsightPartitionStatus)
    latest_refresh_failure: str = ""
    warnings: list[str] = Field(default_factory=list)


class EarningsInsightLineage(BaseModel):
    selected_observation_ids: list[str] = Field(default_factory=list)
    known_at_values: list[datetime] = Field(default_factory=list)
    release_versions: dict[str, str] = Field(default_factory=dict)


class EarningsInsightSnapshot(BaseModel):
    report: EarningsInsightReport = Field(default_factory=EarningsInsightReport)
    index: dict[str, dict[str, EarningsInsightObservation]] = Field(
        default_factory=dict)
    sectors: dict[str, dict[str, dict[str, EarningsInsightObservation]]] = Field(
        default_factory=dict)
    status: EarningsInsightStatus = Field(default_factory=EarningsInsightStatus)
    lineage: EarningsInsightLineage = Field(default_factory=EarningsInsightLineage)


def _json(value: str, default):
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _partition(manifest: dict | None, *, absent_state: str) -> EarningsInsightPartitionStatus:
    if manifest is None:
        return EarningsInsightPartitionStatus(state=absent_state)
    return EarningsInsightPartitionStatus(
        state=str(manifest.get("status") or "shadow"),
        release_id=str(manifest.get("release_id") or ""),
        extractor_version=str(manifest.get("extractor_version") or ""),
        passed=bool(manifest.get("passed")),
        quality=dict(manifest.get("quality") or {}),
    )


def _observation(products, observation_id: str) -> EarningsInsightObservation | None:
    row = products.structured.observation(observation_id)
    if row is None:
        return None
    dimensions = _json(row.get("dimensions_json", ""), {})
    evidence = []
    for anchor in products.structured.evidence_links(observation_id=observation_id):
        region = tuple(_json(anchor.get("region_json", ""), []))
        evidence.append(EarningsInsightEvidence(
            anchor_kind=anchor.get("anchor_kind") or "text_span",
            page_number=int(anchor.get("page_number") or 0),
            char_start=int(anchor.get("char_start") or 0),
            char_end=int(anchor.get("char_end") or 0),
            chart_id=anchor.get("chart_id") or "",
            region=region,
            document_id=anchor.get("document_id") or "",
            version_id=anchor.get("version_id") or "",
        ))
    return EarningsInsightObservation(
        observation_id=observation_id,
        entity_id=row["entity_id"], metric_id=row["metric_id"],
        period=row["period"], period_basis=row["period_basis"],
        estimate_state=str(dimensions.pop("estimate_state", "not_applicable")),
        value=float(row["value"]), unit=row["unit"],
        known_at=datetime.fromisoformat(row["known_at"]),
        dimensions=dimensions, quality_status=row.get("quality_status") or "accepted",
        evidence=evidence,
    )


def _latest_failure(repository, release: dict | None) -> str:
    history = repository.ingestion_history(
        source_id=SOURCE_ID, dataset_id=DATASET_ID, limit=20)
    cutoff = str((release or {}).get("known_at") or "")
    for attempt in history:
        if cutoff and str(attempt.get("started_at") or "") <= cutoff:
            break
        status = str(attempt.get("status") or "")
        if status in _FAILURE_STATES:
            return status
    return ""


def _empty_snapshot(products, *, as_of: datetime | None) -> EarningsInsightSnapshot:
    history = products.structured.ingestion_history(
        source_id=SOURCE_ID, dataset_id=DATASET_ID, limit=1)
    failure = (str(history[0].get("status") or "")
               if history and history[0].get("status") in _FAILURE_STATES else "")
    registered = any(row.get("dataset_id") == DATASET_ID
                     for row in products.structured.datasets())
    state = "unavailable" if failure or not registered else "registered_no_data"
    warning = [f"latest_refresh_failure:{failure}"] if failure else []
    return EarningsInsightSnapshot(status=EarningsInsightStatus(
        state=state, freshness="unavailable", latest_refresh_failure=failure,
        warnings=warning,
        index_release=EarningsInsightPartitionStatus(state=state),
        sector_release=EarningsInsightPartitionStatus(state=state)))


def load_snapshot(products, *, as_of: datetime | None = None,
                  version_id: str = "") -> EarningsInsightSnapshot:
    """Read one released report without fetching or opening physical tables."""
    reference = as_of or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        raise ValueError("FactSet snapshot as_of must be timezone-aware")
    manifests = products.structured.release_manifests(
        dataset_id=DATASET_ID, partition="index_core", as_of=as_of,
        passed_only=True, limit=500)
    index_release = next((item for item in manifests
                          if not version_id or item["version_id"] == version_id), None)
    if index_release is None:
        return _empty_snapshot(products, as_of=as_of)

    sector_release = next((item for item in products.structured.release_manifests(
        dataset_id=DATASET_ID, partition="sector_core", as_of=as_of,
        passed_only=True, limit=500)
        if item["version_id"] == index_release["version_id"]), None)
    selected_ids = list(index_release.get("observation_ids") or [])
    if sector_release:
        selected_ids.extend(sector_release.get("observation_ids") or [])
    selected_ids = list(dict.fromkeys(selected_ids))

    index: dict[str, dict[str, EarningsInsightObservation]] = {}
    sectors: dict[str, dict[str, dict[str, EarningsInsightObservation]]] = {}
    selected: list[EarningsInsightObservation] = []
    for observation_id in selected_ids:
        item = _observation(products, observation_id)
        if item is None:
            continue
        selected.append(item)
        if item.entity_id == "SP500":
            index.setdefault(item.period, {})[item.metric_id] = item
        elif item.entity_id.startswith("GICS_"):
            sectors.setdefault(item.entity_id, {}).setdefault(
                item.period, {})[item.metric_id] = item

    report_date = date.fromisoformat(index_release["report_date"])
    age_days = max(0, (reference.astimezone(timezone.utc).date() - report_date).days)
    failure = _latest_failure(products.structured, index_release)
    freshness = "stale" if age_days > FRESHNESS_DAYS or failure else "fresh"
    state = "stale" if freshness == "stale" else str(index_release["status"])
    warnings: list[str] = []
    if age_days > FRESHNESS_DAYS:
        warnings.append(f"report_age_exceeds_{FRESHNESS_DAYS}_days")
    if failure:
        warnings.append(f"latest_refresh_failure:{failure}")
    if sector_release is None:
        warnings.append("sector_partition_unavailable_for_selected_report")

    official_url = ""
    artifacts = products.structured.artifacts_for(
        dataset_id=DATASET_ID, limit=500)
    for artifact in artifacts:
        if artifact.get("artifact_id") != index_release["artifact_id"]:
            continue
        official_url = str(artifact.get("source_url") or "")
        break
    document = products.unstructured.documents_by_id(
        [index_release["document_id"]]).get(index_release["document_id"], {})
    official_url = official_url or str(document.get("source_url") or "")

    return EarningsInsightSnapshot(
        report=EarningsInsightReport(
            report_date=report_date, document_id=index_release["document_id"],
            version_id=index_release["version_id"],
            artifact_id=index_release["artifact_id"], official_url=official_url,
            known_at=datetime.fromisoformat(index_release["known_at"])),
        index=index, sectors=sectors,
        status=EarningsInsightStatus(
            state=state, freshness=freshness, age_days=age_days,
            index_release=_partition(index_release, absent_state="unavailable"),
            sector_release=_partition(
                sector_release, absent_state="registered_no_data"),
            latest_refresh_failure=failure, warnings=warnings),
        lineage=EarningsInsightLineage(
            selected_observation_ids=[item.observation_id for item in selected],
            known_at_values=sorted({item.known_at for item in selected}),
            release_versions={
                "index_core": index_release["extractor_version"],
                **({"sector_core": sector_release["extractor_version"]}
                   if sector_release else {}),
            }))


def available_vintages(products, *, as_of: datetime | None = None,
                       limit: int = 500) -> list[EarningsInsightSnapshot]:
    manifests = products.structured.release_manifests(
        dataset_id=DATASET_ID, partition="index_core", as_of=as_of,
        passed_only=True, limit=limit)
    return [load_snapshot(products, as_of=as_of, version_id=row["version_id"])
            for row in manifests]


def operational_status(products, *, as_of: datetime | None = None,
                       limit: int = 20) -> dict:
    """Partition-aware source validation, release, health and lineage report."""
    snapshot = load_snapshot(products, as_of=as_of)
    attempts = products.structured.ingestion_history(
        source_id=SOURCE_ID, dataset_id=DATASET_ID, limit=limit)
    partitions = {}
    for partition in ("index_core", "sector_core"):
        releases = products.structured.release_manifests(
            dataset_id=DATASET_ID, partition=partition, as_of=as_of,
            passed_only=False, limit=limit)
        partitions[partition] = {
            "latest_attempt": releases[0] if releases else None,
            "latest_passing": next((row for row in releases if row["passed"]), None),
        }
    registered = any(row.get("dataset_id") == DATASET_ID
                     for row in products.structured.datasets())
    evidence_counts = {"text_span": 0, "image_region": 0}
    for observation_id in snapshot.lineage.selected_observation_ids:
        for anchor in products.structured.evidence_links(
                observation_id=observation_id):
            kind = anchor.get("anchor_kind") or "text_span"
            evidence_counts[kind] = evidence_counts.get(kind, 0) + 1
    return {
        "source_validation": {
            "registered": registered, "source_id": SOURCE_ID,
            "dataset_id": DATASET_ID},
        "release_check": partitions,
        "health": {
            "latest_attempt": attempts[0] if attempts else None,
            "latest_attempt_failure": snapshot.status.latest_refresh_failure,
            "snapshot_state": snapshot.status.state,
            "freshness": snapshot.status.freshness,
        },
        "lineage": {
            "selected_observation_ids": snapshot.lineage.selected_observation_ids,
            "evidence_counts": evidence_counts,
            "release_versions": snapshot.lineage.release_versions,
        },
        "snapshot_manifest": {
            "report_date": (snapshot.report.report_date.isoformat()
                            if snapshot.report.report_date else None),
            "document_id": snapshot.report.document_id,
            "version_id": snapshot.report.version_id,
            "artifact_id": snapshot.report.artifact_id,
            "observation_ids": snapshot.lineage.selected_observation_ids,
            "warnings": snapshot.status.warnings,
        },
        "attempts": attempts,
    }


def to_earnings_backdrop(snapshot: EarningsInsightSnapshot):
    """One-way DTO compatibility mapping; never reparses the source document."""
    from ...schemas.macro_strategy import EarningsBackdrop

    if not snapshot.index:
        return EarningsBackdrop(
            source=snapshot.report.official_url, report_date=snapshot.report.report_date,
            degraded=True, notes=list(snapshot.status.warnings) or [snapshot.status.state])
    flat = [item for metrics in snapshot.index.values() for item in metrics.values()]

    def pick(metric_id: str):
        choices = [item for item in flat if item.metric_id == metric_id]
        return max(choices, key=lambda item: (item.known_at, item.period), default=None)

    def value(metric_id: str, *, percent: bool = False):
        item = pick(metric_id)
        if item is None:
            return None
        return item.value * 100 if percent else item.value

    growth = pick("earnings.eps.yoy_growth")
    revision = pick("earnings.revision.improved_sector_count")
    guidance_negative = pick("earnings.guidance.negative_count")
    comparison = str((revision.dimensions if revision else {}).get(
        "comparison_date") or "")
    comparison_label = comparison
    if comparison:
        try:
            comparison_label = date.fromisoformat(comparison).strftime("%B %-d")
        except ValueError:
            pass

    def quarter(period: str) -> str:
        return f"Q{period[-1]} {period[:4]}" if len(period) == 6 and period[4] == "Q" else period

    return EarningsBackdrop(
        source=snapshot.report.official_url or snapshot.report.version_id,
        report_date=snapshot.report.report_date,
        quarter=quarter(growth.period) if growth else "",
        growth_pct=value("earnings.eps.yoy_growth", percent=True),
        growth_basis=growth.estimate_state if growth else "",
        sectors_higher=(int(revision.value) if revision else None),
        revision_direction=str((revision.dimensions if revision else {}).get(
            "revision_direction") or ""),
        prior_as_of=comparison_label,
        guidance_quarter=quarter(guidance_negative.period) if guidance_negative else "",
        guidance_negative=(int(guidance_negative.value) if guidance_negative else None),
        guidance_positive=(int(value("earnings.guidance.positive_count"))
                           if value("earnings.guidance.positive_count") is not None else None),
        pct_reported=value("earnings.reporting.coverage", percent=True),
        pct_eps_beat=value("earnings.eps.above_estimate_share", percent=True),
        pct_revenue_beat=value("earnings.revenue.above_estimate_share", percent=True),
        fwd_pe=value("valuation.forward_pe"),
        fwd_pe_5y_avg=value("valuation.forward_pe.average_5y"),
        fwd_pe_10y_avg=value("valuation.forward_pe.average_10y"),
        degraded=False, notes=list(snapshot.status.warnings))


__all__ = [
    "EarningsInsightEvidence", "EarningsInsightLineage",
    "EarningsInsightObservation", "EarningsInsightPartitionStatus",
    "EarningsInsightReport", "EarningsInsightSnapshot", "EarningsInsightStatus",
    "available_vintages", "load_snapshot", "operational_status",
    "to_earnings_backdrop",
]
