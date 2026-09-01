"""Inventory and manifest contracts for the data-layer cutover."""

from .inventory import (
    LegacyConsumer,
    LegacyObject,
    MigrationDomain,
    MigrationInventory,
    MigrationValidation,
    load_migration_inventory,
)
from .runner import MigrationManifest, SQLiteMigrationRunner, TableMigrationResult, default_data_db_path
from .governed_structured import GovernedStructuredMigrationRunner
from .structured import StructuredLegacyMigrationRunner, StructuredMigrationManifest
from .company_financials import CompanyFinancialSemanticRepair, CompanyFinancialSemanticRepairResult
from .retirement import LegacyDataRetirement

__all__ = [
    "LegacyConsumer",
    "LegacyObject",
    "MigrationDomain",
    "MigrationInventory",
    "MigrationManifest",
    "MigrationValidation",
    "GovernedStructuredMigrationRunner",
    "CompanyFinancialSemanticRepair",
    "CompanyFinancialSemanticRepairResult",
    "SQLiteMigrationRunner",
    "StructuredLegacyMigrationRunner",
    "StructuredMigrationManifest",
    "TableMigrationResult",
    "default_data_db_path",
    "load_migration_inventory",
    "LegacyDataRetirement",
]
