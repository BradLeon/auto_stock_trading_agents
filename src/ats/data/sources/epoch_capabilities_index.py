"""Epoch Capabilities Index: one snapshot of Epoch AI's published model scores.

The file lists every model with its current estimate, and Epoch refits those
estimates as new benchmark results arrive.  Each collection is stored as its own
dated snapshot, so an earlier estimate stays readable after a refit.

The index has an arbitrary scale.  It is kept apart from the percent benchmark
matrix and takes part in none of its frontier or threshold rules.  The date
Epoch lists beside a model is kept as that and is never used as a release date.

Data: Epoch AI, https://epoch.ai/eci, Creative Commons Attribution 4.0.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..core.structured_models import (
    AdapterArtifact,
    AdapterBatch,
    AdapterFailure,
    DiscoveryResult,
    DiscoveryStatus,
    FetchRequest,
    IngestionStatus,
    NativeRecord,
    ReleaseCandidate,
)

CSV_URL = "https://epoch.ai/data/eci_scores.csv"
SOURCE_ID = "epoch_capabilities_index"
DATASET_ID = "epoch_capabilities_index_models"
PARSER_VERSION = "epoch_capabilities_index/v1"
METHODOLOGY_REGIME = "epoch_eci_anchored/v1"
ATTRIBUTION = "Epoch AI, Epoch Capabilities Index, https://epoch.ai/eci"
LICENSE = "CC BY 4.0"
UNIT = "index_points"
SCORE_FIELD = "eci"
# Epoch fixes the scale by two published reference points.  If either moves, the
# whole scale has been recalibrated and old and new values are not comparable.
CALIBRATION_ANCHORS = {"Claude 3.5 Sonnet": 130.0, "GPT-5": 150.0}
ANCHOR_TOLERANCE = 0.05
# The file has a few hundred models and only grows; far fewer means truncation.
MIN_MODELS = 150
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024
REQUIRED_COLUMNS = ("Model", "Display name", "eci", "eci_ci_low", "eci_ci_high", "date",
                    "Organization")
# First organisation listed by Epoch -> panel lab.  Closed list: no fuzzy match.
ORGANIZATION_LABS = {
    "openai": "OPENAI", "anthropic": "ANTHROPIC", "google deepmind": "GOOGLE",
    "google": "GOOGLE", "xai": "XAI", "deepseek": "DEEPSEEK", "moonshot": "MOONSHOT",
    "tencent": "TENCENT", "z.ai (zhipu ai)": "ZAI", "alibaba": "ALIBABA",
}


class EpochIndexError(ValueError):
    """The score file cannot safely enter the governed store."""


def _now() -> datetime:
    return datetime.now(UTC)


def _number(value: str, *, field: str, model: str, optional: bool = False) -> float | None:
    if optional and not value.strip():
        return None
    try:
        number = float(value)
    except ValueError as exc:
        raise EpochIndexError(f"schema_drift:{field}_not_numeric:{model}") from exc
    if not math.isfinite(number) or not 0 <= number <= 1000:
        raise EpochIndexError(f"schema_drift:{field}_out_of_range:{model}")
    return number


def lab_for(organization: str) -> str:
    return ORGANIZATION_LABS.get(organization.split(",", 1)[0].strip().casefold(), "")


def parse_scores_payload(payload: bytes, *, fetched_at: datetime,
                         min_models: int = MIN_MODELS
                         ) -> tuple[list[NativeRecord], dict[str, Any]]:
    """Normalise one score file into one record per model."""
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EpochIndexError("invalid_encoding") from exc
    reader = csv.DictReader(io.StringIO(text))
    missing = [name for name in REQUIRED_COLUMNS if name not in (reader.fieldnames or [])]
    if missing:
        raise EpochIndexError(f"schema_drift:columns_missing:{','.join(missing)}")
    rows = list(reader)
    if len(rows) < min_models:
        raise EpochIndexError(f"model_list_too_short:{len(rows)}")
    scores: dict[str, float] = {}
    period = fetched_at.astimezone(UTC).date().isoformat()
    records: list[NativeRecord] = []
    for row in rows:
        model = (row.get("Model") or "").strip()
        if not model or None in row.values():
            raise EpochIndexError(f"schema_drift:row_incomplete:{model or '?'}")
        if model in scores:
            raise EpochIndexError(f"duplicate_model:{model}")
        score = _number(row["eci"], field="eci", model=model)
        low = _number(row["eci_ci_low"], field="eci_ci_low", model=model, optional=True)
        high = _number(row["eci_ci_high"], field="eci_ci_high", model=model, optional=True)
        if low is not None and high is not None and low > high:
            raise EpochIndexError(f"schema_drift:interval_reversed:{model}")
        listed = row["date"].strip()
        try:
            date.fromisoformat(listed)
        except ValueError as exc:
            raise EpochIndexError(f"schema_drift:date_invalid:{model}") from exc
        scores[model] = score
        organization = row["Organization"].strip()
        slug = re.sub(r"[^A-Z0-9.]+", "-", model.upper()).strip("-")
        records.append(NativeRecord(
            entity_id=f"EPOCH_MODEL:{slug}", provider_field=SCORE_FIELD,
            period=period, period_start=period, period_end=period, value=score, unit=UNIT,
            period_basis="snapshot",
            dimensions={"model_name": model, "organization": organization,
                        "lab_id": lab_for(organization), "epoch_listed_date": listed,
                        "methodology_regime": METHODOLOGY_REGIME},
            raw={"eci_ci_low": low, "eci_ci_high": high,
                 "display_name": row["Display name"].strip(),
                 "accessibility": (row.get("Model accessibility") or "").strip()}))
    for name, expected in CALIBRATION_ANCHORS.items():
        actual = scores.get(name)
        if actual is None or abs(actual - expected) > ANCHOR_TOLERANCE:
            raise EpochIndexError(
                f"schema_drift:calibration_changed:{name}={actual} expected {expected}")
    if len({record.entity_id for record in records}) != len(records):
        raise EpochIndexError("duplicate_model_identity")
    diagnostics = {
        "listed_models": len(rows), "snapshot_date": period, "source_url": CSV_URL,
        "parser_version": PARSER_VERSION, "license": LICENSE, "attribution": ATTRIBUTION,
        "latest_epoch_listed_date": max(r.dimensions["epoch_listed_date"] for r in records),
    }
    return records, diagnostics


class EpochCapabilitiesIndexAdapter:
    source_id = SOURCE_ID
    dataset_id = DATASET_ID

    def __init__(self, *, opener: Callable[..., Any] | None = None,
                 clock: Callable[[], datetime] | None = None,
                 min_models: int = MIN_MODELS):
        self.opener = opener or urlopen
        self.clock = clock or _now
        self.min_models = min_models

    def _request(self) -> bytes:
        # Public file: no credential is sent.
        request = Request(CSV_URL, headers={"Accept": "text/csv",
                                            "User-Agent": "ATS-EvidenceObserver/1.0"})
        try:
            with self.opener(request, timeout=30) as response:
                payload = response.read(MAX_PAYLOAD_BYTES + 1)
        except HTTPError as exc:
            raise ConnectionError(f"epoch_http_{exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise ConnectionError(f"epoch_unreachable:{getattr(exc, 'reason', exc)}") from exc
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise EpochIndexError("payload_too_large")
        return payload

    def _request_with_retry(self) -> bytes:
        for _attempt in range(2):
            try:
                return self._request()
            except ConnectionError:
                time.sleep(0.05)
        return self._request()

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        fetched = self.clock().astimezone(UTC)

        def failed(status: IngestionStatus, message: str) -> AdapterBatch:
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                status=status, fetched_at=fetched,
                                failures=[AdapterFailure(status=status, message=message)])

        try:
            payload = self._request_with_retry()
            records, diagnostics = parse_scores_payload(
                payload, fetched_at=fetched, min_models=self.min_models)
        except ConnectionError as exc:
            return failed(IngestionStatus.UNREACHABLE, str(exc))
        except EpochIndexError as exc:
            message = str(exc)
            return failed(IngestionStatus.METHODOLOGY_DRIFT if "schema_drift" in message
                          else IngestionStatus.PARSE_FAILED, message)
        digest = hashlib.sha256(payload).hexdigest()
        slice_key = f"epoch_eci:{diagnostics['snapshot_date']}:{digest[:16]}"
        records = [record.model_copy(update={"slice_key": slice_key}) for record in records]
        artifact = AdapterArtifact(
            artifact_key=slice_key, payload=payload,
            query_scope={"snapshot_date": diagnostics["snapshot_date"]}, source_url=CSV_URL,
            source_version=f"v1:{diagnostics['snapshot_date']}:{digest}",
            media_type="text/csv", retention="query_slice", storage_mode="full",
            pointer=CSV_URL, metadata=diagnostics)
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id,
            status=IngestionStatus.SUCCEEDED, fetched_at=fetched, records=records,
            artifacts=[artifact],
            provider_metadata={"source_url": CSV_URL, "parser_version": PARSER_VERSION,
                               "methodology_regime": METHODOLOGY_REGIME, "license": LICENSE,
                               "listed_models": diagnostics["listed_models"]})

    def discover(self, request: FetchRequest) -> DiscoveryResult:
        batch = self.fetch(request)
        if not batch.records:
            return DiscoveryResult(
                source_id=request.source_id, dataset_id=request.dataset_id,
                checked_at=batch.fetched_at, status=DiscoveryStatus.UNREACHABLE,
                diagnostics={"failures": [item.message for item in batch.failures]})
        latest = batch.records[0].period
        digest = batch.artifacts[0].source_version.rsplit(":", 1)[1]
        candidate = ReleaseCandidate(
            identity=f"EPOCH_ECI:{latest}:{digest}", period=latest, urls=[CSV_URL],
            methodology_fingerprint=METHODOLOGY_REGIME,
            metadata={"row_count": len(batch.records)})
        return DiscoveryResult(
            source_id=request.source_id, dataset_id=request.dataset_id,
            checked_at=batch.fetched_at, status=DiscoveryStatus.NEW_RELEASE,
            latest_upstream_identity=candidate.identity, latest_available_period=latest,
            candidates=[candidate])
