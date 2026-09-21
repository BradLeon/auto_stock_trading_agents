"""Typed, report-consistent FactSet Earnings Insight data product."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
import json
import re
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


class EarningsInsightDiagnostic(BaseModel):
    """A deterministic relationship, with the source observations kept explicit."""

    diagnostic_id: str
    label: str
    value: float
    unit: str
    input_observation_ids: list[str] = Field(default_factory=list)


class EarningsInsightNarrativeEvidence(BaseModel):
    """One bounded excerpt selected from the stored report pages."""

    topic: str
    topic_label: str
    page_number: int
    section_title: str = ""
    char_start: int
    char_end: int
    text: str
    document_id: str = ""
    version_id: str = ""


class EarningsInsightAnalysisPacket(BaseModel):
    """Complete but bounded evidence prepared for Macro and Sector consumers."""

    report: EarningsInsightReport = Field(default_factory=EarningsInsightReport)
    status: EarningsInsightStatus = Field(default_factory=EarningsInsightStatus)
    observation_groups: dict[str, list[EarningsInsightObservation]] = Field(
        default_factory=dict)
    diagnostics: list[EarningsInsightDiagnostic] = Field(default_factory=list)
    narrative_evidence: list[EarningsInsightNarrativeEvidence] = Field(
        default_factory=list)
    sectors: dict[str, dict[str, dict[str, EarningsInsightObservation]]] = Field(
        default_factory=dict)
    lineage: EarningsInsightLineage = Field(default_factory=EarningsInsightLineage)

    @property
    def observation_count(self) -> int:
        return sum(len(items) for items in self.observation_groups.values())

    @property
    def index(self) -> dict[str, dict[str, EarningsInsightObservation]]:
        """Compatibility view for the narrow legacy EarningsBackdrop mapper."""
        periods: dict[str, dict[str, EarningsInsightObservation]] = {}
        for items in self.observation_groups.values():
            for item in items:
                periods.setdefault(item.period, {})[item.metric_id] = item
        return periods


_ANALYSIS_GROUPS = {
    "reporting_progress": {"earnings.reporting.coverage"},
    "earnings_revenue_surprises": {
        "earnings.eps.above_estimate_share", "earnings.eps.inline_estimate_share",
        "earnings.eps.below_estimate_share", "earnings.revenue.above_estimate_share",
        "earnings.revenue.inline_estimate_share", "earnings.revenue.below_estimate_share",
        "earnings.eps.surprise_pct", "earnings.revenue.surprise_pct",
    },
    "earnings_revenue_growth": {
        "earnings.eps.yoy_growth", "earnings.revenue.yoy_growth",
    },
    "profit_margin": {"earnings.net_profit_margin"},
    "company_guidance": {
        "earnings.guidance.positive_count", "earnings.guidance.negative_count",
    },
    "estimate_revision_breadth": {"earnings.revision.improved_sector_count"},
    "valuation": {
        "valuation.forward_pe", "valuation.trailing_pe",
        "valuation.forward_pe.average_5y", "valuation.forward_pe.average_10y",
        "valuation.trailing_pe.average_5y", "valuation.trailing_pe.average_10y",
    },
    "ratings_and_target_price": {
        "consensus.rating.buy_share", "consensus.rating.hold_share",
        "consensus.rating.sell_share", "consensus.target.upside",
    },
}

_NARRATIVE_TOPICS = (
    ("earnings_concentration", "盈利集中度",
     (r"exclud(?:e|ing)[^.]{0,220}(?:alphabet|amazon)",
      r"(?:alphabet|amazon)[^.]{0,220}(?:contribut|earnings growth)")),
    ("excluding_major_companies", "剔除主要公司后的增长",
     (r"exclud(?:e|ing)[^.]{0,260}(?:growth rate|earnings growth)",)),
    ("gaap_non_gaap", "GAAP 与 Non-GAAP 口径",
     (r"non[- ]gaap", r"gaap earnings")),
    ("sector_contribution", "行业贡献",
     (r"largest contributor", r"due to the [^.]{0,100} sector",
      r"sector was the largest")),
    ("margin_drivers", "利润率变化原因",
     (r"profit margin[^.]{0,260}(?:due to|because|driven|increase|decrease)",
      r"margin[^.]{0,180}(?:cost|expense|pricing)")),
    ("valuation_and_sentiment", "估值、评级和目标价背景",
     (r"forward 12-month p/e", r"buy ratings", r"target price")),
)


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


def _latest_index_observations(
        snapshot: EarningsInsightSnapshot) -> list[EarningsInsightObservation]:
    """Select one released value per index metric without inventing missing data."""
    by_metric: dict[str, EarningsInsightObservation] = {}
    for period_metrics in snapshot.index.values():
        for item in period_metrics.values():
            current = by_metric.get(item.metric_id)
            if current is None or (item.known_at, item.period) > (
                    current.known_at, current.period):
                by_metric[item.metric_id] = item
    return sorted(by_metric.values(), key=lambda item: (item.metric_id, item.period))


def _group_observations(
        observations: list[EarningsInsightObservation],
        warnings: list[str]) -> dict[str, list[EarningsInsightObservation]]:
    groups = {name: [] for name in _ANALYSIS_GROUPS}
    unknown: list[EarningsInsightObservation] = []
    for item in observations:
        group_name = next((name for name, metrics in _ANALYSIS_GROUPS.items()
                           if item.metric_id in metrics), "")
        if group_name:
            groups[group_name].append(item)
        else:
            unknown.append(item)
    if unknown:
        groups["other"] = unknown
        warnings.append("unclassified_index_metrics:" + ",".join(
            item.metric_id for item in unknown))
    return groups


def _diagnostics(
        observations: list[EarningsInsightObservation]) -> list[EarningsInsightDiagnostic]:
    by_metric = {item.metric_id: item for item in observations}
    results: list[EarningsInsightDiagnostic] = []

    def difference(diagnostic_id: str, label: str, left: str, right: str,
                   *, scale: float = 1.0, unit: str) -> None:
        lhs, rhs = by_metric.get(left), by_metric.get(right)
        if lhs is None or rhs is None:
            return
        results.append(EarningsInsightDiagnostic(
            diagnostic_id=diagnostic_id, label=label,
            value=round((lhs.value - rhs.value) * scale, 4), unit=unit,
            input_observation_ids=[lhs.observation_id, rhs.observation_id]))

    difference(
        "eps_minus_revenue_growth", "盈利增长减营收增长",
        "earnings.eps.yoy_growth", "earnings.revenue.yoy_growth",
        scale=100, unit="percentage_point")
    difference(
        "eps_minus_revenue_surprise", "盈利超预期幅度减营收超预期幅度",
        "earnings.eps.surprise_pct", "earnings.revenue.surprise_pct",
        scale=100, unit="percentage_point")

    positive = by_metric.get("earnings.guidance.positive_count")
    negative = by_metric.get("earnings.guidance.negative_count")
    if positive is not None and negative is not None:
        inputs = [positive.observation_id, negative.observation_id]
        if negative.value:
            results.append(EarningsInsightDiagnostic(
                diagnostic_id="positive_negative_guidance_ratio",
                label="正面指引与负面指引之比",
                value=round(positive.value / negative.value, 4), unit="ratio",
                input_observation_ids=inputs))
        results.append(EarningsInsightDiagnostic(
            diagnostic_id="positive_minus_negative_guidance",
            label="正面指引减负面指引家数",
            value=round(positive.value - negative.value, 4), unit="count",
            input_observation_ids=inputs))

    for prefix, label in (("forward", "前瞻市盈率"), ("trailing", "过去十二个月市盈率")):
        current = by_metric.get(f"valuation.{prefix}_pe")
        if current is None:
            continue
        for horizon, horizon_label in (("5y", "五年均值"), ("10y", "十年均值")):
            average = by_metric.get(f"valuation.{prefix}_pe.average_{horizon}")
            if average is None or not average.value:
                continue
            results.append(EarningsInsightDiagnostic(
                diagnostic_id=f"{prefix}_pe_vs_{horizon}_average",
                label=f"{label}相对{horizon_label}",
                value=round((current.value / average.value - 1) * 100, 4),
                unit="percent", input_observation_ids=[
                    current.observation_id, average.observation_id]))
    return results


def _bounded_excerpt(text: str, match: re.Match[str], *, limit: int = 900) -> tuple[int, int, str]:
    """Keep a paragraph-sized citation around a matched analytical statement."""
    window_start = max(0, match.start() - 600)
    sentence_start = text.rfind(". ", window_start, match.start())
    newline_start = text.rfind("\n", window_start, match.start())
    boundary = max(sentence_start + 2 if sentence_start >= 0 else 0,
                   newline_start + 1 if newline_start >= 0 else 0)
    start = boundary if boundary else window_start
    search_end = min(len(text), match.end() + 520)
    sentence_end = text.find(". ", match.end(), search_end)
    newline_end = text.find("\n", match.end(), search_end)
    candidates = [value for value in (
        sentence_end + 1 if sentence_end >= 0 else -1,
        newline_end if newline_end >= 0 else -1) if value >= match.end()]
    end = min(candidates) if candidates else search_end
    if end - start < 140:
        end = min(len(text), start + max(140, match.end() - start + 220))
    if end - start > limit:
        end = start + limit
    raw = text[start:end]
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw.rstrip())
    start += leading
    end = start + max(0, trailing - leading)
    return start, end, re.sub(r"\s+", " ", text[start:end]).strip()


def _select_narrative_evidence(
        products, snapshot: EarningsInsightSnapshot,
        *, limit: int = 6) -> list[EarningsInsightNarrativeEvidence]:
    if not snapshot.report.version_id:
        return []
    pages = products.unstructured.document_pages(snapshot.report.version_id)
    selected: list[EarningsInsightNarrativeEvidence] = []
    used_spans: set[tuple[int, int, int]] = set()
    for topic, label, patterns in _NARRATIVE_TOPICS:
        chosen = None
        candidates = [
            page for page in pages
            if "table of contents" not in str(page.get("section_title") or "").lower()]
        if topic == "valuation_and_sentiment":
            candidates.sort(key=lambda page: (
                int(page.get("page_number") or 0) < 15,
                int(page.get("page_number") or 0)))
        for page in candidates:
            text = str(page.get("text") or "")
            for pattern in patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
                if match:
                    chosen = (page, text, match)
                    break
            if chosen:
                break
        if chosen is None:
            continue
        page, page_text, match = chosen
        local_start, local_end, excerpt = _bounded_excerpt(page_text, match)
        page_number = int(page.get("page_number") or 0)
        span_key = (page_number, local_start, local_end)
        if not excerpt or span_key in used_spans:
            continue
        used_spans.add(span_key)
        page_char_start = int(page.get("char_start") or 0)
        selected.append(EarningsInsightNarrativeEvidence(
            topic=topic, topic_label=label, page_number=page_number,
            section_title=str(page.get("section_title") or ""),
            char_start=page_char_start + local_start,
            char_end=page_char_start + local_end, text=excerpt,
            document_id=snapshot.report.document_id,
            version_id=snapshot.report.version_id))
        if len(selected) >= limit:
            break
    return selected


def load_analysis_packet(products, *, as_of: datetime | None = None,
                         version_id: str = "") -> EarningsInsightAnalysisPacket:
    """Build the bounded, traceable FactSet input shared by analysis workflows."""
    snapshot = load_snapshot(products, as_of=as_of, version_id=version_id)
    warnings = list(snapshot.status.warnings)
    observations = _latest_index_observations(snapshot)
    groups = _group_observations(observations, warnings)
    expected = set().union(*_ANALYSIS_GROUPS.values())
    present = {item.metric_id for item in observations}
    missing = sorted(expected - present)
    if missing:
        warnings.append("missing_index_metrics:" + ",".join(missing))
    status = snapshot.status.model_copy(update={"warnings": list(dict.fromkeys(warnings))})
    return EarningsInsightAnalysisPacket(
        report=snapshot.report, status=status, observation_groups=groups,
        diagnostics=_diagnostics(observations),
        narrative_evidence=_select_narrative_evidence(products, snapshot),
        sectors=snapshot.sectors, lineage=snapshot.lineage)


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
    "EarningsInsightAnalysisPacket", "EarningsInsightDiagnostic",
    "EarningsInsightEvidence", "EarningsInsightLineage",
    "EarningsInsightNarrativeEvidence",
    "EarningsInsightObservation", "EarningsInsightPartitionStatus",
    "EarningsInsightReport", "EarningsInsightSnapshot", "EarningsInsightStatus",
    "available_vintages", "load_analysis_packet", "load_snapshot", "operational_status",
    "to_earnings_backdrop",
]
