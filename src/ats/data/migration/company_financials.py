"""Auditable semantic repair for pre-governance company-financial rows.

The initial DefeatBeta statement ingestion labelled every ``diluted_eps`` and
``total_debt`` row as GAAP.  That was not safe: per-share history can be split
adjusted, TSM's mirror is ADR-denominated, and provider debt may include items
outside SEC's official total-debt concept.  This repair changes *series
semantics*, not values or artifacts, so accepted observation IDs remain stable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


_SOURCE = "defeatbeta_stock_statement"
_DATASET = "company_financials"
_EPS = "financial.eps.diluted.gaap"
_DEBT = "financial.total_debt.gaap"
_REPAIR_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_company_financial_semantic_repairs (
    repair_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, database_path TEXT NOT NULL,
    dry_run INTEGER NOT NULL, backup_path TEXT NOT NULL, status TEXT NOT NULL,
    details_json TEXT NOT NULL
);
"""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class SeriesRepair:
    old_series_id: str
    new_series_id: str
    entity_id: str
    old_metric_id: str
    new_metric_id: str
    old_adjustment: str
    new_adjustment: str
    old_unit: str
    new_unit: str
    observation_count: int


@dataclass(frozen=True)
class CompanyFinancialSemanticRepairResult:
    repair_id: str
    database_path: str
    dry_run: bool
    backup_path: str
    status: str
    series: tuple[SeriesRepair, ...]
    candidates_reclassified: int
    stale_conflicts_removed: int
    conflicts_recomputed: int
    scope_open_conflicts: int
    unrelated_open_conflicts: int

    @property
    def reconciled(self) -> bool:
        return self.status == "reconciled" and self.scope_open_conflicts == 0

    def model_dump(self) -> dict:
        return {
            **asdict(self),
            "series": [asdict(item) for item in self.series],
            "reconciled": self.reconciled,
        }


class CompanyFinancialSemanticRepair:
    """Reclassify legacy mirror series without mutating their economic values."""

    def __init__(self, database_path: str | Path):
        self.path = Path(database_path).expanduser().resolve()

    @staticmethod
    def _target(row: sqlite3.Row) -> tuple[str, str, str, dict]:
        """Return metric, adjustment, unit and dimensions for one old series."""
        dimensions = json.loads(row["dimensions_json"] or "{}")
        if row["metric_id"] == _DEBT:
            return ("financial.total_debt.provider_reported", "provider_reported",
                    row["unit"], dimensions)
        if row["entity_id"].upper() == "TSM":
            dimensions["share_basis"] = "adr"
            return ("financial.eps.diluted.adr", "provider_reported", "TWD/ADR", dimensions)
        return ("financial.eps.diluted.market_adjusted", "split_adjusted",
                row["unit"], dimensions)

    @staticmethod
    def _identity(row: sqlite3.Row, *, metric_id: str, adjustment: str,
                  unit: str, dimensions: dict) -> tuple[str, str]:
        payload = {
            "source_id": row["source_id"], "dataset_id": row["dataset_id"],
            "entity_id": row["entity_id"], "metric_id": metric_id,
            "unit": unit, "currency": row["currency"],
            "period_basis": row["period_basis"], "adjustment": adjustment,
            "dimensions": dimensions,
        }
        identity_hash = hashlib.sha256(_json(payload).encode()).hexdigest()
        return identity_hash[:24], identity_hash

    def plan(self) -> tuple[SeriesRepair, ...]:
        if not self.path.is_file():
            raise FileNotFoundError(f"company-financial database not found: {self.path}")
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT s.*,count(o.observation_id) AS observation_count "
                "FROM structured_series s LEFT JOIN structured_observations o "
                "ON o.series_id=s.series_id "
                "WHERE s.source_id=? AND s.dataset_id=? AND s.metric_id IN (?,?) "
                "GROUP BY s.series_id ORDER BY s.entity_id,s.metric_id,s.period_basis",
                (_SOURCE, _DATASET, _EPS, _DEBT),
            ).fetchall()
            plan = []
            for row in rows:
                metric, adjustment, unit, dimensions = self._target(row)
                series_id, _ = self._identity(
                    row, metric_id=metric, adjustment=adjustment, unit=unit,
                    dimensions=dimensions)
                plan.append(SeriesRepair(
                    old_series_id=row["series_id"], new_series_id=series_id,
                    entity_id=row["entity_id"], old_metric_id=row["metric_id"],
                    new_metric_id=metric, old_adjustment=row["adjustment"],
                    new_adjustment=adjustment, old_unit=row["unit"], new_unit=unit,
                    observation_count=int(row["observation_count"]),
                ))
            return tuple(plan)
        finally:
            conn.close()

    def _backup(self, root: str | Path) -> Path:
        backup_root = Path(root).expanduser().resolve()
        backup_root.mkdir(parents=True, exist_ok=True)
        target = backup_root / (
            f"{self.path.stem}.company-financial-semantics."
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.sqlite")
        source = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        backup = sqlite3.connect(target)
        try:
            source.backup(backup)
        finally:
            backup.close()
            source.close()
        if not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError("company-financial backup failed")
        return target

    @staticmethod
    def _candidate_target(entity_id: str, provider_field: str, metric_id: str,
                          unit: str) -> tuple[str, str, str] | None:
        if provider_field == "total_debt" and metric_id == _DEBT:
            return provider_field, "financial.total_debt.provider_reported", unit
        if provider_field != "diluted_eps" or metric_id != _EPS:
            return None
        if entity_id.upper() == "TSM":
            return "tsm_diluted_eps_adr_twd", "financial.eps.diluted.adr", "TWD/ADR"
        return provider_field, "financial.eps.diluted.market_adjusted", unit

    @staticmethod
    def _recompute_conflicts(conn: sqlite3.Connection) -> int:
        """Rebuild only mirror-as-right-side conflicts under current semantics."""
        tolerance_row = conn.execute(
            "SELECT quality_json FROM structured_datasets WHERE dataset_id=?", (_DATASET,)
        ).fetchone()
        quality = json.loads(tolerance_row[0] or "{}") if tolerance_row else {}
        tolerance = float(quality.get("reconciliation_relative_tolerance", 1e-9))
        # Match repository ``latest_only`` semantics: historical vintages remain
        # auditable, but cannot create a new open conflict once superseded.
        rows = conn.execute(
            "SELECT o.observation_id,o.period,o.value,s.dataset_id,s.entity_id,s.metric_id,"
            "s.unit,s.currency,s.period_basis,s.adjustment "
            "FROM structured_observations o JOIN structured_series s ON s.series_id=o.series_id "
            "WHERE s.source_id=? AND s.dataset_id=? "
            "AND o.quality_status IN ('accepted','warning','conflict') "
            "AND NOT EXISTS (SELECT 1 FROM structured_observations newer "
            "WHERE newer.series_id=o.series_id AND newer.period=o.period "
            "AND (newer.known_at>o.known_at OR (newer.known_at=o.known_at "
            "AND newer.fetched_at>o.fetched_at)))",
            (_SOURCE, _DATASET),
        ).fetchall()
        created = 0
        for row in rows:
            matches = conn.execute(
                "SELECT o.observation_id,o.value FROM structured_observations o "
                "JOIN structured_series s ON s.series_id=o.series_id "
                "WHERE s.dataset_id=? AND s.entity_id=? AND s.metric_id=? AND o.period=? "
                "AND s.source_id!=? AND s.unit=? AND s.currency=? AND s.period_basis=? "
                "AND s.adjustment=? AND o.quality_status IN ('accepted','warning','conflict') "
                "AND NOT EXISTS (SELECT 1 FROM structured_observations newer "
                "WHERE newer.series_id=o.series_id AND newer.period=o.period "
                "AND (newer.known_at>o.known_at OR (newer.known_at=o.known_at "
                "AND newer.fetched_at>o.fetched_at)))",
                (row["dataset_id"], row["entity_id"], row["metric_id"], row["period"],
                 _SOURCE, row["unit"], row["currency"], row["period_basis"],
                 row["adjustment"]),
            ).fetchall()
            for other in matches:
                if abs(float(other["value"]) - float(row["value"])) <= max(
                        1e-9, tolerance * max(abs(float(other["value"])), abs(float(row["value"])))):
                    continue
                absolute = abs(float(other["value"]) - float(row["value"]))
                relative = absolute / abs(float(other["value"])) if other["value"] else None
                conflict_id = hashlib.sha1(
                    f"{row['dataset_id']}|{row['entity_id']}|{row['metric_id']}|{row['period']}|"
                    f"{other['observation_id']}|{_SOURCE}|{row['value']}".encode()).hexdigest()[:24]
                conn.execute(
                    "INSERT OR IGNORE INTO structured_conflicts VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,'open',?)",
                    (conflict_id, row["dataset_id"], row["entity_id"], row["metric_id"],
                     row["period"], other["observation_id"], _SOURCE, row["value"],
                     absolute, relative, _stamp()),
                )
                conn.execute(
                    "UPDATE structured_observations SET quality_status='conflict',"
                    "quality_json=? WHERE observation_id=?",
                    (_json({"reason_codes": ["cross_source_conflict"]}), row["observation_id"]),
                )
                created += 1
        return created

    def run(self, *, backup_root: str | Path | None = None,
            dry_run: bool = True) -> CompanyFinancialSemanticRepairResult:
        plan = self.plan()
        if not dry_run and not backup_root:
            raise ValueError("backup_root is required for a non-dry-run semantic repair")
        if dry_run:
            return CompanyFinancialSemanticRepairResult(
                repair_id="", database_path=str(self.path), dry_run=True, backup_path="",
                status="reconciled", series=plan, candidates_reclassified=0,
                stale_conflicts_removed=0, conflicts_recomputed=0,
                scope_open_conflicts=0, unrelated_open_conflicts=0,
            )

        backup_path = self._backup(backup_root)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(_REPAIR_SCHEMA)
            # Catalog bootstrap makes the new governed metric definitions and provider
            # mappings visible before rows use them.
            from ...structured import SQLiteStructuredRepository

            repo = SQLiteStructuredRepository(self.path)
            try:
                repo.bootstrap_catalog()
            finally:
                repo.close()

            with conn:
                for item in plan:
                    row = conn.execute(
                        "SELECT * FROM structured_series WHERE series_id=?", (item.old_series_id,)
                    ).fetchone()
                    if row is None:
                        raise RuntimeError(f"series disappeared during repair: {item.old_series_id}")
                    metric, adjustment, unit, dimensions = self._target(row)
                    new_id, identity_hash = self._identity(
                        row, metric_id=metric, adjustment=adjustment, unit=unit,
                        dimensions=dimensions)
                    duplicate = conn.execute(
                        "SELECT 1 FROM structured_observations WHERE series_id=? AND period IN "
                        "(SELECT period FROM structured_observations WHERE series_id=?) LIMIT 1",
                        (new_id, item.old_series_id),
                    ).fetchone()
                    if duplicate:
                        raise RuntimeError(
                            f"semantic repair would merge observation history into existing series: {new_id}")
                    conn.execute(
                        "INSERT OR IGNORE INTO structured_series VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (new_id, identity_hash, row["source_id"], row["dataset_id"],
                         row["entity_id"], metric, unit, row["currency"],
                         row["period_basis"], adjustment, _json(dimensions), row["created_at"]),
                    )
                    conn.execute("UPDATE structured_observations SET series_id=? WHERE series_id=?",
                                 (new_id, item.old_series_id))
                    conn.execute("DELETE FROM structured_series WHERE series_id=?", (item.old_series_id,))

                candidate_rows = conn.execute(
                    "SELECT candidate_id,entity_id,provider_field,metric_id,unit "
                    "FROM structured_candidates WHERE source_id=? AND dataset_id=?",
                    (_SOURCE, _DATASET),
                ).fetchall()
                candidates = 0
                for row in candidate_rows:
                    target = self._candidate_target(
                        row["entity_id"], row["provider_field"], row["metric_id"], row["unit"])
                    if target is None:
                        continue
                    provider_field, metric, unit = target
                    conn.execute(
                        "UPDATE structured_candidates SET provider_field=?,metric_id=?,unit=? "
                        "WHERE candidate_id=?",
                        (provider_field, metric, unit, row["candidate_id"]),
                    )
                    candidates += 1

                stale = conn.execute(
                    "DELETE FROM structured_conflicts WHERE dataset_id=? AND right_source_id=?",
                    (_DATASET, _SOURCE),
                ).rowcount
                conn.execute(
                    "UPDATE structured_observations SET quality_status='accepted',quality_json=? "
                    "WHERE series_id IN (SELECT series_id FROM structured_series WHERE source_id=? "
                    "AND dataset_id=?) AND quality_status='conflict'",
                    (_json({"reason_codes": ["semantic_reclassification"]}), _SOURCE, _DATASET),
                )
                recomputed = self._recompute_conflicts(conn)
                entities = sorted({item.entity_id for item in plan})
                placeholders = ",".join("?" for _ in entities) or "''"
                scope_conflicts = conn.execute(
                    "SELECT count(*) FROM structured_conflicts WHERE dataset_id=? AND status='open' "
                    f"AND entity_id IN ({placeholders})", (_DATASET, *entities),
                ).fetchone()[0]
                all_conflicts = conn.execute(
                    "SELECT count(*) FROM structured_conflicts WHERE dataset_id=? AND status='open'",
                    (_DATASET,),
                ).fetchone()[0]
                repair_id = uuid4().hex
                status = "reconciled" if scope_conflicts == 0 else "conflict_remaining"
                result = CompanyFinancialSemanticRepairResult(
                    repair_id=repair_id, database_path=str(self.path), dry_run=False,
                    backup_path=str(backup_path), status=status, series=plan,
                    candidates_reclassified=candidates, stale_conflicts_removed=stale,
                    conflicts_recomputed=recomputed, scope_open_conflicts=scope_conflicts,
                    unrelated_open_conflicts=all_conflicts - scope_conflicts,
                )
                conn.execute(
                    "INSERT INTO data_company_financial_semantic_repairs VALUES (?,?,?,?,?,?,?)",
                    (repair_id, _stamp(), str(self.path), 0, str(backup_path), status,
                     _json(result.model_dump())),
                )
            return result
        finally:
            conn.close()


__all__ = [
    "CompanyFinancialSemanticRepair",
    "CompanyFinancialSemanticRepairResult",
    "SeriesRepair",
]
