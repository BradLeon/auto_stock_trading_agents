"""Unified structured adapter, admission, failure isolation and source selection."""

from datetime import datetime, timedelta, timezone
import json

from ats.structured import (
    AdapterArtifact,
    AdapterBatch,
    AdapterFailure,
    FetchRequest,
    IngestionPipeline,
    IngestionStatus,
    NativeRecord,
    ProviderMapping,
    SQLiteStructuredRepository,
    SourceSelector,
    StructuredCatalog,
)


NOW = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


class FakeAdapter:
    def __init__(self, batch=None, error=None):
        self.batch = batch
        self.error = error
        self.requests = []

    def fetch(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.batch


def _repo(tmp_path):
    repo = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    for source in ("sec_companyfacts", "defeatbeta_stock_statement"):
        repo.register_mapping(ProviderMapping(
            provider=source, provider_field="Revenue",
            metric_id="financial.revenue.gaap"))
    return repo


def _request(source="sec_companyfacts", entity="MSFT"):
    return FetchRequest(
        source_id=source, dataset_id="company_financials", entities=[entity],
        periods=["FY2026Q4"], query_scope={"entity": entity, "period": "FY2026Q4"})


def _record(**changes):
    values = dict(
        entity_id="MSFT", provider_field="Revenue", period="FY2026Q4",
        value=100.0, unit="USD", currency="USD", period_basis="quarter",
        adjustment="gaap", period_start="2026-04-01", period_end="2026-06-30",
        published_at=NOW - timedelta(hours=2), raw={"concept": "Revenue", "value": 100})
    values.update(changes)
    return NativeRecord(**values)


def _batch(source="sec_companyfacts", *, records=None, status=IngestionStatus.SUCCEEDED,
           failures=None, version="v1"):
    return AdapterBatch(
        source_id=source, dataset_id="company_financials", status=status,
        fetched_at=NOW, records=records if records is not None else [_record()],
        artifacts=[AdapterArtifact(
            payload={"records": [r.model_dump(mode="json") for r in (records or [_record()])]},
            query_scope={"entity": "MSFT", "period": "FY2026Q4"},
            source_url="https://example.test/source", source_version=version)],
        failures=failures or [], provider_metadata={"source_version": version})


def test_adapter_batch_is_raw_and_pipeline_owns_persistence(tmp_path):
    repo = _repo(tmp_path)
    adapter = FakeAdapter(_batch())

    result = IngestionPipeline(repo).run(adapter, _request())

    assert result["status"] == "succeeded" and result["accepted"] == 1
    assert len(adapter.requests) == 1
    observations = repo.observations(dataset_id="company_financials")
    assert [(row["entity_id"], row["metric_id"], row["value"])
            for row in observations] == [("MSFT", "financial.revenue.gaap", 100.0)]
    candidate = repo.candidates()[0]
    assert candidate["status"] == "accepted"
    assert candidate["artifact_id"] == observations[0]["artifact_id"]
    artifact = repo.conn.execute(
        "SELECT * FROM structured_artifacts WHERE artifact_id=?",
        (observations[0]["artifact_id"],)).fetchone()
    assert artifact is not None and artifact["source_version"] == "v1"


def test_central_admission_quarantines_all_reason_codes_and_keeps_raw(tmp_path):
    repo = _repo(tmp_path)
    bad = _record(entity_id="AMZN", provider_field="Mystery", period="", value="NaN?",
                  unit="", currency="", published_at=NOW + timedelta(days=1),
                  raw={"provider_row": "kept"})

    result = IngestionPipeline(repo).run(
        FakeAdapter(_batch(records=[bad])), _request(entity="MSFT"))

    assert result["status"] == "validation_failed"
    assert result["accepted"] == 0 and result["quarantined"] == 1
    candidate = repo.candidates(status="quarantined")[0]
    reasons = set(json.loads(candidate["reason_codes_json"]))
    assert reasons == {
        "entity_mismatch", "period_unresolved", "value_not_numeric",
        "metric_unmapped", "unit_unresolved", "published_after_fetch",
    }
    assert json.loads(candidate["raw_payload"]) == {"provider_row": "kept"}
    pending = repo.conn.execute("SELECT * FROM structured_pending_mappings").fetchone()
    assert pending["provider_field"] == "Mystery"
    assert repo.observations() == []


def test_currency_is_required_for_currency_metric(tmp_path):
    repo = _repo(tmp_path)
    result = IngestionPipeline(repo).run(
        FakeAdapter(_batch(records=[_record(currency="")])), _request())

    assert result["status"] == "validation_failed"
    assert json.loads(repo.candidates()[0]["reason_codes_json"]) == ["currency_unresolved"]


def test_explicit_provider_terminal_states_do_not_become_zero_or_success(tmp_path):
    repo = _repo(tmp_path)
    pipeline = IngestionPipeline(repo)

    for status in (IngestionStatus.ZERO_MATCH, IngestionStatus.NOT_YET_PUBLISHED,
                   IngestionStatus.NO_COVERAGE, IngestionStatus.STALE):
        batch = AdapterBatch(
            source_id="sec_companyfacts", dataset_id="company_financials",
            status=status, fetched_at=NOW)
        result = pipeline.run(FakeAdapter(batch), _request())
        assert result["status"] == status.value

    assert [row["status"] for row in reversed(repo.ingestion_history())] == [
        "zero_match", "not_yet_published", "no_coverage", "stale"]


def test_adapter_exceptions_have_distinct_statuses(tmp_path):
    repo = _repo(tmp_path)
    pipeline = IngestionPipeline(repo)

    unauthorized = pipeline.run(FakeAdapter(error=PermissionError("token")), _request())
    unreachable = pipeline.run(FakeAdapter(error=TimeoutError("timeout")), _request())
    parse = pipeline.run(FakeAdapter(error=ValueError("schema changed")), _request())

    assert [unauthorized["status"], unreachable["status"], parse["status"]] == [
        "unauthorized", "unreachable", "parse_failed"]


def test_single_entity_failure_is_partial_and_does_not_drop_good_slice(tmp_path):
    repo = _repo(tmp_path)
    batch = _batch(
        records=[_record()], status=IngestionStatus.PARTIAL,
        failures=[AdapterFailure(
            status=IngestionStatus.NO_COVERAGE, entity_id="KLAC",
            slice_key="FY2026Q4", message="mirror has no KLAC")])

    result = IngestionPipeline(repo).run(FakeAdapter(batch), _request())

    assert result["status"] == "partial" and result["accepted"] == 1
    run = repo.ingestion_history()[0]
    assert json.loads(run["reason_codes_json"]) == {"no_coverage": 1}
    assert repo.observations()[0]["entity_id"] == "MSFT"


def test_run_many_isolates_sources_and_continues_after_failure(tmp_path):
    repo = _repo(tmp_path)
    jobs = [
        (FakeAdapter(error=TimeoutError("SEC down")), _request()),
        (FakeAdapter(_batch(source="defeatbeta_stock_statement")),
         _request(source="defeatbeta_stock_statement")),
    ]

    results = IngestionPipeline(repo).run_many(jobs)

    assert [row["status"] for row in results] == ["unreachable", "succeeded"]
    assert repo.observations()[0]["source_id"] == "defeatbeta_stock_statement"


def test_duplicate_is_no_change_and_revision_appends(tmp_path):
    repo = _repo(tmp_path)
    pipeline = IngestionPipeline(repo)

    first = pipeline.run(FakeAdapter(_batch(version="v1")), _request())
    repeat = pipeline.run(FakeAdapter(_batch(version="v1")), _request())
    revised_record = _record(value=102.0, raw={"concept": "Revenue", "value": 102})
    revised = pipeline.run(
        FakeAdapter(_batch(records=[revised_record], version="v2")), _request())

    assert [first["status"], repeat["status"], revised["status"]] == [
        "succeeded", "no_change", "succeeded"]
    assert [row["value"] for row in repo.observations(latest_only=False)] == [100.0, 102.0]


def test_parallel_sources_are_preserved_conflict_and_official_is_selected(tmp_path):
    repo = _repo(tmp_path)
    pipeline = IngestionPipeline(repo)
    pipeline.run(FakeAdapter(_batch()), _request())
    mirror_record = _record(value=99.0, raw={"concept": "Revenue", "value": 99})
    pipeline.run(
        FakeAdapter(_batch(source="defeatbeta_stock_statement", records=[mirror_record])),
        _request(source="defeatbeta_stock_statement"))

    rows = repo.observations(dataset_id="company_financials")
    assert {row["source_id"]: row["value"] for row in rows} == {
        "sec_companyfacts": 100.0, "defeatbeta_stock_statement": 99.0}
    assert {row["quality_status"] for row in rows} == {"accepted", "conflict"}
    assert repo.conn.execute("SELECT count(*) FROM structured_conflicts").fetchone()[0] == 1

    selected = SourceSelector(repo).select("company_financials", rows)
    assert selected.selected_source == "sec_companyfacts"
    assert selected.selection_reason == "primary_source_available"
    assert selected.conflict is True
    assert selected.alternatives[0]["source_id"] == "defeatbeta_stock_statement"


def test_fallback_reason_is_explicit_when_official_source_missing(tmp_path):
    repo = _repo(tmp_path)
    pipeline = IngestionPipeline(repo)
    mirror = FakeAdapter(_batch(source="defeatbeta_stock_statement"))
    pipeline.run(mirror, _request(source="defeatbeta_stock_statement"))

    selected = SourceSelector(repo).select("company_financials", repo.observations())

    assert selected.selected_source == "defeatbeta_stock_statement"
    assert selected.selection_reason == "fallback_source_used"
    assert selected.alternatives == []
