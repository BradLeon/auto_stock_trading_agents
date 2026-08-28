"""Safety gates for legacy-data inventory and migration planning."""

from __future__ import annotations

from pathlib import Path

from ats.data.migration import load_migration_inventory


ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_legacy_inventory_has_owners_consumers_and_rollback_paths():
    inventory = load_migration_inventory()
    result = inventory.validate()

    assert result.valid is True
    assert {item.id for item in inventory.legacy_objects} >= {
        "data-platform-products", "memory-data-tables", "legacy-structured-config",
    }
    assert {item.id for item in inventory.consumers} >= {
        "pead-graph", "sector-agent", "evidence-chain", "chief-graph",
    }
    assert {item.id for item in inventory.domains} == {
        "structured-governed-history", "structured-legacy-measurements",
        "unstructured-documents", "unstructured-evidence",
    }
    evidence = next(item for item in inventory.domains if item.id == "unstructured-evidence")
    assert set(evidence.source_tables) == {
        "evidence_observations", "evidence_failures", "evidence_facts",
        "evidence_fact_projections",
    }
    assert "task_projections" not in evidence.source_tables


def test_inventory_dry_run_rejects_unowned_missing_and_unbacked_assets(tmp_path):
    inventory_path = tmp_path / "inventory.yaml"
    migration_path = tmp_path / "migration.yaml"
    inventory_path.write_text(
        "version: 1\n"
        "legacy_objects:\n"
        "  - id: unowned\n"
        "    kind: module\n"
        "    paths: [src/ats/data/__init__.py, missing.py]\n"
        "    target_owner: ''\n"
        "    data_domains: [structured]\n"
        "    rollback: ''\n"
        "consumers:\n"
        "  - id: consumer\n"
        "    paths: [src/ats/graph/pead.py]\n"
        "    target_interface: ''\n"
        "    sources: [company_financials]\n"
        "    rollback: ''\n",
        encoding="utf-8",
    )
    migration_path.write_text(
        "version: 1\nbackup: {}\n"
        "domains:\n"
        "  - id: domain\n"
        "    source_tables: [measurement_points]\n"
        "    target_owner: ''\n"
        "    target_tables: [structured_observations]\n"
        "    batch_key: [point_id]\n"
        "    stable_keys: [point_id]\n"
        "    reconcile: []\n"
        "    rollback: ''\n",
        encoding="utf-8",
    )

    result = load_migration_inventory(inventory_path, migration_path).validate()

    assert result.valid is False
    assert "legacy_path_missing" in result.reason_codes
    assert "legacy_owner_missing" in result.reason_codes
    assert "legacy_rollback_missing" in result.reason_codes
    assert "consumer_target_missing" in result.reason_codes
    assert "consumer_rollback_missing" in result.reason_codes
    assert "backup_root_missing" in result.reason_codes
    assert "verified_backup_required" in result.reason_codes
    assert "migration_target_missing" in result.reason_codes
    assert "migration_reconcile_missing" in result.reason_codes
    assert "migration_rollback_missing" in result.reason_codes


def test_migration_plan_cli_is_read_only(capsys):
    from ats.runtime.cli import main

    assert main(["data", "migration-plan"]) == 0
    output = capsys.readouterr().out
    assert "legacy_inventory.yaml" in output
    assert '"valid": true' in output
