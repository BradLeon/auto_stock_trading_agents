from datetime import datetime, timezone

from ats.data.catalog.loader import DataCatalog
from ats.data.pipelines.unstructured import document_ingest as fixed_sources
from ats.data.sec import SecFetchResult
from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository
from ats.data.runtime.repository import platform_data_db_path


NOW = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("ATS_DOCS_ROOT", str(tmp_path / "documents"))
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "test research@example.com")
    return PlatformUnstructuredRepository(tmp_path / "fixed.sqlite", writable=True)


def test_fixed_sources_are_registered_but_not_article_adapters():
    from ats.data.pipelines.unstructured.article_ingest import _article_sources

    catalog = DataCatalog.load()
    target = {source.id for source in catalog.target_unstructured_sources()}
    assert fixed_sources.DOCUMENT_SOURCE_IDS <= target
    assert not fixed_sources.DOCUMENT_SOURCE_IDS.intersection(_article_sources())
    assert catalog.validate().valid


def test_fixed_universe_budget_covers_current_scope_and_fails_closed_on_growth(
        tmp_path, monkeypatch):
    sources = fixed_sources._rows()
    symbols = fixed_sources._universe("", excluded_entities=("005930.KS",))
    assert len(symbols) == len(set(symbols))
    assert "005930.KS" not in symbols
    for source_id in ("defeatbeta_sec_filing_index", "defeatbeta_earnings_transcript"):
        budget = sources[source_id]["request_budget"]
        assert len(symbols) * budget["max_rows_per_symbol"] <= budget["max_rows_per_run"]

    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(fixed_sources, "_universe", lambda _entity, **_kw: [f"SYM{i}" for i in range(41)])
    monkeypatch.setattr(fixed_sources, "_hf_snapshot", lambda _policy: (_ for _ in ()).throw(
        AssertionError("provider must not be queried for silently truncated scope")))
    try:
        try:
            fixed_sources.ingest_documents(
                "defeatbeta_sec_filing_index", now=NOW, store=store)
        except ValueError as exc:
            assert str(exc) == "fixed_universe_exceeds_row_budget"
        else:
            raise AssertionError("oversized universe was silently truncated")
    finally:
        store.close()


def test_fixed_source_requires_worker_before_provider_access(tmp_path, monkeypatch):
    monkeypatch.setenv("ATS_PERSISTENT_QUEUE_PATH", str(tmp_path / "queue.sqlite"))
    monkeypatch.setattr(fixed_sources, "_hf_snapshot", lambda _policy: (_ for _ in ()).throw(
        AssertionError("provider called without lease")))
    store = PlatformUnstructuredRepository(platform_data_db_path(), writable=True)
    try:
        fixed_sources.ingest_documents("defeatbeta_sec_filing_index", store=store)
    except PermissionError as exc:
        assert "managed-queue lease" in str(exc)
    else:
        raise AssertionError("fixed provider called outside the managed queue")
    finally:
        store.close()


def test_curated_corpus_publishes_only_registered_manifest_members(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    try:
        result = fixed_sources.ingest_documents(
            "ai_hardware_knowledge_corpus", now=NOW, store=store)
        assert result["status"] == "succeeded"
        assert result["discovered"] == 10
        assert result["accepted"] == 10
        assert len(result["published_version_ids"]) == 10
        again = fixed_sources.ingest_documents(
            "ai_hardware_knowledge_corpus", now=NOW, store=store)
        assert again["status"] == "no_change"
        assert again["published_version_ids"] == []
    finally:
        store.close()


def test_sec_index_does_not_publish_third_party_row_as_official_body(
        tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    snapshot = {"revision": "a" * 40, "spec_sha256": "b" * 64,
                "file_sha256": "c" * 64, "file_updated_at": NOW.isoformat(),
                "source_url": "https://huggingface.co/datasets/defeatbeta/yahoo-finance-data/resolve/"
                              + "a" * 40 + "/data/US/stock_sec_filing.parquet"}
    monkeypatch.setattr(fixed_sources, "_hf_snapshot", lambda policy: snapshot)
    monkeypatch.setattr(fixed_sources, "_query_hf", lambda *a, **k: [{
        "symbol": "NVDA", "cik": "1045810", "accession_number": "0001045810-26-000123",
        "company_name": "NVIDIA Corporation", "form_type": "10-Q",
        "filing_date": "2026-09-20", "report_date": "2026-07-31",
        "acceptance_date_time": "2026-09-20T12:00:00Z",
        "filing_url": "https://www.sec.gov/Archives/edgar/data/1045810/"
                      "000104581026000123/nvda-20260731.htm"}])
    try:
        result = fixed_sources.ingest_documents(
            "defeatbeta_sec_filing_index", entity="NVDA", now=NOW, store=store)
        assert result["status"] == "succeeded"
        assert result["accepted"] == 1
        assert result["published_version_ids"] == []
        assert store.conn.execute("SELECT COUNT(*) FROM data_documents").fetchone()[0] == 0
        row = store.conn.execute(
            "SELECT revision,file_sha256 FROM data_fixed_source_snapshots").fetchone()
        assert row["revision"] == "a" * 40 and row["file_sha256"] == "c" * 64
    finally:
        store.close()


def test_fixed_source_reports_missing_entity_instead_of_claiming_full_coverage(
        tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(fixed_sources, "_universe", lambda _entity, **_kw: ["NVDA", "005930.KS"])
    monkeypatch.setattr(fixed_sources, "_hf_snapshot", lambda _policy: {
        "revision": "a" * 40, "spec_sha256": "b" * 64,
        "file_sha256": "c" * 64, "file_updated_at": NOW.isoformat(),
        "source_url": "https://huggingface.co/example.parquet"})
    monkeypatch.setattr(fixed_sources, "_query_hf", lambda *_a, **_k: [{
        "symbol": "NVDA", "cik": "1045810",
        "accession_number": "0001045810-26-000123", "form_type": "10-Q",
        "filing_url": "https://www.sec.gov/Archives/edgar/data/1045810/"
                      "000104581026000123/nvda.htm"}])
    try:
        result = fixed_sources.ingest_documents(
            "defeatbeta_sec_filing_index", now=NOW, store=store)
        assert result["status"] == "partial"
        assert result["coverage_missing_entities"] == ["005930.KS"]
        run = store.conn.execute(
            "SELECT reason_codes FROM data_ingestion_runs WHERE run_id=?",
            (result["run_id"],)).fetchone()
        assert "coverage_missing_entities" in run["reason_codes"]
    finally:
        store.close()


def test_invalid_official_url_is_quarantined_before_fetch(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(fixed_sources, "_hf_snapshot", lambda policy: {
        "revision": "a" * 40, "spec_sha256": "b" * 64,
        "file_sha256": "c" * 64, "file_updated_at": NOW.isoformat(),
        "source_url": "https://huggingface.co/example.parquet"})
    monkeypatch.setattr(fixed_sources, "_query_hf", lambda *a, **k: [{
        "symbol": "NVDA", "cik": "1045810", "accession_number": "0001045810-26-000123",
        "form_type": "10-Q", "filing_url": "https://www.evil.test/filing.htm"}])
    monkeypatch.setattr(fixed_sources, "fetch_filing", lambda *_args: (_ for _ in ()).throw(
        AssertionError("untrusted URL fetched")))
    try:
        result = fixed_sources.ingest_documents(
            "defeatbeta_sec_filing_index", entity="NVDA", now=NOW, store=store)
        assert result["status"] == "quarantined"
        assert result["published_version_ids"] == []
    finally:
        store.close()


def test_transcript_period_and_speaker_segments_are_required():
    row = {"symbol": "NVDA", "fiscal_year": 2026, "fiscal_quarter": 2,
           "transcripts": [{"paragraph_number": 1, "speaker": "CEO", "content": "Revenue grew."}]}
    assert fixed_sources._transcript_row(row) == (
        "NVDA", "Q2 FY2026", "## Prepared Remarks\n\n**CEO:** Revenue grew.")
    row["transcripts"][0]["speaker"] = ""
    try:
        fixed_sources._transcript_row(row)
    except ValueError as exc:
        assert "segment_invalid" in str(exc)
    else:
        raise AssertionError("speakerless transcript accepted")


def test_pinned_transcript_and_official_filing_publish_as_distinct_documents(
        tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    snapshot = {"revision": "a" * 40, "spec_sha256": "b" * 64,
                "file_sha256": "c" * 64, "file_updated_at": NOW.isoformat(),
                "source_url": "https://huggingface.co/datasets/defeatbeta/yahoo-finance-data/"
                              "resolve/" + "a" * 40 + "/data/US/file.parquet"}
    monkeypatch.setattr(fixed_sources, "_hf_snapshot", lambda _policy: snapshot)
    filing = {"symbol": "NVDA", "cik": "1045810",
              "accession_number": "0001045810-26-000123",
              "company_name": "NVIDIA Corporation", "form_type": "10-Q",
              "filing_date": "2026-09-20", "report_date": "2026-07-31",
              "filing_url": "https://www.sec.gov/Archives/edgar/data/1045810/"
                            "000104581026000123/nvda-20260731.htm"}
    monkeypatch.setattr(fixed_sources, "_query_hf", lambda _snapshot, source, **_kwargs:
                        [filing] if source == "defeatbeta_sec_filing_index" else [{
                            "symbol": "NVDA", "fiscal_year": 2026, "fiscal_quarter": 2,
                            "report_date": "2026-08-20", "transcripts_id": 991,
                            "transcripts": [{"paragraph_number": 1, "speaker": "CEO",
                                             "content": "NVIDIA sales improved. " * 80}]}])
    monkeypatch.setattr(fixed_sources, "fetch_filing",
                        lambda row, _: (SecFetchResult(text="NVIDIA Corporation quarterly filing. " * 80,
                            url=row["filing_url"], status="succeeded"), "regulatory_filing"))
    try:
        index = fixed_sources.ingest_documents(
            "defeatbeta_sec_filing_index", entity="NVDA", now=NOW, store=store)
        official = fixed_sources.ingest_documents(
            "sec_edgar_filing_body", entity="NVDA", now=NOW, store=store)
        transcript = fixed_sources.ingest_documents(
            "defeatbeta_earnings_transcript", entity="NVDA", now=NOW, store=store)
        assert index["published_version_ids"] == []
        assert official["status"] == "succeeded" and official["accepted"] == 1
        assert transcript["status"] == "succeeded" and transcript["accepted"] == 1
        assert len(official["published_version_ids"]) == 1
        assert len(transcript["published_version_ids"]) == 1
        assert store.conn.execute("SELECT COUNT(*) FROM data_documents").fetchone()[0] == 2
    finally:
        store.close()


def test_latest_pinned_upstream_snapshot_is_valid_despite_quiet_earnings_period(
        tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(fixed_sources, "_hf_snapshot", lambda _policy: {
        "revision": "a" * 40, "spec_sha256": "b" * 64,
        "file_sha256": "c" * 64, "file_updated_at": "2026-09-20T00:00:00Z",
        "source_url": "https://huggingface.co/datasets/defeatbeta/yahoo-finance-data/"
                      "resolve/" + "a" * 40 + "/data/US/file.parquet"})
    monkeypatch.setattr(fixed_sources, "_query_hf", lambda *_args, **_kwargs: [{
        "symbol": "NVDA", "cik": "1045810", "accession_number": "0001045810-26-000123",
        "form_type": "10-Q", "filing_url": "https://www.sec.gov/Archives/edgar/data/"
                                         "1045810/000104581026000123/nvda.htm"}])
    try:
        result = fixed_sources.ingest_documents(
            "defeatbeta_sec_filing_index", entity="NVDA", now=NOW, store=store)
        assert result["status"] == "succeeded"
        assert result["snapshot_stale"] is False
        run = store.conn.execute(
            "SELECT status,reason_codes FROM data_ingestion_runs WHERE run_id=?",
            (result["run_id"],)).fetchone()
        assert run["status"] == "succeeded" and "snapshot_stale" not in run["reason_codes"]
    finally:
        store.close()


def test_transcript_revision_appends_immutable_version_and_repeat_is_no_change(
        tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    current = {"revision": "a" * 40, "text": "NVIDIA revenue improved. " * 80}
    monkeypatch.setattr(fixed_sources, "_hf_snapshot", lambda _policy: {
        "revision": current["revision"], "spec_sha256": "b" * 64,
        "file_sha256": "c" * 64, "file_updated_at": NOW.isoformat(),
        "source_url": "https://huggingface.co/example.parquet"})
    monkeypatch.setattr(fixed_sources, "_query_hf", lambda *_a, **_k: [{
        "symbol": "NVDA", "fiscal_year": 2026, "fiscal_quarter": 2,
        "report_date": "2026-08-20", "transcripts_id": 991,
        "transcripts": [{"paragraph_number": 1, "speaker": "CEO",
                         "content": current["text"]}]}])
    try:
        first = fixed_sources.ingest_documents(
            "defeatbeta_earnings_transcript", entity="NVDA", now=NOW, store=store)
        assert first["accepted"] == 1 and len(first["published_version_ids"]) == 1
        current["revision"] = "d" * 40
        current["text"] = "NVIDIA guidance improved. " * 80
        second = fixed_sources.ingest_documents(
            "defeatbeta_earnings_transcript", entity="NVDA", now=NOW, store=store)
        assert second["accepted"] == 1
        assert second["document_ids"] == first["document_ids"]
        versions = store.document_versions(first["document_ids"][0])
        assert len(versions) == 2
        assert versions[0]["content_hash"] != versions[1]["content_hash"]
        third = fixed_sources.ingest_documents(
            "defeatbeta_earnings_transcript", entity="NVDA", now=NOW, store=store)
        assert third["status"] == "no_change" and third["no_change"] == 1
        assert len(store.document_versions(first["document_ids"][0])) == 2
    finally:
        store.close()


def test_official_body_revision_is_separate_from_index_metadata(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(fixed_sources, "_hf_snapshot", lambda _policy: {
        "revision": "a" * 40, "spec_sha256": "b" * 64,
        "file_sha256": "c" * 64, "file_updated_at": NOW.isoformat(),
        "source_url": "https://huggingface.co/example.parquet"})
    filing = {"symbol": "NVDA", "cik": "1045810",
              "accession_number": "0001045810-26-000123", "company_name": "NVIDIA Corporation",
              "form_type": "10-Q", "filing_date": "2026-09-20",
              "filing_url": "https://www.sec.gov/Archives/edgar/data/1045810/"
                            "000104581026000123/nvda.htm"}
    monkeypatch.setattr(fixed_sources, "_query_hf", lambda *_a, **_k: [filing])
    current = {"body": "NVIDIA Corporation quarterly filing. " * 80}
    monkeypatch.setattr(fixed_sources, "fetch_filing", lambda row, _: (
        SecFetchResult(text=current["body"], url=row["filing_url"], status="succeeded"),
        "regulatory_filing"))
    try:
        index = fixed_sources.ingest_documents(
            "defeatbeta_sec_filing_index", entity="NVDA", now=NOW, store=store)
        assert index["accepted"] == 1 and not index["published_version_ids"]
        first = fixed_sources.ingest_documents(
            "sec_edgar_filing_body", entity="NVDA", now=NOW, store=store)
        assert first["accepted"] == 1
        current["body"] = "NVIDIA Corporation revised quarterly filing. " * 80
        second = fixed_sources.ingest_documents(
            "sec_edgar_filing_body", entity="NVDA", now=NOW, store=store)
        assert second["accepted"] == 1
        assert second["document_ids"] == first["document_ids"]
        assert len(store.document_versions(first["document_ids"][0])) == 2
        third = fixed_sources.ingest_documents(
            "sec_edgar_filing_body", entity="NVDA", now=NOW, store=store)
        assert third["no_change"] == 1
    finally:
        store.close()
