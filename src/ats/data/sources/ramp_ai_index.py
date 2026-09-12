"""Governed Ramp AI Index adapter.

The public Ramp page exposes a ``Get the data`` control which copies a small,
machine-readable TSV to the clipboard. This module treats that export (or an
already persisted fixture passed by the ingestion caller) as the only active
source-native interface. API/MCP credential acquisition is deliberately out of
scope for the first release and is not implemented in this adapter.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
from typing import Any, Callable, Iterable

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


PAGE_URL = "https://ramp.com/data/ai-index#adoption#overall"
SCOPES = (
    "adoption_overall",
    "adoption_overall_models",
    "adoption_sector",
    "spend_per_employee_overall",
    "model_market_share_overall",
)
ADOPTION_SCOPES = SCOPES[:3]
SPEND_SCOPES = SCOPES[3:]
OUT_OF_SCOPE = ("business_size", "geographies")
ADOPTION_DATASET = "ramp_ai_adoption"
SPEND_DATASET = "ramp_ai_spend"
SCOPE_DATASET = {scope: (ADOPTION_DATASET if scope in ADOPTION_SCOPES else SPEND_DATASET)
                 for scope in SCOPES}
SCOPE_SLUGS = {
    "adoption_overall": "adoption-overall",
    "adoption_overall_models": "adoption-overall-models",
    "adoption_sector": "adoption-sector",
    "spend_per_employee_overall": "spend-per-employee-overall",
    "model_market_share_overall": "model-market-share-overall",
}
MIN_TSV_BYTES = 16
PARSER_VERSION = "ramp_ai_index/v2"


class RampExportError(ValueError):
    """A visible chart export could not be interpreted safely."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").replace("\ufeff", "").strip().casefold().split())


def _header_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _norm(value)).strip("_")


def _field(row: dict[str, Any], *names: str) -> Any:
    normalized = {_header_key(key): value for key, value in row.items()}
    for name in names:
        key = _header_key(name)
        if key in normalized and normalized[key] not in (None, ""):
            return normalized[key]
    return ""


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.casefold() in {"na", "n/a", "-", "—", "."}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("() ").replace(",", "").replace("$", "").replace("%", "")
    try:
        result = float(text)
    except ValueError as exc:
        raise RampExportError(f"invalid_numeric_value:{value}") from exc
    if not math.isfinite(result):
        raise RampExportError(f"non_finite_numeric_value:{value}")
    return -result if negative else result


def _period(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    # ISO, US and common month labels are all used by public chart exports.
    match = re.search(r"(20\d{2})[-/]?(0[1-9]|1[0-2])", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-01"
    for fmt in ("%B %Y", "%b %Y", "%m/%d/%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-01")
        except ValueError:
            continue
    raise RampExportError(f"invalid_period:{value}")


def _slug(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_")
    return text.upper() or "UNKNOWN"


def _release_label(periods: Iterable[str], supplied: str = "") -> str:
    if supplied:
        return supplied
    values = sorted(item for item in periods if item)
    return values[-1][:7] if values else "unknown"


def _schema_fingerprint(payload: bytes) -> str:
    """Hash the exact TSV header so a column/shape change is observable."""
    first_line = payload.splitlines()[0] if payload.splitlines() else b""
    return hashlib.sha256(first_line.strip()).hexdigest()


def _methodology_fingerprint(*, scope: str, method: str, schema_fingerprint: str) -> str:
    return hashlib.sha256(json.dumps({
        "scope": scope,
        "method": method,
        "parser_version": PARSER_VERSION,
        "schema_fingerprint": schema_fingerprint,
        "methodology_regime": "ramp_ai_index_v1",
    }, sort_keys=True).encode()).hexdigest()


def read_clipboard_tsv(payload: bytes | str, *, required_headers: Iterable[str] = ()) -> tuple[bytes, list[dict[str, str]]]:
    """Validate and decode the exact clipboard bytes returned by Ramp."""
    if isinstance(payload, str):
        raw = payload.encode("utf-8")
    elif isinstance(payload, bytes):
        raw = payload
    else:
        raise RampExportError("export_unreadable:clipboard_payload_not_bytes")
    if len(raw) < MIN_TSV_BYTES:
        raise RampExportError("export_unreadable:empty_or_truncated_tsv")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RampExportError("export_unreadable:clipboard_not_utf8") from exc
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    headers = list(reader.fieldnames or [])
    if not headers:
        raise RampExportError("export_unreadable:missing_tsv_header")
    normalized = {_header_key(item) for item in headers}
    missing = [_header_key(item) for item in required_headers if _header_key(item) not in normalized]
    if missing:
        raise RampExportError(f"export_unreadable:missing_headers:{','.join(missing)}")
    rows = [{str(key): (value or "") for key, value in row.items()} for row in reader]
    if not rows:
        raise RampExportError("export_unreadable:no_data_rows")
    return raw, rows


def _base_dimensions(*, scope: str, period: str, segment: str = "", source_method: str = "official_clipboard") -> dict[str, Any]:
    return {
        "scope": scope,
        "statistical_unit": "Ramp relevant American businesses",
        "denominator_scope": "Ramp businesses in the relevant chart cohort",
        "technology_scope": "AI products or services with a positive Ramp transaction",
        "geography": "United States / Ramp network (not a national estimate)",
        "reference_period": period,
        "segment": segment,
        "methodology_regime": "ramp_ai_index_v1",
        "source_method": source_method,
    }


def _adoption_entity(scope: str, series: str) -> tuple[str, str]:
    label = str(series or "Overall").strip()
    if scope == "adoption_sector":
        return f"NAICS:{_slug(label)}", label
    if _norm(label) in {"overall", "ramp overall", "ramp"}:
        return "RAMP_OVERALL", "Ramp Overall"
    return f"RAMP_VENDOR:{_slug(label)}", label


def parse_adoption_tsv(payload: bytes | str, *, scope: str, fetched_at: datetime | None = None,
                       slice_key: str = "", source_method: str = "official_clipboard",
                       release_label: str = "") -> list[NativeRecord]:
    """Parse Overall, Overall+Models, or NAICS sector chart exports."""
    if scope not in ADOPTION_SCOPES:
        raise ValueError(f"ramp_adoption_scope_invalid:{scope}")
    fetched_at = fetched_at or _now()
    raw, rows = read_clipboard_tsv(payload, required_headers=("Date",))
    del raw
    output: list[NativeRecord] = []
    periods: list[str] = []
    for row in rows:
        period = _period(_field(row, "Date", "Month", "Period"), release_label)
        periods.append(period)
        series = str(_field(row, "Series", "Vendor", "Sector", "Industry", "NAICS") or "Overall")
        entity_id, segment = _adoption_entity(scope, series)
        adoption = _number(_field(row, "Adoption rate (%)", "Adoption rate", "Adoption"))
        if adoption is None:
            raise RampExportError(f"missing_adoption_value:{scope}:{period}:{series}")
        if not 0 <= adoption <= 100:
            raise RampExportError(f"adoption_out_of_range:{scope}:{period}:{adoption}")
        provider_field = "vendor_adoption_pct" if scope == "adoption_overall_models" and entity_id != "RAMP_OVERALL" else "adoption_rate_pct"
        dims = _base_dimensions(scope=scope, period=period, segment=segment, source_method=source_method)
        dims.update({"vendor": segment if entity_id.startswith("RAMP_VENDOR:") else "",
                     "naics_group": segment if entity_id.startswith("NAICS:") else "",
                     "release_label": release_label or period[:7]})
        raw_row = {"provider_row": row, "scope": scope}
        output.append(NativeRecord(entity_id=entity_id, provider_field=provider_field, period=period,
                                   value=adoption, unit="percent", period_basis="calendar_month",
                                   period_start=period, period_end=period, published_at=None,
                                   dimensions=dims, raw=raw_row, slice_key=slice_key))
        for field, name in (("monthly_change_pp", "Monthly change (pp)"),
                            ("yearly_change_pp", "Yearly change (pp)")):
            change = _number(_field(row, name, name.replace(" (pp)", "")))
            if change is not None:
                output.append(NativeRecord(entity_id=entity_id, provider_field=field, period=period,
                                           value=change, unit="percentage_point", period_basis="calendar_month",
                                           period_start=period, period_end=period, published_at=None,
                                           dimensions={**dims, "provider_change": True}, raw=raw_row,
                                           slice_key=slice_key))
    if not output:
        raise RampExportError(f"export_unreadable:no_adoption_rows:{scope}")
    return output


def parse_spend_tsv(payload: bytes | str, *, fetched_at: datetime | None = None,
                    slice_key: str = "", release_label: str = "",
                    source_method: str = "official_clipboard") -> list[NativeRecord]:
    """Parse Median/Top 10%/Top 1% AI spend per employee export."""
    fetched_at = fetched_at or _now()
    raw, rows = read_clipboard_tsv(payload, required_headers=("Date",))
    del raw
    output: list[NativeRecord] = []
    quantile_columns = (("median", ("Median", "Median (USD / employee / month)")),
                        ("top10", ("Top 10%", "Top 10")),
                        ("top1", ("Top 1%", "Top 1")))
    for row in rows:
        period = _period(_field(row, "Date", "Month", "Period"), release_label)
        for quantile, aliases in quantile_columns:
            value = _number(_field(row, *aliases))
            if value is None:
                continue
            if value < 0:
                raise RampExportError(f"spend_negative:{period}:{quantile}:{value}")
            output.append(NativeRecord(
                entity_id=f"QUANTILE:{quantile.upper()}", provider_field="ai_spend_per_employee",
                period=period, value=value, unit="usd_per_employee_month",
                currency="USD", period_basis="calendar_month", period_start=period, period_end=period,
                # Ramp's public export supplies a reference month but no
                # publication timestamp. Keep fetched_at as the vintage time;
                # leaving published_at empty makes identical re-imports
                # idempotent instead of creating false content vintages.
                published_at=None,
                dimensions={**_base_dimensions(scope="spend_per_employee_overall", period=period,
                                               segment=quantile, source_method=source_method),
                            "quantile": quantile, "release_label": release_label or period[:7]},
                raw={"provider_row": row, "scope": "spend_per_employee_overall", "quantile": quantile},
                slice_key=slice_key,
            ))
        # A long-form export (quantile + value) is also accepted.
        if not any(_field(row, *aliases) not in (None, "") for _, aliases in quantile_columns):
            quantile = _norm(_field(row, "Quantile", "Series", "Segment"))
            value = _number(_field(row, "Value", "AI spend", "Spend per employee", "USD / employee / month"))
            if quantile and value is not None:
                canonical = "top1" if "top 1" in quantile or "top1" in quantile else "top10" if "top 10" in quantile or "top10" in quantile else "median"
                output.append(NativeRecord(
                    entity_id=f"QUANTILE:{canonical.upper()}", provider_field="ai_spend_per_employee",
                    period=period, value=value, unit="usd_per_employee_month", currency="USD",
                    period_basis="calendar_month",
                    period_start=period, period_end=period, published_at=None,
                    dimensions={**_base_dimensions(scope="spend_per_employee_overall", period=period,
                                                   segment=canonical, source_method=source_method),
                                "quantile": canonical, "release_label": release_label or period[:7]},
                    raw={"provider_row": row, "scope": "spend_per_employee_overall", "quantile": canonical},
                    slice_key=slice_key))
    if not output:
        raise RampExportError("export_unreadable:no_spend_rows")
    return output


def parse_model_market_share_tsv(payload: bytes | str, *, fetched_at: datetime | None = None,
                                 slice_key: str = "", release_label: str = "",
                                 source_method: str = "official_clipboard") -> list[NativeRecord]:
    """Parse provider/model/API spend share rows from Token Spend Management."""
    fetched_at = fetched_at or _now()
    raw, rows = read_clipboard_tsv(payload, required_headers=("Provider", "Model"))
    del raw
    output: list[NativeRecord] = []
    for row in rows:
        period = _period(_field(row, "Date", "Month", "Period"), release_label or "")
        provider = str(_field(row, "Provider", "Vendor") or "Unknown").strip()
        model = str(_field(row, "Model", "Model name") or "Unknown").strip()
        spend_type = str(_field(row, "Spend type", "Spend Type", "Type") or "API").strip()
        value = _number(_field(row, "API spend share (%)", "API spend share", "Spend share (%)", "Share (%)", "Value"))
        if value is None:
            raise RampExportError(f"missing_model_share_value:{period}:{provider}:{model}")
        if not 0 <= value <= 100:
            raise RampExportError(f"model_share_out_of_range:{period}:{provider}:{model}:{value}")
        entity_id = f"MODEL:{_slug(provider)}:{_slug(model)}:{_slug(spend_type)}"
        dims = _base_dimensions(scope="model_market_share_overall", period=period,
                                segment=model, source_method=source_method)
        dims.update({"provider": provider, "model": model, "spend_type": spend_type,
                     "cohort": "Token Spend Management connected businesses",
                     "technology_scope": "model-attributed API spend",
                     "release_label": release_label or period[:7]})
        output.append(NativeRecord(entity_id=entity_id, provider_field="api_spend_share_pct",
                                   period=period, value=value, unit="percent", period_basis="calendar_month",
                                   period_start=period, period_end=period, published_at=None,
                                   dimensions=dims, raw={"provider_row": row, "scope": "model_market_share_overall"},
                                   slice_key=slice_key))
    if not output:
        raise RampExportError("export_unreadable:no_model_share_rows")
    return output


def validate_ramp_records(records: list[NativeRecord]) -> list[str]:
    """Return deterministic quality-gate diagnostics without dropping rows."""
    errors: list[str] = []
    seen: set[tuple[str, str, str, str]] = set()
    spend_by_period: dict[str, dict[str, float]] = {}
    for record in records:
        key = (record.period, record.entity_id, record.provider_field, record.unit)
        if key in seen:
            errors.append(f"duplicate_cell:{'|'.join(key)}")
        seen.add(key)
        value = float(record.value)
        if record.unit == "percent" and not 0 <= value <= 100:
            errors.append(f"percentage_out_of_range:{key}")
        if record.unit == "usd_per_employee_month":
            if value < 0:
                errors.append(f"spend_negative:{key}")
            quantile = str((record.dimensions or {}).get("quantile", ""))
            spend_by_period.setdefault(record.period, {})[quantile] = value
    for period, values in spend_by_period.items():
        if {"median", "top10", "top1"} <= set(values) and not values["median"] <= values["top10"] <= values["top1"]:
            errors.append(f"spend_quantile_order_failed:{period}")
    return sorted(set(errors))


class RampBrowserAdapter:
    """Small browser boundary used by tests and the desktop browser runner.

    ``exporter`` receives a registered scope and must return the bytes copied by
    the visible ``Get the data`` button.  It deliberately has no screenshot or
    hidden-URL fallback.
    """

    def __init__(self, exporter: Callable[[str], bytes | str] | None = None):
        self.exporter = exporter

    def export(self, scope: str) -> bytes:
        if scope not in SCOPES:
            raise RampExportError(f"scope_not_registered:{scope}")
        if self.exporter is None:
            raise RampExportError("export_unreadable:browser_exporter_not_configured")
        try:
            payload = self.exporter(scope)
        except PermissionError as exc:
            raise RampExportError("export_unreadable:clipboard_permission_denied") from exc
        return read_clipboard_tsv(payload)[0]


class RampAIIndexAdapter:
    source_id = "ramp_ai_index"
    dataset_id = ADOPTION_DATASET

    def __init__(self, *, browser: RampBrowserAdapter | None = None,
                 export_dir: str | Path | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.browser = browser
        self.export_dir = Path(export_dir) if export_dir else None
        self.clock = clock or _now
        # Discovery and ingestion are one controlled probe.  Keep the exact
        # export payloads in memory so release_check can hand them to the
        # ingestion run without opening the browser a second time.  This is a
        # transient runtime cache; raw bytes remain persisted as artifacts by
        # fetch(), not in the source-check metadata table.
        self.discovered_payloads: dict[str, str] = {}

    def _payloads(self, request: FetchRequest) -> dict[str, tuple[bytes, str]]:
        scope_filter = str((request.query_scope or {}).get("scope", ""))
        requested = [scope_filter] if scope_filter else [scope for scope in SCOPES if SCOPE_DATASET[scope] == request.dataset_id]
        values = (request.query_scope or {}).get("payloads") or (request.query_scope or {}).get("browser_exports") or {}
        output: dict[str, tuple[bytes, str]] = {}
        for scope in requested:
            if scope not in SCOPES:
                continue
            value = values.get(scope) if isinstance(values, dict) else None
            if value is not None:
                output[scope] = (value.encode("utf-8") if isinstance(value, str) else bytes(value), "official_clipboard")
                continue
            # The scheduled job may be run headlessly.  A desktop/browser
            # runner can place the five official Get-the-data TSVs in this
            # governed inbox before invoking the CLI; the same adapter then
            # performs discovery and ingestion without another browser pass.
            if self.export_dir is not None:
                path = self.export_dir / f"{scope}.tsv"
                if path.is_file():
                    output[scope] = (path.read_bytes(), "official_clipboard")
                    continue
            if self.browser is not None:
                output[scope] = (self.browser.export(scope), "official_clipboard")
                continue
            raise PermissionError(f"export_unreadable:official_web_export_required:{scope}")
        return output

    def discover(self, request: FetchRequest) -> DiscoveryResult:
        checked = self.clock().astimezone(timezone.utc)
        candidates: list[ReleaseCandidate] = []
        failures: dict[str, str] = {}
        self.discovered_payloads = {}
        scope_filter = str((request.query_scope or {}).get("scope", ""))
        scopes = [scope_filter] if scope_filter else [scope for scope in SCOPES if SCOPE_DATASET[scope] == request.dataset_id]
        try:
            payloads = self._payloads(request)
        except Exception as exc:
            for scope in scopes:
                failures[scope] = f"{type(exc).__name__}:{exc}"
            status = DiscoveryStatus.EXPORT_UNREADABLE
            return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                                   status=status, diagnostics={"scope_failures": failures, "out_of_scope": OUT_OF_SCOPE})
        for scope, (payload, method) in payloads.items():
            digest = hashlib.sha256(payload).hexdigest()
            try:
                if scope in ADOPTION_SCOPES:
                    rows = parse_adoption_tsv(payload, scope=scope, fetched_at=checked,
                                              slice_key=f"ramp:{scope}:{digest[:16]}")
                elif scope == "spend_per_employee_overall":
                    rows = parse_spend_tsv(payload, fetched_at=checked, slice_key=f"ramp:{scope}:{digest[:16]}")
                else:
                    rows = parse_model_market_share_tsv(payload, fetched_at=checked, slice_key=f"ramp:{scope}:{digest[:16]}")
                latest = max((row.period for row in rows), default="")
                identity = f"ramp_page:{PAGE_URL}:{SCOPE_SLUGS[scope]}:{latest}:{digest}"
                schema_fingerprint = _schema_fingerprint(payload)
                methodology_fingerprint = _methodology_fingerprint(
                    scope=scope, method=method, schema_fingerprint=schema_fingerprint)
                self.discovered_payloads[scope] = payload.decode("utf-8")
                candidates.append(ReleaseCandidate(identity=identity, period=latest,
                                                   urls=[PAGE_URL],
                                                   methodology_fingerprint=methodology_fingerprint,
                                                   metadata={"scope": scope, "export_method": method,
                                                             "payload_sha256": digest, "latest_release_label": latest,
                                                             "rows": len(rows), "parser_version": PARSER_VERSION,
                                                             "schema_fingerprint": schema_fingerprint,
                                                             "methodology_regime": "ramp_ai_index_v1",
                                                             "out_of_scope": list(OUT_OF_SCOPE)}))
            except Exception as exc:
                failures[scope] = f"{type(exc).__name__}:{exc}"
        if not candidates:
            status = DiscoveryStatus.EXPORT_UNREADABLE
            return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                                   status=status, diagnostics={"scope_failures": failures, "out_of_scope": OUT_OF_SCOPE})
        candidates.sort(key=lambda item: item.period)
        combined = hashlib.sha256("|".join(item.identity for item in candidates).encode()).hexdigest()
        status = DiscoveryStatus.PARTIAL if failures else DiscoveryStatus.NEW_RELEASE
        return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                               status=status, latest_upstream_identity=f"RAMP:{combined}",
                               latest_available_period=max(item.period for item in candidates),
                               candidates=candidates,
                               diagnostics={"scope_failures": failures, "out_of_scope": OUT_OF_SCOPE,
                                            "published_scope_count": len(candidates)})

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        fetched = self.clock().astimezone(timezone.utc)
        records: list[NativeRecord] = []
        artifacts: list[AdapterArtifact] = []
        failures: list[AdapterFailure] = []
        warnings: list[str] = []
        try:
            payloads = self._payloads(request)
        except PermissionError as exc:
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                status=IngestionStatus.EXPORT_UNREADABLE, fetched_at=fetched,
                                failures=[AdapterFailure(status=IngestionStatus.EXPORT_UNREADABLE,
                                                         message=str(exc), slice_key="ramp")],
                                provider_metadata={"out_of_scope": list(OUT_OF_SCOPE)})
        except Exception as exc:
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                status=IngestionStatus.EXPORT_UNREADABLE, fetched_at=fetched,
                                failures=[AdapterFailure(status=IngestionStatus.EXPORT_UNREADABLE,
                                                         message=f"{type(exc).__name__}:{exc}", slice_key="ramp")])
        for scope, (payload, method) in payloads.items():
            digest = hashlib.sha256(payload).hexdigest()
            slice_key = f"ramp:{scope}:{digest[:16]}"
            try:
                if scope in ADOPTION_SCOPES:
                    parsed = parse_adoption_tsv(payload, scope=scope, fetched_at=fetched, slice_key=slice_key)
                elif scope == "spend_per_employee_overall":
                    parsed = parse_spend_tsv(payload, fetched_at=fetched, slice_key=slice_key)
                else:
                    parsed = parse_model_market_share_tsv(payload, fetched_at=fetched, slice_key=slice_key)
                quality_errors = validate_ramp_records(parsed)
                if quality_errors:
                    raise RampExportError("quality_gate_failed:" + ";".join(quality_errors))
                records.extend(parsed)
                latest = max((row.period for row in parsed), default="unknown")
                release = _release_label((row.period for row in parsed), latest)
                source_version = f"ramp_page:{PAGE_URL}:{SCOPE_SLUGS[scope]}:{release}:{digest}"
                artifacts.append(AdapterArtifact(
                    artifact_key=slice_key, payload=payload,
                    query_scope={"scope": scope, "dataset_id": request.dataset_id,
                                 "out_of_scope": list(OUT_OF_SCOPE)},
                    source_url=PAGE_URL,
                    source_version=source_version, media_type="text/tab-separated-values",
                    retention="query_slice", storage_mode="full", pointer=PAGE_URL,
                    metadata={"scope": scope, "chart_slug": SCOPE_SLUGS[scope],
                              "latest_release_label": release, "payload_sha256": digest,
                              "export_method": method,
                              "parser_version": PARSER_VERSION, "schema_fingerprint": _schema_fingerprint(payload),
                              "methodology_fingerprint": _methodology_fingerprint(
                                  scope=scope, method=method,
                                  schema_fingerprint=_schema_fingerprint(payload)),
                              "out_of_scope": list(OUT_OF_SCOPE)}))
            except Exception as exc:
                status = IngestionStatus.METHODOLOGY_DRIFT if "schema" in str(exc).casefold() else IngestionStatus.EXPORT_UNREADABLE
                failures.append(AdapterFailure(status=status, message=f"{type(exc).__name__}:{exc}", slice_key=slice_key))
        if records and failures:
            status = IngestionStatus.PARTIAL
        elif records:
            status = IngestionStatus.SUCCEEDED
        else:
            status = IngestionStatus.EXPORT_UNREADABLE if failures else IngestionStatus.NO_COVERAGE
        return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id, status=status,
                            fetched_at=fetched, records=records, artifacts=artifacts,
                            failures=failures,
                            provider_metadata={"scope_count": len(payloads), "out_of_scope": list(OUT_OF_SCOPE),
                                               "export_methods": sorted({method for _, method in payloads.values()}),
                                               "warnings": (["vendor_shares_may_overlap"] if "adoption_overall_models" in payloads else []) + warnings,
                                               "source_conflicts": warnings})


# Backward-friendly spelling used by adapters and external integrations.
RampAdapter = RampAIIndexAdapter
