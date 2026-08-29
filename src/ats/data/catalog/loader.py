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
    """Read-only view over the unified catalog and legacy source registries.

    The catalog file is intentionally an index. Existing detailed structured and
    unstructured registries are loaded through explicit legacy paths until their
    contents are migrated into ``config/data``.
    """

    def __init__(self, raw: dict[str, Any], *, path: Path):
        self.raw = raw
        self.path = path
        self.version = int(raw.get("version", 1))
        if self.version != 1:
            raise ValueError(f"unsupported data catalog version: {self.version}")
        self._legacy_structured = None
        self._legacy_sources: dict[str, Any] | None = None
        self._legacy_news: dict[str, Any] | None = None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "DataCatalog":
        resolved = Path(path) if path else _repo_root() / "config" / "data" / "catalog.yaml"
        resolved = resolved.expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"data catalog not found: {resolved}")
        raw = yaml.load(resolved.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}
        return cls(raw, path=resolved)

    def _legacy_path(self, key: str, default: str) -> Path:
        configured = ((self.raw.get("legacy") or {}).get(key) or default)
        return (self.path.parent / configured).resolve()

    def structured_catalog(self):
        if self._legacy_structured is None:
            from ...structured import StructuredCatalog

            self._legacy_structured = StructuredCatalog.load(
                self._legacy_path("structured_catalog", "../structured_data.yaml"))
        return self._legacy_structured

    def _load_registry(self, key: str, default: str) -> dict[str, Any]:
        attr = {"sources": "_legacy_sources", "news_sources": "_legacy_news"}.get(
            key, "_legacy_" + key)
        cached = getattr(self, attr)
        if cached is None:
            path = self._legacy_path(key, default)
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
            reasons = [item["reason"] for item in checks if not item["passed"]]
            return CatalogValidation(valid=False, checks=checks,
                                     reason_codes=reasons or ["legacy_config_missing"])
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
        return self._load_registry("sources", "../sources.yaml")

    def unstructured_news(self) -> dict[str, Any]:
        return self._load_registry("news_sources", "../news_sources.yaml")

    def consumer_release_inventory(self) -> dict[str, Any]:
        """Return the checked-in consumer release classification inventory."""
        configured = ((self.raw.get("domains") or {}).get("consumer_release")
                      or "consumer_release.yaml")
        path = (self.path.parent / configured).resolve()
        from ..release_assessment import load_consumer_release_inventory

        return load_consumer_release_inventory(path)

    def unstructured_sources(self) -> list[CatalogSource]:
        raw = self.unstructured_registry()
        out: list[CatalogSource] = []
        for source_id, row in (raw.get("sources") or {}).items():
            out.append(CatalogSource(
                id=source_id, domain="unstructured", provider=row.get("label", ""),
                adapter=row.get("adapter", ""), status="registered",
                cadence=row.get("cadence", ""), policy={"legacy_registry": "sources"},
            ))
        for source_id, row in (raw.get("article_sources") or {}).items():
            out.append(CatalogSource(
                id=source_id, domain="unstructured", provider=row.get("label", ""),
                adapter=row.get("adapter", ""), status="registered",
                cadence=row.get("cadence", ""), policy={"legacy_registry": "article_sources"},
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
            "structured_catalog": "../structured_data.yaml",
            "sources": "../sources.yaml",
            "news_sources": "../news_sources.yaml",
        }.items():
            check(f"legacy:{key}:exists", self._legacy_path(key, default).exists(),
                  f"legacy_config_missing:{key}")
        consumer_release = ((self.raw.get("domains") or {}).get("consumer_release")
                            or "consumer_release.yaml")
        consumer_release_path = (self.path.parent / consumer_release).resolve()
        check("domain:consumer_release:exists", consumer_release_path.exists(),
              "consumer_release_inventory_missing")
        if consumer_release_path.exists():
            try:
                self.consumer_release_inventory()
            except (OSError, ValueError, yaml.YAMLError) as exc:
                check("domain:consumer_release:valid", False,
                      f"consumer_release_inventory_invalid:{type(exc).__name__}")
            else:
                check("domain:consumer_release:valid", True)

        try:
            structured = self.structured_catalog()
        except FileNotFoundError:
            reasons = [item["reason"] for item in checks if not item["passed"]]
            return CatalogValidation(valid=False, checks=checks,
                                     reason_codes=reasons or ["legacy_config_missing"])
        source_rows = structured.raw.get("sources", {}) or {}
        dataset_rows = structured.raw.get("datasets", {}) or {}
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
            for source_id in [*(row.get("primary_sources") or []),
                              *(row.get("fallback_sources") or [])]:
                check(f"dataset:{dataset_id}:source:{source_id}:exists",
                      source_id in source_rows, "source_not_configured")

        # The controlled structured registry is the authority for numeric adapter keys.
        from ...structured.runtime_registry import _RUNTIMES

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
