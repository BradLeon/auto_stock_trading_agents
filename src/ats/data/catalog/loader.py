"""Unified data catalog loader with legacy configuration compatibility."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import CatalogDataset, CatalogSource, CatalogValidation


class _UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate keys instead of silently overwriting."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node, deep: bool = False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate catalog key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _repo_root() -> Path:
    from ...config import REPO_ROOT

    return REPO_ROOT


class DataCatalog:
    """Read-only view over the unified data catalog and its domain registries."""

    def __init__(self, raw: dict[str, Any], *, path: Path):
        self.raw = raw
        self.path = path
        self.version = int(raw.get("version", 1))
        if self.version != 1:
            raise ValueError(f"unsupported data catalog version: {self.version}")
        self._structured = None
        self._sources: dict[str, Any] | None = None
        self._news_sources: dict[str, Any] | None = None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "DataCatalog":
        resolved = Path(path) if path else _repo_root() / "config" / "data" / "catalog.yaml"
        resolved = resolved.expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"data catalog not found: {resolved}")
        raw = yaml.load(resolved.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}
        return cls(raw, path=resolved)

    def _domain_path(self, key: str, default: str) -> Path:
        configured = ((self.raw.get("domains") or {}).get(key) or default)
        return (self.path.parent / configured).resolve()

    def structured_catalog(self):
        if self._structured is None:
            from .structured import StructuredCatalog

            self._structured = StructuredCatalog.load(
                self._domain_path("structured", "structured.yaml"))
        return self._structured

    def _load_registry(self, key: str, default: str) -> dict[str, Any]:
        attr = {"sources": "_sources", "news_sources": "_news_sources"}[key]
        cached = getattr(self, attr)
        if cached is None:
            path = self._domain_path(key, default)
            cached = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            setattr(self, attr, cached)
        return cached

    def sources(self) -> list[CatalogSource]:
        explicit = self.raw.get("sources") or {}
        if explicit:
            return [CatalogSource(id=source_id, **(row or {}))
                    for source_id, row in explicit.items()]
        structured = self.structured_catalog()
        out: list[CatalogSource] = []
        for source in structured.sources():
            out.append(CatalogSource(
                id=source.id,
                domain="runtime" if source.persistence.value == "runtime" else "structured",
                provider=source.provider,
                adapter=source.adapter,
                status=source.catalog_status.value,
                datasets=source.datasets,
                cadence=source.cadence,
                request_budget=dict(source.constraints.get("internal_request_budget") or {}),
                policy={"retention": source.retention, "upstream": source.upstream,
                        "excludes": source.constraints.get("excludes", [])},
            ))
        return out

    def datasets(self) -> list[CatalogDataset]:
        explicit = self.raw.get("datasets") or {}
        if explicit:
            return [CatalogDataset(id=dataset_id, **(row or {}))
                    for dataset_id, row in explicit.items()]
        try:
            structured = self.structured_catalog()
        except FileNotFoundError:
            return []
        return [CatalogDataset(
            id=dataset.id,
            domain="structured",
            status=dataset.catalog_status.value,
            sources=[*dataset.primary_sources, *dataset.fallback_sources],
            entities=dataset.entities,
            metrics=dataset.core_metrics,
            quality=dataset.quality,
        ) for dataset in structured.datasets()]

    def unstructured_registry(self) -> dict[str, Any]:
        return self._load_registry("sources", "sources.yaml")

    def unstructured_news(self) -> dict[str, Any]:
        return self._load_registry("news_sources", "news_sources.yaml")

    def target_unstructured_sources(self) -> list[CatalogSource]:
        """Return only sources explicitly registered in the target registry.

        This is the source set for new governed paths (notably scheduling and
        refresh validation). Legacy registry rows are intentionally excluded:
        their presence is useful for inventory and compatibility, but is not
        evidence that they satisfy the target registration contract.
        """
        configured_path = self._domain_path("unstructured", "unstructured.yaml")
        configured = yaml.load(configured_path.read_text(encoding="utf-8"),
                               Loader=_UniqueKeyLoader) or {}
        explicit = configured.get("sources") or {}
        return [
            CatalogSource(id=source_id, **(row or {}))
            for source_id, row in explicit.items()
        ]

    def unstructured_sources(self) -> list[CatalogSource]:
        """Return a compatibility view combining target and legacy sources.

        Callers implementing a new governed path must use
        :meth:`target_unstructured_sources`; this method remains for existing
        inventory/report consumers until those callers are migrated.
        """
        out = self.target_unstructured_sources()
        explicit_ids = {item.id for item in out}
        raw = self.unstructured_registry()
        for source_id, row in (raw.get("sources") or {}).items():
            if source_id in explicit_ids:
                continue
            row = row or {}
            domain = str(row.get("domain") or "unstructured")
            canonical_source_id = str(row.get("canonical_source_id") or "")
            out.append(CatalogSource(
                id=source_id, domain=domain, provider=row.get("label", ""),
                adapter=row.get("adapter", ""), status="registered",
                cadence=row.get("cadence", ""),
                datasets=[str(row["dataset_id"])] if row.get("dataset_id") else [],
                policy={"registry": "sources",
                        **({"canonical_source_id": canonical_source_id}
                           if canonical_source_id else {})},
            ))
        for source_id, row in (raw.get("article_sources") or {}).items():
            if source_id in explicit_ids:
                continue
            out.append(CatalogSource(
                id=source_id, domain="unstructured", provider=row.get("label", ""),
                adapter=row.get("adapter", ""), status="registered",
                cadence=row.get("cadence", ""), policy={"registry": "article_sources"},
            ))
        for row in (self.unstructured_news().get("rss") or []):
            if row.get("name"):
                out.append(CatalogSource(
                    id=str(row["name"]), domain="unstructured", provider="RSS",
                    adapter="rss", status="registered", policy={"url": row.get("url", "")},
                ))
        for row in (self.unstructured_news().get("newsletters", {}).get("research_feeds") or []):
            if row.get("name"):
                out.append(CatalogSource(
                    id=str(row["name"]), domain="unstructured", provider="newsletter/RSS",
                    adapter="rss", status="registered", policy={"url": row.get("url", "")},
                ))
        return out

    def statuses(self) -> dict[str, dict[str, Any]]:
        """Return config-level status; actual data coverage remains runtime-derived."""

        return {
            "sources": {
                item.id: {"domain": item.domain, "status": item.status,
                          "datasets": item.datasets}
                for item in [*self.sources(), *self.unstructured_sources()]
            },
            "datasets": {
                item.id: {"domain": item.domain, "status": item.status,
                          "sources": item.sources}
                for item in self.datasets()
            },
            "coverage_note": "config status is not proof of accepted observations; use data availability",
        }

    def validate(self) -> CatalogValidation:
        checks: list[dict[str, Any]] = []

        def check(name: str, passed: bool, reason: str = "") -> None:
            checks.append({"check": name, "passed": bool(passed), "reason": "" if passed else reason})

        check("catalog_version", self.version == 1, "unsupported_catalog_version")
        for key, default in {
            "structured": "structured.yaml",
            "unstructured": "unstructured.yaml",
        }.items():
            check(f"domain:{key}:exists", self._domain_path(key, default).exists(),
                  f"domain_config_missing:{key}")
        domains = self.raw.get("domains") or {}
        check("catalog:domains:authoritative_only",
              set(domains) == {"structured", "unstructured"},
              "catalog_must_only_assemble_domain_registries")
        check("catalog:source_definitions:domain_owned",
              not (self.raw.get("sources") or {}) and not (self.raw.get("datasets") or {}),
              "catalog_must_not_define_sources_or_datasets")
        try:
            structured = self.structured_catalog()
        except FileNotFoundError:
            reasons = [item["reason"] for item in checks if not item["passed"]]
            return CatalogValidation(valid=False, checks=checks,
                                     reason_codes=reasons or ["legacy_config_missing"])
        source_rows = structured.raw.get("sources", {}) or {}
        dataset_rows = structured.raw.get("datasets", {}) or {}
        metric_rows = structured.raw.get("metric_definitions", {}) or {}
        entity_rows = structured.raw.get("entities", {}) or {}
        allowed_unit_families = {
            "categorical", "count", "currency", "currency_per_item",
            "currency_per_share", "index", "multiple", "percent", "ratio", "scale_1_5",
        }
        for metric_id, row in metric_rows.items():
            check(f"metric:{metric_id}:unit_family",
                  str((row or {}).get("unit_family", "")) in allowed_unit_families,
                  "invalid_metric_unit_family")
        for entity_id, row in entity_rows.items():
            aliases = list((row or {}).get("aliases") or [])
            normalized = [str(alias).strip().casefold() for alias in aliases if str(alias).strip()]
            check(f"entity:{entity_id}:aliases", bool(normalized), "entity_aliases_missing")
            check(f"entity:{entity_id}:aliases_unique",
                  len(normalized) == len(set(normalized)), "duplicate_entity_alias")
        for source_id, row in source_rows.items():
            datasets = list(row.get("datasets") or [])
            runtime = row.get("catalog_status") == "runtime_excluded"
            check(f"source:{source_id}:adapter", bool(row.get("adapter")),
                  "adapter_missing")
            check(f"source:{source_id}:budget", runtime or bool(row.get("internal_request_budget")),
                  "request_budget_missing")
            for dataset_id in datasets:
                dataset = dataset_rows.get(dataset_id)
                check(f"source:{source_id}:dataset:{dataset_id}", dataset is not None,
                      "dataset_not_configured")
                if dataset is not None:
                    refs = [*(dataset.get("primary_sources") or []),
                            *(dataset.get("fallback_sources") or [])]
                    check(f"dataset:{dataset_id}:source:{source_id}:reciprocal",
                          source_id in refs, "dataset_source_reference_missing")
        for dataset_id, row in dataset_rows.items():
            for metric_id in row.get("core_metrics") or []:
                check(f"dataset:{dataset_id}:metric:{metric_id}:exists",
                      metric_id in metric_rows, "metric_not_configured")
            for source_id in [*(row.get("primary_sources") or []),
                              *(row.get("fallback_sources") or [])]:
                check(f"dataset:{dataset_id}:source:{source_id}:exists",
                      source_id in source_rows, "source_not_configured")

        unstructured_path = self._domain_path("unstructured", "unstructured.yaml")
        try:
            unstructured = yaml.load(unstructured_path.read_text(encoding="utf-8"),
                                     Loader=_UniqueKeyLoader) or {}
        except (OSError, ValueError, yaml.YAMLError) as exc:
            check("unstructured_registry:parse", False,
                  f"unstructured_registry_invalid:{type(exc).__name__}")
            unstructured = {}
        target_unstructured_rows = unstructured.get("sources") or {}
        target_unstructured_datasets = unstructured.get("datasets") or {}
        duplicate_ids = set(source_rows).intersection(target_unstructured_rows)
        check("source_identity:unique_across_domains", not duplicate_ids,
              "source_id_defined_in_multiple_domain_registries")
        for source_id, row in target_unstructured_rows.items():
            row = row or {}
            status = str(row.get("status") or "registered")
            kind = str(row.get("source_kind") or "")
            check(f"unstructured:{source_id}:stable_identity",
                  bool(source_id) and all(char.islower() or char.isdigit() or char == "_"
                                          for char in str(source_id)),
                  "dynamic_or_invalid_source_identity")
            check(f"unstructured:{source_id}:adapter",
                  bool(row.get("adapter")), "adapter_missing")
            check(f"unstructured:{source_id}:source_kind",
                  kind in {"article", "fixed_index", "official_body",
                           "fixed_transcript", "repository_corpus"},
                  "source_kind_invalid")
            check(f"unstructured:{source_id}:no_symbol_template",
                  not any(marker in str(source_id) for marker in (":", "{", "}", "/")),
                  "dynamic_source_identity")
            for dataset_id in row.get("datasets") or []:
                dataset = target_unstructured_datasets.get(dataset_id)
                check(f"unstructured:{source_id}:dataset:{dataset_id}",
                      dataset is not None, "dataset_not_configured")
                if dataset is not None:
                    check(f"unstructured:{source_id}:dataset:{dataset_id}:reciprocal",
                          source_id in (dataset.get("sources") or []),
                          "dataset_source_reference_missing")
            if status == "registered":
                check(f"unstructured:{source_id}:budget",
                      bool(row.get("request_budget")), "request_budget_missing")
                policy = row.get("policy") or {}
                check(f"unstructured:{source_id}:governance",
                      bool(policy.get("permission")) and bool(policy.get("usage")) and
                      bool(policy.get("retention")) and
                      ("fallback" in policy or "fallback_mode" in policy),
                      "permission_usage_retention_or_fallback_missing")
                if row.get("adapter") == "semianalysis":
                    transport = row.get("transport") or {}
                    check(f"unstructured:{source_id}:transport",
                          isinstance(transport, dict) and
                          bool((transport.get("imap") or {}).get("senders")) and
                          bool(transport.get("research_feeds")),
                          "semianalysis_transport_contract_missing")
                if kind == "article":
                    check(f"unstructured:{source_id}:ingestion_contract",
                          isinstance(row.get("ingestion"), dict),
                          "governed_ingestion_contract_missing")
                    ingestion = row.get("ingestion") or {}
                    for required in ("entity", "doc_type", "pages", "max_per_run",
                                     "min_body_chars", "match"):
                        check(f"unstructured:{source_id}:ingestion:{required}",
                              required in ingestion, "governed_ingestion_field_missing")
                else:
                    check(f"unstructured:{source_id}:fixed_policy",
                          bool(policy.get("fallback") == "none") and
                          bool(policy.get("retention")) and bool(policy.get("usage")),
                          "fixed_source_policy_missing")
                    if kind in {"fixed_index", "fixed_transcript"}:
                        check(f"unstructured:{source_id}:immutable_revision",
                              bool(policy.get("immutable_revision_required")) and
                              bool(policy.get("repo")) and bool(policy.get("file")) and
                              bool(policy.get("spec")),
                              "fixed_revision_contract_missing")
                    if kind == "repository_corpus":
                        members = policy.get("members") or []
                        check(f"unstructured:{source_id}:members",
                              bool(members) and len(members) == len(set(members)) and
                              all((self.path.parent.parent.parent / member).is_file()
                                  for member in members),
                              "curated_corpus_members_missing")
        for dataset_id, row in target_unstructured_datasets.items():
            row = row or {}
            doc_types = row.get("document_types") or []
            record_types = row.get("record_types") or []
            check(f"unstructured_dataset:{dataset_id}:types",
                  bool(doc_types or record_types) and all(
                      doc_type in (unstructured.get("document_types") or [])
                      for doc_type in doc_types) and all(
                      record_type in (unstructured.get("record_types") or [])
                      for record_type in record_types),
                  "document_or_record_type_unregistered")
            for source_id in row.get("sources") or []:
                source = target_unstructured_rows.get(source_id)
                check(f"unstructured_dataset:{dataset_id}:source:{source_id}",
                      source is not None and dataset_id in (source.get("datasets") or []),
                      "source_dataset_reference_missing")
        schedule_path = self.path.parent / "schedules.yaml"
        if schedule_path.is_file():
            schedule = yaml.load(schedule_path.read_text(encoding="utf-8"),
                                 Loader=_UniqueKeyLoader) or {}
            for job_id, job in (((schedule.get("unstructured") or {}).get("jobs")) or {}).items():
                job = job or {}
                job_sources = job.get("sources") or ([job["source"]] if job.get("source") else [])
                for source_id in job_sources:
                    check(f"unstructured_job:{job_id}:source:{source_id}",
                          source_id in target_unstructured_rows,
                          "legacy_only_unstructured_job_source")
                    source = target_unstructured_rows.get(source_id) or {}
                    for dataset_id in job.get("datasets") or []:
                        check(f"unstructured_job:{job_id}:dataset:{dataset_id}",
                              dataset_id in (source.get("datasets") or []),
                              "unstructured_job_dataset_mismatch")

        # The controlled structured registry is the authority for numeric adapter keys.
        from ..adapters.structured.registry import _RUNTIMES

        for source_id, row in source_rows.items():
            if row.get("catalog_status") in {"planned", "deferred", "runtime_excluded"}:
                continue
            check(f"source:{source_id}:runtime_adapter",
                  row.get("adapter") in _RUNTIMES, "adapter_runtime_unregistered")

        # New catalog entries are validated in addition to (and before their
        # eventual replacement of) the legacy structured registry.
        explicit_sources = self.raw.get("sources") or {}
        explicit_datasets = self.raw.get("datasets") or {}
        for source_id, row in explicit_sources.items():
            status = row.get("status", "registered")
            runtime = status in {"runtime/excluded", "runtime_excluded"} or \
                row.get("domain") == "runtime"
            check(f"catalog:source:{source_id}:adapter",
                  runtime or bool(row.get("adapter")), "adapter_missing")
            if not runtime and status not in {"planned", "deferred", "disabled"}:
                check(f"catalog:source:{source_id}:runtime_adapter",
                      row.get("adapter") in _RUNTIMES, "adapter_runtime_unregistered")
            for dataset_id in row.get("datasets") or []:
                check(f"catalog:source:{source_id}:dataset:{dataset_id}",
                      dataset_id in explicit_datasets or dataset_id in dataset_rows,
                      "dataset_not_configured")
        for dataset_id, row in explicit_datasets.items():
            for source_id in row.get("sources") or []:
                check(f"catalog:dataset:{dataset_id}:source:{source_id}",
                      source_id in explicit_sources or source_id in source_rows,
                      "source_not_configured")

        reasons = [item["reason"] for item in checks if not item["passed"]]
        return CatalogValidation(valid=not reasons, checks=checks, reason_codes=reasons)


def load_data_catalog(path: str | Path | None = None) -> DataCatalog:
    return DataCatalog.load(path)


__all__ = ["DataCatalog", "load_data_catalog"]
