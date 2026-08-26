"""Structured repository foundation: additive storage, identity and point-in-time truth."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3

from ats.structured import (
    ArtifactDescriptor,
    MetricDefinition,
    ObservationInput,
    ProviderMapping,
    SeriesIdentity,
    SQLiteStructuredRepository,
    StructuredCatalog,
)


T0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _repo(tmp_path, name="structured.sqlite"):
    return SQLiteStructuredRepository(
        tmp_path / name, artifact_root=tmp_path / "artifacts")


def _observation(value=100.0, *, known_at=T0, published_at=None, raw=None):
    return ObservationInput(
        series=SeriesIdentity(
            source_id="sec_companyfacts", dataset_id="company_financials",
            entity_id="msft", metric_id="financial.revenue.gaap",
            unit="USD", currency="USD", period_basis="quarter", adjustment="gaap"),
        period="FY2026Q4", period_start="2026-04-01", period_end="2026-06-30",
        value=value, published_at=published_at or (known_at - timedelta(hours=1)),
        known_at=known_at, fetched_at=known_at,
        raw=raw or {"concept": "Revenue", "value": value},
    )


def test_empty_database_bootstraps_catalog_without_workflow_tables(tmp_path):
    repo = _repo(tmp_path)
    repo.bootstrap_catalog(StructuredCatalog.load())

    tables = {row[0] for row in repo.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "structured_observations" in tables
    assert "structured_artifacts" in tables
    assert "cycles" not in tables
    assert repo.conn.execute("SELECT count(*) FROM structured_sources").fetchone()[0] == 12
    assert repo.conn.execute("SELECT count(*) FROM structured_datasets").fetchone()[0] == 6
    assert repo.conn.execute("SELECT count(*) FROM structured_metrics").fetchone()[0] == 41


def test_repository_uses_separate_path_when_configured(monkeypatch, tmp_path):
    from ats.structured.repository import default_db_path

    legacy = tmp_path / "legacy.sqlite"
    structured = tmp_path / "only-structured.sqlite"
    monkeypatch.setenv("ATS_DB_PATH", str(legacy))
    monkeypatch.delenv("ATS_STRUCTURED_DB_PATH", raising=False)
    assert default_db_path() == str(legacy)  # default: colocated with the workflow DB

    monkeypatch.setenv("ATS_STRUCTURED_DB_PATH", str(structured))

    assert default_db_path() == str(structured)
    repo = SQLiteStructuredRepository(default_db_path(), artifact_root=tmp_path / "a")
    assert structured.exists()
    assert not legacy.exists()
    repo.close()


def test_content_addressed_artifact_is_deduplicated_and_reproducible(tmp_path):
    repo = _repo(tmp_path)
    descriptor = ArtifactDescriptor(
        source_id="sec_companyfacts", dataset_id="company_financials", fetched_at=T0,
        query_scope={"entity": "MSFT", "period": "FY2026Q4"},
        source_url="https://data.sec.gov/example", source_version="accession-1",
        media_type="application/json", retention="full_response")
    body = {"entity": "MSFT", "facts": [{"value": 100}]}

    first = repo.put_artifact(body, descriptor)
    second = repo.put_artifact(body, descriptor)

    assert first.id == second.id
    assert first.content_hash == second.content_hash
    assert repo.artifacts.read(first.relative_path) == repo.artifacts.encode(body)
    assert repo.conn.execute(
        "SELECT count(*) FROM structured_artifact_blobs").fetchone()[0] == 1
    assert repo.conn.execute("SELECT count(*) FROM structured_artifacts").fetchone()[0] == 1


def test_pointer_only_artifact_retains_query_and_source_metadata(tmp_path):
    repo = _repo(tmp_path)
    descriptor = ArtifactDescriptor(
        source_id="licensed", dataset_id="company_financials", fetched_at=T0,
        query_scope={"entity": "MSFT"}, pointer="provider://snapshot/123",
        source_version="123", storage_mode="pointer", retention="metadata_only")

    artifact = repo.put_artifact(None, descriptor)
    row = dict(repo.conn.execute(
        "SELECT * FROM structured_artifacts WHERE artifact_id=?", (artifact.id,)).fetchone())

    assert row["storage_mode"] == "pointer"
    assert row["pointer"] == "provider://snapshot/123"
    assert json.loads(row["query_scope_json"]) == {"entity": "MSFT"}


def test_metric_mapping_and_unknown_field_pending_pool_are_explicit(tmp_path):
    repo = _repo(tmp_path)
    repo.register_metric(MetricDefinition(
        id="financial.revenue.gaap", unit_family="currency", adjustment="gaap"))
    repo.register_mapping(ProviderMapping(
        provider="fixture", provider_field="Total Revenue",
        metric_id="financial.revenue.gaap"))

    assert repo.resolve_metric("fixture", "Total Revenue") == "financial.revenue.gaap"
    assert repo.resolve_metric("fixture", "Mystery Row") is None

    first = repo.record_pending_mapping(
        provider="fixture", dataset_id="company_financials",
        provider_field="Mystery Row", sample={"value": 12})
    second = repo.record_pending_mapping(
        provider="fixture", dataset_id="company_financials",
        provider_field="Mystery Row", sample={"value": 13})
    row = dict(repo.conn.execute(
        "SELECT * FROM structured_pending_mappings WHERE pending_id=?", (first,)).fetchone())
    assert first == second
    assert row["seen_count"] == 2
    assert json.loads(row["sample_payload"])["value"] == 13


def test_observation_is_idempotent_and_revision_appends_new_vintage(tmp_path):
    repo = _repo(tmp_path)

    first = repo.save_observation(_observation())
    repeated = repo.save_observation(_observation(
        known_at=T0 + timedelta(days=1), published_at=T0 - timedelta(hours=1)))
    revised = repo.save_observation(_observation(
        102.0, known_at=T0 + timedelta(days=2), raw={"concept": "Revenue", "value": 102}))

    assert first.created is True
    assert repeated.created is False and repeated.id == first.id
    assert revised.created is True and revised.id != first.id
    all_rows = repo.observations(latest_only=False)
    assert [row["value"] for row in all_rows] == [100.0, 102.0]
    assert repo.observations()[0]["value"] == 102.0


def test_strict_as_of_requires_both_publication_and_system_visibility(tmp_path):
    repo = _repo(tmp_path)
    original = _observation(
        100.0, known_at=T0,
        published_at=T0 - timedelta(hours=1), raw={"version": 1})
    revised = _observation(
        102.0, known_at=T0 + timedelta(days=5),
        published_at=T0 + timedelta(days=4), raw={"version": 2})
    repo.save_observation(original)
    repo.save_observation(revised)

    before_known = repo.observations(as_of=T0 - timedelta(hours=2))
    between = repo.observations(as_of=T0 + timedelta(days=1))
    after_revision = repo.observations(as_of=T0 + timedelta(days=6))

    assert before_known == []
    assert [row["value"] for row in between] == [100.0]
    assert [row["value"] for row in after_revision] == [102.0]


def test_reopen_preserves_vintages_and_is_idempotent(tmp_path):
    path = tmp_path / "restart.sqlite"
    repo = SQLiteStructuredRepository(path, artifact_root=tmp_path / "artifacts")
    saved = repo.save_observation(_observation())
    repo.close()

    reopened = SQLiteStructuredRepository(path, artifact_root=tmp_path / "artifacts")
    assert reopened.observations()[0]["observation_id"] == saved.id
    assert reopened.save_observation(_observation()).created is False
    assert reopened.conn.execute(
        "SELECT count(*) FROM structured_migrations").fetchone()[0] == 1


def test_old_measurements_remain_readable_and_audit_does_not_rewrite(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE measurement_series (
            series_id TEXT PRIMARY KEY, source_id TEXT, series TEXT, label TEXT,
            entity TEXT, unit TEXT, cadence TEXT, updated_at TEXT);
        CREATE TABLE measurement_points (
            point_id TEXT PRIMARY KEY, series_id TEXT, period TEXT, value REAL,
            unit TEXT, published_at TEXT, fetched_at TEXT, content_hash TEXT,
            raw_payload TEXT);
        INSERT INTO measurement_series VALUES
            ('tw:value','tw','value','Taiwan','TW_IC_EXPORT','USD M','monthly','2026-08-01');
        INSERT INTO measurement_points VALUES
            ('p1','tw:value','2026-06',100,'USD M','','2026-08-01T00:00:00+00:00','h','{}');
    """)
    conn.commit()
    conn.close()
    before = path.read_bytes()

    repo = SQLiteStructuredRepository(path, artifact_root=tmp_path / "artifacts")
    rows = repo.legacy_measurements(source_id="tw")
    audit = repo.audit_legacy_measurements(at=T0)

    assert [(row["period"], row["value"]) for row in rows] == [("2026-06", 100.0)]
    assert audit["series_count"] == 1 and audit["point_count"] == 1
    assert audit["missing_published_at"] == 1
    assert "remain unknown" in audit["note"]
    assert tuple(repo.conn.execute(
        "SELECT published_at,value FROM measurement_points").fetchone()) == ("", 100.0)
    assert path.read_bytes() != before  # only additive structured tables/audit were added


def test_concurrent_readers_see_committed_observation(tmp_path):
    path = tmp_path / "concurrent.sqlite"
    writer = SQLiteStructuredRepository(path, artifact_root=tmp_path / "artifacts")
    writer.save_observation(_observation())

    def read_value(_):
        reader = SQLiteStructuredRepository(path, artifact_root=tmp_path / "artifacts")
        try:
            return reader.observations()[0]["value"]
        finally:
            reader.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(read_value, range(12))) == [100.0] * 12
