"""OpenRouter public model list: per-model unit prices, one snapshot per UTC day.

The list answers only with the prices in force now, so history exists solely
because snapshots are stored; nothing before the first collection can be
reconstructed.  Prices are published as price facts and never rewrite token
observations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..core.structured_models import (
    AdapterArtifact, AdapterBatch, AdapterFailure, DiscoveryResult,
    DiscoveryStatus, FetchRequest, IngestionStatus, NativeRecord, ReleaseCandidate,
)
from .openrouter_rankings import model_author

API_URL = "https://openrouter.ai/api/v1/models"
SOURCE_ID = "openrouter_pricing"
DATASET_ID = "openrouter_model_pricing_daily"
PARSER_VERSION = "openrouter_pricing/v1"
METHODOLOGY_REGIME = "openrouter_listed_unit_price/v1"
UNIT = "USD_per_1M_tokens"
INPUT_FIELD = "input_price"
OUTPUT_FIELD = "output_price"
# The public list has several hundred models; a much shorter answer is a truncated
# or changed response, not a market where most models vanished overnight.
MIN_LISTED_MODELS = 50
MAX_PAYLOAD_BYTES = 10 * 1024 * 1024

BASIS_LISTED = "listed"
BASIS_TIME_WEIGHTED = "time_weighted_week"
_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_MINUTES_PER_DAY = 24 * 60
_TIME_KEYS = frozenset({"utc_days", "utc_start", "utc_end"})
_LENGTH_KEYS = frozenset({"min_prompt_tokens"})
# Price fields an override may carry.  Any other key is a pricing condition this
# parser does not understand, and the model is left without a reference price.
_OVERRIDE_PRICE_KEYS = frozenset({
    "prompt", "completion", "input_cache_read", "input_cache_write", "input_cache_write_1h",
    "image", "image_output", "audio", "audio_output", "input_audio_cache",
    "internal_reasoning", "web_search",
})


class OpenRouterPricingError(ValueError):
    """The model list cannot safely enter the governed store."""


class UnresolvedPrice(ValueError):
    """One model's price cannot be stated as a single unit price."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _price(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise UnresolvedPrice("price_not_numeric") from exc
    if not number.is_finite():
        raise UnresolvedPrice("price_not_numeric")
    # OpenRouter uses -1 for routers whose price depends on the model they pick.
    if number < 0:
        raise UnresolvedPrice("variable_price")
    return number


def _minute(value: Any) -> int:
    """HHMM integer (0..2400) to minute of day."""
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise UnresolvedPrice("time_rule_invalid") from exc
    hours, minutes = divmod(number, 100)
    if not (0 <= hours <= 24 and 0 <= minutes < 60) or hours * 60 + minutes > _MINUTES_PER_DAY:
        raise UnresolvedPrice("time_rule_invalid")
    return hours * 60 + minutes


def _week_minutes(rule: dict[str, Any]) -> list[int]:
    days = rule.get("utc_days") or list(_DAYS)
    if any(day not in _DAYS for day in days):
        raise UnresolvedPrice("time_rule_invalid")
    start = _minute(rule.get("utc_start", 0))
    # An end of 0 means midnight at the end of the day.
    end = _minute(rule.get("utc_end", 0)) or _MINUTES_PER_DAY
    spans = [(start, end)] if start < end else [(start, _MINUTES_PER_DAY), (0, end)]
    return [_DAYS.index(day) * _MINUTES_PER_DAY + minute
            for day in days for low, high in spans for minute in range(low, high)]


def reference_price(pricing: dict[str, Any]) -> tuple[Decimal, Decimal, str, bool]:
    """Return (input, output, basis, has_prompt_length_tiers) in USD per token.

    A model priced by time of day gets the average over one week weighted by
    window length, because the list's top-level price is whichever window was in
    force at the moment of the request.  A model tiered by prompt length keeps
    its top-level (shortest prompt) price: usage data does not say how long the
    prompts were.
    """
    overrides = pricing.get("overrides") or []
    if not isinstance(overrides, list) or any(not isinstance(item, dict) for item in overrides):
        raise UnresolvedPrice("overrides_invalid")
    time_rules, tiered = [], False
    for rule in overrides:
        conditions = set(rule) - _OVERRIDE_PRICE_KEYS
        if conditions and conditions <= _TIME_KEYS:
            time_rules.append(rule)
        elif conditions and conditions <= _LENGTH_KEYS:
            tiered = True
        else:
            raise UnresolvedPrice("unknown_price_condition")
    if not time_rules:
        return (_price(pricing.get("prompt")), _price(pricing.get("completion")),
                BASIS_LISTED, tiered)
    if tiered:
        raise UnresolvedPrice("mixed_price_conditions")
    return (_week_average(time_rules, "prompt"), _week_average(time_rules, "completion"),
            BASIS_TIME_WEIGHTED, False)


def _week_average(rules: list[dict[str, Any]], field: str) -> Decimal:
    covered: set[int] = set()
    total = Decimal(0)
    for rule in rules:
        minutes = _week_minutes(rule)
        if covered.intersection(minutes):
            raise UnresolvedPrice("time_rules_overlap")
        covered.update(minutes)
        total += _price(rule.get(field)) * len(minutes)
    week = len(_DAYS) * _MINUTES_PER_DAY
    if len(covered) != week:
        raise UnresolvedPrice("time_rules_incomplete")
    return total / week


def cache_read_price(pricing: dict[str, Any]) -> Decimal | None:
    """Listed cache-read price in USD per token, on the same basis as `reference_price`.

    Kept beside the input price as a price fact; the estimate does not use it.
    """
    time_rules = [rule for rule in pricing.get("overrides") or []
                  if _TIME_KEYS.intersection(rule)]
    try:
        if time_rules:
            return _week_average(time_rules, "input_cache_read")
        return _price(pricing.get("input_cache_read"))
    except UnresolvedPrice:
        return None


def price_key(model_id: str, canonical_slug: str) -> str:
    """The identity usage rows carry: dated slug plus the route variant, if any."""
    variant = model_id.partition(":")[2]
    base = canonical_slug.partition(":")[0]
    return f"{base}:{variant}" if variant else base


def parse_models_payload(payload: bytes, *, fetched_at: datetime,
                         min_models: int = MIN_LISTED_MODELS
                         ) -> tuple[list[NativeRecord], dict[str, Any], bytes]:
    """Normalise one model list.  Returns records, diagnostics and the kept payload."""
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenRouterPricingError("invalid_json") from exc
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise OpenRouterPricingError("schema_drift:data_not_list")
    listed = body["data"]
    if len(listed) < min_models:
        raise OpenRouterPricingError(f"model_list_too_short:{len(listed)}")
    period = fetched_at.astimezone(timezone.utc).date().isoformat()
    records: list[NativeRecord] = []
    kept: list[dict[str, Any]] = []
    skipped: dict[str, list[str]] = {}
    seen: set[str] = set()
    for row in listed:
        if not isinstance(row, dict):
            raise OpenRouterPricingError("schema_drift:row_not_object")
        model_id = str(row.get("id") or "").strip().casefold()
        canonical = str(row.get("canonical_slug") or "").strip().casefold()
        pricing = row.get("pricing")
        if not model_id or not canonical or not isinstance(pricing, dict):
            raise OpenRouterPricingError(f"schema_drift:model_fields_missing:{model_id or '?'}")
        kept.append({key: row.get(key) for key in
                     ("id", "canonical_slug", "name", "created", "pricing", "alias_target")})
        # "~vendor/name-latest" entries point at another entry; usage names the target.
        if row.get("alias_target") or model_id.startswith("~"):
            skipped.setdefault("alias", []).append(model_id)
            continue
        key = price_key(model_id, canonical)
        if key in seen:
            raise OpenRouterPricingError(f"duplicate_price_key:{key}")
        seen.add(key)
        try:
            input_price, output_price, basis, tiered = reference_price(pricing)
        except UnresolvedPrice as exc:
            skipped.setdefault(str(exc), []).append(model_id)
            continue
        author, _is_other = model_author(key)
        dims = {"model_permaslug": key, "model_id": model_id, "author": author,
                "methodology_regime": METHODOLOGY_REGIME}
        # The day's artifact keeps every pricing field; rows repeat only what a read needs,
        # so a top-level price that merely follows the time of day creates no revision.
        cache = cache_read_price(pricing)
        raw = {"price_basis": basis, "prompt_length_tiers": tiered,
               "input_cache_read": None if cache is None else float(cache * 1_000_000)}
        for field, per_token in ((INPUT_FIELD, input_price), (OUTPUT_FIELD, output_price)):
            records.append(NativeRecord(
                entity_id=f"OPENROUTER_MODEL:{key.upper().replace('/', ':')}",
                provider_field=field, period=period, period_start=period, period_end=period,
                value=float(per_token * 1_000_000), unit=UNIT, currency="USD",
                period_basis="utc_day", dimensions=dims, raw=raw))
    if not records:
        raise OpenRouterPricingError("no_priced_models")
    trimmed = json.dumps({"data": sorted(kept, key=lambda item: str(item["id"]))},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    diagnostics = {
        "listed_models": len(listed), "priced_models": len(records) // 2,
        "skipped": {reason: sorted(ids) for reason, ids in sorted(skipped.items())},
        "snapshot_date": period, "source_url": API_URL, "parser_version": PARSER_VERSION,
        "original_sha256": hashlib.sha256(payload).hexdigest(), "original_bytes": len(payload),
        "artifact_content": "identifier and pricing fields only",
    }
    return records, diagnostics, trimmed


class OpenRouterPricingAdapter:
    source_id = SOURCE_ID
    dataset_id = DATASET_ID

    def __init__(self, *, opener: Callable[..., Any] | None = None,
                 clock: Callable[[], datetime] | None = None,
                 min_models: int = MIN_LISTED_MODELS):
        self.opener = opener or urlopen
        self.clock = clock or _now
        self.min_models = min_models

    def _request(self) -> bytes:
        # Public endpoint: no credential is sent.
        request = Request(API_URL, headers={"Accept": "application/json",
                                            "User-Agent": "ATS-EvidenceObserver/1.0"})
        try:
            with self.opener(request, timeout=30) as response:
                payload = response.read(MAX_PAYLOAD_BYTES + 1)
        except HTTPError as exc:
            if exc.code == 429:
                raise ConnectionError("openrouter_rate_limited") from exc
            raise ConnectionError(f"openrouter_http_{exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise ConnectionError(f"openrouter_unreachable:{getattr(exc, 'reason', exc)}") from exc
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise OpenRouterPricingError("payload_too_large")
        return payload

    def _request_with_retry(self) -> bytes:
        for attempt in range(2):
            try:
                return self._request()
            except ConnectionError:
                time.sleep(0.05)
        return self._request()

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        fetched = self.clock().astimezone(timezone.utc)

        def failed(status: IngestionStatus, message: str) -> AdapterBatch:
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                status=status, fetched_at=fetched,
                                failures=[AdapterFailure(status=status, message=message)])

        try:
            payload = self._request_with_retry()
            records, diagnostics, trimmed = parse_models_payload(
                payload, fetched_at=fetched, min_models=self.min_models)
        except ConnectionError as exc:
            return failed(IngestionStatus.UNREACHABLE, str(exc))
        except OpenRouterPricingError as exc:
            message = str(exc)
            return failed(IngestionStatus.METHODOLOGY_DRIFT if "schema_drift" in message
                          else IngestionStatus.PARSE_FAILED, message)
        digest = hashlib.sha256(trimmed).hexdigest()
        slice_key = f"openrouter_pricing:{diagnostics['snapshot_date']}:{digest[:16]}"
        records = [record.model_copy(update={"slice_key": slice_key}) for record in records]
        artifact = AdapterArtifact(
            artifact_key=slice_key, payload=trimmed,
            query_scope={"snapshot_date": diagnostics["snapshot_date"]}, source_url=API_URL,
            source_version=f"v1:{diagnostics['snapshot_date']}:{digest}",
            media_type="application/json", retention="query_slice", storage_mode="full",
            pointer=API_URL, metadata=diagnostics)
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id,
            status=IngestionStatus.SUCCEEDED, fetched_at=fetched, records=records,
            artifacts=[artifact],
            provider_metadata={"api_url": API_URL, "parser_version": PARSER_VERSION,
                               "methodology_regime": METHODOLOGY_REGIME,
                               "priced_models": diagnostics["priced_models"],
                               "skipped_counts": {reason: len(ids) for reason, ids
                                                  in diagnostics["skipped"].items()}})

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
            identity=f"OPENROUTER_PRICING:{latest}:{digest}", period=latest, urls=[API_URL],
            methodology_fingerprint=METHODOLOGY_REGIME,
            metadata={"row_count": len(batch.records)})
        return DiscoveryResult(
            source_id=request.source_id, dataset_id=request.dataset_id,
            checked_at=batch.fetched_at, status=DiscoveryStatus.NEW_RELEASE,
            latest_upstream_identity=candidate.identity, latest_available_period=latest,
            candidates=[candidate])
