"""Work-only RPS GenAI Adoption Tracker adapter via public FRED CSV endpoints."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from typing import Any

from ..core.structured_models import (
    AdapterArtifact, AdapterBatch, AdapterFailure, DiscoveryResult, DiscoveryStatus, FetchRequest,
    IngestionStatus, NativeRecord, ReleaseCandidate,
)


FRED_GRAPH = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
FRED_METADATA = "https://fred.stlouisfed.org/graph/api/series/?id="
SERIES = (
    "RPSGENAIUSAGESHAREWORK", "RPSGENAIUSAGESHARELWWORK", "RPSGENAIUSAGESHAREEDLWWOR",
    "RPSGENAIASSISTWRKHRSALL", "RPSGENAITSALL",
)
ENTITY = "WORKER_POP:US:EMPLOYED_18_64"
APPROVED_METADATA = {
    series_id: {"frequency": "Quarterly", "season": "Not Seasonally Adjusted", "units": "Percent"}
    for series_id in SERIES
}


def _quarter(date_value: str) -> str:
    year, month, _ = date_value.split("-", 2)
    return f"{year}-Q{((int(month) - 1) // 3) + 1}"


def parse_fred_csv(series_id: str, payload: bytes, *, fetched_at: datetime,
                   slice_key: str, metadata: dict[str, Any] | None = None) -> list[NativeRecord]:
    if series_id not in SERIES:
        raise ValueError(f"fred_series_out_of_scope:{series_id}")
    metadata = metadata or {}
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    date_field = "DATE" if reader.fieldnames and "DATE" in reader.fieldnames else "observation_date"
    if not reader.fieldnames or date_field not in reader.fieldnames or series_id not in reader.fieldnames:
        raise ValueError(f"fred_schema_missing:{series_id}")
    records: list[NativeRecord] = []
    seen_periods: set[str] = set()
    for row in reader:
        value = row.get(series_id, "")
        if value in {"", ".", "NA"}:
            continue
        try:
            numeric = float(value)
        except ValueError as exc:
            raise ValueError(f"fred_value_invalid:{series_id}:{value}") from exc
        if not 0 <= numeric <= 100:
            raise ValueError(f"fred_percent_out_of_range:{series_id}:{numeric}")
        observation_date = row[date_field]
        if len(observation_date) != 10 or observation_date[5:7] not in {"01", "04", "07", "10"}:
            raise ValueError(f"fred_frequency_drift:{series_id}:{observation_date}")
        period = _quarter(observation_date)
        if period in seen_periods:
            raise ValueError(f"fred_duplicate_period:{series_id}:{period}")
        seen_periods.add(period)
        records.append(NativeRecord(entity_id=ENTITY, provider_field=series_id,
                                    period=period, value=numeric, unit="percent",
                                    period_basis="calendar_quarter", period_start=observation_date,
                                    period_end=observation_date, published_at=fetched_at,
                                    dimensions={"statistical_unit": "US employed adults age 18-64",
                                                "denominator_scope": "employed_adults_18_64",
                                                "technology_scope": "self_reported_GenAI_work_use",
                                                "fred_series_id": series_id, "seasonal_adjustment": "not_seasonally_adjusted",
                                                "frequency": metadata.get("frequency", "Quarterly"),
                                                "source_institution": "Real-Time Population Survey via FRED",
                                                "self_reported": True, "formal_enterprise_deployment_confirmed": False,
                                                "notes": metadata.get("notes", "")},
                                    raw={"provider_row": row, "series_id": series_id,
                                         "series_metadata": metadata}, slice_key=slice_key))
    return records


class RPSGenAIAdoptionAdapter:
    source_id = "rps_genai_adoption"
    dataset_id = "ai_worker_adoption_us"

    def __init__(self, *, client=None, clock=None):
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _bytes(self, client, series_id: str) -> bytes:
        response = client.get(f"{FRED_GRAPH}{series_id}", timeout=60)
        response.raise_for_status()
        return bytes(response.content)

    def _metadata(self, client, series_id: str) -> dict[str, Any]:
        response = client.get(f"{FRED_METADATA}{series_id}", timeout=60)
        response.raise_for_status()
        payload = response.json()
        try:
            chart = payload["chart_series"][0]
            item = chart["series_objects"]["a"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"fred_metadata_schema_drift:{series_id}") from exc
        metadata = {key: item.get(key, "") for key in
                    ("series_id", "title", "frequency", "frequency_short", "season", "season_short",
                     "units", "units_short", "notes", "last_updated")}
        expected = APPROVED_METADATA[series_id]
        for key, value in expected.items():
            if metadata.get(key) != value:
                raise ValueError(f"fred_metadata_drift:{series_id}:{key}:{metadata.get(key)}")
        notes = str(metadata.get("notes", "")).casefold()
        if "employed adults aged 18-64" not in notes or "real-time population survey" not in notes:
            raise ValueError(f"fred_notes_drift:{series_id}")
        metadata["metadata_hash"] = hashlib.sha256(
            json.dumps(metadata, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        metadata["metadata_url"] = f"{FRED_METADATA}{series_id}"
        metadata["series_url"] = f"https://fred.stlouisfed.org/series/{series_id}"
        return metadata

    @staticmethod
    def _logic_failures(records: list[NativeRecord]) -> list[str]:
        values: dict[str, dict[str, float]] = {}
        for record in records:
            values.setdefault(record.period, {})[record.provider_field] = float(record.value)
        failures = []
        for period, row in values.items():
            adoption, weekly, daily = (row.get(SERIES[0]), row.get(SERIES[1]), row.get(SERIES[2]))
            if None not in (adoption, weekly, daily) and not daily <= weekly <= adoption:
                failures.append(f"rps_persistence_order_failed:{period}:{daily}:{weekly}:{adoption}")
        return failures

    def discover(self, request: FetchRequest) -> DiscoveryResult:
        import httpx
        checked = self.clock().astimezone(timezone.utc)
        client = self.client or httpx.Client(follow_redirects=True)
        close = self.client is None
        try:
            candidates = []
            failures: dict[str, str] = {}
            for series_id in SERIES:
                try:
                    metadata = self._metadata(client, series_id)
                    payload = self._bytes(client, series_id)
                    rows = parse_fred_csv(series_id, payload, fetched_at=checked,
                                          slice_key=f"fred:{series_id}", metadata=metadata)
                    if rows:
                        latest = rows[-1]
                        digest = hashlib.sha256(payload).hexdigest()
                        identity = f"FRED:{series_id}:{latest.period}:{metadata['last_updated']}:{digest}"
                        candidates.append(ReleaseCandidate(identity=identity, period=latest.period,
                                                           urls=[f"{FRED_GRAPH}{series_id}", metadata["metadata_url"]],
                                                           methodology_fingerprint=metadata["metadata_hash"],
                                                           metadata={**metadata, "content_hash": digest,
                                                                     "latest_observation_period": latest.period}))
                except Exception as exc:
                    failures[series_id] = f"{type(exc).__name__}:{exc}"
            if not candidates:
                return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                                       status=(DiscoveryStatus.UNREACHABLE if failures else DiscoveryStatus.NOT_YET_PUBLISHED),
                                       diagnostics={"series_failures": failures})
            latest_period = max(item.period for item in candidates)
            identity = hashlib.sha256("|".join(item.identity for item in candidates).encode()).hexdigest()
            return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                                   status=DiscoveryStatus.NEW_RELEASE, latest_upstream_identity=f"FRED:{identity}",
                                   latest_available_period=latest_period, candidates=candidates,
                                   diagnostics={"series_periods": {item.metadata["series_id"]: item.period for item in candidates},
                                                "series_failures": failures,
                                                "series_alignment": len({item.period for item in candidates}) == 1})
        finally:
            if close:
                client.close()

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        import httpx
        fetched = self.clock().astimezone(timezone.utc)
        client = self.client or httpx.Client(follow_redirects=True)
        close = self.client is None
        try:
            records: list[NativeRecord] = []
            artifacts: list[AdapterArtifact] = []
            failures = []
            supplied = (request.query_scope or {}).get("discovery_candidates") or []
            selected = ([ReleaseCandidate.model_validate(item) for item in supplied] if supplied else
                        self.discover(request).candidates)
            for candidate in selected:
                series_id = str(candidate.metadata.get("series_id", ""))
                if series_id not in SERIES:
                    continue
                try:
                    payload = self._bytes(client, series_id)
                    digest = hashlib.sha256(payload).hexdigest()
                    if candidate.metadata.get("content_hash") and digest != candidate.metadata["content_hash"]:
                        raise ValueError(f"fred_content_changed_after_discovery:{series_id}")
                    slice_key = f"fred:{series_id}:{digest[:16]}"
                    parsed = parse_fred_csv(series_id, payload, fetched_at=fetched, slice_key=slice_key,
                                            metadata=candidate.metadata)
                    records.extend(parsed)
                    artifacts.append(AdapterArtifact(artifact_key=slice_key, payload=payload,
                        query_scope={"series_id": series_id}, source_url=f"{FRED_GRAPH}{series_id}",
                        source_version=candidate.identity, media_type="text/csv", retention="query_slice",
                        storage_mode="full", pointer=f"{FRED_GRAPH}{series_id}",
                        metadata={"fred_series_id": series_id, "content_hash": digest,
                                  "candidate": candidate.model_dump(mode="json"), "parser_version": "v2"}))
                except Exception as exc:
                    failures.append({"series_id": series_id, "error": f"{type(exc).__name__}:{exc}"})
            logic_failures = self._logic_failures(records)
            if logic_failures:
                bad_periods = {failure.split(":", 2)[1] for failure in logic_failures}
                # Quarantine the complete bad quarter across all five series so
                # consumers cannot combine valid-looking intensity cells with an
                # invalid adoption/persistence denominator.
                records = [record for record in records if record.period not in bad_periods]
                failures.extend({"series_id": "cross_series", "error": failure}
                                for failure in logic_failures)
            status = (IngestionStatus.PARTIAL if failures and records else
                      IngestionStatus.VALIDATION_FAILED if failures else
                      IngestionStatus.SUCCEEDED if records else IngestionStatus.NO_COVERAGE)
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                status=status, fetched_at=fetched, records=records, artifacts=artifacts,
                                failures=[AdapterFailure(status=IngestionStatus.VALIDATION_FAILED,
                                                         message=item["error"],
                                                         slice_key=f"fred:{item['series_id']}")
                                          for item in failures],
                                provider_metadata={"series_failures": failures,
                                                   "series_periods": {series_id: max(
                                                       (record.period for record in records if record.provider_field == series_id),
                                                       default="") for series_id in SERIES},
                                                   "series_alignment": len({max(
                                                       (record.period for record in records if record.provider_field == series_id),
                                                       default="") for series_id in SERIES if any(
                                                           record.provider_field == series_id for record in records)}) == 1,
                                                   "warnings": ["partial_series_update"] if failures else []})
        finally:
            if close:
                client.close()
