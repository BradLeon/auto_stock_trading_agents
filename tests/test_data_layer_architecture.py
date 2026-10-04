"""Static guardrails for the unified data-layer dependency direction."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ats.data.core import (
    EntityKind,
    EntityRef,
    IngestionRun,
    IngestionRunStatus,
    LineageRef,
    QualityLevel,
    QualitySnapshot,
    SourceKind,
    SourceRef,
    SourceStatus,
)


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "ats" / "data"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _files(area: str) -> list[Path]:
    path = DATA / area
    return sorted(path.rglob("*.py")) if path.exists() else []


def _violations(area: str, forbidden: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for path in _files(area):
        for module in _imports(path):
            if module == forbidden or any(module.startswith(prefix + ".") for prefix in forbidden):
                violations.append(f"{path.relative_to(ROOT)} imports {module}")
    return violations


def test_data_layer_package_skeleton_exists():
    expected = [
        DATA / "core",
        DATA / "catalog",
        DATA / "adapters" / "structured",
        DATA / "adapters" / "unstructured",
        DATA / "pipelines" / "common",
        DATA / "pipelines" / "structured",
        DATA / "pipelines" / "unstructured",
        DATA / "stores" / "structured",
        DATA / "stores" / "unstructured",
        DATA / "runtime",
    ]
    assert all(path.is_dir() for path in expected)


def test_adapters_do_not_depend_on_consumers_or_memory():
    forbidden = ("ats.data.products", "ats.data.products", "ats.memory", "ats.data.structured")
    assert not _violations("adapters", forbidden)


def test_stores_do_not_depend_on_adapters_or_network_clients():
    forbidden = ("ats.data.adapters", "ats.memory", "requests", "httpx", "yfinance", "feedparser")
    assert not _violations("stores", forbidden)


def test_runtime_does_not_depend_on_persistent_stores():
    forbidden = ("ats.data.stores", "ats.data.structured", "ats.memory")
    assert not _violations("runtime", forbidden)


def test_new_products_do_not_import_provider_adapters():
    forbidden = ("ats.data.adapters", "ats.data.sources", "ats.data.structured.runtime_registry")
    assert not _violations("products", forbidden)


def test_core_contracts_are_domain_neutral_and_timezone_safe():
    entity = EntityRef(id="msft", kind=EntityKind.COMPANY)
    source = SourceRef(id="sec", kind=SourceKind.OFFICIAL, status=SourceStatus.PUBLISHED)
    run = IngestionRun(
        id="run-1",
        source_id=source.id,
        dataset_id="company_financials",
        status=IngestionRunStatus.SUCCEEDED,
        started_at=datetime(2026, 8, 26, 1, tzinfo=timezone.utc),
    )
    assert entity.id == "MSFT"
    assert source.status is SourceStatus.PUBLISHED
    assert run.started_at.tzinfo is not None
    assert QualitySnapshot(level=QualityLevel.ACCEPTED).level is QualityLevel.ACCEPTED
    assert LineageRef(source_id=source.id).source_id == "sec"

    with pytest.raises(ValueError, match="timezone-aware"):
        IngestionRun(
            id="run-2",
            source_id="sec",
            started_at=datetime(2026, 8, 26, 1),
        )


def test_product_compatibility_entrypoints_share_the_same_implementation():
    from ats.data.products import DataProducts as LegacyDataProducts
    from ats.data.products import get_data_products as legacy_get
    from ats.data.products import DataProducts, get_data_products

    assert DataProducts is LegacyDataProducts
    assert get_data_products is legacy_get


def test_structured_compatibility_entrypoint_reexports_legacy_surface():
    import ats.data.structured as legacy
    import ats.data.structured as unified

    assert unified.StructuredCatalog is legacy.StructuredCatalog
    assert unified.get_repository is legacy.get_repository


def test_structured_lifecycle_surfaces_share_legacy_implementations():
    from ats.data.adapters.structured.registry import validate_source_registration
    from ats.data.pipelines.structured.ingestion import IngestionPipeline
    from ats.data.products.structured import StructuredDataProducts
    from ats.data.stores.structured.repository import SQLiteStructuredRepository
    from ats.data.structured import IngestionPipeline as LegacyPipeline
    from ats.data.structured import SQLiteStructuredRepository as LegacyRepository
    from ats.data.structured import validate_source_registration as legacy_validate
    from ats.data.products import DataProducts as LegacyProducts

    assert validate_source_registration is legacy_validate
    assert IngestionPipeline is LegacyPipeline
    assert SQLiteStructuredRepository is LegacyRepository
    assert StructuredDataProducts is LegacyProducts


def test_retired_namespaces_and_config_aliases_are_absent():
    retired_paths = (
        ROOT / "src" / "ats" / "structured",
        ROOT / "src" / "ats" / "data_platform",
        ROOT / "config" / "structured_data.yaml",
        ROOT / "config" / "sources.yaml",
        ROOT / "config" / "news_sources.yaml",
    )
    # Python bytecode directories are ignored build residue; the importable package
    # and configuration aliases themselves must be absent.
    assert not any(path.is_file() or (path / "__init__.py").is_file()
                       for path in retired_paths)
    for path in (ROOT / "src" / "ats").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "ats.data_platform" not in text
        assert "from ats.structured" not in text
