"""OpenRouter Rankings Data API adapter.

The adapter deliberately consumes the documented daily token dataset only.  It
does not scrape the rankings page, infer request share, or turn token volume
into revenue.  ``Other`` is a first-class source bucket and model authors are
resolved from an explicit, versioned alias table; unknown authors stay
unknown.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
import time
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..core.structured_models import (
    AdapterArtifact, AdapterBatch, AdapterFailure, DiscoveryResult,
    DiscoveryStatus, FetchRequest, IngestionStatus, NativeRecord, ReleaseCandidate,
)

API_URL = "https://openrouter.ai/api/v1/datasets/rankings-daily"
PAGE_URL = "https://openrouter.ai/rankings"
SOURCE_ID = "openrouter_rankings"
DATASET_ID = "openrouter_rankings_daily"
PARSER_VERSION = "openrouter_rankings/v1"
METHODOLOGY_REGIME = "openrouter_public_routed_tokens/v1"
LICENSE = "CC BY 4.0"
DEFAULT_LOOKBACK_DAYS = 14
MAX_DAYS_PER_REQUEST = 30
MAX_REQUESTS_PER_MINUTE = 30
MAX_REQUESTS_PER_DAY = 500

AUTHOR_ALIASES = {
    "openai": "openai", "anthropic": "anthropic", "google": "google",
    "deepseek": "deepseek", "meta-llama": "meta", "meta": "meta",
    "mistralai": "mistral", "mistral": "mistral", "qwen": "alibaba",
    "tencent": "tencent", "z-ai": "z-ai", "zai": "z-ai",
    "microsoft": "microsoft", "x-ai": "xai", "xai": "xai",
}


class OpenRouterDataError(ValueError):
    """The source response cannot safely enter the governed store."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_date(value: Any) -> date:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise OpenRouterDataError(f"invalid_date:{value}") from exc


def _slug(value: Any) -> str:
    return str(value or "").strip().casefold()


def model_author(model_permaslug: str) -> tuple[str, bool]:
    """Return (canonical author, is_other) without guessing unknown vendors."""
    slug = _slug(model_permaslug)
    if slug in {"other", "unknown", ""}:
        return "unattributed_other", True
    prefix = slug.split("/", 1)[0]
    return AUTHOR_ALIASES.get(prefix, "unknown_author"), False


def _tokens(value: Any) -> int:
    try:
        number = int(str(value).replace(",", ""))
    except (TypeError, ValueError) as exc:
        raise OpenRouterDataError(f"invalid_total_tokens:{value}") from exc
    if number < 0:
        raise OpenRouterDataError(f"negative_total_tokens:{value}")
    return number


def parse_rankings_payload(payload: bytes | str, *, fetched_at: datetime | None = None,
                           source_url: str = API_URL) -> tuple[list[NativeRecord], dict[str, Any]]:
    """Normalize one API response and run date/model identity quality gates."""
    fetched_at = fetched_at or _now()
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OpenRouterDataError("invalid_json:utf8") from exc
    try:
        body = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise OpenRouterDataError("invalid_json") from exc
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise OpenRouterDataError("schema_drift:data_not_list")
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    # ``version`` and scope are part of the public API contract.  A new
    # version or a private/account-only response must not silently mix into
    # the public routed-token regime.
    api_version = str(meta.get("version", "v1"))
    if api_version not in {"v1", "1"}:
        raise OpenRouterDataError(f"methodology_drift:version:{api_version}")
    scope = meta.get("scope") or meta.get("request_scope") or ""
    if scope and str(scope).casefold() not in {"public", "public_routed", "public-routed"}:
        raise OpenRouterDataError(f"methodology_drift:scope:{scope}")
    seen: set[tuple[str, str]] = set()
    records: list[NativeRecord] = []
    dates: set[str] = set()
    other_counts: dict[str, int] = {}
    totals_by_date: dict[str, int] = {}
    for row in body["data"]:
        if not isinstance(row, dict):
            raise OpenRouterDataError("schema_drift:row_not_object")
        model = str(row.get("model_permaslug") or row.get("model") or "").strip()
        if not model:
            raise OpenRouterDataError("schema_drift:model_permaslug_missing")
        period = _as_date(row.get("date")).isoformat()
        key = (period, model)
        if key in seen:
            raise OpenRouterDataError(f"duplicate_model_date:{period}:{model}")
        seen.add(key)
        value = _tokens(row.get("total_tokens"))
        rank = row.get("rank")
        if rank not in (None, ""):
            try:
                rank = int(rank)
            except (TypeError, ValueError) as exc:
                raise OpenRouterDataError(f"invalid_rank:{rank}") from exc
            if rank < 1 or rank > 50:
                raise OpenRouterDataError(f"invalid_rank:{rank}")
        author, is_other = model_author(model)
        dates.add(period)
        totals_by_date[period] = totals_by_date.get(period, 0) + value
        if is_other:
            other_counts[period] = other_counts.get(period, 0) + 1
        dims = {
            "model_permaslug": model,
            "model_name": row.get("model_name") or row.get("name") or model,
            "model_author": row.get("author") or author,
            "author": author,
            "rank": rank,
            "is_other": is_other,
            "is_free_route": ":free" in model or "(free)" in str(row.get("model_name") or "").casefold(),
            "reference_date": period,
            "coverage": "top_50_plus_other" if is_other or len(body["data"]) <= 51 else "provider_response",
            "methodology_regime": METHODOLOGY_REGIME,
            "source_url": source_url,
        }
        records.append(NativeRecord(
            entity_id=f"OPENROUTER_MODEL:{model.upper().replace('/', ':')}",
            provider_field="total_tokens", period=period, period_start=period,
            period_end=period, value=value, unit="tokens", period_basis="utc_day",
            dimensions=dims, raw={"provider_row": row, "meta": meta},
        ))
    if not records:
        raise OpenRouterDataError("no_data_rows")
    for period, count in other_counts.items():
        if count > 1:
            raise OpenRouterDataError(f"other_not_unique:{period}")
    declared_total = meta.get("total_tokens")
    if isinstance(declared_total, dict):
        for period, expected in declared_total.items():
            if _tokens(expected) != totals_by_date.get(str(period)[:10], 0):
                raise OpenRouterDataError(f"token_conservation_failed:{period}")
    elif declared_total not in (None, "") and len(totals_by_date) == 1:
        if _tokens(declared_total) != next(iter(totals_by_date.values())):
            raise OpenRouterDataError("token_conservation_failed")
    diagnostics = {
        "as_of": meta.get("as_of"), "version": api_version,
        "start_date": meta.get("start_date"), "end_date": meta.get("end_date"),
        "window_days": meta.get("window_days"), "source_url": source_url,
        "dates": sorted(dates), "row_count": len(records),
        "payload_sha256": hashlib.sha256((payload if isinstance(payload, str) else str(payload)).encode()).hexdigest(),
        "license": LICENSE, "citation": "OpenRouter Rankings Data API / CC BY 4.0",
        "parser_version": PARSER_VERSION,
    }
    return records, diagnostics


def validate_rankings_records(records: Iterable[NativeRecord]) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    per_date: dict[str, list[int]] = {}
    other: dict[str, int] = {}
    for record in records:
        key = (record.period, record.entity_id)
        if key in seen:
            errors.append(f"duplicate_cell:{record.period}:{record.entity_id}")
        seen.add(key)
        try:
            value = int(record.value)
        except (TypeError, ValueError):
            errors.append(f"token_not_integer:{key}")
            continue
        if value < 0:
            errors.append(f"token_negative:{key}")
        per_date.setdefault(record.period, []).append(value)
        if (record.dimensions or {}).get("is_other"):
            other[record.period] = other.get(record.period, 0) + 1
    for period, count in other.items():
        if count != 1:
            errors.append(f"other_count:{period}:{count}")
    return sorted(set(errors))


class OpenRouterRankingsAdapter:
    source_id = SOURCE_ID
    dataset_id = DATASET_ID

    def __init__(self, *, api_key: str | None = None,
                 opener: Callable[..., Any] | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.api_key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
        self.opener = opener or urlopen
        self.clock = clock or _now

    @staticmethod
    def _date_windows(start: date, end: date, days: int = MAX_DAYS_PER_REQUEST):
        current = start
        while current <= end:
            window_end = min(end, current + timedelta(days=days - 1))
            yield current, window_end
            current = window_end + timedelta(days=1)

    def _request(self, start: date, end: date) -> bytes:
        if not self.api_key:
            raise PermissionError("openrouter_api_key_missing")
        query = urlencode({"start_date": start.isoformat(), "end_date": end.isoformat(), "period": "day"})
        req = Request(f"{API_URL}?{query}", headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json", "User-Agent": "ATS-EvidenceObserver/1.0"})
        try:
            with self.opener(req, timeout=30) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise PermissionError(f"openrouter_http_{exc.code}") from exc
            if exc.code == 429:
                raise RuntimeError("openrouter_rate_limited") from exc
            raise ConnectionError(f"openrouter_http_{exc.code}") from exc
        except URLError as exc:
            raise ConnectionError(f"openrouter_unreachable:{exc.reason}") from exc

    def _request_with_retry(self, start: date, end: date) -> bytes:
        last: Exception | None = None
        for attempt in range(3):
            try:
                return self._request(start, end)
            except PermissionError:
                raise
            except (RuntimeError, ConnectionError) as exc:
                last = exc
                if attempt < 2:
                    # Keep the retry bounded; the scheduler owns the larger
                    # backoff and the API's 30/min, 500/day limits.
                    time.sleep(0.05 * (attempt + 1))
        assert last is not None
        raise last

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        fetched = self.clock().astimezone(timezone.utc)
        query = request.query_scope or {}
        end = _as_date(query.get("end_date") or (fetched.date() - timedelta(days=1)))
        start = _as_date(query.get("start_date") or query.get("initial_history_start_date")
                         or (end - timedelta(days=DEFAULT_LOOKBACK_DAYS - 1)))
        if start > end:
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                status=IngestionStatus.VALIDATION_FAILED, fetched_at=fetched,
                                failures=[AdapterFailure(status=IngestionStatus.VALIDATION_FAILED, message="start_after_end")])
        records: list[NativeRecord] = []
        artifacts: list[AdapterArtifact] = []
        failures: list[AdapterFailure] = []
        for window_start, window_end in self._date_windows(start, end):
            try:
                payload = self._request_with_retry(window_start, window_end)
                digest = hashlib.sha256(payload).hexdigest()
                parsed, diagnostics = parse_rankings_payload(payload, fetched_at=fetched)
                slice_key = f"openrouter:{window_start}:{window_end}:{digest[:16]}"
                parsed = [record.model_copy(update={"slice_key": slice_key}) for record in parsed]
                quality = validate_rankings_records(parsed)
                if quality:
                    raise OpenRouterDataError("quality_gate_failed:" + ";".join(quality))
                records.extend(parsed)
                artifacts.append(AdapterArtifact(artifact_key=slice_key, payload=payload,
                    query_scope={"start_date": window_start.isoformat(), "end_date": window_end.isoformat(), "period": "day"},
                    source_url=API_URL, source_version=f"v1:{window_start}:{window_end}:{digest}",
                    media_type="application/json", retention="query_slice", storage_mode="full",
                    pointer=API_URL, metadata={**diagnostics, "credential_redacted": True,
                                               "window_start": window_start.isoformat(), "window_end": window_end.isoformat()}))
            except PermissionError as exc:
                failures.append(AdapterFailure(status=IngestionStatus.UNAUTHORIZED, message=str(exc), slice_key=f"{window_start}:{window_end}"))
            except RuntimeError as exc:
                failures.append(AdapterFailure(status=IngestionStatus.STALE if "rate" in str(exc) else IngestionStatus.UNREACHABLE, message=str(exc), slice_key=f"{window_start}:{window_end}"))
            except (ConnectionError, OpenRouterDataError) as exc:
                message = str(exc)
                status = (IngestionStatus.METHODOLOGY_DRIFT
                          if "schema" in message or "methodology_drift" in message
                          else IngestionStatus.PARSE_FAILED)
                failures.append(AdapterFailure(status=status, message=str(exc), slice_key=f"{window_start}:{window_end}"))
        if records and failures:
            status = IngestionStatus.PARTIAL
        elif records:
            status = IngestionStatus.SUCCEEDED
        elif failures:
            status = failures[0].status
        else:
            status = IngestionStatus.NO_COVERAGE
        return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id, status=status,
                            fetched_at=fetched, records=records, artifacts=artifacts, failures=failures,
                            provider_metadata={"api_url": API_URL, "period": "day", "license": LICENSE,
                                               "request_budget": {"requests_per_minute": MAX_REQUESTS_PER_MINUTE, "requests_per_day": MAX_REQUESTS_PER_DAY},
                                               "parser_version": PARSER_VERSION, "methodology_regime": METHODOLOGY_REGIME})

    def discover(self, request: FetchRequest) -> DiscoveryResult:
        checked = self.clock().astimezone(timezone.utc)
        try:
            batch = self.fetch(request)
        except Exception as exc:
            return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                                   status=DiscoveryStatus.UNREACHABLE, diagnostics={"error": str(exc)})
        if not batch.records:
            status = DiscoveryStatus.ACCESS_REQUIRED if any(f.status == IngestionStatus.UNAUTHORIZED for f in batch.failures) else DiscoveryStatus.UNREACHABLE
            return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                                   status=status, diagnostics={"failures": [f.message for f in batch.failures]})
        latest = max(row.period for row in batch.records)
        digest = hashlib.sha256(json.dumps([row.model_dump(mode="json") for row in batch.records], sort_keys=True).encode()).hexdigest()
        candidate = ReleaseCandidate(identity=f"OPENROUTER:{latest}:{digest}", period=latest, urls=[API_URL],
                                     methodology_fingerprint=METHODOLOGY_REGIME,
                                     metadata={"row_count": len(batch.records), "license": LICENSE})
        return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                               status=DiscoveryStatus.NEW_RELEASE, latest_upstream_identity=candidate.identity,
                               latest_available_period=latest, candidates=[candidate],
                               diagnostics={"failures": [f.message for f in batch.failures]})


OpenRouterAdapter = OpenRouterRankingsAdapter
