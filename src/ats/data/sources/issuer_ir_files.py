"""Issuer investor-relations financial files -> governed ``company_financials`` rows.

Why this source exists: TSM, ASML and SKHY are foreign issuers whose latest quarter the
statement vendors (defeatbeta / yfinance) publish late, SEC XBRL has no quarterly
facts for, and the free fundamentals APIs do not cover.  Their own IR files are the
authoritative copy.

Contract (deterministic, no model requests):

* a *parser* turns one file into reported statement lines (``IRDocument``) with the file,
  page/row and column each number came from;
* ``run_checks`` verifies the statements internally (cash-flow bridge, balance sheet,
  net income across statements, currency and entity identity).  A *hard* failure
  quarantines the affected period's rows with a named reason -- never a silent write;
  a *soft* failure is kept as a warning in the row's lineage;
* ``IssuerIRFilesAdapter`` hands the rows to the central ``IngestionPipeline`` with the
  original file stored as the raw artifact (sha256 in ``source_version``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Callable

import yaml

from ..structured import (
    AdapterArtifact,
    AdapterBatch,
    AdapterFailure,
    FetchRequest,
    IngestionStatus,
    NativeRecord,
)

PARSER_VERSION = 1
KNOWN_CURRENCIES = frozenset({"USD", "EUR", "TWD", "KRW", "JPY", "CNY", "HKD", "GBP"})

# Stored unit family per canonical field. ``flow`` rows are quarter durations,
# ``instant`` rows are period-end balances, ``per_share`` rows carry currency/share.
FIELD_KIND = {
    "revenue": "flow", "gross_profit": "flow", "operating_income": "flow",
    "net_income": "flow", "cash_from_operations": "flow", "capex": "flow",
    "eps_diluted": "per_share", "eps_diluted_adr": "per_adr", "eps_basic": "per_share",
    "cash_and_equivalents": "instant", "inventory": "instant", "total_debt": "instant",
    "long_term_debt": "instant",
    "total_assets": "instant", "total_liabilities": "instant",
    "stockholders_equity": "instant",
}


class ParseError(ValueError):
    """The file is not the document the parser expects; nothing may be written."""


class AcquisitionError(RuntimeError):
    """The issuer's site no longer looks the way the acquirer expects (not a network fault)."""


@dataclass(frozen=True)
class Located:
    """One reported number and where in the file it was read."""

    value: float
    location: str          # e.g. "p2 'Net Revenue'" or "row 5 'Revenue'"
    label: str = ""


@dataclass
class IRValue:
    field: str                       # canonical field, see FIELD_KIND
    period_end: str
    value: float                     # as reported in the table unit (millions, or per-share)
    location: str
    label: str
    basis: str = ""                  # quarter | instant
    derived: dict | None = None      # {"calculation", "left": {...}, "right": {...}} for sums/differences
    notes: dict = field(default_factory=dict)
    provider_field: str = ""         # override of the default ``issuer_ir:<field>`` provider field
    adjustment: str = ""             # override of the default ``gaap`` adjustment


@dataclass
class CheckResult:
    name: str
    period: str                      # "" = whole document
    passed: bool
    severity: str                    # hard | soft
    detail: str = ""


@dataclass
class IRDocument:
    entity: str
    parser: str
    currency: str                    # detected from the document text
    table_scale: float               # reported unit -> base currency units (1e6 for "millions")
    scale_label: str
    values: list[IRValue]
    lines: dict[str, dict[str, Located]]       # period_end -> canonical line -> number (for checks)
    periods: list[str]                          # all period ends in the file, oldest first
    current_period: str
    identity_text: str = ""
    published_on: date | None = None
    meta: dict = field(default_factory=dict)
    checks: list[CheckResult] = field(default_factory=list)
    abs_tolerance: float | None = None   # the table's own rounding, when finer than the issuer default


# --------------------------------------------------------------------------- #
# Calendar helpers
# --------------------------------------------------------------------------- #
def quarter_end(year: int, quarter: int) -> date:
    month = quarter * 3
    day = 31 if month in (3, 12) else 30
    return date(year, month, day)


def quarter_start(end: date) -> date:
    return date(end.year, ((end.month - 1) // 3) * 3 + 1, 1)


# --------------------------------------------------------------------------- #
# Self-checks
# --------------------------------------------------------------------------- #
def _close(left: float, right: float, *, abs_tol: float, rel_tol: float) -> bool:
    return abs(left - right) <= max(abs_tol, rel_tol * max(abs(left), abs(right)))


def run_checks(doc: IRDocument, *, configured_currency: str, abs_tol: float, rel_tol: float,
               identity_markers: list[str], cash_definition_matches_balance_sheet: bool = True,
               fx_effect_reported: bool = True) -> list[CheckResult]:
    """Deterministic acceptance checks on the parsed statements.

    ``abs_tol`` is in the table's reported unit (the tables are rounded to millions, so
    sums can be off by a few units); ``rel_tol`` covers the same effect on large totals.
    """
    results: list[CheckResult] = []

    def add(name: str, period: str, ok: bool, detail: str = "", severity: str = "hard") -> None:
        results.append(CheckResult(name, period, bool(ok), severity, "" if ok else detail))

    add("currency_known", "", configured_currency in KNOWN_CURRENCIES,
        f"configured currency {configured_currency!r} is not a known currency")
    add("currency_matches_document", "", doc.currency == configured_currency,
        f"document says {doc.currency!r}, configuration says {configured_currency!r}")
    upper = doc.identity_text.upper()
    add("issuer_identity", "", any(marker.upper() in upper for marker in identity_markers),
        f"none of {identity_markers} found in the document header")

    ordered = sorted(doc.lines)
    for period in ordered:
        line = {key: located.value for key, located in doc.lines[period].items()}

        def has(*keys: str) -> bool:
            return all(key in line for key in keys)

        def tie(name: str, left: float, right: float, text: str, severity: str = "hard") -> None:
            ok = _close(left, right, abs_tol=abs_tol, rel_tol=rel_tol)
            add(name, period, ok, f"{text}: {left:,.3f} vs {right:,.3f}", severity)

        if has("revenue", "cost_of_revenue", "gross_profit"):
            tie("gross_profit_equals_revenue_minus_cost", line["revenue"] - line["cost_of_revenue"],
                line["gross_profit"], "revenue - cost of revenue != gross profit")
        if has("revenue", "gross_profit"):
            add("gross_profit_not_above_revenue", period,
                line["revenue"] > 0 and line["gross_profit"] <= line["revenue"] + abs_tol,
                "gross profit exceeds revenue or revenue is not positive")
        if has("net_income_total", "net_income_parent", "net_income_nci"):
            tie("net_income_parent_plus_nci_equals_total",
                line["net_income_parent"] + line["net_income_nci"], line["net_income_total"],
                "net income attributable to parent + non-controlling != total net income")
        if has("net_income_is", "net_income_cf"):
            tie("net_income_same_in_income_and_cash_flow_statements", line["net_income_is"],
                line["net_income_cf"], "income statement net income != cash flow statement net income")
        if has("total_assets", "total_liabilities", "equity_total"):
            tie("balance_sheet_balances", line["total_liabilities"] + line["equity_total"],
                line["total_assets"], "liabilities + equity != total assets")
        if has("total_assets", "current_assets", "noncurrent_assets"):
            tie("assets_equal_current_plus_noncurrent", line["current_assets"] + line["noncurrent_assets"],
                line["total_assets"], "current + non-current assets != total assets")
        total_liabilities = line.get("total_liabilities", line.get("derived_total_liabilities"))
        if total_liabilities is not None and "current_liabilities" in line:
            add("liabilities_cover_current_liabilities", period,
                total_liabilities + abs_tol >= line["current_liabilities"],
                "total liabilities are smaller than current liabilities")
        if has("cfo", "cfi", "cff", "net_change"):
            gap = line["net_change"] - (line["cfo"] + line["cfi"] + line["cff"] + line.get("fx", 0.0))
            if fx_effect_reported:
                add("cash_flow_bridge", period, abs(gap) <= max(abs_tol, rel_tol * abs(line["net_change"])),
                    f"CFO + CFI + CFF + FX differs from net change in cash by {gap:,.3f}")
            else:
                # The aggregator omits the exchange-rate effect: the residual *is* that effect.
                base = max(abs(line.get("cash_begin", 0.0)), 1.0)
                add("cash_flow_bridge_residual_is_plausible_fx", period, abs(gap) <= 0.10 * base,
                    f"residual {gap:,.0f} exceeds 10% of beginning cash", severity="soft")
        if has("cash_begin", "net_change", "cash_end"):
            tie("cash_roll_forward", line["cash_begin"] + line["net_change"], line["cash_end"],
                "beginning cash + net change != ending cash")
        if has("cash_end", "cash_balance_sheet"):
            tie("cash_flow_ending_cash_matches_balance_sheet", line["cash_end"], line["cash_balance_sheet"],
                "cash flow statement ending cash != balance sheet cash",
                severity="hard" if cash_definition_matches_balance_sheet else "soft")
        if has("cash", "short_term_investments", "cash_and_short_term_investments"):
            tie("cash_plus_short_term_investments", line["cash"] + line["short_term_investments"],
                line["cash_and_short_term_investments"], "cash + short-term investments != combined line")
        if has("cfo", "capex", "free_cash_flow"):
            tie("free_cash_flow_equals_cfo_minus_capex", line["cfo"] - line["capex"],
                line["free_cash_flow"], "CFO - capex != reported free cash flow")
        for key in ("capex", "inventory", "total_assets"):
            if key in line:
                add(f"{key}_not_negative", period, line[key] >= 0, f"{key} is negative: {line[key]}")

    # a quarter's opening cash is the previous quarter's closing cash
    for earlier, later in zip(ordered, ordered[1:]):
        left, right = doc.lines[earlier].get("cash_end"), doc.lines[later].get("cash_begin")
        if left and right and _months_apart(earlier, later) == 3:
            ok = _close(left.value, right.value, abs_tol=abs_tol, rel_tol=rel_tol)
            add("cash_begin_equals_previous_cash_end", later, ok,
                f"opening cash {right.value:,.3f} != previous closing cash {left.value:,.3f}")
    return results


def _months_apart(earlier: str, later: str) -> int:
    left, right = date.fromisoformat(earlier), date.fromisoformat(later)
    return (right.year - left.year) * 12 + right.month - left.month


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FileRule:
    pattern: str
    parser: str
    rank: int = 0       # when two files carry the same period, the higher rank (then the newer document) wins


@dataclass(frozen=True)
class IssuerConfig:
    entity: str
    name: str
    currency: str
    source_nature: str
    drop_dir: str
    files: tuple[FileRule, ...]
    identity_markers: tuple[str, ...]
    abs_tolerance: float
    rel_tolerance: float
    raw: dict = field(default_factory=dict)


def config_path() -> Path:
    from ...config import REPO_ROOT
    root = Path(os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))
    return root / "data" / "issuer_ir.yaml"


def load_issuer_configs(path: str | Path | None = None) -> dict[str, IssuerConfig]:
    payload = yaml.safe_load(Path(path or config_path()).read_text(encoding="utf-8")) or {}
    if payload.get("version") != 1:
        raise ValueError("unsupported issuer_ir config version")
    defaults = payload.get("defaults") or {}
    configs = {}
    for entity, body in (payload.get("issuers") or {}).items():
        merged = {**defaults, **(body or {})}
        configs[str(entity).upper()] = IssuerConfig(
            entity=str(entity).upper(), name=str(merged.get("name", entity)),
            currency=str(merged.get("currency", "")),
            source_nature=str(merged.get("source_nature", "issuer_official")),
            drop_dir=str(merged.get("drop_dir") or f"var/ir_drop/{str(entity).upper()}"),
            files=tuple(FileRule(str(rule["pattern"]), str(rule["parser"]), int(rule.get("rank", 0)))
                        for rule in (merged.get("files") or [])),
            identity_markers=tuple(str(item) for item in (merged.get("identity_markers") or [])),
            abs_tolerance=float(merged.get("abs_tolerance", 3.0)),
            rel_tolerance=float(merged.get("rel_tolerance", 1e-4)),
            raw=merged)
    return configs


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
def _unit_for(kind: str, currency: str) -> str:
    if kind == "per_share":
        return f"{currency}/share"
    if kind == "per_adr":
        return f"{currency}/ADR"
    return currency


def _provider_field(value: IRValue, source_nature: str) -> str:
    if value.provider_field:
        return value.provider_field
    name = value.field
    if name == "total_debt":
        if value.derived:
            return "issuer_ir:total_debt_derived"
        if source_nature != "issuer_official":
            return "issuer_ir:total_debt_provider_defined"
    if name == "total_liabilities" and value.derived:
        return "issuer_ir:total_liabilities_derived"
    return f"issuer_ir:{name}"


def build_records(doc: IRDocument, *, config: IssuerConfig, file_name: str, sha256: str,
                  slice_key: str, published_at: datetime | None, origin: dict | None = None) -> tuple[list[NativeRecord], list[CheckResult]]:
    """Turn the parsed values into NativeRecords, quarantining periods whose checks failed."""
    checks = run_checks(
        doc, configured_currency=config.currency,
        abs_tol=doc.abs_tolerance if doc.abs_tolerance is not None else config.abs_tolerance,
        rel_tol=config.rel_tolerance, identity_markers=list(config.identity_markers),
        cash_definition_matches_balance_sheet=bool(config.raw.get("cash_definition_matches_balance_sheet", True)),
        fx_effect_reported=bool(config.raw.get("fx_effect_reported", True)))
    checks = [*checks, *doc.meta.get("extra_checks", [])]
    doc.checks = checks
    document_failures = [c for c in checks if not c.passed and c.severity == "hard" and not c.period]
    period_failures: dict[str, list[CheckResult]] = {}
    for check in checks:
        if not check.passed and check.severity == "hard" and check.period:
            period_failures.setdefault(check.period, []).append(check)
    warnings = [c for c in checks if not c.passed and c.severity == "soft"]
    passed = sorted({c.name for c in checks if c.passed})

    records: list[NativeRecord] = []
    for value in doc.values:
        kind = FIELD_KIND[value.field]
        end = date.fromisoformat(value.period_end)
        flow = kind != "instant"
        basis = value.basis or ("instant" if kind == "instant" else "quarter")
        scale = 1.0 if kind in {"per_share", "per_adr"} else doc.table_scale
        # amounts are published in millions: whole currency units are the real precision, and the
        # xls floats carry binary noise below that (10671434.921423001 x 1e6)
        stored = round(value.value * scale, 3 if kind != "flow" and kind != "instant" else 0)
        reasons = [f"check_failed:{c.name}" for c in document_failures]
        reasons += [f"check_failed:{c.name}" for c in period_failures.get(value.period_end, [])]
        row_warnings = [f"{c.name}@{c.period}" for c in warnings if c.period in {"", value.period_end}]
        raw = {
            "provenance": {
                "file": file_name, "sha256": sha256, "location": value.location, "label": value.label,
                "parser": doc.parser, "parser_version": PARSER_VERSION,
                "column_period": value.period_end, **({"origin": origin} if origin else {}),
                "period_role": "current" if value.period_end == doc.current_period else "comparative",
            },
            "reported_value": value.value, "table_unit": doc.scale_label, "stored_unit_scale": scale,
            "source_nature": config.source_nature, "currency": config.currency,
            "checks_passed": passed, "check_warnings": row_warnings,
            **value.notes,
        }
        if value.derived:
            raw.update(value.derived)
            # amounts in the derivation are stored in base currency units, like ``value``
            for side in ("left", "right"):
                if side in raw and "value" in raw[side]:
                    raw[side] = {**raw[side], "value": raw[side]["value"] * scale}
        if reasons:
            raw["quarantine_reasons"] = reasons
        records.append(NativeRecord(
            entity_id=config.entity, provider_field=_provider_field(value, config.source_nature),
            period=value.period_end, value=stored, unit=_unit_for(kind, config.currency),
            currency=config.currency, period_basis=basis,
            adjustment=value.adjustment or (
                "provider_reported" if (value.field == "total_debt" and not value.derived
                                        and config.source_nature != "issuer_official") else "gaap"),
            period_start=quarter_start(end).isoformat() if flow and basis == "quarter" else "",
            period_end=value.period_end, published_at=published_at,
            dimensions={"statement_scope": "consolidated", "source_nature": config.source_nature,
                        "share_basis": {"per_adr": "adr", "per_share": "ordinary_share"}.get(kind, "")},
            raw=raw, slice_key=slice_key))
    return records, checks


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #
ParserFn = Callable[[Path, bytes], IRDocument]


def sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + ".meta.json")


def read_sidecar(path: Path) -> dict:
    """Where an acquired file came from (URL, accession, filing date), written by its acquirer."""
    try:
        return json.loads(sidecar_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _parsers() -> dict[str, ParserFn]:
    from . import issuer_ir_parsers
    return issuer_ir_parsers.PARSERS


class IssuerIRFilesAdapter:
    """Parses issuer IR files found in each issuer's drop directory.

    The directory is filled by the issuer's file acquirer (a scheduled download) or by
    hand; the adapter is agnostic.  Re-running over the same files writes nothing new.
    """

    source_id = "issuer_ir_files"
    dataset_id = "company_financials"

    def __init__(self, *, configs: dict[str, IssuerConfig] | None = None, clock=None,
                 root: Path | None = None, acquirers: dict | None = None):
        self._configs = configs
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.root = root
        self.acquirers = acquirers

    @property
    def configs(self) -> dict[str, IssuerConfig]:
        if self._configs is None:
            self._configs = load_issuer_configs()
        return self._configs

    def _root(self) -> Path:
        if self.root is not None:
            return Path(self.root)
        from ...config import REPO_ROOT
        return Path(REPO_ROOT)

    def _batch(self, request: FetchRequest, fetched_at: datetime, status: IngestionStatus, *,
               failures=(), records=(), artifacts=(), metadata=None) -> AdapterBatch:
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id, status=status,
            fetched_at=fetched_at, records=list(records), artifacts=list(artifacts),
            failures=list(failures), provider_metadata=metadata or {})

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        if len(request.entities) != 1:
            raise ValueError("issuer IR adapter requires exactly one entity per slice")
        symbol = request.entities[0].upper()
        fetched_at = self.clock().astimezone(timezone.utc)
        config = self.configs.get(symbol)
        if config is None:
            return self._batch(request, fetched_at, IngestionStatus.NO_COVERAGE, failures=[AdapterFailure(
                status=IngestionStatus.NO_COVERAGE, entity_id=symbol,
                message=f"{symbol} has no issuer IR file configuration")])

        drop = Path(config.drop_dir)
        drop = drop if drop.is_absolute() else self._root() / drop
        acquisition: dict = {}
        failures: list[AdapterFailure] = []
        # ``acquirers=None`` -> the configured acquirer; an explicit mapping (even empty) replaces it
        acquirer = (self.acquirers.get(symbol) if self.acquirers is not None
                    else self._configured_acquirer(config))
        if acquirer is not None:
            try:
                acquisition = acquirer(config, drop, request, fetched_at) or {}
            except PermissionError:
                raise
            except AcquisitionError as exc:
                acquisition = {"error": str(exc)}
                failures.append(AdapterFailure(
                    status=IngestionStatus.PARSE_FAILED, entity_id=symbol,
                    message=f"{symbol} IR file acquisition: {exc}"))
            except Exception as exc:
                from ..pipelines.structured.ingestion import _is_transport_error
                if not _is_transport_error(exc):
                    raise
                # keep parsing what is already in the folder; the outage itself stays visible
                acquisition = {"error": f"{type(exc).__name__}: {exc}"}
                failures.append(AdapterFailure(
                    status=IngestionStatus.UNREACHABLE, entity_id=symbol,
                    message=f"{symbol} IR file acquisition failed: {exc}"))
        files = self._files(config, drop, request)
        if not files:
            if failures:
                return self._batch(request, fetched_at, IngestionStatus.UNREACHABLE, failures=failures,
                                   metadata={"acquisition": acquisition})
            return self._batch(request, fetched_at, IngestionStatus.NO_CHANGE, metadata={
                "note": f"no IR files in {config.drop_dir}", "acquisition": acquisition})

        parsers = _parsers()
        candidates: list[tuple[tuple, NativeRecord]] = []
        artifacts: list[AdapterArtifact] = []
        file_reports = []
        for path, rule in files:
            payload = path.read_bytes()
            sha256 = hashlib.sha256(payload).hexdigest()
            key = sha256[:16]
            try:
                parser = parsers[rule.parser]
            except KeyError:
                failures.append(AdapterFailure(
                    status=IngestionStatus.PARSE_FAILED, entity_id=symbol, slice_key=key,
                    message=f"{path.name}: unknown parser {rule.parser!r}"))
                continue
            try:
                doc = parser(path, payload)
            except ParseError as exc:
                failures.append(AdapterFailure(
                    status=IngestionStatus.PARSE_FAILED, entity_id=symbol, slice_key=key,
                    message=f"{path.name}: {exc}"))
                continue
            origin = read_sidecar(path)
            published_on = doc.published_on
            if published_on is None and origin.get("filed"):
                published_on = date.fromisoformat(str(origin["filed"])[:10])
            published_at = (datetime.combine(published_on, datetime.min.time(), tzinfo=timezone.utc)
                            if published_on else None)
            built, checks = build_records(doc, config=config, file_name=path.name, sha256=sha256,
                                          slice_key=key, published_at=published_at, origin=origin or None)
            if request.periods:
                built = [item for item in built if item.period in set(request.periods)]
            precedence = (rule.rank, doc.current_period)
            candidates.extend((precedence, item) for item in built)
            quarantined = sum(1 for item in built if item.raw.get("quarantine_reasons"))
            file_reports.append({
                "file": path.name, "sha256": sha256, "parser": rule.parser,
                "periods": doc.periods, "current_period": doc.current_period,
                "records": len(built), "quarantined": quarantined,
                "failed_checks": [f"{c.name}@{c.period or 'document'}" for c in checks
                                  if not c.passed and c.severity == "hard"],
                "warnings": [f"{c.name}@{c.period or 'document'}" for c in checks
                             if not c.passed and c.severity == "soft"]})
            media = {".pdf": "application/pdf", ".xls": "application/vnd.ms-excel",
                     ".htm": "text/html", ".html": "text/html"}.get(path.suffix.lower(), "application/octet-stream")
            artifacts.append(AdapterArtifact(
                artifact_key=key, payload=payload, media_type=media, retention="full_response",
                source_url=str(origin.get("source_url") or path.resolve().as_uri()), source_version=sha256,
                query_scope={"entity": symbol, "file": path.name},
                metadata={"file": path.name, "sha256": sha256, "parser": rule.parser,
                          "source_nature": config.source_nature, "currency": config.currency,
                          "origin": origin, "coverage": file_reports[-1]}))

        # Two files can carry the same period (an older and a newer statement, a PDF and its SEC
        # exhibit): the higher-ranked, then newer, document supplies the value.  Rows held back by a
        # failed check are always kept so the quarantine stays visible.
        records: list[NativeRecord] = []
        best: dict[tuple, tuple[tuple, NativeRecord]] = {}
        for precedence, record in candidates:
            if record.raw.get("quarantine_reasons"):
                records.append(record)
                continue
            slot = (record.provider_field, record.period, record.period_basis)
            if slot not in best or precedence > best[slot][0]:
                best[slot] = (precedence, record)
        records.extend(record for _, record in best.values())
        superseded = len(candidates) - len(records)

        warnings = sorted({w for report in file_reports for w in report["warnings"]})
        metadata = {"files": file_reports, "acquisition": acquisition, "warnings": warnings,
                    "superseded_rows": superseded,
                    "source_version": ",".join(r["sha256"][:12] for r in file_reports)}
        if not records:
            status = IngestionStatus.PARSE_FAILED if any(
                f.status == IngestionStatus.PARSE_FAILED for f in failures) else IngestionStatus.NO_CHANGE
            return self._batch(request, fetched_at, status, failures=failures, metadata=metadata)
        return self._batch(request, fetched_at, IngestionStatus.PARTIAL if failures else IngestionStatus.SUCCEEDED,
                           failures=failures, records=records, artifacts=artifacts, metadata=metadata)

    @staticmethod
    def _configured_acquirer(config: IssuerConfig):
        kind = str((config.raw.get("acquisition") or {}).get("kind") or "")
        if not kind:
            return None
        from . import issuer_ir_acquire
        return issuer_ir_acquire.ACQUIRERS.get(kind)

    @staticmethod
    def _files(config: IssuerConfig, drop: Path, request: FetchRequest) -> list[tuple[Path, FileRule]]:
        explicit = (request.query_scope or {}).get("files")
        found: list[tuple[Path, FileRule]] = []
        if explicit:
            for name in explicit:
                path = Path(name) if Path(name).is_absolute() else drop / name
                rule = next((r for r in config.files if path.match(r.pattern)), None)
                if path.is_file() and rule:
                    found.append((path, rule))
            return found
        if not drop.is_dir():
            return []
        seen: set[Path] = set()
        for rule in config.files:
            for path in sorted(drop.glob(rule.pattern)):
                if path.is_file() and path not in seen and not path.name.startswith("."):
                    seen.add(path)
                    found.append((path, rule))
        return found
