"""Retirement is a first-class lifecycle state, not just deleted configuration.

These tests pin the three properties that make a retirement safe, using the real
checked-in catalog where possible and fixture catalogs where the scenario needs a
different disposition:

* a tombstoned id cannot silently come back to life (catalog + registration gate),
* a retired source disappears from every default view without touching live ones,
* stored data is only removed by an explicitly confirmed purge, never as a side
  effect of collection, publication, rollback or discovery.

The end-to-end purge against the production database lives in the change's
acceptance record; here everything runs on throwaway databases.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest
import yaml

from ats.data.adapters.structured.registry import validate_source_registration
from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import (
    RetirementDisposition, StructuredDataset, StructuredSource)
from ats.data.pipelines.structured.discovery import release_check
from ats.data.stores.structured.repository import SQLiteStructuredRepository


RETIRED_ID = "retired_probe"
LIVE_ID = "live_source"


def _catalog(tmp_path, *, retired_disposition: str = "purged",
             duplicate_in_both: bool = False) -> StructuredCatalog:
    """Build a fixture catalog holding one live source and one tombstone."""
    live = {
        "catalog_status": "current_partial", "persistence": "persistent",
        "provider": "Fixture Live", "adapter": "fixture_adapter",
        "datasets": ["live_dataset"], "cadence": "monthly",
        "retention": "query_slice",
        "internal_request_budget": {"concurrency": 1},
    }
    raw = {
        "version": 1,
        "feature_flags": {"default_mode": "legacy", "sources": {},
                          "consumers": {"test_consumer": "legacy"}},
        "sources": {LIVE_ID: live},
        "retired_sources": {
            RETIRED_ID: {
                "retired_at": "2026-09-01",
                "reason": "fixture retirement",
                "prior_catalog_status": "current_partial",
                "prior_rollout_mode": "legacy",
                "disposition": retired_disposition,
                "disposition_at": "2026-09-02",
                "successor": "",
                "spec_removed": True,
            }
        },
        "datasets": {
            "live_dataset": {
                "catalog_status": "current_partial", "entities": ["ACME"],
                "expected_cadence": "monthly", "primary_sources": [LIVE_ID],
                "core_metrics": ["metric.value"],
                "quality": {"percentage_range": [0, 100]},
                "acceptance_samples": ["ACME"],
            },
        },
        "metric_definitions": {
            "metric.value": {"value_type": "number", "unit_family": "percent",
                             "period_basis": "calendar_month"},
        },
        "provider_mappings": {LIVE_ID: {"value": "metric.value"}},
    }
    if duplicate_in_both:
        raw["sources"][RETIRED_ID] = dict(live, datasets=["retired_dataset"],
                                          adapter="fixture_adapter")
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return StructuredCatalog.load(path)


def _repository(tmp_path, catalog) -> SQLiteStructuredRepository:
    repository = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog(catalog)
    return repository


def _seed_source(repository, source_id: str, dataset_id: str, *,
                 payloads: dict[str, object], series: int = 2) -> dict:
    """Insert one source's series, observations, artifacts and a source check.

    ``payloads`` maps a local key to the blob payload.  Two seeds sharing the
    same payload end up sharing one content-addressed blob, which is exactly the
    deduplication case the purge must not over-delete.

    The registration mirror rows are written explicitly: in reality a source is
    registered while active and keeps its `structured_sources` row after the
    config entry is deleted, which is *why* a purge has something to clean up.
    """
    now = datetime(2026, 9, 10, tzinfo=timezone.utc).isoformat()
    repository.register_source(StructuredSource(
        id=source_id, provider="Fixture", adapter="fixture_adapter",
        datasets=[dataset_id], cadence="monthly", retention="query_slice"))
    repository.register_dataset(StructuredDataset(
        id=dataset_id, primary_sources=[source_id], core_metrics=["metric.value"],
        quality={"percentage_range": [0, 100]}, acceptance_samples=["ACME"]))
    artifacts = []
    for key, payload in payloads.items():
        blob = repository.artifacts.put(payload, suffix=".bin")
        repository.conn.execute(
            "INSERT OR IGNORE INTO structured_artifact_blobs"
            "(blob_id,content_hash,relative_path,bytes,created_at) VALUES (?,?,?,?,?)",
            (blob.blob_id, blob.content_hash, blob.relative_path, blob.bytes, now))
        artifact_id = f"{source_id}-{key}"
        repository.conn.execute(
            "INSERT INTO structured_artifacts(artifact_id,blob_id,source_id,dataset_id,"
            "fetched_at,query_scope_json,query_hash,source_url,source_version,media_type,"
            "retention,storage_mode,pointer,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (artifact_id, blob.blob_id, source_id, dataset_id, now, "{}", f"q-{key}",
             "", "v1", "application/json", "query_slice", "blob", "", "{}"))
        artifacts.append((artifact_id, blob, payload))
    repository.conn.execute(
        "INSERT INTO structured_source_checks(check_id,source_id,dataset_id,checked_at,status) "
        "VALUES (?,?,?,?,?)", (f"chk-{source_id}", source_id, dataset_id, now, "succeeded"))
    series_ids = []
    for index in range(series):
        series_id = f"{source_id}-s{index}"
        repository.conn.execute(
            "INSERT INTO structured_series(series_id,identity_hash,source_id,dataset_id,"
            "entity_id,metric_id,unit,currency,period_basis,adjustment,dimensions_json,"
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (series_id, f"{source_id}-h{index}", source_id, dataset_id, "ACME",
             "metric.value", "percent", "", "calendar_month", "", "{}", now))
        series_ids.append(series_id)
        repository.conn.execute(
            "INSERT INTO structured_observations(observation_id,series_id,period,period_start,"
            "period_end,event_time,value,published_at,known_at,fetched_at,artifact_id,"
            "content_hash,quality_status,quality_json,raw_payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{series_id}-o", series_id, "2026-08", "2026-08-01", "2026-08-31", now,
             10.0 + index, now, now, now, artifacts[0][0], artifacts[0][1].content_hash,
             "accepted", "{}", "{}"))
    repository.conn.execute(
        "INSERT INTO structured_ingestion_runs(run_id,source_id,dataset_id,query_scope_json,"
        "started_at,completed_at,status,reason_codes_json,note) VALUES (?,?,?,?,?,?,?,?,?)",
        (f"run-{source_id}", source_id, dataset_id, "{}", now, now, "succeeded", "[]", ""))
    repository.conn.commit()
    return {"artifacts": artifacts, "series_ids": series_ids}


def _counts(repository, source_id: str) -> dict:
    return repository.purge_plan(source_id)["row_counts"]


# --------------------------------------------------------------------------- 3.1


def test_catalog_rejects_source_present_in_both_registries(tmp_path):
    """The only other way a retired id can come back is a stale `sources` entry."""
    _catalog(tmp_path)  # a clean split loads fine

    with pytest.raises(ValueError) as exc:
        _catalog(tmp_path, duplicate_in_both=True)
    message = str(exc.value)
    assert RETIRED_ID in message
    assert "retired_sources" in message


def test_catalog_exposes_tombstones_separately_from_active_sources(tmp_path):
    catalog = _catalog(tmp_path)
    assert [source.id for source in catalog.sources()] == [LIVE_ID]
    tombstone = catalog.retired_source(RETIRED_ID)
    assert tombstone is not None
    assert tombstone.disposition == RetirementDisposition.PURGED
    assert tombstone.retired_at.isoformat() == "2026-09-01"
    assert catalog.retired_source(LIVE_ID) is None
    assert [row.id for row in catalog.retired_sources()] == [RETIRED_ID]


def test_checked_in_catalog_retires_ons_bics_ai():
    """The real catalog carries the tombstone and no active ONS entry."""
    catalog = StructuredCatalog.load()
    assert catalog.retired_source("ons_bics_ai") is not None
    ids = [source.id for source in catalog.sources()]
    assert "ons_bics_ai" not in ids
    assert "ai_enterprise_adoption_uk" not in [row.id for row in catalog.datasets()]
    assert not [metric for metric in catalog.metrics()
                if metric.id.startswith("ai.uk_enterprise_adoption")]


# --------------------------------------------------------------------------- 3.2


def test_validate_source_registration_fails_closed_on_retired_source(tmp_path):
    catalog = _catalog(tmp_path)
    result = validate_source_registration(RETIRED_ID, catalog=catalog)
    assert result["valid"] is False
    assert result["reason_codes"] == ["source_retired"]
    assert result["checks"] == [
        {"check": "source_retired", "passed": False, "reason": "source_retired"}]
    assert result["retired_at"] == "2026-09-01"
    assert result["reason"] == "fixture retirement"
    assert result["disposition"] == "purged"
    # A live source is unaffected by the tombstone sitting next to it.
    assert "source_retired" not in validate_source_registration(
        LIVE_ID, catalog=catalog)["reason_codes"]


def test_real_ons_source_reports_retired_not_unconfigured():
    """Without the tombstone branch this would read `source_not_configured`."""
    result = validate_source_registration("ons_bics_ai")
    assert result["valid"] is False
    assert result["reason_codes"] == ["source_retired"]
    assert result["disposition"] == "purged"
    assert result["retired_at"] == "2026-09-19"


# --------------------------------------------------------------------------- 3.3


def test_retired_source_is_absent_from_default_discovery_and_catalog_views(tmp_path):
    catalog = _catalog(tmp_path)
    repository = _repository(tmp_path, catalog)
    _seed_source(repository, RETIRED_ID, "retired_dataset", payloads={"a": {"n": 1}})
    checks_before = len(repository.source_checks(source_id=RETIRED_ID))

    # Default discovery enumerates the active registry, so the tombstoned id is
    # not even a candidate: no due-check, no artifact, no new vintage.
    result = release_check(repository, catalog=catalog, source_ids=[], force=False)
    attempted = [row["source_id"] for row in result["sources"]]
    assert attempted == [LIVE_ID]
    assert RETIRED_ID not in json.dumps(result)
    assert len(repository.source_checks(source_id=RETIRED_ID)) == checks_before

    # Catalog views enumerate the registration mirror.  The retired source still
    # has one (deleting config never deletes database rows), so this documents
    # the pre-purge state rather than a clean one.
    assert RETIRED_ID in [row["source_id"] for row in repository.sources()]


def test_retired_source_without_live_entry_is_skipped_by_group_discovery(tmp_path):
    """A group check for a retired source's former group returns nothing for it."""
    catalog = _catalog(tmp_path)
    repository = _repository(tmp_path, catalog)
    result = release_check(repository, group="retired_group", catalog=catalog)
    assert result["sources"] == []


# --------------------------------------------------------------------------- 4.1


def test_purge_dry_run_reports_without_writing(tmp_path):
    catalog = _catalog(tmp_path)
    repository = _repository(tmp_path, catalog)
    _seed_source(repository, RETIRED_ID, "retired_dataset", payloads={"a": {"n": 1}})
    before = _counts(repository, RETIRED_ID)

    plan = repository.purge_source(RETIRED_ID, catalog=catalog)
    assert plan["mode"] == "dry_run"
    assert plan["tombstoned"] is True
    assert plan["row_counts"]["observations"] == 2
    assert plan["row_counts"]["series"] == 2
    assert plan["row_counts"]["artifacts"] == 1
    assert plan["row_counts"]["source_checks"] == 1
    assert plan["blobs"] == 1 and plan["bytes_freed"] > 0

    assert _counts(repository, RETIRED_ID) == before
    assert repository.purge_records(source_id=RETIRED_ID) == []


def test_purge_confirmed_removes_only_the_target_source(tmp_path):
    catalog = _catalog(tmp_path)
    repository = _repository(tmp_path, catalog)
    _seed_source(repository, RETIRED_ID, "retired_dataset", payloads={"a": {"n": 1}})
    _seed_source(repository, LIVE_ID, "live_dataset", payloads={"b": {"n": 2}})
    live_before = _counts(repository, LIVE_ID)

    result = repository.purge_source(RETIRED_ID, confirm=True, catalog=catalog,
                                     actor="pytest")
    assert result["mode"] == "purged"
    assert result["deleted"]["observations"] == 2
    assert result["deleted"]["series"] == 2
    assert result["deleted"]["artifacts"] == 1
    assert result["deleted"]["source_checks"] == 1
    assert result["deleted"]["registered_sources"] == 1
    assert result["deleted"]["registered_datasets"] == 1

    after = _counts(repository, RETIRED_ID)
    assert after["observations"] == 0 and after["series"] == 0
    assert after["artifacts"] == 0 and after["source_checks"] == 0
    assert repository.source(RETIRED_ID) is None
    assert repository.dataset("retired_dataset") is None
    # The live source is untouched, including its shared deduplication surface.
    assert _counts(repository, LIVE_ID) == live_before


# --------------------------------------------------------------------------- 4.2


def test_purge_keeps_a_blob_another_source_still_references(tmp_path):
    """Shared content identity survives, even though the file is the same bytes."""
    catalog = _catalog(tmp_path)
    repository = _repository(tmp_path, catalog)
    shared = {"same": "bytes"}
    _seed_source(repository, RETIRED_ID, "retired_dataset",
                 payloads={"shared": shared, "own": {"only": RETIRED_ID}})
    _seed_source(repository, LIVE_ID, "live_dataset", payloads={"shared": shared})

    plan = repository.purge_source(RETIRED_ID, catalog=catalog)
    assert plan["blobs_total"] == 2
    assert plan["blobs"] == 1, "a blob referenced by another source must not be scheduled"

    repository.purge_source(RETIRED_ID, confirm=True, catalog=catalog, actor="pytest")
    remaining = [row["blob_id"] for row in repository.artifacts_for(source_id=LIVE_ID)]
    assert remaining, "the live source keeps its artifact row"
    blob_id = remaining[0]
    blob_row = repository.conn.execute(
        "SELECT blob_id, relative_path FROM structured_artifact_blobs WHERE blob_id=?",
        (blob_id,)).fetchone()
    assert blob_row is not None, "the shared blob row must be preserved"
    assert (repository.artifacts.root / blob_row["relative_path"]).exists(), \
        "the shared blob file must still be on disk"


def test_purge_removes_files_for_exclusive_blobs(tmp_path):
    catalog = _catalog(tmp_path)
    repository = _repository(tmp_path, catalog)
    seeded = _seed_source(repository, RETIRED_ID, "retired_dataset",
                          payloads={"own": {"only": RETIRED_ID}})
    _, blob, _ = seeded["artifacts"][0]
    target = repository.artifacts.root / blob.relative_path
    assert target.exists()

    result = repository.purge_source(RETIRED_ID, confirm=True, catalog=catalog,
                                     actor="pytest")
    assert result["blob_files_removed"] == 1
    assert not target.exists()
    assert repository.conn.execute(
        "SELECT COUNT(*) FROM structured_artifact_blobs WHERE blob_id=?",
        (blob.blob_id,)).fetchone()[0] == 0


def test_purge_leaves_ingestion_history_as_recorded_residue(tmp_path):
    """The run ledger is operational history, so it stays and is reported."""
    catalog = _catalog(tmp_path)
    repository = _repository(tmp_path, catalog)
    _seed_source(repository, RETIRED_ID, "retired_dataset", payloads={"a": {"n": 1}})

    result = repository.purge_source(RETIRED_ID, confirm=True, catalog=catalog,
                                     actor="pytest")
    assert result["residual_tables"] == {"structured_ingestion_runs": 1}
    assert repository.conn.execute(
        "SELECT COUNT(*) FROM structured_ingestion_runs WHERE source_id=?",
        (RETIRED_ID,)).fetchone()[0] == 1


# --------------------------------------------------------------------------- 4.3


def test_purge_refuses_source_without_retirement_tombstone(tmp_path):
    catalog = _catalog(tmp_path)
    repository = _repository(tmp_path, catalog)
    _seed_source(repository, LIVE_ID, "live_dataset", payloads={"b": {"n": 2}})
    before = _counts(repository, LIVE_ID)

    with pytest.raises(ValueError) as exc:
        repository.purge_source(LIVE_ID, confirm=True, catalog=catalog, actor="pytest")
    assert "tombstone" in str(exc.value)
    assert _counts(repository, LIVE_ID) == before
    assert repository.purge_records(source_id=LIVE_ID) == []


# --------------------------------------------------------------------------- 4.4


def test_purge_writes_a_queryable_audit_record(tmp_path):
    catalog = _catalog(tmp_path)
    repository = _repository(tmp_path, catalog)
    _seed_source(repository, RETIRED_ID, "retired_dataset", payloads={"a": {"n": 1}})

    result = repository.purge_source(RETIRED_ID, confirm=True, catalog=catalog,
                                     actor="pytest", exported=False,
                                     note="acceptance run")
    records = repository.purge_records(source_id=RETIRED_ID)
    assert len(records) == 1
    record = records[0]
    assert record["purge_id"] == result["purge_id"]
    assert record["source_id"] == RETIRED_ID
    assert record["dataset_ids"] == ["retired_dataset"]
    assert record["actor"] == "pytest"
    assert record["observations"] == 2 and record["series"] == 2
    assert record["artifacts"] == 1 and record["source_checks"] == 1
    assert record["blobs"] == 1
    assert record["freed_bytes"] == result["bytes_freed"] > 0
    assert record["sources"] == 1 and record["datasets"] == 1
    assert record["exported"] is False
    assert record["residuals"] == {"structured_ingestion_runs": 1}
    assert record["note"] == "acceptance run"
    assert record["purged_at"]


def test_purge_records_are_filtered_by_source(tmp_path):
    catalog = _catalog(tmp_path)
    repository = _repository(tmp_path, catalog)
    _seed_source(repository, RETIRED_ID, "retired_dataset", payloads={"a": {"n": 1}})
    repository.purge_source(RETIRED_ID, confirm=True, catalog=catalog, actor="pytest")
    assert repository.purge_records(source_id=LIVE_ID) == []
    assert len(repository.purge_records()) == 1


# --------------------------------------------------- routine paths never purge


def test_routine_lifecycle_paths_never_delete_retired_data(tmp_path):
    """Collection and discovery must skip or refuse -- never clean up."""
    catalog = _catalog(tmp_path)
    repository = _repository(tmp_path, catalog)
    _seed_source(repository, RETIRED_ID, "retired_dataset", payloads={"a": {"n": 1}})
    before = _counts(repository, RETIRED_ID)

    # Discovery skips it.  An explicit collection attempt is refused by the
    # registration gate, and an explicitly named retired source is reported as a
    # source-local failure rather than being silently cleaned up.
    release_check(repository, catalog=catalog)
    from ats.data.adapters.structured.registry import build_ingestion

    with pytest.raises(ValueError, match="source_retired"):
        build_ingestion(RETIRED_ID, catalog=catalog)
    named = release_check(repository, source_ids=[RETIRED_ID], catalog=catalog)
    assert [row["status"] for row in named["sources"]] == ["validation_failed"]
    assert "source_retired" in json.dumps(named)

    assert _counts(repository, RETIRED_ID) == before
    assert repository.purge_records() == []


# ------------------------------------------------------- retired data is invisible


def test_tombstone_explains_retained_orphan_rows(tmp_path):
    """A `retained_orphan` disposition is how leftover rows stay explainable."""
    catalog = _catalog(tmp_path, retired_disposition="retained_orphan")
    repository = _repository(tmp_path, catalog)
    _seed_source(repository, RETIRED_ID, "retired_dataset", payloads={"a": {"n": 1}})
    before = _counts(repository, RETIRED_ID)
    checks_before = len(repository.source_checks(source_id=RETIRED_ID))

    # The dry run is the only thing an operator gets for a retained orphan: it
    # explains what exists, but the disposition says not to remove it.
    result = repository.purge_source(RETIRED_ID, catalog=catalog)
    assert result["mode"] == "dry_run"
    assert result["disposition"] == "retained_orphan"
    assert _counts(repository, RETIRED_ID) == before

    # Even when the data is deliberately kept, the id stays retired: it produces
    # no new discovery or vintage and cannot be re-registered.
    assert RETIRED_ID not in [row["source_id"]
                              for row in release_check(repository, catalog=catalog)["sources"]]
    assert len(repository.source_checks(source_id=RETIRED_ID)) == checks_before
    assert validate_source_registration(RETIRED_ID, catalog=catalog)["valid"] is False
    # The mirror row is exactly the residue the tombstone exists to explain.
    assert RETIRED_ID in [row["source_id"] for row in repository.sources()]


def test_ai_adoption_bundle_never_references_a_retired_source():
    """The bundle is the Observer's only governed input, so it is the last gate."""
    from ats.data.products.ai_adoption_bundle import build

    class _Structured:
        @staticmethod
        def observation(observation_id):  # pragma: no cover - no lineage in stub
            return None

    class _Products:
        structured = _Structured()

        @staticmethod
        def census_btos_ai_snapshot(**_):
            return {"source_id": "us_census_btos", "period": "2026-05", "current_use": None,
                    "history": [], "rows": [], "methodology_regime": "v2", "freshness": {}}

        @staticmethod
        def rps_genai_adoption_snapshot(**_):
            return {"source_id": "rps_genai_adoption", "period": "2026-Q2", "latest": {},
                    "history": {"last_week": []}, "derivations": {}, "freshness": {}}

        @staticmethod
        def ai_production_penetration(**_):
            return {"source_id": "anthropic_economic_index", "period_rows": [],
                    "latest_period": None}

    bundle = build(_Products())
    retired_ids = {row.id for row in StructuredCatalog.load().retired_sources()}
    assert retired_ids, "the checked-in catalog must still carry the ONS tombstone"
    serialized = json.dumps(bundle, default=str)
    for retired_id in retired_ids:
        assert retired_id not in serialized
    # Three axes survive a retirement: no empty axis, no placeholder row.
    assert set(bundle["axes"]) == {"enterprise_breadth", "worker_persistence",
                                   "task_production"}
