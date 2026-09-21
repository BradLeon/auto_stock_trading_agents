"""Census BTOS Core AI adoption adapter (new any-business-function regime only)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import tempfile
from typing import Any

from ..core.structured_models import (
    AdapterArtifact, AdapterBatch, DiscoveryResult, DiscoveryStatus, FetchRequest,
    IngestionStatus, NativeRecord, ReleaseCandidate,
)


BTOS_API = "https://www.census.gov/hfp/btos/api"
BTOS_HISTORICAL_DOWNLOADS = "https://www.census.gov/hfp/btos/data_downloads"
NEW_REGIME_START = date(2025, 11, 17)
CURRENT_FIELD = "current_ai_yes_pct"
EXPECTED_FIELD = "expected_ai_yes_pct"
APPROVED_QUESTION_PREFIXES = (
    "in the last two weeks, did this business use artificial intelligence in any of its business functions?",
    "during the next six months, do you think this business will be using artificial intelligence in any of its business functions?",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_date(value: Any) -> date | None:
    text = str(value or "")[:20]
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        for pattern in ("%d-%b-%y", "%d-%b-%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(text.split(" ")[0], pattern).date()
            except ValueError:
                continue
        return None


def _norm(text: Any) -> str:
    return " ".join(str(text or "").casefold().replace("–", "-").split())


def _question_norm(text: Any) -> str:
    """Canonicalize harmless provider punctuation without weakening semantics."""
    return _norm(text).replace("artificial intelligence (ai)", "artificial intelligence")


def _value(row: dict[str, Any], *keys: str) -> Any:
    # Caller order expresses semantic precedence (e.g. ANSWER over OPTION_TEXT).
    normalized = {_norm(key).replace("_", " "): value for key, value in row.items()}
    for key in keys:
        if (value := normalized.get(_norm(key).replace("_", " "))) not in (None, ""):
            return value
    return ""


def question_fingerprint(question: dict[str, Any]) -> str:
    # Stable methodology fingerprint: provider IDs vary by collection period and
    # must not turn a stable question regime into a new methodology.
    payload = _question_norm(_value(question, "text", "question_text", "question"))
    return hashlib.sha256(payload.encode()).hexdigest()


def is_new_ai_question(question: dict[str, Any], *, start: date | None = None) -> bool:
    text = _question_norm(_value(question, "text", "question_text", "question", "label"))
    start = start or _as_date(_value(question, "collection_start", "start_date"))
    timing = _norm(_value(question, "option_text", "question_kind", "label"))
    valid_timing = "last two weeks" in text or "six months" in text or "future" in timing
    approved = any(text.startswith(prefix) for prefix in APPROVED_QUESTION_PREFIXES)
    return bool(start and start >= NEW_REGIME_START and valid_timing
                and approved and "artificial intelligence" in text and "any of its business functions" in text)


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("data", "periods", "results", "observations"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def _period_id(row: dict[str, Any]) -> str:
    return str(_value(row, "period", "period_id", "id") or "")


def _period_window(row: dict[str, Any]) -> tuple[str, str]:
    raw_start = str(_value(row, "collection_start", "start_date", "period_start") or "")
    raw_end = str(_value(row, "collection_end", "end_date", "period_end") or "")
    start = (_as_date(raw_start).isoformat() if _as_date(raw_start) else raw_start.strip()[:10])
    end = (_as_date(raw_end).isoformat() if _as_date(raw_end) else raw_end.strip()[:10])
    return start, end


def _candidate(period: dict[str, Any], questions: list[dict[str, Any]]) -> ReleaseCandidate | None:
    start, end = _period_window(period)
    start_date = _as_date(start)
    if not start_date or start_date < NEW_REGIME_START:
        return None
    identifier = _period_id(period)
    questions_for_period = [item for item in questions if str(_value(item, "period_id", "period")) == identifier]
    matching = [item for item in questions_for_period if is_new_ai_question(item, start=start_date)]
    # Both current and expected Core questions must use the new wording; period
    # 85--87 revised only one of the two and are therefore out of scope.
    timing = {_norm(_value(item, "option_text", "question_kind", "label")) for item in matching}
    has_current = any("current" in item or "last two" in item for item in timing)
    has_expected = any("future" in item or "expect" in item or "six month" in item for item in timing)
    if len(matching) < 2 or not (has_current and has_expected):
        return None
    if not identifier:
        return None
    fingerprint = hashlib.sha256("|".join(sorted(question_fingerprint(item) for item in matching)).encode()).hexdigest()
    return ReleaseCandidate(
        identity=f"BTOS:{identifier}:{fingerprint[:16]}", period=identifier,
        urls=[f"{BTOS_API}/periods/{identifier}/data"], methodology_fingerprint=fingerprint,
        metadata={"period": period, "questions": matching, "period_start": start, "period_end": end},
    )


def _entity(row: dict[str, Any]) -> str:
    naics3 = _value(row, "NAICS3")
    naics2 = _value(row, "NAICS2")
    naics = naics3 or naics2 or _value(row, "naics", "naics_code")
    size = _value(row, "EMPSIZE", "employment_size", "size")
    if naics and size:
        return f"BUSINESS_POP:US:NAICS:{naics}:EMP:{size}"
    if naics:
        return f"BUSINESS_POP:US:NAICS:{naics}"
    if size:
        return f"BUSINESS_POP:US:EMP:{size}"
    return "BUSINESS_POP:US:ALL"


def _stratum(row: dict[str, Any]) -> str:
    naics2, naics3, size = _value(row, "NAICS2"), _value(row, "NAICS3"), _value(row, "EMPSIZE")
    if naics3 and size:
        return "naics3_by_employment_size"
    if naics2 and size:
        return "naics2_by_employment_size"
    if naics3:
        return "naics3"
    if naics2:
        return "naics2"
    if size:
        return "employment_size"
    return "national"


def _number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _value(row, key)
        if value not in {None, "", "NA", "N/A", "-"}:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def parse_btos_period(*, rows: list[dict[str, Any]], candidate: ReleaseCandidate,
                      fetched_at: datetime) -> tuple[list[NativeRecord], bytes]:
    """Normalize provider rows while retaining all answers for audit and QA."""
    meta = candidate.metadata
    period_start, period_end = meta.get("period_start", ""), meta.get("period_end", "")
    try:
        published_at = parsedate_to_datetime(meta.get("last_modified", "")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        published_at = fetched_at
    output: list[NativeRecord] = []
    kept: list[dict[str, Any]] = []
    response_groups: dict[tuple[str, str], list[float]] = {}
    seen: set[tuple[str, str, str]] = set()
    eligible_source_rows = 0
    published_source_rows = 0
    for row in rows:
        geography = _norm(_value(row, "geography", "geo_level", "level"))
        if geography in {"state", "msa", "metropolitan statistical area"}:
            continue
        if _value(row, "STATE", "state") or _value(row, "MSA", "msa"):
            continue
        question_text = _norm(_value(row, "question_text", "question") or
                              _value((meta.get("questions") or [{}])[0], "question", "text"))
        if "any of its business functions" not in question_text or "artificial intelligence" not in question_text:
            continue
        required = ("ANSWER", "OPTION_TEXT")
        if any(not _value(row, field) for field in required):
            raise ValueError(f"btos_schema_drift:missing_{'_or_'.join(required).lower()}")
        eligible_source_rows += 1
        kept.append(row)  # retain suppressed cells in the immutable filtered artifact
        answer = _norm(_value(row, "answer", "answer_text", "response", "option_text"))
        value = _number(row, "ESTIMATE_PERCENTAGE", "estimate", "value", "percent", "pct")
        if value is None:
            continue
        published_source_rows += 1
        if not 0 <= value <= 100:
            raise ValueError(f"btos_percent_out_of_range:{value}")
        question_kind = _norm(_value(row, "question_kind", "question_id", "option_text") or question_text)
        expected = "future" in question_kind or "six month" in question_text or "expect" in question_text
        yes = answer in {"yes", "y"}
        duplicate_key = (_entity(row), question_kind, answer)
        if duplicate_key in seen:
            raise ValueError(f"btos_duplicate_cell:{duplicate_key}")
        seen.add(duplicate_key)
        response_groups.setdefault((_entity(row), question_kind), []).append(value)
        # Preserve every published answer under one governed raw-response metric;
        # emit a second headline record for Yes so consumers need not reconstruct it.
        field = "btos_response_share"
        dimensions = {
            "statistical_unit": "US employer business", "denominator_scope": "in_scope_employer_businesses",
            "question_regime": "any_business_function_v2", "question_fingerprint": candidate.methodology_fingerprint,
            "answer": answer or "unknown", "estimate_type": row.get("estimate_type", "weighted_share"),
            "stratum_type": _stratum(row),
            "naics": _value(row, "NAICS3", "NAICS2", "naics", "naics_code"),
            "employment_size": _value(row, "EMPSIZE", "employment_size", "size"),
        }
        output.append(NativeRecord(entity_id=_entity(row), provider_field=field, period=candidate.period,
                                   value=value, unit="percent", period_basis="survey_reference_window",
                                   period_start=period_start, period_end=period_end, published_at=published_at,
                                   dimensions=dimensions, raw={"provider_row": row, "candidate": candidate.model_dump(mode="json")},
                                   slice_key=f"btos:{candidate.period}"))
        if yes:
            output.append(NativeRecord(entity_id=_entity(row), provider_field=(EXPECTED_FIELD if expected else CURRENT_FIELD),
                                       period=candidate.period, value=value, unit="percent", period_basis="survey_reference_window",
                                       period_start=period_start, period_end=period_end, published_at=published_at,
                                       dimensions={**dimensions, "headline": True},
                                       raw={"provider_row": row, "candidate": candidate.model_dump(mode="json")},
                                       slice_key=f"btos:{candidate.period}"))
        se = _number(row, "standard_error", "se", "estimate_se")
        if se is not None and se < 0:
            raise ValueError(f"btos_standard_error_negative:{se}")
        if yes and se is not None:
            output.append(NativeRecord(entity_id=_entity(row), provider_field=("expected_ai_yes_se" if expected else "current_ai_yes_se"),
                                       period=candidate.period, value=se, unit="percent", period_basis="survey_reference_window",
                                       period_start=period_start, period_end=period_end, published_at=published_at,
                                       dimensions=dimensions, raw={"provider_row": row, "candidate": candidate.model_dump(mode="json")},
                                       slice_key=f"btos:{candidate.period}"))
    bad_sums = [key for key, values in response_groups.items()
                if len(values) == 3 and abs(sum(values) - 100.0) > 0.15]
    if bad_sums:
        raise ValueError(f"btos_answer_sum_failed:{len(bad_sums)}")
    if len(kept) != eligible_source_rows:
        raise ValueError("btos_reconciliation_impossible")
    response_observations = sum(record.provider_field == "btos_response_share" for record in output)
    if response_observations != published_source_rows:
        raise ValueError(f"btos_source_observation_reconciliation_failed:{published_source_rows}:{response_observations}")
    payload = "\n".join(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in kept).encode()
    return output, payload


class CensusBTOSAdapter:
    source_id = "us_census_btos"
    dataset_id = "ai_enterprise_adoption_us"

    def __init__(self, *, client=None, clock=None):
        self.client = client
        self.clock = clock or _now

    def _json(self, client, url: str) -> Any:
        response = client.get(url, timeout=60)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _download_records(client, url: str, *, max_bytes: int = 52_428_800) -> tuple[list[dict[str, Any]], str, int]:
        """Stream the official response to a spool while hashing every byte."""
        digest = hashlib.sha256()
        size = 0
        with tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024) as spool:
            with client.stream("GET", url, timeout=60, follow_redirects=True) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError(f"btos_response_too_large:{size}")
                    digest.update(chunk)
                    spool.write(chunk)
            spool.seek(0)
            payload = json.load(spool)
        return _records(payload), digest.hexdigest(), size

    @staticmethod
    def _revision_metadata(client, url: str) -> dict[str, Any]:
        """Use official response metadata as a cheap revision detector."""
        response = client.head(url, timeout=60, follow_redirects=True)
        response.raise_for_status()
        last_modified = response.headers.get("last-modified", "")
        return {
            "request_identity": f"HEAD {url}",
            "last_modified": last_modified,
            "content_type": response.headers.get("content-type", ""),
        }

    def discover(self, request: FetchRequest) -> DiscoveryResult:
        import httpx
        checked = self.clock().astimezone(timezone.utc)
        client = self.client or httpx.Client(follow_redirects=True)
        close = self.client is None
        try:
            periods = _records(self._json(client, f"{BTOS_API}/periods"))
            questions = _records(self._json(client, f"{BTOS_API}/questions"))
            # The public endpoint currently returns ["bad request"] without
            # parameters. Keep that fact explicit and use answer labels embedded
            # in each period's data rows as the authoritative fallback.
            try:
                answer_metadata = _records(self._json(client, f"{BTOS_API}/answers"))
            except Exception:
                answer_metadata = []
            try:
                historical_metadata = self._revision_metadata(client, BTOS_HISTORICAL_DOWNLOADS)
            except Exception as exc:
                historical_metadata = {"request_identity": f"HEAD {BTOS_HISTORICAL_DOWNLOADS}",
                                       "status": "unreachable", "error": type(exc).__name__}
            scheduled = [candidate for period in periods if (candidate := _candidate(period, questions))]
            candidates: list[ReleaseCandidate] = []
            future_periods: list[str] = []
            for candidate in scheduled:
                end = _as_date(candidate.metadata.get("period_end"))
                if end and end > checked.date():
                    future_periods.append(candidate.period)
                    continue
                revision = self._revision_metadata(client, candidate.urls[0])
                revision_token = revision.get("last_modified") or "published-no-last-modified"
                identity = f"{candidate.identity}:{hashlib.sha256(revision_token.encode()).hexdigest()[:16]}"
                candidates.append(candidate.model_copy(update={
                    "identity": identity,
                    "metadata": {**candidate.metadata, **revision,
                                 "periods_request_identity": f"GET {BTOS_API}/periods",
                                 "questions_request_identity": f"GET {BTOS_API}/questions",
                                 "answers_request_identity": f"GET {BTOS_API}/answers",
                                 "answers_metadata_row_count": len(answer_metadata),
                                 "answers_semantics": "embedded_in_period_data_rows",
                                 "historical_downloads": historical_metadata},
                }))
            if not candidates:
                return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id,
                                       checked_at=checked, status=DiscoveryStatus.NOT_YET_PUBLISHED,
                                       diagnostics={"period_count": len(periods), "question_count": len(questions),
                                                    "answer_metadata_count": len(answer_metadata),
                                                    "historical_downloads": historical_metadata,
                                                    "future_periods": future_periods})
            candidates.sort(key=lambda item: int(item.period) if item.period.isdigit() else -1)
            latest = candidates[-1]
            collection_identity = "BTOS_COLLECTION:" + hashlib.sha256(
                "|".join(item.identity for item in candidates).encode()).hexdigest()
            return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id,
                                   checked_at=checked, status=DiscoveryStatus.NEW_RELEASE,
                                   latest_upstream_identity=collection_identity,
                                   latest_available_period=(latest.metadata.get("period_end") or latest.period),
                                   candidates=candidates, diagnostics={"period_count": len(periods),
                                                                       "answer_metadata_count": len(answer_metadata),
                                                                       "historical_downloads": historical_metadata,
                                                                       "published_candidate_count": len(candidates),
                                                                       "latest_period_identity": latest.identity,
                                                                       "future_periods": future_periods})
        finally:
            if close:
                client.close()

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        import httpx
        fetched = self.clock().astimezone(timezone.utc)
        client = self.client or httpx.Client(follow_redirects=True)
        close = self.client is None
        try:
            candidates = (request.query_scope or {}).get("discovery_candidates") or []
            if candidates:
                selected_candidates = [ReleaseCandidate.model_validate(item) for item in candidates]
            else:
                discovery = self.discover(request)
                if not discovery.candidates:
                    return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                        status=IngestionStatus.NOT_YET_PUBLISHED, fetched_at=fetched)
                selected_candidates = discovery.candidates
            records: list[NativeRecord] = []
            artifacts: list[AdapterArtifact] = []
            for candidate in selected_candidates:
                rows, upstream_hash, upstream_bytes = self._download_records(client, candidate.urls[0])
                parsed, payload = parse_btos_period(rows=rows, candidate=candidate, fetched_at=fetched)
                answers = sorted({_norm(_value(row, "answer", "answer_text", "response"))
                                  for row in rows if _value(row, "answer", "answer_text", "response")})
                suppressed = sum(_number(row, "ESTIMATE_PERCENTAGE", "estimate", "value", "percent", "pct") is None
                                 for row in rows if "any of its business functions" in
                                 _question_norm(_value(row, "question_text", "question")))
                records.extend(parsed)
                artifacts.append(AdapterArtifact(artifact_key=f"btos:{candidate.period}", payload=payload,
                    query_scope={"period": candidate.period, "regime": "any_business_function_v2"},
                    source_url=candidate.urls[0], source_version=f"{candidate.identity}:{upstream_hash}",
                    media_type="application/x-ndjson", retention="query_slice", storage_mode="filtered",
                    pointer=candidate.urls[0], metadata={"candidate": candidate.model_dump(mode="json"),
                        "parser_version": "v2", "upstream_content_sha256": upstream_hash,
                        "upstream_bytes": upstream_bytes, "upstream_row_count": len(rows),
                        "filtered_row_count": payload.count(b"\n") + bool(payload),
                        "normalized_observation_count": len(parsed), "suppressed_cell_count": suppressed,
                        "answer_options": answers, "answer_endpoint_note": "answers are embedded in data rows"}))
            if not records:
                return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                    status=IngestionStatus.ZERO_MATCH, fetched_at=fetched)
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                status=IngestionStatus.SUCCEEDED, fetched_at=fetched, records=records, artifacts=artifacts,
                                provider_metadata={"period_count": len(selected_candidates),
                                                   "source_row_observation_reconciled": True})
        finally:
            if close:
                client.close()
