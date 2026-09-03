"""Governed FactSet PDF acquisition and unstructured document projection."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from time import monotonic
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from ..core.structured_models import (
    ArtifactDescriptor,
    EvidenceLink,
    IngestionStatus,
    ObservationInput,
    QualityStatus,
    SeriesIdentity,
    VerificationStatus,
)
from ..rollout_modes import source_mode
from ..source_cache import CachedDoc
from ..sources.factset_earnings_insight import (
    FactSetFetch,
    FactSetPDF,
    FactSetSourceError,
    STABLE_URL,
    fetch_report,
    inspect_pdf,
)
from ..sources.factset_earnings_text import (
    CandidateStatus,
    EXTRACTOR_VERSION,
    FactSetExtractionRun,
    MetricGroup,
    ReportPhase,
    extract_index_text,
    merge_candidate_evidence,
    new_extraction_run,
    validate_index_candidates,
)
from ..sources.factset_earnings_charts import (
    ChartRegistry,
    ChartTable,
    decode_082826_sector_tables,
    emit_chart_candidates,
    validate_sector_table,
)


SOURCE_ID = "factset_earnings_insight_metrics"
DOCUMENT_SOURCE_ID = "factset_earnings_insight_doc"
DATASET_ID = "sp500_earnings_insight"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FactSetDocumentPipeline:
    """Persist exact PDF bytes, extracted text, page map and image inventory once."""

    def __init__(self, structured_repository, document_store, *,
                 clock: Callable[[], datetime] = _now, client=None):
        self.structured = structured_repository
        self.documents = document_store
        self.clock = clock
        self.client = client

    def _local_source(self, path: str | Path) -> FactSetFetch:
        target = Path(path).expanduser().resolve()
        body = target.read_bytes()
        if not body.startswith(b"%PDF"):
            from ..sources.factset_earnings_insight import FactSetFailure

            raise FactSetSourceError(
                FactSetFailure.NOT_PDF,
                f"local import is not a PDF: {target.name}")
        stamp = self.clock()
        if stamp.tzinfo is None:
            raise ValueError("FactSet import clock must be timezone-aware")
        return FactSetFetch(
            stable_url=STABLE_URL,
            final_url=STABLE_URL,
            status_code=200,
            etag="",
            last_modified="",
            mime_type="application/pdf",
            body=body,
            fetched_at=stamp.astimezone(timezone.utc),
        )

    def run(self, *, local_pdf: str | Path | None = None,
            processor_version: str = "factset-document-v1",
            url: str = STABLE_URL, source: FactSetFetch | None = None,
            document: FactSetPDF | None = None) -> dict:
        source = source or (self._local_source(local_pdf) if local_pdf else fetch_report(
            url=url, client=self.client, clock=self.clock))
        document = document or inspect_pdf(source)
        fetched_at = source.fetched_at
        common_metadata = {
            "stable_url": source.stable_url,
            "final_url": source.final_url,
            "etag": source.etag,
            "last_modified": source.last_modified,
            "first_seen_at": fetched_at.isoformat(),
            "fetched_at": fetched_at.isoformat(),
            "mime_type": source.mime_type,
            "bytes": source.byte_count,
            "pdf_sha256": source.content_hash,
            "report_date": document.report_date.isoformat(),
            "page_count": document.page_count,
            "text_sha256": document.text_hash,
            "usage": "internal_only",
        }
        if local_pdf:
            common_metadata["local_import_name"] = Path(local_pdf).name
        pdf_artifact = self.structured.put_artifact(
            source.body,
            ArtifactDescriptor(
                source_id=SOURCE_ID, dataset_id=DATASET_ID,
                fetched_at=fetched_at,
                query_scope={"pdf_sha256": source.content_hash, "projection": "source_pdf"},
                source_url=source.final_url,
                source_version=source.etag or source.last_modified or source.content_hash,
                media_type="application/pdf", retention="licensed_internal_research",
                metadata=common_metadata))
        text_artifact = self.structured.put_artifact(
            document.text,
            ArtifactDescriptor(
                source_id=SOURCE_ID, dataset_id=DATASET_ID,
                fetched_at=fetched_at,
                query_scope={"pdf_sha256": source.content_hash,
                             "projection": "extracted_text"},
                source_url=source.final_url,
                source_version=document.text_hash,
                media_type="text/plain", retention="licensed_internal_research",
                metadata={**common_metadata, "projection": "extracted_text"}))
        text_path = self.structured.artifacts.root / text_artifact.relative_path
        doc = CachedDoc(
            symbol="SP500", period=document.report_date.isoformat(),
            doc_type="research_article", text=document.text, path=text_path,
            version_path=text_path, source=DOCUMENT_SOURCE_ID,
            source_url=source.final_url, fetched_at=fetched_at.isoformat(),
            sha256=document.text_hash, external_id=source.content_hash,
            title=document.title, published_at=document.report_date.isoformat(),
            related_entities=(), completeness="full", carrier_format="pdf",
            mime_source="application/pdf")
        self.documents.save_document(doc, note="licensed internal research")
        version = self.documents.latest_document_version(doc.document_id)
        if version is None:
            raise RuntimeError("FactSet document version was not persisted")
        version_id = version["version_id"]
        self.documents.link_document_artifact(
            version_id, pdf_artifact.id, role="source_pdf",
            media_type="application/pdf", content_hash=source.content_hash)
        self.documents.save_document_pages(version_id, document.pages)

        image_artifact_ids: list[str] = []
        for image in document.images:
            artifact = self.structured.put_artifact(
                image.data,
                ArtifactDescriptor(
                    source_id=SOURCE_ID, dataset_id=DATASET_ID,
                    fetched_at=fetched_at,
                    query_scope={"pdf_sha256": source.content_hash,
                                 "projection": "page_image",
                                 "page_number": image.page_number,
                                 "image_number": image.image_number},
                    source_url=source.final_url,
                    source_version=hashlib.sha256(image.data).hexdigest(),
                    media_type=image.media_type,
                    retention="licensed_internal_research",
                    metadata={"chart_id": image.chart_id,
                              "page_number": image.page_number,
                              "width": image.width, "height": image.height,
                              "color_space": image.color_space,
                              "bits_per_component": image.bits_per_component,
                              "usage": "internal_only"}))
            image_artifact_ids.append(artifact.id)
            self.documents.link_document_artifact(
                version_id, artifact.id, role="page_image",
                page_number=image.page_number, region=image.region,
                media_type=image.media_type, content_hash=artifact.content_hash)

        claimed = self.documents.begin_document_processing(
            doc.document_id, "factset_document", processor_version,
            at=fetched_at.isoformat())
        if claimed is None:
            return {
                "status": "no_change", "document_id": doc.document_id,
                "document_version_id": version_id, "artifact_id": pdf_artifact.id,
                "pdf_sha256": source.content_hash,
                "report_date": document.report_date.isoformat(),
                "known_at": fetched_at.isoformat(), "page_count": document.page_count,
                "image_count": len(document.images), "processor_version": processor_version,
            }
        self.documents.finish_document_processing(
            claimed, "factset_document", processor_version, ok=True,
            outputs=1 + len(document.pages) + len(image_artifact_ids),
            note=f"pdf={source.content_hash}; text={document.text_hash}",
            at=fetched_at.isoformat())
        return {
            "status": "succeeded", "document_id": doc.document_id,
            "document_version_id": version_id, "artifact_id": pdf_artifact.id,
            "text_artifact_id": text_artifact.id,
            "page_image_artifact_ids": image_artifact_ids,
            "pdf_sha256": source.content_hash, "text_sha256": document.text_hash,
            "stable_url": source.stable_url, "final_url": source.final_url,
            "etag": source.etag, "last_modified": source.last_modified,
            "bytes": source.byte_count, "mime_type": source.mime_type,
            "report_date": document.report_date.isoformat(),
            "known_at": fetched_at.isoformat(), "page_count": document.page_count,
            "image_count": len(document.images), "processor_version": processor_version,
        }


_OUTCOME_METRICS = {
    "earnings.reporting.coverage",
    "earnings.eps.above_estimate_share",
    "earnings.eps.inline_estimate_share",
    "earnings.eps.below_estimate_share",
    "earnings.revenue.above_estimate_share",
    "earnings.revenue.inline_estimate_share",
    "earnings.revenue.below_estimate_share",
    "earnings.eps.surprise_pct", "earnings.revenue.surprise_pct",
    "earnings.eps.yoy_growth", "earnings.revenue.yoy_growth",
    "earnings.net_profit_margin",
    "earnings.revision.improved_sector_count",
}
_FORWARD_METRICS = {
    "earnings.guidance.positive_count", "earnings.guidance.negative_count",
    "valuation.forward_pe", "valuation.trailing_pe",
    "valuation.forward_pe.average_5y", "valuation.forward_pe.average_10y",
    "valuation.trailing_pe.average_5y", "valuation.trailing_pe.average_10y",
    "consensus.rating.buy_share", "consensus.rating.hold_share",
    "consensus.rating.sell_share", "consensus.target.upside",
}
_REQUIRED_INDEX_TEXT = {
    ReportPhase.PRE_REPORTING: _FORWARD_METRICS,
    ReportPhase.IN_PROGRESS: _OUTCOME_METRICS | _FORWARD_METRICS,
    ReportPhase.SUBSTANTIALLY_COMPLETE: _OUTCOME_METRICS | _FORWARD_METRICS,
    ReportPhase.UNKNOWN_TEMPLATE: set(),
}


class FactSetIndexPipeline:
    """Admit index text candidates and write one independent release manifest."""

    def __init__(self, structured_repository, *, required_metrics=None):
        self.structured = structured_repository
        self.required_metrics = required_metrics or _REQUIRED_INDEX_TEXT

    @staticmethod
    def _quality(candidates, required: set[str], missing: dict) -> dict:
        by_group: dict[str, dict] = {}
        for candidate in candidates:
            group = candidate.metric_group.value
            row = by_group.setdefault(group, {
                "observed": 0, "admitted": 0, "conflicts": 0,
                "quarantined": 0, "evidence_anchors": 0,
            })
            row["observed"] += 1
            row["evidence_anchors"] += len(candidate.evidence)
            if candidate.status == CandidateStatus.ACCEPTED:
                row["admitted"] += 1
            elif candidate.status == CandidateStatus.CONFLICT:
                row["conflicts"] += 1
            else:
                row["quarantined"] += 1
        accepted_metrics = {
            candidate.metric_id for candidate in candidates
            if candidate.status == CandidateStatus.ACCEPTED
        }
        required_missing = sorted(required - accepted_metrics)
        required_rows = [candidate for candidate in candidates
                         if candidate.metric_id in required]
        anchors = sum(len(candidate.evidence) for candidate in required_rows
                      if candidate.status == CandidateStatus.ACCEPTED)
        evidence_ratio = anchors / len(required) if required else 1.0
        return {
            "expected_metrics": sorted(required),
            "accepted_metrics": sorted(accepted_metrics),
            "missing_required_metrics": required_missing,
            "declared_missing": missing,
            "groups": by_group,
            "evidence_coverage": min(1.0, evidence_ratio),
            "passed": not required_missing and evidence_ratio >= 1.0,
        }

    def run(self, document, *, document_id: str, version_id: str,
            artifact_id: str, known_at: datetime,
            extractor_version: str = EXTRACTOR_VERSION) -> dict:
        self.structured.bootstrap_catalog()
        ingestion_id = self.structured.begin_ingestion(
            source_id=SOURCE_ID, dataset_id=DATASET_ID,
            query_scope={"document_version_id": version_id,
                         "partition": "index_core",
                         "extractor_version": extractor_version})
        extracted = extract_index_text(
            document, document_id=document_id, version_id=version_id,
            known_at=known_at, extractor_version=extractor_version)
        merged = merge_candidate_evidence(extracted.candidates)
        extracted.candidates = merged.candidates
        validated = validate_index_candidates(extracted)
        observation_ids: list[str] = []
        created = unchanged = quarantined = conflicts = 0
        for candidate in validated.candidates:
            raw = candidate.model_dump(mode="json")
            self.structured.save_candidate(
                candidate_id=candidate.candidate_id, run_id=ingestion_id,
                source_id=candidate.source_id, dataset_id=candidate.dataset_id,
                entity_id=candidate.entity_id,
                provider_field=candidate.provider_field,
                metric_id=candidate.metric_id, period=candidate.period.value,
                value=candidate.value, unit=candidate.unit, currency="",
                status=candidate.status.value,
                reason_codes=candidate.reason_codes, artifact_id=artifact_id,
                raw=raw, at=known_at)
            observation_id = ""
            if candidate.status == CandidateStatus.ACCEPTED:
                vintage = self.structured.save_observation(ObservationInput(
                    series=SeriesIdentity(
                        source_id=candidate.source_id,
                        dataset_id=candidate.dataset_id,
                        entity_id=candidate.entity_id,
                        metric_id=candidate.metric_id, unit=candidate.unit,
                        period_basis=candidate.period.basis,
                        dimensions={
                            "estimate_state": candidate.estimate_state.value,
                            **{key: value for key, value in candidate.dimensions.items()
                               if not key.startswith("raw_") and
                               key != "supporting_raw_tokens"},
                        }),
                    period=candidate.period.value, value=float(candidate.value),
                    event_time=datetime.combine(
                        candidate.report_date, datetime.min.time(), tzinfo=timezone.utc),
                    known_at=candidate.known_at, fetched_at=candidate.known_at,
                    artifact_id=artifact_id, quality_status=QualityStatus.ACCEPTED,
                    quality={"partition": "index_core",
                             "extractor_version": candidate.extractor_version},
                    raw={
                        "report_date": candidate.report_date.isoformat(),
                        "document_id": document_id, "version_id": version_id,
                        "candidate_id": candidate.candidate_id,
                        "raw_token": candidate.raw_token,
                        "raw_value": candidate.raw_value,
                    }))
                observation_id = vintage.id
                observation_ids.append(vintage.id)
                created += int(vintage.created)
                unchanged += int(not vintage.created)
            elif candidate.status == CandidateStatus.CONFLICT:
                conflicts += 1
            else:
                quarantined += 1
            for anchor in candidate.evidence:
                self.structured.save_evidence_link(EvidenceLink(
                    observation_id=observation_id,
                    candidate_id=candidate.candidate_id,
                    document_id=anchor.document_id, version_id=anchor.version_id,
                    anchor_kind=anchor.anchor_kind,
                    page_number=anchor.page_number,
                    char_start=anchor.char_start, char_end=anchor.char_end,
                    chart_id=anchor.chart_id, region=anchor.region,
                    extraction_method=anchor.extraction_method,
                    source_tier="licensed_primary",
                    verification_status=(VerificationStatus.ACCEPTED
                                         if observation_id
                                         else VerificationStatus.NEEDS_EVIDENCE)))

        required = set(self.required_metrics.get(extracted.phase, set()))
        quality = self._quality(validated.candidates, required, validated.missing)
        if extracted.phase == ReportPhase.UNKNOWN_TEMPLATE:
            quality["passed"] = False
            quality["template_reasons"] = extracted.reason_codes
        mode = source_mode("factset_earnings_insight_index")
        release_status = "platform" if quality["passed"] and mode == "platform" else "shadow"
        release_id = self.structured.save_release_manifest(
            source_id=SOURCE_ID, dataset_id=DATASET_ID, partition="index_core",
            report_date=document.report_date.isoformat(), document_id=document_id,
            version_id=version_id, artifact_id=artifact_id, known_at=known_at,
            extractor_version=extractor_version, status=release_status,
            passed=bool(quality["passed"]), quality=quality,
            observation_ids=list(dict.fromkeys(observation_ids)))
        if conflicts or quarantined:
            ingestion_status = IngestionStatus.PARTIAL if observation_ids else IngestionStatus.VALIDATION_FAILED
        elif created:
            ingestion_status = IngestionStatus.SUCCEEDED
        elif unchanged:
            ingestion_status = IngestionStatus.NO_CHANGE
        else:
            ingestion_status = IngestionStatus.ZERO_MATCH
        self.structured.finish_ingestion(
            ingestion_id, status=ingestion_status.value,
            discovered=len(validated.candidates), accepted=created,
            quarantined=quarantined + conflicts, unchanged=unchanged,
            reason_codes=validated.reason_counts,
            note=f"index_core={release_status}; release={release_id}", at=known_at)
        return {
            "run_id": ingestion_id, "status": ingestion_status.value,
            "phase": extracted.phase.value, "release_id": release_id,
            "release_status": release_status, "quality": quality,
            "accepted": created, "unchanged": unchanged,
            "quarantined": quarantined, "conflicts": conflicts,
            "merged_duplicates": merged.merged_duplicates,
            "observation_ids": list(dict.fromkeys(observation_ids)),
        }


_REQUIRED_SECTOR_CHARTS = {
    "earnings_revenue_scorecard", "earnings_revenue_surprise",
    "earnings_revenue_growth", "net_profit_margin", "eps_guidance",
    "geographic_revenue_exposure", "forward_pe", "target_ratings",
}


class FactSetSectorPipeline:
    """Persist validated sector cells and gate their release as one partition."""

    def __init__(self, structured_repository, *, required_chart_ids=None,
                 registry: ChartRegistry | None = None):
        self.structured = structured_repository
        self.required_chart_ids = set(
            required_chart_ids or _REQUIRED_SECTOR_CHARTS)
        self.registry = registry or ChartRegistry()

    def run(self, document, *, tables: list[ChartTable], document_id: str,
            version_id: str, artifact_id: str, known_at: datetime,
            extractor_version: str = "factset-chart-v1",
            annotated_cells_ok: bool = False) -> dict:
        self.structured.bootstrap_catalog()
        ingestion_id = self.structured.begin_ingestion(
            source_id=SOURCE_ID, dataset_id=DATASET_ID,
            query_scope={"document_version_id": version_id,
                         "partition": "sector_core",
                         "extractor_version": extractor_version})
        extraction_run = new_extraction_run(
            document, document_id=document_id, version_id=version_id,
            known_at=known_at, extractor_version=extractor_version)
        definitions = {definition.chart_id: definition
                       for definition in self.registry.definitions}
        table_quality: dict[str, dict] = {}
        candidates = []
        supplied: set[str] = set()
        for table in tables:
            supplied.add(table.chart_id)
            definition = definitions.get(table.chart_id)
            if definition is None:
                table_quality[table.chart_id] = {
                    "passed": False, "reason_codes": ["unknown_chart_template"]}
                continue
            emitted = emit_chart_candidates(definition, table, extraction_run)
            checked = validate_sector_table(
                definition, table, emitted.candidates)
            candidates.extend(checked.candidates)
            table_quality[table.chart_id] = {
                "passed": checked.passed,
                "reason_codes": checked.reason_codes,
                "entities": checked.entities, "columns": checked.columns,
                "observed_cells": len(checked.candidates),
                "admitted_cells": sum(
                    candidate.status == CandidateStatus.ACCEPTED
                    for candidate in checked.candidates),
            }
        missing_tables = sorted(self.required_chart_ids - supplied)
        all_tables_pass = all(
            table_quality.get(chart_id, {}).get("passed", False)
            for chart_id in self.required_chart_ids)
        recognized_template = extraction_run.phase != ReportPhase.UNKNOWN_TEMPLATE
        partition_passed = (
            recognized_template and not missing_tables and all_tables_pass
            and annotated_cells_ok)
        observation_ids: list[str] = []
        created = unchanged = quarantined = 0
        for candidate in candidates:
            raw = candidate.model_dump(mode="json")
            self.structured.save_candidate(
                candidate_id=candidate.candidate_id, run_id=ingestion_id,
                source_id=candidate.source_id, dataset_id=candidate.dataset_id,
                entity_id=candidate.entity_id,
                provider_field=candidate.provider_field,
                metric_id=candidate.metric_id, period=candidate.period.value,
                value=candidate.value, unit=candidate.unit, currency="",
                status=candidate.status.value,
                reason_codes=candidate.reason_codes, artifact_id=artifact_id,
                raw=raw, at=known_at)
            observation_id = ""
            if candidate.status == CandidateStatus.ACCEPTED:
                vintage = self.structured.save_observation(ObservationInput(
                    series=SeriesIdentity(
                        source_id=SOURCE_ID, dataset_id=DATASET_ID,
                        entity_id=candidate.entity_id, metric_id=candidate.metric_id,
                        unit=candidate.unit, period_basis=candidate.period.basis,
                        dimensions={
                            "estimate_state": candidate.estimate_state.value,
                            **candidate.dimensions,
                        }),
                    period=candidate.period.value, value=float(candidate.value),
                    event_time=datetime.combine(
                        candidate.report_date, datetime.min.time(), tzinfo=timezone.utc),
                    known_at=known_at, fetched_at=known_at,
                    artifact_id=artifact_id, quality_status=QualityStatus.ACCEPTED,
                    quality={"partition": "sector_core",
                             "extractor_version": extractor_version},
                    raw={"report_date": candidate.report_date.isoformat(),
                         "document_id": document_id, "version_id": version_id,
                         "candidate_id": candidate.candidate_id,
                         "raw_token": candidate.raw_token}))
                observation_id = vintage.id
                observation_ids.append(vintage.id)
                created += int(vintage.created)
                unchanged += int(not vintage.created)
            else:
                quarantined += 1
            for anchor in candidate.evidence:
                self.structured.save_evidence_link(EvidenceLink(
                    observation_id=observation_id,
                    candidate_id=candidate.candidate_id,
                    document_id=anchor.document_id, version_id=anchor.version_id,
                    anchor_kind="image_region", page_number=anchor.page_number,
                    char_start=0, char_end=0, chart_id=anchor.chart_id,
                    region=anchor.region, extraction_method=anchor.extraction_method,
                    source_tier="licensed_primary",
                    verification_status=(VerificationStatus.ACCEPTED
                                         if observation_id
                                         else VerificationStatus.NEEDS_EVIDENCE)))
        quality = {
            "passed": partition_passed,
            "annotated_cells_ok": annotated_cells_ok,
            "expected_chart_ids": sorted(self.required_chart_ids),
            "missing_chart_ids": missing_tables,
            "tables": table_quality,
            "observed_cells": len(candidates),
            "admitted_cells": len(observation_ids),
            "quarantined_cells": quarantined,
            "template_status": extraction_run.template_status,
            "template_reasons": extraction_run.reason_codes,
        }
        mode = source_mode("factset_earnings_insight_sector")
        release_status = "platform" if partition_passed and mode == "platform" else "shadow"
        release_id = self.structured.save_release_manifest(
            source_id=SOURCE_ID, dataset_id=DATASET_ID, partition="sector_core",
            report_date=document.report_date.isoformat(), document_id=document_id,
            version_id=version_id, artifact_id=artifact_id, known_at=known_at,
            extractor_version=extractor_version, status=release_status,
            passed=partition_passed, quality=quality,
            observation_ids=list(dict.fromkeys(observation_ids)))
        if quarantined:
            status = IngestionStatus.PARTIAL if observation_ids else IngestionStatus.VALIDATION_FAILED
        elif created:
            status = IngestionStatus.SUCCEEDED
        elif unchanged:
            status = IngestionStatus.NO_CHANGE
        else:
            status = IngestionStatus.ZERO_MATCH
        reason_codes = {}
        for details in table_quality.values():
            for reason in details.get("reason_codes", []):
                reason_codes[reason] = reason_codes.get(reason, 0) + 1
        if missing_tables:
            reason_codes["sector_tables_missing"] = len(missing_tables)
        if not annotated_cells_ok:
            reason_codes["annotated_cells_unverified"] = 1
        if not recognized_template:
            reason_codes["unknown_template"] = 1
        self.structured.finish_ingestion(
            ingestion_id, status=status.value, discovered=len(candidates),
            accepted=created, quarantined=quarantined, unchanged=unchanged,
            reason_codes=reason_codes,
            note=f"sector_core={release_status}; release={release_id}", at=known_at)
        return {
            "run_id": ingestion_id, "status": status.value,
            "release_id": release_id, "release_status": release_status,
            "quality": quality, "accepted": created, "unchanged": unchanged,
            "quarantined": quarantined,
            "observation_ids": list(dict.fromkeys(observation_ids)),
        }


def _sanitized_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class FactSetWeeklyPipeline:
    """One observable acquisition-to-independent-release entry point."""

    def __init__(self, structured_repository, document_store, *,
                 clock: Callable[[], datetime] = _now, client=None):
        self.structured = structured_repository
        self.documents = document_store
        self.clock = clock
        self.client = client

    def run(self, *, local_pdf: str | Path | None = None,
            document_processor_version: str = "factset-document-v1",
            index_extractor_version: str = EXTRACTOR_VERSION,
            sector_extractor_version: str = "factset-chart-v1",
            sector_tables: list[ChartTable] | None = None,
            annotated_cells_ok: bool = False,
            url: str = STABLE_URL) -> dict:
        started = monotonic()
        document_pipeline = FactSetDocumentPipeline(
            self.structured, self.documents, clock=self.clock, client=self.client)
        if source_mode("factset_earnings_insight_doc") == "off":
            return {"status": "off", "elapsed_seconds": 0.0,
                    "index_core": {"status": "off"},
                    "sector_core": {"status": "off"}}
        try:
            source = (document_pipeline._local_source(local_pdf)
                      if local_pdf else fetch_report(
                          url=url, client=self.client, clock=self.clock))
            document = inspect_pdf(source)
            projected = document_pipeline.run(
                local_pdf=local_pdf, processor_version=document_processor_version,
                source=source, document=document)
            known_at = datetime.fromisoformat(projected["known_at"])
            index = FactSetIndexPipeline(self.structured).run(
                document, document_id=projected["document_id"],
                version_id=projected["document_version_id"],
                artifact_id=projected["artifact_id"], known_at=known_at,
                extractor_version=index_extractor_version)
            layout_decode = None
            tables = sector_tables
            if tables is None:
                # This decoder consumes only `document.images`; it does not
                # load a golden manifest or any reviewed values.  The sector
                # release remains shadow until a separate acceptance compare
                # supplies an explicit passing result.
                layout_decode = decode_082826_sector_tables(document)
                tables = (layout_decode.tables
                          if layout_decode.status.value == "succeeded" else [])
            sector = FactSetSectorPipeline(self.structured).run(
                document, tables=list(tables),
                document_id=projected["document_id"],
                version_id=projected["document_version_id"],
                artifact_id=projected["artifact_id"], known_at=known_at,
                extractor_version=sector_extractor_version,
                annotated_cells_ok=annotated_cells_ok)
            status = ("succeeded" if index["quality"]["passed"] else
                      "partial" if projected["status"] in {"succeeded", "no_change"}
                      else projected["status"])
            return {
                "status": status,
                "provenance": {
                    "stable_url": _sanitized_url(source.stable_url),
                    "final_url": _sanitized_url(source.final_url),
                    "response_status": source.status_code,
                    "artifact_hash": projected["pdf_sha256"],
                    "report_date": projected["report_date"],
                    "page_count": projected["page_count"],
                    "chart_image_count": projected["image_count"],
                },
                "document": projected, "index_core": index, "sector_core": sector,
                "sector_decoder": ({
                    "status": layout_decode.status.value,
                    "table_count": len(layout_decode.tables),
                    "reason_codes": layout_decode.reason_codes,
                } if layout_decode is not None else {"status": "caller_supplied_tables"}),
                "counters": {
                    "index_candidates": (index["accepted"] + index["unchanged"]
                                         + index["quarantined"] + index["conflicts"]),
                    "index_admitted": index["accepted"],
                    "index_conflicts": index["conflicts"],
                    "index_quarantined": index["quarantined"],
                    "sector_candidates": (sector["accepted"] + sector["unchanged"]
                                          + sector["quarantined"]),
                    "sector_admitted": sector["accepted"],
                    "sector_quarantined": sector["quarantined"],
                },
                "elapsed_seconds": round(monotonic() - started, 3),
            }
        except FactSetSourceError as exc:
            run_id = self.structured.begin_ingestion(
                source_id=SOURCE_ID, dataset_id=DATASET_ID,
                query_scope={"pipeline": "factset_weekly_ingest"})
            status = {
                "unreachable": IngestionStatus.UNREACHABLE,
                "unauthorized": IngestionStatus.UNAUTHORIZED,
                "not_pdf": IngestionStatus.NOT_PDF,
                "parse_failed": IngestionStatus.PARSE_FAILED,
            }[exc.status.value]
            self.structured.finish_ingestion(
                run_id, status=status.value, reason_codes={exc.status.value: 1},
                note=str(exc)[:240], at=self.clock())
            return {
                "status": status.value, "failure": exc.status.value,
                "index_core": {"status": "unavailable"},
                "sector_core": {"status": "unavailable"},
                "elapsed_seconds": round(monotonic() - started, 3),
            }


__all__ = [
    "FactSetDocumentPipeline", "FactSetIndexPipeline", "FactSetSectorPipeline",
    "FactSetWeeklyPipeline",
]
