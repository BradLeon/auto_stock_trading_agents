from __future__ import annotations

from datetime import datetime, timezone

from ats.data.migration import CompanyFinancialSemanticRepair
from ats.data.structured import (
    ArtifactDescriptor,
    ObservationInput,
    SeriesIdentity,
    SQLiteStructuredRepository,
)


NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


def _save(repo, *, source: str, entity: str, metric: str, value: float,
          unit: str, adjustment: str, basis: str = "quarter") -> str:
    artifact = repo.put_artifact(
        {"entity": entity, "metric": metric, "value": value},
        ArtifactDescriptor(
            source_id=source, dataset_id="company_financials", fetched_at=NOW,
            query_scope={"entity": entity, "metric": metric}),
    )
    return repo.save_observation(ObservationInput(
        series=SeriesIdentity(
            source_id=source, dataset_id="company_financials", entity_id=entity,
            metric_id=metric, unit=unit, currency=unit.split("/")[0],
            period_basis=basis, adjustment=adjustment,
            dimensions={"statement_scope": "reported"}),
        period="2026-06-30", period_end="2026-06-30", value=value,
        known_at=NOW, fetched_at=NOW, artifact_id=artifact.id,
    )).id


def test_semantic_repair_reclassifies_legacy_mirror_rows_with_backup_and_is_idempotent(tmp_path):
    db = tmp_path / "data.sqlite"
    repo = SQLiteStructuredRepository(db, artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog()
    klac_eps = _save(
        repo, source="defeatbeta_stock_statement", entity="KLAC",
        metric="financial.eps.diluted.gaap", value=0.82, unit="USD/share",
        adjustment="gaap")
    _save(
        repo, source="sec_companyfacts", entity="KLAC",
        metric="financial.eps.diluted.gaap", value=8.16, unit="USD/share",
        adjustment="gaap")
    _save(
        repo, source="defeatbeta_stock_statement", entity="TSM",
        metric="financial.eps.diluted.gaap", value=136.23, unit="TWD/share",
        adjustment="gaap")
    _save(
        repo, source="defeatbeta_stock_statement", entity="TSM",
        metric="financial.total_debt.gaap", value=1_000, unit="TWD",
        adjustment="gaap", basis="instant")
    repo.record_conflict(
        dataset_id="company_financials", entity_id="KLAC",
        metric_id="financial.eps.diluted.gaap", period="2026-06-30",
        left_observation_id=klac_eps, right_source_id="defeatbeta_stock_statement",
        right_value=0.82, absolute_difference=7.34, relative_difference=0.9, at=NOW)
    repo.save_candidate(
        candidate_id="old-tsm-eps", run_id="run", source_id="defeatbeta_stock_statement",
        dataset_id="company_financials", entity_id="TSM", provider_field="diluted_eps",
        metric_id="financial.eps.diluted.gaap", period="2026-06-30", value=136.23,
        unit="TWD/share", currency="TWD", status="accepted", reason_codes=[],
        artifact_id="", raw={"item_name": "diluted_eps"}, at=NOW)
    repo.close()

    repair = CompanyFinancialSemanticRepair(db)
    preview = repair.run()
    assert preview.dry_run and len(preview.series) == 3
    assert not (tmp_path / "backups").exists()

    result = repair.run(backup_root=tmp_path / "backups", dry_run=False)
    assert result.reconciled is True
    assert result.scope_open_conflicts == 0
    assert result.unrelated_open_conflicts == 0
    assert result.stale_conflicts_removed == 1
    assert (tmp_path / "backups").exists()

    checked = SQLiteStructuredRepository(db, artifact_root=tmp_path / "artifacts")
    rows = checked.observations(dataset_id="company_financials", latest_only=False)
    mirror = [row for row in rows if row["source_id"] == "defeatbeta_stock_statement"]
    assert {row["metric_id"] for row in mirror} == {
        "financial.eps.diluted.market_adjusted",
        "financial.eps.diluted.adr",
        "financial.total_debt.provider_reported",
    }
    tsm_eps = next(row for row in mirror if row["entity_id"] == "TSM" and
                   row["metric_id"] == "financial.eps.diluted.adr")
    assert (tsm_eps["unit"], tsm_eps["adjustment"]) == ("TWD/ADR", "provider_reported")
    candidate = checked.conn.execute(
        "SELECT provider_field,metric_id,unit FROM structured_candidates "
        "WHERE candidate_id='old-tsm-eps'").fetchone()
    assert tuple(candidate) == ("tsm_diluted_eps_adr_twd", "financial.eps.diluted.adr", "TWD/ADR")
    assert checked.conn.execute("SELECT count(*) FROM structured_conflicts").fetchone()[0] == 0
    checked.close()

    retry = repair.run(backup_root=tmp_path / "backups", dry_run=False)
    assert retry.reconciled is True
    assert retry.series == ()
