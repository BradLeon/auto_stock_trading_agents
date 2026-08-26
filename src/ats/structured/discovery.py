"""Dynamic structured-data discovery built from catalog plus repository state."""

from __future__ import annotations

from collections import defaultdict
import json

from .catalog import StructuredCatalog
from .flags import source_mode
from .reporting import build_quality_report


class DataDiscovery:
    def __init__(self, repository, *, catalog: StructuredCatalog | None = None):
        self.repository = repository
        self.catalog = catalog or StructuredCatalog.load()

    def catalog_view(self) -> dict:
        observations = self.repository.observations(
            latest_only=True, accepted_only=True, limit=1_000_000)
        by_dataset: dict[str, list[dict]] = defaultdict(list)
        by_metric: dict[str, list[dict]] = defaultdict(list)
        by_source: dict[str, list[dict]] = defaultdict(list)
        for row in observations:
            by_dataset[row["dataset_id"]].append(row)
            by_metric[row["metric_id"]].append(row)
            by_source[row["source_id"]].append(row)
        quality = build_quality_report(self.repository)
        quality_by_dataset = {row["dataset_id"]: row for row in quality["datasets"]}
        health = {row["source_id"]: row for row in self.repository.source_health()}

        sources = []
        for source in self.catalog.sources():
            rows = by_source[source.id]
            last = health.get(source.id) or {}
            sources.append({
                "source_id": source.id, "provider": source.provider,
                "adapter": source.adapter, "catalog_status": source.catalog_status.value,
                "persistence": source.persistence.value, "release_mode": source_mode(source.id),
                "datasets": source.datasets, "accepted_observations": len(rows),
                "last_ingestion_status": last.get("last_status") or "no_run",
                "last_completed_at": last.get("last_completed_at") or None,
                "availability": "runtime_excluded" if source.catalog_status.value == "runtime_excluded"
                else ("queryable" if rows else "registered_no_data"),
            })

        datasets = []
        for dataset in self.catalog.datasets():
            rows = by_dataset[dataset.id]
            periods = sorted({row["period"] for row in rows if row.get("period")})
            metrics = sorted({row["metric_id"] for row in rows})
            entities = sorted({row["entity_id"] for row in rows})
            qrow = quality_by_dataset.get(dataset.id) or {}
            datasets.append({
                "dataset_id": dataset.id, "catalog_status": dataset.catalog_status.value,
                "availability": "queryable" if rows else "registered_no_data",
                "accepted_observations": len(rows), "entities": entities,
                "actual_metrics": metrics, "core_metrics": dataset.core_metrics,
                "period_start": periods[0] if periods else None,
                "period_end": periods[-1] if periods else None,
                "latest_known_at": max((row["known_at"] for row in rows), default=None),
                "expected_cadence": dataset.expected_cadence,
                "primary_sources": dataset.primary_sources,
                "fallback_sources": dataset.fallback_sources,
                "quality_status": qrow.get("overall_status", "not_evaluated"),
            })

        metrics = []
        for metric in self.catalog.metrics():
            rows = by_metric[metric.id]
            metrics.append({
                "metric_id": metric.id, "description": metric.description,
                "unit_family": metric.unit_family, "period_basis": metric.period_basis,
                "derived": metric.derived,
                "availability": "queryable" if rows else "registered_no_data",
                "accepted_observations": len(rows),
                "datasets": sorted({row["dataset_id"] for row in rows}),
                "entities": sorted({row["entity_id"] for row in rows}),
            })
        return {"schema_version": 1, "sources": sources, "datasets": datasets,
                "metrics": metrics, "summary": {
                    "queryable_datasets": sum(row["availability"] == "queryable"
                                               for row in datasets),
                    "registered_datasets": len(datasets),
                    "queryable_metrics": sum(row["availability"] == "queryable"
                                              for row in metrics),
                    "accepted_observations": len(observations),
                }}

    def describe(self, value: str, *, kind: str = "") -> dict:
        target = value.strip()
        view = self.catalog_view()
        collections = {
            "source": ("sources", "source_id"),
            "dataset": ("datasets", "dataset_id"),
            "metric": ("metrics", "metric_id"),
        }
        selected = [kind] if kind else list(collections)
        for current in selected:
            collection, key = collections[current]
            row = next((item for item in view[collection]
                        if item[key].casefold() == target.casefold()), None)
            if row:
                examples = self.examples(dataset=row["dataset_id"]) \
                    if current == "dataset" else None
                return {"found": True, "kind": current, "id": row[key],
                        "details": row, "examples": examples}
        entity = target.upper()
        rows = self.repository.observations(
            entity_id=entity, latest_only=True, accepted_only=True, limit=100_000)
        registered = next((row for row in self.repository.entities()
                           if row["entity_id"] == entity), None)
        if rows or registered:
            return {"found": True, "kind": "entity", "id": entity,
                    "details": {
                        "entity": registered,
                        "availability": "queryable" if rows else "registered_no_data",
                        "accepted_observations": len(rows),
                        "datasets": sorted({row["dataset_id"] for row in rows}),
                        "metrics": sorted({row["metric_id"] for row in rows}),
                        "period_start": min((row["period"] for row in rows), default=None),
                        "period_end": max((row["period"] for row in rows), default=None),
                    }}
        return {"found": False, "kind": kind or "unknown", "id": target,
                "reason": "not_registered_or_observed"}

    def availability(self, *, entity: str = "", dataset: str = "") -> dict:
        if not entity and not dataset:
            raise ValueError("availability requires entity or dataset")
        rows = self.repository.observations(
            entity_id=entity or None, dataset_id=dataset or None,
            latest_only=True, accepted_only=True, limit=1_000_000)
        by_dataset: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_dataset[row["dataset_id"]].append(row)
        configured = self.catalog.datasets()
        if dataset:
            configured = [row for row in configured if row.id == dataset]
            if not configured:
                raise KeyError(f"unknown structured dataset: {dataset}")
        output = []
        for item in configured:
            current = by_dataset[item.id]
            periods = sorted({row["period"] for row in current})
            output.append({
                "dataset_id": item.id,
                "entity_id": entity.upper() if entity else None,
                "status": "queryable" if current else "no_coverage",
                "accepted_observations": len(current),
                "metrics": sorted({row["metric_id"] for row in current}),
                "entities": sorted({row["entity_id"] for row in current}),
                "period_start": periods[0] if periods else None,
                "period_end": periods[-1] if periods else None,
                "latest_known_at": max((row["known_at"] for row in current), default=None),
            })
        return {"entity_filter": entity.upper() if entity else None,
                "dataset_filter": dataset or None, "datasets": output}

    def examples(self, *, dataset: str = "") -> dict:
        rows = self.repository.observations(
            dataset_id=dataset or None, latest_only=True,
            accepted_only=True, limit=10_000)
        if not rows:
            return {"dataset_id": dataset or None, "status": "no_data", "examples": []}
        row = sorted(rows, key=lambda item: (
            item["dataset_id"], item["metric_id"], item["entity_id"], item["period"]))[0]
        dataset_id = row["dataset_id"]
        metric = row["metric_id"]
        entity = row["entity_id"]
        commands = [
            f"ats data availability --dataset {dataset_id} --entity {entity}",
            f"ats data series --dataset {dataset_id} --metric {metric} --entity {entity}",
            f"ats data series --dataset {dataset_id} --metric {metric} --entity {entity} --vintages",
            f"ats data lineage {row['observation_id']}",
        ]
        if len([item for item in rows if item["metric_id"] == metric
                and item["entity_id"] == entity]) >= 2:
            commands.append(
                f"ats data derive --dataset {dataset_id} --metric {metric} "
                f"--entity {entity} --operation yoy")
        return {"dataset_id": dataset_id, "status": "ok",
                "selected_from_actual_coverage": {
                    "metric_id": metric, "entity_id": entity, "period": row["period"],
                    "observation_id": row["observation_id"]},
                "examples": commands,
                "python": (
                    "from ats.data_platform import get_data_products\n"
                    "data = get_data_products().metric_series(\n"
                    f"    dataset={dataset_id!r}, metric={metric!r}, entity={entity!r})")}


def render_discovery_markdown(result: dict) -> str:
    if "summary" in result:
        lines = ["# Structured Data Catalog", "",
                 f"Accepted observations: {result['summary']['accepted_observations']}", "",
                 "| Dataset | Availability | Observations | Metrics | Entities | Quality |",
                 "|---|---|---:|---:|---:|---|"]
        for row in result["datasets"]:
            lines.append(
                f"| `{row['dataset_id']}` | `{row['availability']}` | "
                f"{row['accepted_observations']} | {len(row['actual_metrics'])} | "
                f"{len(row['entities'])} | `{row['quality_status']}` |")
        return "\n".join(lines) + "\n"
    if "examples" in result and result.get("status") in {"ok", "no_data"}:
        lines = ["# Structured Data Examples", "", f"Status: `{result['status']}`", ""]
        lines.extend(f"- `{command}`" for command in result.get("examples", []))
        return "\n".join(lines) + "\n"
    return "```json\n" + json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n```\n"
