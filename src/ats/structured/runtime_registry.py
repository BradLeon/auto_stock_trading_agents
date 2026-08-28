"""Controlled runtime registry for configured structured source adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .catalog import StructuredCatalog
from .models import CatalogStatus, FetchRequest


@dataclass(frozen=True)
class RuntimeSourceSpec:
    adapter_key: str
    factory: Callable[[], object] | None
    requires_entities: bool = False
    ingest_supported: bool = True
    note: str = ""


def _tw_mof():
    from ..data.sources.tw_mof import TaiwanMOFAdapter

    return TaiwanMOFAdapter()


def _kr_ecos():
    from ..data.sources.kr_ecos import KoreaECOSAdapter

    return KoreaECOSAdapter()


def _sec_companyfacts():
    from ..data.sources.company_financials import SECCompanyFactsAdapter

    return SECCompanyFactsAdapter()


def _company_disclosures():
    from ..data.sources.company_financials import CompanyDisclosuresAdapter

    return CompanyDisclosuresAdapter()


def _defeatbeta():
    from ..data.sources.company_financials import DefeatBetaStatementAdapter

    return DefeatBetaStatementAdapter()


def _yfinance_financials():
    from ..data.sources.company_financials import YFinanceFinancialStatementsAdapter

    return YFinanceFinancialStatementsAdapter()


def _consensus():
    from ..data.sources.market_consensus import YFinanceConsensusAdapter

    return YFinanceConsensusAdapter()


def _trendforce():
    from ..data.sources.trendforce import TrendForceDRAMAdapter

    return TrendForceDRAMAdapter()


_RUNTIMES: dict[str, RuntimeSourceSpec] = {
    "tw_mof": RuntimeSourceSpec("tw_mof", _tw_mof),
    "kr_ecos": RuntimeSourceSpec("kr_ecos", _kr_ecos),
    "sec_companyfacts": RuntimeSourceSpec(
        "sec_companyfacts", _sec_companyfacts, requires_entities=True),
    "company_disclosures": RuntimeSourceSpec(
        "company_disclosures", _company_disclosures, requires_entities=True),
    "defeatbeta_stock_statement": RuntimeSourceSpec(
        "defeatbeta_stock_statement", _defeatbeta, requires_entities=True),
    "yfinance_financials": RuntimeSourceSpec(
        "yfinance_financials", _yfinance_financials, requires_entities=True),
    "consensus": RuntimeSourceSpec("consensus", _consensus, requires_entities=True),
    "trendforce": RuntimeSourceSpec("trendforce", _trendforce),
    "document_numeric_evidence": RuntimeSourceSpec(
        "document_numeric_evidence", None, ingest_supported=False,
        note="Evidence candidates enter through EvidenceWorkbench review, not remote fetch."),
}


def register_runtime(spec: RuntimeSourceSpec) -> None:
    """Register a controlled adapter factory (primarily for extensions and tests)."""
    _RUNTIMES[spec.adapter_key] = spec


def runtime_spec(source_id: str, *, catalog: StructuredCatalog | None = None) -> RuntimeSourceSpec | None:
    catalog = catalog or StructuredCatalog.load()
    row = (catalog.raw.get("sources", {}) or {}).get(source_id) or {}
    return _RUNTIMES.get(str(row.get("adapter", "")))


def validate_source_registration(source_id: str, *,
                                 catalog: StructuredCatalog | None = None) -> dict:
    """Validate the configuration/runtime/quality contract without network access."""
    catalog = catalog or StructuredCatalog.load()
    source_rows = catalog.raw.get("sources", {}) or {}
    dataset_rows = catalog.raw.get("datasets", {}) or {}
    row = source_rows.get(source_id)
    checks: list[dict] = []

    def check(name: str, passed: bool, reason: str = "") -> None:
        checks.append({"check": name, "passed": bool(passed),
                       "reason": "" if passed else reason})

    check("source_configured", row is not None, "source_not_configured")
    if row is None:
        return {"source_id": source_id, "valid": False, "checks": checks,
                "reason_codes": ["source_not_configured"]}

    runtime_excluded = row.get("catalog_status") == CatalogStatus.RUNTIME_EXCLUDED.value
    check("persistent_boundary", not runtime_excluded, "runtime_source_excluded")
    if runtime_excluded:
        return {"source_id": source_id, "adapter_key": str(row.get("adapter", "")),
                "datasets": [], "valid": False, "checks": checks,
                "reason_codes": ["runtime_source_excluded"]}
    implemented = row.get("catalog_status") not in {
        CatalogStatus.PLANNED.value, CatalogStatus.DEFERRED.value}
    check("implementation_status", implemented, "source_not_implemented")
    datasets = list(row.get("datasets") or [])
    check("datasets_declared", bool(datasets), "source_has_no_datasets")
    for dataset_id in datasets:
        dataset = dataset_rows.get(dataset_id)
        check(f"dataset:{dataset_id}:exists", dataset is not None,
              "dataset_not_configured")
        if dataset is None:
            continue
        references = [*(dataset.get("primary_sources") or []),
                      *(dataset.get("fallback_sources") or [])]
        check(f"dataset:{dataset_id}:references_source", source_id in references,
              "dataset_does_not_reference_source")
        check(f"dataset:{dataset_id}:quality", bool(dataset.get("quality")),
              "quality_thresholds_missing")
        check(f"dataset:{dataset_id}:acceptance_samples",
              bool(dataset.get("acceptance_samples") or dataset.get("entities")),
              "acceptance_samples_missing")

    budget = row.get("internal_request_budget") or {}
    check("request_budget", bool(budget), "request_budget_missing")
    adapter_key = str(row.get("adapter", ""))
    spec = _RUNTIMES.get(adapter_key)
    check("runtime_registered", spec is not None, "adapter_runtime_unregistered")
    if spec is not None:
        check("unified_ingestion_supported", spec.ingest_supported,
              "adapter_not_supported_by_unified_ingestion")
        if spec.ingest_supported:
            try:
                adapter = spec.factory() if spec.factory else None
            except Exception as exc:  # factory/config dependency failure
                check("adapter_constructible", False,
                      f"adapter_factory_failed:{type(exc).__name__}:{exc}")
            else:
                check("adapter_constructible", adapter is not None,
                      "adapter_factory_returned_none")
    reasons = [item["reason"] for item in checks if not item["passed"]]
    return {"source_id": source_id, "adapter_key": adapter_key,
            "datasets": datasets, "valid": not reasons, "checks": checks,
            "reason_codes": reasons}


def build_ingestion(source_id: str, *, entities: list[str] | None = None,
                    periods: list[str] | None = None,
                    query_scope: dict | None = None,
                    catalog: StructuredCatalog | None = None) -> tuple[object, FetchRequest]:
    catalog = catalog or StructuredCatalog.load()
    validation = validate_source_registration(source_id, catalog=catalog)
    if not validation["valid"]:
        raise ValueError(
            f"source {source_id} is not runnable: {', '.join(validation['reason_codes'])}")
    source = next(item for item in catalog.sources() if item.id == source_id)
    spec = runtime_spec(source_id, catalog=catalog)
    assert spec is not None and spec.factory is not None
    normalized_entities = [item.upper() for item in (entities or []) if item]
    if spec.requires_entities and not normalized_entities:
        raise ValueError(f"source {source_id} requires at least one --entity")
    if len(source.datasets) != 1:
        raise ValueError(
            f"source {source_id} must declare exactly one dataset for unified ingestion")
    return spec.factory(), FetchRequest(
        source_id=source_id, dataset_id=source.datasets[0],
        entities=normalized_entities, periods=periods or [],
        query_scope=query_scope or {})


def ingest_source(repository, source_id: str, *, entities: list[str] | None = None,
                  periods: list[str] | None = None, query_scope: dict | None = None,
                  catalog: StructuredCatalog | None = None, force: bool = False) -> dict:
    """Run one controlled source slice, honoring the source release gate."""
    from .flags import source_mode
    from .ingestion import IngestionPipeline

    mode = source_mode(source_id)
    if not force and mode not in {"shadow", "platform", "fallback"}:
        raise PermissionError(
            f"source {source_id} mode is {mode}; publish it or use --force for isolation")
    adapter, request = build_ingestion(
        source_id, entities=entities, periods=periods,
        query_scope=query_scope, catalog=catalog)
    result = IngestionPipeline(repository).run(adapter, request)
    return {**result, "source_id": source_id, "dataset_id": request.dataset_id,
            "source_mode": mode, "forced": force}
