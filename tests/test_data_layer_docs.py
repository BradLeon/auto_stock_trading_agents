"""Documentation checks for the unified data-layer contract."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / "docs" / name).read_text(encoding="utf-8")


def test_architecture_doc_points_to_unified_catalog_and_target_tree():
    text = _read("DATA_ARCHITECTURE.md")
    assert "config/data/catalog.yaml" in text
    assert "ats.data.products" in text
    assert "ats.data.adapters" in text
    assert "ats.data.stores" in text
    assert "runtime/excluded" in text


def test_developer_doc_describes_component_boundaries_and_imports():
    text = _read("STRUCTURED_DATA_DEVELOPER.md")
    for value in (
        "ats/data/", "adapters/structured", "adapters/unstructured", "pipelines",
        "stores", "products", "ats.data.products", "ats.data_platform",
        "ats data config",
    ):
        assert value in text


def test_operations_doc_separates_config_and_read_only_validation():
    text = _read("STRUCTURED_DATA_OPERATIONS.md")
    for value in (
        "config/data/catalog.yaml", "config/data/structured.yaml",
        "config/data/unstructured.yaml", "config/data/providers/",
        "ats_cli data config", "不联网、不写数据库、不修改发布状态",
    ):
        assert value in text


def test_user_doc_explains_persistent_unstructured_and_runtime_boundaries():
    text = _read("STRUCTURED_DATA_USER_GUIDE.md")
    for value in (
        "ats.data.products", "ats.data.runtime", "文档/证据", "当前股价",
        "ats_cli data config", "data catalog", "data availability",
    ):
        assert value in text
