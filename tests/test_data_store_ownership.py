"""Repository ownership and staged reconciliation tests."""

import re

from ats.data.stores import DATA_LAYER_TABLES, WORKFLOW_MEMORY_TABLES, reconcile_rows
from ats.data.stores.unstructured.repository import UnstructuredRepository
from ats.data_platform import DataProducts
from ats.memory.store import _SCHEMA
from ats.structured import SQLiteStructuredRepository, StructuredCatalog


def test_legacy_sqlite_tables_have_explicit_data_or_memory_ownership():
    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", _SCHEMA))
    assert tables
    assert tables <= DATA_LAYER_TABLES | WORKFLOW_MEMORY_TABLES
    assert DATA_LAYER_TABLES.isdisjoint(WORKFLOW_MEMORY_TABLES)


def test_workflow_results_are_memory_not_data_layer_inputs():
    workflow_state = {"task_projections", "claim_proposals", "claim_assessments"}

    assert workflow_state <= WORKFLOW_MEMORY_TABLES
    assert workflow_state.isdisjoint(DATA_LAYER_TABLES)


def test_data_products_routes_document_reads_through_data_repository():
    class Backend:
        def documents(self, **_):
            return []

        def facts(self, **_):
            return []

        def task_projections(self, **_):
            return []

    backend = Backend()
    products = DataProducts(store=backend)
    assert isinstance(products.unstructured, UnstructuredRepository)
    assert products.unstructured.backend is backend


def test_reconciliation_reports_missing_unexpected_and_value_differences():
    result = reconcile_rows(
        [{"id": "a", "value": 1}, {"id": "b", "value": 2}],
        [{"id": "a", "value": 3}, {"id": "c", "value": 4}],
        key_fields=("id",), value_fields=("value",),
    )
    assert result.matched is False
    assert result.missing == ["b"]
    assert result.unexpected == ["c"]
    assert result.mismatches[0]["key"] == "a"


def test_structured_repository_restart_keeps_catalog_and_legacy_schema(tmp_path):
    db_path = tmp_path / "structured.sqlite"
    first = SQLiteStructuredRepository(db_path, artifact_root=tmp_path / "artifacts")
    first.bootstrap_catalog(StructuredCatalog.load())
    assert first.dataset("company_financials") is not None
    first.close()

    second = SQLiteStructuredRepository(db_path, artifact_root=tmp_path / "artifacts")
    second.bootstrap_catalog(StructuredCatalog.load())
    assert second.dataset("company_financials") is not None
    second.close()
