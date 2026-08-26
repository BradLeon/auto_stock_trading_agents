"""Unified adapter batch contract and central structured admission pipeline."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import math
from typing import Protocol

from .models import (
    AdapterBatch,
    AdmissionResult,
    ArtifactDescriptor,
    FetchRequest,
    IngestionStatus,
    NativeRecord,
    ObservationInput,
    QualityStatus,
    SeriesIdentity,
)
from .repository import SQLiteStructuredRepository


class StructuredAdapter(Protocol):
    def fetch(self, request: FetchRequest) -> AdapterBatch: ...


class CentralAdmission:
    def __init__(self, repository: SQLiteStructuredRepository):
        self.repository = repository

    @staticmethod
    def _candidate_id(batch: AdapterBatch, record: NativeRecord) -> str:
        payload = (
            f"{batch.source_id}|{batch.dataset_id}|{record.entity_id}|"
            f"{record.provider_field}|{record.period}|{record.value}|{record.raw}"
        )
        return hashlib.sha1(payload.encode()).hexdigest()[:24]

    def admit(self, *, batch: AdapterBatch, request: FetchRequest,
              record: NativeRecord, artifact_id: str, run_id: str) -> AdmissionResult:
        reasons: list[str] = []
        source = self.repository.source(batch.source_id)
        dataset = self.repository.dataset(batch.dataset_id)
        if source is None:
            reasons.append("source_unregistered")
        elif source["catalog_status"] == "runtime_excluded":
            reasons.append("runtime_source_excluded")
        if dataset is None:
            reasons.append("dataset_unregistered")
        if request.entities and record.entity_id not in request.entities:
            reasons.append("entity_mismatch")
        if not record.entity_id:
            reasons.append("entity_unresolved")
        if not record.period and record.event_time is None:
            reasons.append("period_unresolved")
        try:
            numeric = float(record.value)
            if not math.isfinite(numeric):
                reasons.append("value_not_finite")
        except (TypeError, ValueError):
            numeric = 0.0
            reasons.append("value_not_numeric")
        metric_id = self.repository.resolve_metric(batch.source_id, record.provider_field)
        if metric_id is None and self.repository.metric(record.provider_field):
            metric_id = record.provider_field
        if metric_id is None:
            reasons.append("metric_unmapped")
            self.repository.record_pending_mapping(
                provider=batch.source_id, dataset_id=batch.dataset_id,
                provider_field=record.provider_field, sample=record.raw or {
                    "value": record.value, "unit": record.unit})
        metric = self.repository.metric(metric_id) if metric_id else None
        if not record.unit:
            reasons.append("unit_unresolved")
        if metric and metric["unit_family"] in {"currency", "currency_per_share"} \
                and not record.currency:
            reasons.append("currency_unresolved")
        if record.published_at and record.published_at > batch.fetched_at:
            reasons.append("published_after_fetch")
        if not artifact_id:
            reasons.append("artifact_missing")

        candidate_id = self._candidate_id(batch, record)
        if reasons:
            self.repository.save_candidate(
                candidate_id=candidate_id, run_id=run_id, source_id=batch.source_id,
                dataset_id=batch.dataset_id, entity_id=record.entity_id,
                provider_field=record.provider_field, metric_id=metric_id or "",
                period=record.period, value=record.value, unit=record.unit,
                currency=record.currency, status="quarantined", reason_codes=reasons,
                artifact_id=artifact_id, raw=record.raw, at=batch.fetched_at)
            return AdmissionResult(candidate_id=candidate_id, status="quarantined",
                                   reason_codes=reasons, metric_id=metric_id or "")

        conflict_rows = [row for row in self.repository.comparable_observations(
            dataset_id=batch.dataset_id, entity_id=record.entity_id,
            metric_id=metric_id, period=record.period)
            if row["source_id"] != batch.source_id and row["period"] == record.period
            and row["unit"] == record.unit and row["currency"] == record.currency
            and row["period_basis"] == record.period_basis
            and row["adjustment"] == record.adjustment]
        quality = QualityStatus.CONFLICT if any(
            not math.isclose(float(row["value"]), numeric, rel_tol=1e-9, abs_tol=1e-9)
            for row in conflict_rows) else QualityStatus.ACCEPTED
        observation = ObservationInput(
            series=SeriesIdentity(
                source_id=batch.source_id, dataset_id=batch.dataset_id,
                entity_id=record.entity_id, metric_id=metric_id, unit=record.unit,
                currency=record.currency, period_basis=record.period_basis,
                adjustment=record.adjustment, dimensions=record.dimensions),
            period=record.period or record.event_time.date().isoformat(),
            period_start=record.period_start, period_end=record.period_end,
            event_time=record.event_time, value=numeric,
            published_at=record.published_at, known_at=batch.fetched_at,
            fetched_at=batch.fetched_at, artifact_id=artifact_id,
            quality_status=quality, quality={"reason_codes": ["cross_source_conflict"]
                                             if quality == QualityStatus.CONFLICT else []},
            raw=record.raw)
        vintage = self.repository.save_observation(observation)
        if quality == QualityStatus.CONFLICT:
            for other in conflict_rows:
                if math.isclose(float(other["value"]), numeric, rel_tol=1e-9, abs_tol=1e-9):
                    continue
                absolute = abs(float(other["value"]) - numeric)
                relative = absolute / abs(float(other["value"])) if other["value"] else None
                self.repository.record_conflict(
                    dataset_id=batch.dataset_id, entity_id=record.entity_id,
                    metric_id=metric_id, period=record.period,
                    left_observation_id=other["observation_id"],
                    right_source_id=batch.source_id, right_value=numeric,
                    absolute_difference=absolute, relative_difference=relative,
                    at=batch.fetched_at)
        self.repository.save_candidate(
            candidate_id=candidate_id, run_id=run_id, source_id=batch.source_id,
            dataset_id=batch.dataset_id, entity_id=record.entity_id,
            provider_field=record.provider_field, metric_id=metric_id,
            period=record.period, value=record.value, unit=record.unit,
            currency=record.currency, status="accepted", reason_codes=[],
            artifact_id=artifact_id, raw=record.raw, at=batch.fetched_at)
        return AdmissionResult(candidate_id=candidate_id, status="accepted",
                               observation_id=vintage.id, metric_id=metric_id,
                               created=vintage.created)


class IngestionPipeline:
    def __init__(self, repository: SQLiteStructuredRepository):
        self.repository = repository
        self.admission = CentralAdmission(repository)

    def run(self, adapter: StructuredAdapter, request: FetchRequest) -> dict:
        source = self.repository.source(request.source_id)
        if source and source["catalog_status"] == "runtime_excluded":
            return {"run_id": "", "status": "runtime_excluded",
                    "accepted": 0, "quarantined": 0, "unchanged": 0}
        run_id = self.repository.begin_ingestion(
            source_id=request.source_id, dataset_id=request.dataset_id,
            query_scope=request.query_scope or {
                "entities": request.entities, "periods": request.periods})
        try:
            batch = adapter.fetch(request)
        except PermissionError as exc:
            self.repository.finish_ingestion(
                run_id, status=IngestionStatus.UNAUTHORIZED.value, note=str(exc))
            return {"run_id": run_id, "status": IngestionStatus.UNAUTHORIZED.value,
                    "accepted": 0, "quarantined": 0, "unchanged": 0}
        except (ConnectionError, TimeoutError) as exc:
            self.repository.finish_ingestion(
                run_id, status=IngestionStatus.UNREACHABLE.value, note=str(exc))
            return {"run_id": run_id, "status": IngestionStatus.UNREACHABLE.value,
                    "accepted": 0, "quarantined": 0, "unchanged": 0}
        except Exception as exc:  # adapter parser/schema failures are local to this run
            self.repository.finish_ingestion(
                run_id, status=IngestionStatus.PARSE_FAILED.value, note=str(exc))
            return {"run_id": run_id, "status": IngestionStatus.PARSE_FAILED.value,
                    "accepted": 0, "quarantined": 0, "unchanged": 0}

        if batch.source_id != request.source_id or batch.dataset_id != request.dataset_id:
            self.repository.finish_ingestion(
                run_id, status=IngestionStatus.VALIDATION_FAILED.value,
                reason_codes=["batch_scope_mismatch"])
            return {"run_id": run_id, "status": IngestionStatus.VALIDATION_FAILED.value,
                    "accepted": 0, "quarantined": 0, "unchanged": 0}

        terminal_without_records = {
            IngestionStatus.NO_CHANGE, IngestionStatus.ZERO_MATCH,
            IngestionStatus.NOT_YET_PUBLISHED, IngestionStatus.NO_COVERAGE,
            IngestionStatus.STALE, IngestionStatus.UNREACHABLE,
            IngestionStatus.UNAUTHORIZED, IngestionStatus.PARSE_FAILED,
            IngestionStatus.VALIDATION_FAILED,
        }
        if batch.status in terminal_without_records and not batch.records:
            reasons = Counter(failure.status.value for failure in batch.failures)
            self.repository.finish_ingestion(
                run_id, status=batch.status.value, reason_codes=dict(reasons),
                note="; ".join(f.message for f in batch.failures))
            return {"run_id": run_id, "status": batch.status.value,
                    "accepted": 0, "quarantined": 0, "unchanged": 0}

        artifacts = []
        for item in batch.artifacts:
            artifacts.append(self.repository.put_artifact(
                item.payload,
                ArtifactDescriptor(
                    source_id=batch.source_id, dataset_id=batch.dataset_id,
                    fetched_at=batch.fetched_at,
                    query_scope=item.query_scope or request.query_scope,
                    source_url=item.source_url, source_version=item.source_version,
                    media_type=item.media_type, retention=item.retention,
                    storage_mode=item.storage_mode, pointer=item.pointer,
                    metadata=item.metadata)))
        if not artifacts and batch.records:
            artifacts.append(self.repository.put_artifact(
                [record.model_dump(mode="json") for record in batch.records],
                ArtifactDescriptor(
                    source_id=batch.source_id, dataset_id=batch.dataset_id,
                    fetched_at=batch.fetched_at,
                    query_scope=request.query_scope or {
                        "entities": request.entities, "periods": request.periods},
                    source_version=str(batch.provider_metadata.get("source_version", "")),
                    media_type="application/json", retention="query_slice",
                    metadata=batch.provider_metadata)))
        artifact_id = artifacts[0].id if artifacts else ""
        results = [self.admission.admit(
            batch=batch, request=request, record=record,
            artifact_id=artifact_id, run_id=run_id) for record in batch.records]
        accepted = sum(result.status == "accepted" and result.created for result in results)
        unchanged = sum(result.status == "accepted" and not result.created for result in results)
        quarantined = sum(result.status == "quarantined" for result in results)
        reasons = Counter(code for result in results for code in result.reason_codes)
        reasons.update(failure.status.value for failure in batch.failures)
        if batch.failures or (accepted + unchanged > 0 and quarantined > 0):
            status = IngestionStatus.PARTIAL
        elif quarantined and not (accepted or unchanged):
            status = IngestionStatus.VALIDATION_FAILED
        elif accepted:
            status = IngestionStatus.SUCCEEDED
        elif unchanged:
            status = IngestionStatus.NO_CHANGE
        else:
            status = IngestionStatus.ZERO_MATCH
        self.repository.finish_ingestion(
            run_id, status=status.value, discovered=len(batch.records), accepted=accepted,
            quarantined=quarantined, unchanged=unchanged, reason_codes=dict(reasons),
            note="; ".join(f.message for f in batch.failures))
        return {"run_id": run_id, "status": status.value, "accepted": accepted,
                "quarantined": quarantined, "unchanged": unchanged,
                "results": [result.model_dump(mode="json") for result in results]}

    def run_many(self, jobs: list[tuple[StructuredAdapter, FetchRequest]]) -> list[dict]:
        """Run independent source/entity/slice jobs; one failure never aborts siblings."""
        return [self.run(adapter, request) for adapter, request in jobs]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
