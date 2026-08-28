"""Read-only validation of the legacy-retirement inventory and migration plan."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


LegacyKind = Literal["module", "config", "database", "artifact", "document_root"]


class LegacyObject(BaseModel):
    id: str
    kind: LegacyKind
    paths: list[str] = Field(min_length=1)
    target_owner: str = ""
    data_domains: list[str] = Field(min_length=1)
    rollback: str = ""


class LegacyConsumer(BaseModel):
    id: str
    paths: list[str] = Field(min_length=1)
    target_interface: str = ""
    sources: list[str] = Field(min_length=1)
    rollback: str = ""


class MigrationDomain(BaseModel):
    id: str
    source_tables: list[str] = Field(min_length=1)
    target_owner: str = ""
    target_tables: list[str] = Field(min_length=1)
    batch_key: list[str] = Field(min_length=1)
    stable_keys: list[str] = Field(min_length=1)
    reconcile: list[str] = Field(default_factory=list)
    rollback: str = ""


class MigrationValidation(BaseModel):
    valid: bool
    checks: list[dict[str, str | bool]] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


def _root() -> Path:
    from ...config import REPO_ROOT

    return REPO_ROOT


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class MigrationInventory:
    """The approved retirement scope; loading it never opens a database or source file."""

    def __init__(self, *, inventory_path: Path, migration_path: Path,
                 legacy_objects: list[LegacyObject], consumers: list[LegacyConsumer],
                 domains: list[MigrationDomain], backup: dict,
                 consumer_cutover: dict | None = None):
        self.inventory_path = inventory_path
        self.migration_path = migration_path
        self.legacy_objects = legacy_objects
        self.consumers = consumers
        self.domains = domains
        self.backup = backup
        self.consumer_cutover = dict(consumer_cutover or {})

    @classmethod
    def load(cls, inventory_path: str | Path | None = None,
             migration_path: str | Path | None = None) -> "MigrationInventory":
        root = _root()
        inventory_file = Path(inventory_path or root / "config/data/legacy_inventory.yaml")
        migration_file = Path(migration_path or root / "config/data/migration.yaml")
        inventory_file = inventory_file.expanduser().resolve()
        migration_file = migration_file.expanduser().resolve()
        inventory = _load(inventory_file)
        migration = _load(migration_file)
        if inventory.get("version") != 1:
            raise ValueError("unsupported legacy inventory version")
        if migration.get("version") != 1:
            raise ValueError("unsupported migration plan version")
        return cls(
            inventory_path=inventory_file,
            migration_path=migration_file,
            legacy_objects=[LegacyObject.model_validate(item)
                            for item in inventory.get("legacy_objects", [])],
            consumers=[LegacyConsumer.model_validate(item)
                       for item in inventory.get("consumers", [])],
            domains=[MigrationDomain.model_validate(item)
                     for item in migration.get("domains", [])],
            backup=dict(migration.get("backup") or {}),
            consumer_cutover=dict(migration.get("consumer_cutover") or {}),
        )

    def _path_exists(self, value: str) -> bool:
        # Environment variables and config field references are resolved at run time,
        # so validate their declaration rather than treating them as local paths.
        if value.isupper() and "_" in value:
            return True
        path_value = value.split(":", 1)[0]
        return (_root() / path_value).exists()

    def validate(self) -> MigrationValidation:
        checks: list[dict[str, str | bool]] = []

        def check(name: str, passed: bool, reason: str = "") -> None:
            checks.append({"check": name, "passed": bool(passed),
                           "reason": "" if passed else reason})

        check("inventory:legacy_objects", bool(self.legacy_objects), "legacy_objects_missing")
        check("inventory:consumers", bool(self.consumers), "consumers_missing")
        check("migration:domains", bool(self.domains), "migration_domains_missing")
        check("migration:backup_root", bool(self.backup.get("root_env") and
                                             self.backup.get("default_root")),
              "backup_root_missing")
        check("migration:verified_backup", self.backup.get("require_verified_copy") is True,
              "verified_backup_required")

        seen: set[str] = set()
        for item in self.legacy_objects:
            check(f"legacy:{item.id}:unique", item.id not in seen, "legacy_id_duplicate")
            seen.add(item.id)
            check(f"legacy:{item.id}:paths", all(self._path_exists(path) for path in item.paths),
                  "legacy_path_missing")
            check(f"legacy:{item.id}:owner", bool(item.target_owner), "legacy_owner_missing")
            check(f"legacy:{item.id}:rollback", bool(item.rollback), "legacy_rollback_missing")

        consumer_ids: set[str] = set()
        for consumer in self.consumers:
            check(f"consumer:{consumer.id}:unique", consumer.id not in consumer_ids,
                  "consumer_id_duplicate")
            consumer_ids.add(consumer.id)
            check(f"consumer:{consumer.id}:paths",
                  all(self._path_exists(path) for path in consumer.paths),
                  "consumer_path_missing")
            check(f"consumer:{consumer.id}:target", bool(consumer.target_interface),
                  "consumer_target_missing")
            check(f"consumer:{consumer.id}:rollback", bool(consumer.rollback),
                  "consumer_rollback_missing")

        domain_ids: set[str] = set()
        for domain in self.domains:
            check(f"domain:{domain.id}:unique", domain.id not in domain_ids,
                  "migration_domain_duplicate")
            domain_ids.add(domain.id)
            check(f"domain:{domain.id}:target", bool(domain.target_owner),
                  "migration_target_missing")
            check(f"domain:{domain.id}:rollback", bool(domain.rollback),
                  "migration_rollback_missing")
            check(f"domain:{domain.id}:reconcile", bool(domain.reconcile),
                  "migration_reconcile_missing")

        reasons = [str(row["reason"]) for row in checks if not row["passed"]]
        return MigrationValidation(valid=not reasons, checks=checks, reason_codes=reasons)

    def summary(self) -> dict:
        validation = self.validate()
        return {
            "inventory": str(self.inventory_path),
            "migration_plan": str(self.migration_path),
            "legacy_objects": [item.model_dump(mode="json") for item in self.legacy_objects],
            "consumers": [item.model_dump(mode="json") for item in self.consumers],
            "domains": [item.model_dump(mode="json") for item in self.domains],
            "backup": self.backup,
            "validation": validation.model_dump(mode="json"),
        }


def load_migration_inventory(inventory_path: str | Path | None = None,
                             migration_path: str | Path | None = None) -> MigrationInventory:
    return MigrationInventory.load(inventory_path, migration_path)


__all__ = [
    "LegacyConsumer",
    "LegacyObject",
    "MigrationDomain",
    "MigrationInventory",
    "MigrationValidation",
    "load_migration_inventory",
]
