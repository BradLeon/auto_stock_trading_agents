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


PAGE_URL = "https://ramp.com/data/ai-index"
REGIME = "ramp_ai_index_v2"
# v2 page scopes: only what FRED does not publish (see `ramp_ai_index_fred`). `adoption_overall` and
# `adoption_business_size` come from FRED alone; `model_market_share_overall` is v1 only (discontinued).
SCOPES = (
    "adoption_overall_models",
    "adoption_sector",
    "spend_per_employee_overall",
    "token_spend_share_by_maker",
    "token_spend_share_by_model",
)
ADOPTION_SCOPES = ("adoption_overall", "adoption_overall_models", "adoption_sector")
TOKEN_SCOPES = ("token_spend_share_by_maker", "token_spend_share_by_model")
SPEND_SCOPES = ("spend_per_employee_overall", *TOKEN_SCOPES)
OUT_OF_SCOPE = ("geographies", "token_volume", "token_prices", "ai_share_of_business_spend")
ADOPTION_DATASET = "ramp_ai_adoption"
SPEND_DATASET = "ramp_ai_spend"
SCOPE_DATASET = {scope: (ADOPTION_DATASET if scope in ADOPTION_SCOPES else SPEND_DATASET)
                 for scope in SCOPES}
SCOPE_SLUGS = {
    "adoption_overall_models": "adoption-overall-models",
    "adoption_sector": "adoption-sector",
    "spend_per_employee_overall": "spend-per-employee-overall",
    "token_spend_share_by_maker": "token-spend-share-by-maker",
    "token_spend_share_by_model": "token-spend-share-by-model",
}
MIN_TSV_BYTES = 16
PARSER_VERSION = "ramp_ai_index/v3"
# A governed inbox file counts as this probe's upstream result only while it is younger than one probe cycle;
# an older file is a stale snapshot and must never be reported as "no change upstream".
INBOX_MAX_AGE_DAYS = 10
TOKEN_SHARE_TOLERANCE_PP = 1.0


class RampExportError(ValueError):
    """A visible chart export could not be interpreted safely."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").replace("\ufeff", "").strip().casefold().split())


def _header_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _norm(value)).strip("_")




def _bare_header_key(value: Any) -> str:
    """Header key without a trailing unit in parentheses: "Top 10% (USD / employee / month)" -> "top_10"."""
    return _header_key(re.sub(r"\([^()]*\)\s*$", "", str(value or "")))


def _field(row: dict[str, Any], *names: str) -> Any:
    normalized = {_header_key(key): value for key, value in row.items()}
    bare = {_bare_header_key(key): value for key, value in row.items()}
    for name in names:
        key = _header_key(name)
        if key in normalized and normalized[key] not in (None, ""):
            return normalized[key]
    for name in names:
        key = _bare_header_key(name)
        if key in bare and bare[key] not in (None, ""):
            return bare[key]
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


def _day(value: Any) -> str:
    """A weekly chart's period is the exact week-start date, never collapsed to a month."""
    text = str(value or "").strip()
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise RampExportError(f"invalid_week_period:{value}") from exc


def _slug(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_")
    return text.upper() or "UNKNOWN"


def _release_label(periods: Iterable[str], supplied: str = "") -> str:
    if supplied:
        return supplied
    values = sorted(item for item in periods if item)
    return values[-1][:7] if values else "unknown"


def _schema_fingerprint(payload: bytes) -> str:
    """Hash the exact TSV header so a column/shape change is observable.

    The token-share exports put one model per column, so their header changes whenever a model appears;
    only the first (date) column is part of their shape.
    """
    first_line = payload.splitlines()[0] if payload.splitlines() else b""
    return hashlib.sha256(first_line.strip()).hexdigest()


def _token_schema_fingerprint(payload: bytes) -> str:
    first_line = payload.splitlines()[0] if payload.splitlines() else b""
    return hashlib.sha256(first_line.split(b"\t")[0].strip()).hexdigest()


def _methodology_fingerprint(*, scope: str, method: str, schema_fingerprint: str) -> str:
    return hashlib.sha256(json.dumps({
        "scope": scope,
        "method": method,
        "parser_version": PARSER_VERSION,
        "schema_fingerprint": schema_fingerprint,
        "methodology_regime": REGIME,
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
        "methodology_regime": REGIME,
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
    """Parse Overall + Models (vendors) or NAICS sector chart exports."""
    if scope not in ADOPTION_SCOPES:
        raise ValueError(f"ramp_adoption_scope_invalid:{scope}")
    fetched_at = fetched_at or _now()
    raw, rows = read_clipboard_tsv(payload, required_headers=("Date",))
    del raw
    output: list[NativeRecord] = []
    for row in rows:
        period = _period(_field(row, "Date", "Month", "Period"), release_label)
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
        # The v2 sector export has no yearly change column; a missing column yields no record, not a zero.
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
    """Parse Median/Top 10%/Top 1% AI spend per employee export (v2 headers carry a unit suffix)."""
    fetched_at = fetched_at or _now()
    raw, rows = read_clipboard_tsv(payload, required_headers=("Date",))
    del raw
    output: list[NativeRecord] = []
    quantile_columns = (("median", ("Median",)),
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
    if not output:
        raise RampExportError("export_unreadable:no_spend_rows")
    return output


def _maker_and_model(header: str) -> tuple[str, str]:
    """"Claude Opus 5.5 (Anthropic)" -> ("Anthropic", "Claude Opus 5.5"); the maker is the last parenthetical."""
    text = str(header or "").strip()
    match = re.match(r"^(.*)\(([^()]*)\)\s*$", text)
    if not match:
        return "", text
    return match.group(2).strip(), match.group(1).strip()


def parse_token_share_tsv(payload: bytes | str, *, scope: str, fetched_at: datetime | None = None,
                          slice_key: str = "", source_method: str = "official_clipboard") -> tuple[list[NativeRecord], list[str]]:
    """Parse a weekly Token spend export in Shares mode (one column per maker or per model).

    Returns the records and non-blocking warnings. A Volumes-mode export (an index, not a share) is refused:
    its weekly column total runs well above 100, which a share cannot.
    """
    if scope not in TOKEN_SCOPES:
        raise ValueError(f"ramp_token_scope_invalid:{scope}")
    raw, rows = read_clipboard_tsv(payload, required_headers=("Date",))
    del raw
    output: list[NativeRecord] = []
    totals: dict[str, float] = {}
    by_model = scope == "token_spend_share_by_model"
    for row in rows:
        date_key = next((key for key in row if _header_key(key) == "date"), "")
        period = _day(row.get(date_key))
        for header, cell in row.items():
            if header == date_key:
                continue
            value = _number(cell)
            if value is None:
                continue
            if not 0 <= value <= 100:
                raise RampExportError(f"token_share_out_of_range:{scope}:{period}:{header}:{value}")
            totals[period] = totals.get(period, 0.0) + value
            if by_model:
                maker, model = _maker_and_model(header)
                if not maker:
                    raise RampExportError(f"export_unreadable:token_model_without_maker:{header}")
                entity_id, segment = f"TOKEN_MODEL:{_slug(maker)}:{_slug(model)}", model
            else:
                maker, model = str(header).strip(), ""
                entity_id, segment = f"TOKEN_MAKER:{_slug(maker)}", maker
            dims = _base_dimensions(scope=scope, period=period, segment=segment, source_method=source_method)
            dims.update({"maker": maker, "model": model,
                         "cohort": "Token Spend Management connected businesses",
                         "statistical_unit": "Token spend attributed to models",
                         "denominator_scope": "Total weekly token spend of connected businesses",
                         "technology_scope": "model-attributed token spend via Token Spend Management",
                         "release_label": period})
            output.append(NativeRecord(
                entity_id=entity_id,
                provider_field=f"{scope}_pct", period=period, value=value, unit="percent",
                period_basis="calendar_week", period_start=period, period_end=period, published_at=None,
                dimensions=dims, raw={"provider_column": header, "provider_value": cell, "scope": scope},
                slice_key=slice_key))
    if not output:
        raise RampExportError(f"export_unreadable:no_token_share_rows:{scope}")
    if max(totals.values()) > 100 + TOKEN_SHARE_TOLERANCE_PP:
        raise RampExportError(f"methodology_drift:token_volume_index_not_share:{scope}:max_week_total="
                              f"{max(totals.values()):.2f}")
    warnings = [f"token_share_total_off_100:{period}:{total:.2f}" for period, total in sorted(totals.items())
                if abs(total - 100) > TOKEN_SHARE_TOLERANCE_PP]
    return output, warnings


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


def parse_scope(scope: str, payload: bytes, *, fetched_at: datetime, slice_key: str,
                method: str = "official_clipboard") -> tuple[list[NativeRecord], list[str]]:
    if scope in ADOPTION_SCOPES:
        return parse_adoption_tsv(payload, scope=scope, fetched_at=fetched_at, slice_key=slice_key,
                                  source_method=method), []
    if scope == "spend_per_employee_overall":
        return parse_spend_tsv(payload, fetched_at=fetched_at, slice_key=slice_key, source_method=method), []
    if scope in TOKEN_SCOPES:
        return parse_token_share_tsv(payload, scope=scope, fetched_at=fetched_at, slice_key=slice_key,
                                     source_method=method)
    raise RampExportError(f"scope_not_registered:{scope}")


def split_fred_covered(records: list[NativeRecord], coverage: set[tuple[str, str]]) -> tuple[list[NativeRecord], list[NativeRecord]]:
    """(kept, covered): a page row whose series FRED publishes is never stored, only cross-checked.

    The Overall + Models chart also carries the Ramp Overall line; FRED's `adoption_overall` covers it.
    """
    covered_entities = {entity for _, entity in coverage}
    kept: list[NativeRecord] = []
    covered: list[NativeRecord] = []
    for record in records:
        scope = str((record.dimensions or {}).get("scope") or "")
        if (scope, record.entity_id) in coverage or (
                record.entity_id == "RAMP_OVERALL" and record.entity_id in covered_entities):
            covered.append(record)
        else:
            kept.append(record)
    return kept, covered


def cross_check_fred(covered: list[NativeRecord], fred_values: dict[tuple[str, str], float],
                     *, tolerance_pp: float = 0.01) -> list[str]:
    """`fred_page_mismatch` warnings for covered page rows whose value differs from FRED's same month."""
    warnings = []
    for record in covered:
        if record.provider_field not in {"adoption_rate_pct", "vendor_adoption_pct"}:
            continue
        reference = fred_values.get((record.entity_id, record.period[:7]))
        if reference is not None and abs(float(record.value) - reference) > tolerance_pp:
            warnings.append(f"fred_page_mismatch:{record.entity_id}:{record.period[:7]}:"
                            f"page={float(record.value):.4f}:fred={reference:.4f}")
    return sorted(set(warnings))


def _inbox_exported_at(path: Path) -> datetime:
    sidecar = path.with_suffix(".json")
    if sidecar.is_file():
        try:
            value = json.loads(sidecar.read_text(encoding="utf-8")).get("exported_at", "")
            parsed = datetime.fromisoformat(str(value))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (OSError, ValueError, TypeError):
            pass
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


class RampBrowserAdapter:
    """Small browser boundary used by tests and the headless browser runner.

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
                 clock: Callable[[], datetime] | None = None,
                 fred_coverage: set[tuple[str, str]] | None = None,
                 fred_values: Callable[[], dict[tuple[str, str], float]] | None = None):
        self.browser = browser
        self.export_dir = Path(export_dir) if export_dir else None
        self.clock = clock or _now
        self._fred_coverage = fred_coverage
        self._fred_values = fred_values
        # Discovery and ingestion are one controlled probe.  Keep the exact
        # export payloads in memory so release_check can hand them to the
        # ingestion run without opening the browser a second time.  This is a
        # transient runtime cache; raw bytes remain persisted as artifacts by
        # fetch(), not in the source-check metadata table.
        self.discovered_payloads: dict[str, str] = {}

    @property
    def fred_coverage(self) -> set[tuple[str, str]]:
        if self._fred_coverage is None:
            from .ramp_ai_index_fred import fred_coverage
            self._fred_coverage = fred_coverage()
        return self._fred_coverage

    def _fred_reference(self) -> dict[tuple[str, str], float]:
        if self._fred_values is None:
            return {}
        try:
            return self._fred_values()
        except Exception:  # noqa: BLE001 - the cross-check is diagnostic and must never block the page part
            return {}

    def _payloads(self, request: FetchRequest) -> dict[str, tuple[bytes, str]]:
        scope_filter = str((request.query_scope or {}).get("scope", ""))
        requested = [scope_filter] if scope_filter else [scope for scope in SCOPES if SCOPE_DATASET[scope] == request.dataset_id]
        values = (request.query_scope or {}).get("payloads") or (request.query_scope or {}).get("browser_exports") or {}
        output: dict[str, tuple[bytes, str]] = {}
        now = self.clock().astimezone(timezone.utc)
        for scope in requested:
            if scope not in SCOPES:
                continue
            value = values.get(scope) if isinstance(values, dict) else None
            if value is not None:
                output[scope] = (value.encode("utf-8") if isinstance(value, str) else bytes(value), "official_clipboard")
                continue
            # The browser runner writes each official export into this governed inbox, so the second dataset
            # job of the same probe reads it instead of opening the browser again. A file older than one probe
            # cycle is a stale snapshot, not this probe's upstream result.
            stale = ""
            if self.export_dir is not None:
                path = self.export_dir / f"{scope}.tsv"
                if path.is_file():
                    age_days = (now - _inbox_exported_at(path)).total_seconds() / 86400
                    if age_days <= INBOX_MAX_AGE_DAYS:
                        output[scope] = (path.read_bytes(), "official_clipboard")
                        continue
                    stale = f":inbox_stale_{age_days:.0f}d"
            if self.browser is not None:
                output[scope] = (self.browser.export(scope), "official_clipboard")
                continue
            raise PermissionError(f"export_unreadable:official_web_export_required:{scope}{stale}")
        return output

    def _parse(self, scope: str, payload: bytes, *, fetched_at: datetime, slice_key: str,
               method: str) -> tuple[list[NativeRecord], list[NativeRecord], list[str]]:
        records, warnings = parse_scope(scope, payload, fetched_at=fetched_at, slice_key=slice_key, method=method)
        kept, covered = split_fred_covered(records, self.fred_coverage)
        return kept, covered, warnings

    def discover(self, request: FetchRequest) -> DiscoveryResult:
        checked = self.clock().astimezone(timezone.utc)
        candidates: list[ReleaseCandidate] = []
        failures: dict[str, str] = {}
        warnings: list[str] = []
        covered_rows = 0
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
        fred_values: dict[tuple[str, str], float] | None = None
        for scope, (payload, method) in payloads.items():
            digest = hashlib.sha256(payload).hexdigest()
            try:
                rows, covered, scope_warnings = self._parse(scope, payload, fetched_at=checked,
                                                            slice_key=f"ramp:{scope}:{digest[:16]}", method=method)
                warnings.extend(scope_warnings)
                covered_rows += len(covered)
                if covered:
                    fred_values = self._fred_reference() if fred_values is None else fred_values
                    warnings.extend(cross_check_fred(covered, fred_values))
                if not rows:
                    continue
                latest = max((row.period for row in rows), default="")
                identity = f"ramp_page:{PAGE_URL}:{SCOPE_SLUGS[scope]}:{latest}:{digest}"
                schema_fingerprint = (_token_schema_fingerprint(payload) if scope in TOKEN_SCOPES
                                      else _schema_fingerprint(payload))
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
                                                             "methodology_regime": REGIME,
                                                             "out_of_scope": list(OUT_OF_SCOPE)}))
            except Exception as exc:
                failures[scope] = f"{type(exc).__name__}:{exc}"
        diagnostics = {"scope_failures": failures, "out_of_scope": OUT_OF_SCOPE,
                       "fred_covered_rows": covered_rows, "warnings": sorted(set(warnings))}
        if not candidates:
            status = DiscoveryStatus.EXPORT_UNREADABLE if failures else DiscoveryStatus.NO_CHANGE
            return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                                   status=status, diagnostics=diagnostics)
        candidates.sort(key=lambda item: item.period)
        combined = hashlib.sha256("|".join(item.identity for item in candidates).encode()).hexdigest()
        status = DiscoveryStatus.PARTIAL if failures else DiscoveryStatus.NEW_RELEASE
        return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                               status=status, latest_upstream_identity=f"RAMP:{combined}",
                               latest_available_period=max(item.period for item in candidates),
                               candidates=candidates,
                               diagnostics={**diagnostics, "published_scope_count": len(candidates)})

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
        covered_rows = 0
        for scope, (payload, method) in payloads.items():
            digest = hashlib.sha256(payload).hexdigest()
            slice_key = f"ramp:{scope}:{digest[:16]}"
            try:
                parsed, covered, scope_warnings = self._parse(scope, payload, fetched_at=fetched,
                                                              slice_key=slice_key, method=method)
                covered_rows += len(covered)
                warnings.extend(scope_warnings)
                quality_errors = validate_ramp_records(parsed)
                if quality_errors:
                    raise RampExportError("quality_gate_failed:" + ";".join(quality_errors))
                if not parsed:
                    continue
                records.extend(parsed)
                latest = max((row.period for row in parsed), default="unknown")
                release = _release_label((row.period for row in parsed), latest)
                source_version = f"ramp_page:{PAGE_URL}:{SCOPE_SLUGS[scope]}:{release}:{digest}"
                schema_fingerprint = (_token_schema_fingerprint(payload) if scope in TOKEN_SCOPES
                                      else _schema_fingerprint(payload))
                artifacts.append(AdapterArtifact(
                    artifact_key=slice_key, payload=payload,
                    query_scope={"scope": scope, "dataset_id": request.dataset_id,
                                 "out_of_scope": list(OUT_OF_SCOPE)},
                    source_url=PAGE_URL,
                    source_version=source_version, media_type="text/tab-separated-values",
                    retention="query_slice", storage_mode="full", pointer=PAGE_URL,
                    metadata={"scope": scope, "chart_slug": SCOPE_SLUGS[scope],
                              "latest_release_label": release, "payload_sha256": digest,
                              "export_method": method, "methodology_regime": REGIME,
                              "parser_version": PARSER_VERSION, "schema_fingerprint": schema_fingerprint,
                              "methodology_fingerprint": _methodology_fingerprint(
                                  scope=scope, method=method, schema_fingerprint=schema_fingerprint),
                              "fred_covered_rows": len(covered),
                              "out_of_scope": list(OUT_OF_SCOPE)}))
            except Exception as exc:
                text = str(exc).casefold()
                status = (IngestionStatus.METHODOLOGY_DRIFT if "schema" in text or "methodology_drift" in text
                          else IngestionStatus.EXPORT_UNREADABLE)
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
                                               "fred_covered_rows": covered_rows,
                                               "warnings": (["vendor_shares_may_overlap"] if "adoption_overall_models" in payloads else []) + sorted(set(warnings)),
                                               "source_conflicts": []})


# Backward-friendly spelling used by adapters and external integrations.
RampAdapter = RampAIIndexAdapter
