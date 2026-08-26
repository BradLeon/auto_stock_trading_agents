"""Load the machine-readable structured source, dataset and metric catalog."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import (CatalogStatus, MetricDefinition, Persistence, StructuredDataset,
                     StructuredSource)


class StructuredCatalog:
    def __init__(self, raw: dict[str, Any], *, path: Path):
        self.raw = raw
        self.path = path
        self.version = int(raw.get("version", 0))
        if self.version != 1:
            raise ValueError(f"unsupported structured catalog version: {self.version}")

    @classmethod
    def load(cls, path: str | Path | None = None) -> "StructuredCatalog":
        if path is None:
            from ..config import REPO_ROOT

            path = REPO_ROOT / "config" / "structured_data.yaml"
        resolved = Path(path)
        return cls(yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}, path=resolved)

    def sources(self) -> list[StructuredSource]:
        out = []
        for source_id, row in self.raw.get("sources", {}).items():
            runtime = row.get("catalog_status") == "runtime_excluded"
            out.append(StructuredSource(
                id=source_id,
                provider=row.get("provider", source_id),
                adapter=row.get("adapter", ""),
                persistence=Persistence.RUNTIME if runtime else Persistence(
                    row.get("persistence", "persistent")),
                catalog_status=CatalogStatus(row.get("catalog_status", "planned")),
                cadence=row.get("cadence", ""),
                retention=row.get("retention", "source_policy"),
                datasets=list(row.get("datasets") or []),
                upstream=row.get("upstream", ""),
                constraints={
                    "internal_request_budget": row.get("internal_request_budget", {}),
                    "excludes": row.get("excludes", []),
                },
            ))
        return out

    def datasets(self) -> list[StructuredDataset]:
        out = []
        for dataset_id, row in self.raw.get("datasets", {}).items():
            out.append(StructuredDataset(
                id=dataset_id,
                catalog_status=CatalogStatus(row.get("catalog_status", "planned")),
                expected_cadence=row.get("expected_cadence", ""),
                primary_sources=list(row.get("primary_sources") or []),
                fallback_sources=list(row.get("fallback_sources") or []),
                core_metrics=list(row.get("core_metrics") or []),
                quality=dict(row.get("quality") or {}),
                entities=list(row.get("entities") or []),
                acceptance_samples=list(row.get("acceptance_samples") or []),
            ))
        return out

    def metrics(self) -> list[MetricDefinition]:
        return [MetricDefinition(id=metric_id, **(row or {}))
                for metric_id, row in self.raw.get("metric_definitions", {}).items()]

    def provider_mappings(self) -> list[tuple[str, str, str]]:
        return [(provider, field, metric)
                for provider, mappings in self.raw.get("provider_mappings", {}).items()
                for field, metric in mappings.items()]

    def runtime_excluded(self) -> list[StructuredSource]:
        return [source for source in self.sources()
                if source.catalog_status == CatalogStatus.RUNTIME_EXCLUDED]
