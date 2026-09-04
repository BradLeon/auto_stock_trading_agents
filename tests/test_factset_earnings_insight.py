"""FactSet Earnings Insight governed source, storage and product acceptance."""

from __future__ import annotations

from datetime import date, datetime, timezone
import sqlite3

import pytest
import yaml

from ats.data.catalog import DataCatalog, load_data_catalog
from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import (
    ArtifactDescriptor,
    EvidenceLink,
    ObservationInput,
    QualityStatus,
    SeriesIdentity,
)
from ats.data.products import (
    DataProducts,
    EarningsInsightObservation,
    EarningsInsightReport,
    EarningsInsightSnapshot,
    EarningsInsightStatus,
    to_earnings_backdrop,
)
from ats.data.pipelines.factset_earnings_insight import (
    FactSetDocumentPipeline,
    FactSetIndexPipeline,
    FactSetSectorPipeline,
    FactSetWeeklyPipeline,
)
from ats.data.rollout_modes import read_mode, source_mode
from ats.data.sources.factset_earnings_insight import (
    FactSetFailure,
    FactSetFetch,
    FactSetPDF,
    FactSetSourceError,
    PDFImage,
    PDFPage,
    fetch_report,
)
from ats.data.sources.factset_earnings_text import (
    CandidateStatus,
    EstimateState,
    FactSetCandidate,
    FactSetEvidenceAnchor,
    FactSetExtractionRun,
    MetricGroup,
    ReportPeriod,
    ReportPhase,
    classify_document,
    extract_index_text,
    extract_revision_breadth,
    merge_candidate_evidence,
    new_extraction_run,
    normalize_quarter,
    validate_index_candidates,
)
from ats.data.sources.factset_earnings_charts import (
    ChartCell,
    ChartCropResult,
    ChartImage,
    ChartRegistry,
    ChartTable,
    GICS_ALIASES,
    LocalOCRAdapter,
    OCRDependencyStatus,
    OCRResult,
    OCRStatus,
    emit_chart_candidates,
    extract_chart,
    normalize_sector_label,
    validate_sector_table,
)
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository


def test_factset_catalog_contract_and_independent_rollout(monkeypatch, tmp_path):
    monkeypatch.setenv("ATS_STRUCTURED_RELEASE_FILE", str(tmp_path / "releases.yaml"))
    catalog = load_data_catalog()
    structured = catalog.structured_catalog()
    raw = structured.raw

    doc = next(item for item in catalog.unstructured_sources()
               if item.id == "factset_earnings_insight_doc")
    assert doc.policy["stable_url"] == "https://www.factset.com/earningsinsight"
    assert doc.policy["usage"] == "internal_only"
    assert doc.policy["freshness_days"] == 10
    assert doc.request_budget == {
        "max_requests_per_run": 1, "timeout_seconds": 60, "max_attempts": 3}

    dataset = next(item for item in structured.datasets()
                   if item.id == "sp500_earnings_insight")
    assert dataset.primary_sources == ["factset_earnings_insight_metrics"]
    assert len(dataset.entities) == 12
    assert len(dataset.core_metrics) == 31
    revision = next(metric for metric in structured.metrics()
                    if metric.id == "earnings.revision.improved_sector_count")
    assert revision.unit_family == "count"
    assert revision.entity_scope == ["SP500"]
    assert revision.required_dimensions == [
        "comparison_date", "revision_direction", "sector_total"]
    assert raw["provider_mappings"]["factset_earnings_insight_metrics"][
        "revision_improved_sector_count"] == "earnings.revision.improved_sector_count"
    assert raw["entities"]["GICS_35"]["aliases"] == ["Health Care", "Healthcare"]
    assert catalog.validate().reason_codes == []

    assert source_mode("factset_earnings_insight_doc") == "shadow"
    assert source_mode("factset_earnings_insight_metrics") == "shadow"
    assert source_mode("factset_earnings_insight_index") == "platform"
    assert source_mode("factset_earnings_insight_sector") == "platform"
    assert read_mode("macro_factset") == "platform"
    assert read_mode("sector_factset") == "platform"
    monkeypatch.setenv("ATS_STRUCTURED_MACRO_FACTSET_MODE", "off")
    assert read_mode("macro_factset") == "off"
    assert read_mode("sector_factset") == "platform"


def test_structured_catalog_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "structured.yaml"
    path.write_text(
        "version: 1\nmetric_definitions:\n"
        "  duplicate: {unit_family: ratio}\n"
        "  duplicate: {unit_family: count}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate structured catalog key"):
        StructuredCatalog.load(path)


def _minimal_catalog(tmp_path, *, unit_family="ratio", aliases=None):
    (tmp_path / "sources.yaml").write_text("sources: {}\narticle_sources: {}\n")
    (tmp_path / "news_sources.yaml").write_text("rss: []\n")
    (tmp_path / "unstructured.yaml").write_text("version: 1\nsources: {}\n")
    structured = {
        "version": 1,
        "feature_flags": {"default_mode": "legacy", "sources": {}, "consumers": {}},
        "entities": {"ENTITY": {"kind": "index", "canonical_name": "Entity",
                                  "aliases": aliases if aliases is not None else ["Entity"],
                                  "securities": []}},
        "sources": {},
        "datasets": {},
        "metric_definitions": {"metric": {"value_type": "number",
                                              "unit_family": unit_family}},
        "provider_mappings": {},
    }
    (tmp_path / "structured.yaml").write_text(
        yaml.safe_dump(structured, sort_keys=False), encoding="utf-8")
    (tmp_path / "catalog.yaml").write_text(
        "version: 1\ndomains:\n  structured: structured.yaml\n"
        "  unstructured: unstructured.yaml\n  sources: sources.yaml\n"
        "  news_sources: news_sources.yaml\nsources: {}\ndatasets: {}\n",
        encoding="utf-8",
    )
    return DataCatalog.load(tmp_path / "catalog.yaml")


def test_catalog_validation_rejects_invalid_units_and_missing_aliases(tmp_path):
    result = _minimal_catalog(tmp_path, unit_family="made_up", aliases=[]).validate()
    assert result.valid is False
    assert "invalid_metric_unit_family" in result.reason_codes
    assert "entity_aliases_missing" in result.reason_codes


def test_document_artifact_links_are_idempotent_and_validate_regions(tmp_path):
    store = PlatformUnstructuredRepository(tmp_path / "documents.sqlite", writable=True)
    assert store.link_document_artifact(
        "doc@abc", "artifact", role="source_pdf", media_type="application/pdf",
        content_hash="abc") is True
    assert store.link_document_artifact(
        "doc@abc", "artifact", role="source_pdf", media_type="application/pdf",
        content_hash="abc") is False
    assert len(store.document_artifacts("doc@abc")) == 1
    with pytest.raises(ValueError, match="normalized"):
        store.link_document_artifact(
            "doc@abc", "crop", role="chart_crop", media_type="image/png",
            content_hash="def", page_number=2, region=(0, 0, 2, 1))


def test_document_artifact_schema_migrates_an_existing_platform_database(tmp_path):
    path = tmp_path / "existing.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE data_documents (document_id TEXT PRIMARY KEY, marker TEXT)")
    connection.execute("INSERT INTO data_documents VALUES ('legacy', 'kept')")
    connection.commit()
    connection.close()

    store = PlatformUnstructuredRepository(path, writable=True)
    assert store.link_document_artifact(
        "legacy@v1", "artifact", role="source_pdf",
        media_type="application/pdf", content_hash="abc") is True
    assert store.conn.execute(
        "SELECT marker FROM data_documents WHERE document_id='legacy'").fetchone()[0] == "kept"


def test_structured_evidence_migrates_text_rows_and_supports_image_regions(tmp_path):
    path = tmp_path / "structured.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE structured_evidence_links ("
        "link_id TEXT PRIMARY KEY,observation_id TEXT NOT NULL,candidate_id TEXT NOT NULL,"
        "document_id TEXT NOT NULL,version_id TEXT NOT NULL,char_start INTEGER NOT NULL,"
        "char_end INTEGER NOT NULL,extraction_method TEXT NOT NULL,source_tier TEXT NOT NULL,"
        "verification_status TEXT NOT NULL,reviewer TEXT NOT NULL,reviewed_at TEXT NOT NULL,"
        "created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO structured_evidence_links VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("legacy", "obs", "", "doc", "version", 1, 5, "regex", "primary",
         "accepted", "reviewer", "", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    repo = SQLiteStructuredRepository(path, artifact_root=tmp_path / "artifacts")
    legacy = repo.evidence_links(observation_id="obs")[0]
    assert legacy["anchor_kind"] == "text_span"
    image_id = repo.save_evidence_link(EvidenceLink(
        candidate_id="candidate", document_id="doc", version_id="version",
        anchor_kind="image_region", page_number=17, chart_id="eps_scorecard",
        region=(0.1, 0.2, 0.8, 0.9), extraction_method="layout_ocr_v1",
        source_tier="primary"))
    image = repo.evidence_links(candidate_id="candidate")[0]
    assert image["link_id"] == image_id
    assert image["page_number"] == 17
    assert image["chart_id"] == "eps_scorecard"


class _Response:
    def __init__(self, *, status=200, content=b"%PDF fixture", mime="application/pdf"):
        self.status_code = status
        self.content = content
        self.url = "https://advantage.factset.com/EarningsInsight_082826.pdf"
        self.headers = {
            "content-type": mime,
            "etag": '"weekly-082826"',
            "last-modified": "Fri, 28 Aug 2026 12:00:00 GMT",
        }


class _Client:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def test_fetch_report_returns_redirect_metadata_and_exact_bytes():
    now = datetime(2026, 8, 29, 0, 10, tzinfo=timezone.utc)
    client = _Client(_Response())
    result = fetch_report(client=client, clock=lambda: now)
    assert result.final_url.endswith("EarningsInsight_082826.pdf")
    assert result.etag == '"weekly-082826"'
    assert result.last_modified.startswith("Fri, 28 Aug")
    assert result.mime_type == "application/pdf"
    assert result.body == b"%PDF fixture"
    assert result.byte_count == len(result.body)
    assert result.fetched_at == now
    assert client.calls[0][1]["follow_redirects"] is True


@pytest.mark.parametrize(
    ("response", "error", "expected"),
    [
        (_Response(status=403), None, FactSetFailure.UNAUTHORIZED),
        (_Response(content=b"<html>challenge</html>", mime="text/html"), None,
         FactSetFailure.NOT_PDF),
        (_Response(status=503), None, FactSetFailure.UNREACHABLE),
        (None, TimeoutError("slow"), FactSetFailure.UNREACHABLE),
    ],
)
def test_fetch_report_maps_response_failures(response, error, expected):
    with pytest.raises(FactSetSourceError) as caught:
        fetch_report(client=_Client(response, error))
    assert caught.value.status == expected


@pytest.fixture
def projected_factset_pdf():
    text = "EARNINGS INSIGHT\nFactSet\nAugust 28, 2026\nIndex highlights"
    return FactSetPDF(
        report_date=date(2026, 8, 28), title="FactSet Earnings Insight",
        page_count=2,
        pages=(
            PDFPage(1, text, 0, len(text), "Index highlights"),
            PDFPage(2, "Sector chart", len(text) + 2, len(text) + 14, "Sector chart"),
        ),
        images=(PDFImage(
            page_number=2, image_number=1, chart_id="sector_scorecard",
            data=b"raster samples", media_type="application/x-pdf-image-samples",
            width=900, height=650),),
        text=f"{text}\n\nSector chart", text_hash="text-hash", pdf_hash="pdf-hash")


def test_local_import_projects_prose_and_raster_idempotently(
        tmp_path, monkeypatch, projected_factset_pdf):
    import ats.data.pipelines.factset_earnings_insight as pipeline_module

    local_pdf = tmp_path / "EarningsInsight_082826.pdf"
    local_pdf.write_bytes(b"%PDF fixture")
    imported_at = datetime(2026, 9, 2, 1, 23, tzinfo=timezone.utc)
    monkeypatch.setattr(pipeline_module, "inspect_pdf", lambda source: projected_factset_pdf)
    structured = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    documents = PlatformUnstructuredRepository(tmp_path / "documents.sqlite", writable=True)
    pipeline = FactSetDocumentPipeline(
        structured, documents, clock=lambda: imported_at)

    first = pipeline.run(local_pdf=local_pdf)
    repeated = pipeline.run(local_pdf=local_pdf)
    reprocessed = pipeline.run(local_pdf=local_pdf, processor_version="factset-document-v2")

    assert first["status"] == "succeeded"
    assert repeated["status"] == "no_change"
    assert reprocessed["status"] == "succeeded"
    assert first["known_at"] == imported_at.isoformat()
    assert len(documents.document_pages(first["document_version_id"])) == 2
    links = documents.document_artifacts(first["document_version_id"])
    assert {row["role"] for row in links} == {"source_pdf", "page_image"}
    assert len(structured.artifacts_for(
        source_id="factset_earnings_insight_metrics",
        content_hash=first["pdf_sha256"])) == 1
    assert all("Obsidian" not in row["local_path"] for row in
               documents._rows("SELECT local_path FROM data_document_versions", []))


def test_local_import_rejects_non_pdf_before_projection(tmp_path):
    bad = tmp_path / "report.pdf"
    bad.write_text("login page", encoding="utf-8")
    structured = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    documents = PlatformUnstructuredRepository(tmp_path / "documents.sqlite", writable=True)
    with pytest.raises(FactSetSourceError) as caught:
        FactSetDocumentPipeline(structured, documents).run(local_pdf=bad)
    assert caught.value.status == FactSetFailure.NOT_PDF


def test_typed_candidate_and_report_classification(projected_factset_pdf):
    pages = list(projected_factset_pdf.pages)
    first_text = (
        pages[0].text +
        " with 97% of S&P 500 companies reporting actual results")
    pages[0] = PDFPage(1, first_text, 0, len(first_text), "Index highlights")
    pages[1] = PDFPage(
        2,
        "Table of Contents Earnings & Revenue Scorecard Earnings Growth "
        "Forward Estimates & Valuation",
        pages[1].char_start, pages[1].char_end, "Table of Contents")
    document = projected_factset_pdf.__class__(
        **{**projected_factset_pdf.__dict__, "pages": tuple(pages)})
    phase, coverage, reasons = classify_document(document)
    assert phase == ReportPhase.SUBSTANTIALLY_COMPLETE
    assert coverage == pytest.approx(0.97)
    assert reasons == []
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    run = new_extraction_run(
        document, document_id="doc", version_id="doc@hash", known_at=now)
    candidate = FactSetCandidate(
        run_id=run.run_id, entity_id="SP500", provider_field="eps_yoy_growth",
        metric_id="earnings.eps.yoy_growth", metric_group=MetricGroup.GROWTH,
        period=ReportPeriod(value="2026Q2", basis="target_quarter"),
        estimate_state=EstimateState.BLENDED, unit="ratio", raw_token="52.0%",
        raw_value="52.0", value=0.52, report_date=document.report_date,
        known_at=now, evidence=[FactSetEvidenceAnchor(
            document_id="doc", version_id="doc@hash", page_number=1,
            char_start=1, char_end=6)])
    assert len(candidate.candidate_id) == 24
    assert candidate.raw_token == "52.0%"
    assert normalize_quarter("Q 2 2026") == "2026Q2"


def test_unrecognized_factset_layout_is_quarantined(projected_factset_pdf):
    page = PDFPage(1, "generic research", 0, 16, "")
    document = projected_factset_pdf.__class__(
        **{**projected_factset_pdf.__dict__, "pages": (page,)})
    run = new_extraction_run(
        document, document_id="doc", version_id="doc@unknown",
        known_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
    assert run.phase == ReportPhase.UNKNOWN_TEMPLATE
    assert run.template_status == "quarantined"
    assert "title_anchor_missing" in run.reason_codes


def test_section_extractors_emit_normalized_candidates_with_text_evidence():
    page1 = (
        "EARNINGS INSIGHT FactSet August 28, 2026 Key Metrics "
        "Earnings Scorecard: For Q2 2026 (with 97% of S&P 500 companies reporting actual results). "
        "For Q2 2026, the blended (year-over-year) earnings growth rate for the S&P 500 is 52.0%. "
        "Ten of eleven sectors are reporting higher earnings today (compared to June 30) due to "
        "upward revisions to EPS estimates. "
        "Earnings Guidance: For Q3 2026, 35 S&P 500 companies have issued negative EPS guidance "
        "and 63 S&P 500 companies have issued positive EPS guidance. "
        "The forward 12-month P/E ratio for the S&P 500 is 19.6. "
        "This P/E ratio is below the 5-year average (19.9) but above the 10-year average (19.0).")
    page2 = (
        "Table of Contents Earnings & Revenue Scorecard Earnings Growth Forward Estimates & Valuation. "
        "86% have reported actual EPS above the mean EPS estimate, 3% have reported actual EPS equal "
        "to the mean EPS estimate, and 10% have reported actual EPS below the mean EPS estimate. "
        "77% of the companies have reported actual revenues above estimated revenues, 0% of the "
        "companies have reported actual revenues equal to estimated revenues, and 23% of the companies "
        "have reported actual revenues below estimated revenues. Companies are reporting earnings that "
        "are 26.5% above expectations. Companies are reporting revenues that are 3.2% above expectations. "
        "The blended (year-over-year) revenue growth rate for the S&P 500 for Q2 2026 is 15.5%. "
        "The blended net profit margin for the S&P 500 for Q2 2026 is 17.0%. "
        "The trailing 12-month P/E ratio is 26.4, which is above the 5-year average of 24.4 and above the "
        "10-year average of 23.5. The bottom-up target price for the S&P 500 is 9204.34, which is 19.1% "
        "above the closing price. 59.2% are Buy ratings, 35.9% are Hold ratings, and 4.9% are Sell ratings.")
    document = FactSetPDF(
        report_date=date(2026, 8, 28), title="FactSet Earnings Insight", page_count=2,
        pages=(PDFPage(1, page1, 0, len(page1), "Key Metrics"),
               PDFPage(2, page2, len(page1) + 2, len(page1) + 2 + len(page2),
                       "Table of Contents")),
        images=(), text=f"{page1}\n\n{page2}", text_hash="text", pdf_hash="pdf")
    run = extract_index_text(
        document, document_id="doc", version_id="doc@fixture",
        known_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
    metrics = {candidate.metric_id: candidate for candidate in run.candidates}
    assert run.phase == ReportPhase.SUBSTANTIALLY_COMPLETE
    assert metrics["earnings.reporting.coverage"].value == pytest.approx(0.97)
    assert metrics["earnings.eps.yoy_growth"].estimate_state == EstimateState.BLENDED
    breadth = metrics["earnings.revision.improved_sector_count"]
    assert breadth.value == 10
    assert breadth.dimensions == {
        "comparison_date": "2026-06-30", "revision_direction": "upward",
        "sector_total": 11, "raw_sector_count_token": "Ten",
        "raw_sector_total_token": "eleven",
    }
    assert "Ten of eleven sectors" in breadth.raw_token
    assert metrics["earnings.guidance.negative_count"].period.value == "2026Q3"
    assert metrics["valuation.trailing_pe.average_10y"].value == pytest.approx(23.5)
    assert metrics["consensus.rating.sell_share"].value == pytest.approx(0.049)
    assert all(candidate.evidence[0].char_end > candidate.evidence[0].char_start
               for candidate in run.candidates)
    assert any("chart_extractor_required" in reason for reason in run.reason_codes)


def test_revision_breadth_accepts_digit_wording(projected_factset_pdf):
    first = (
        "EARNINGS INSIGHT FactSet August 28, 2026 Earnings Growth: For Q2 2026, "
        "the blended (year-over-year) earnings growth rate for the S&P 500 is 52.0%. "
        "10 of the 11 sectors have recorded an increase in their earnings growth rate "
        "since June 30 due to upward revisions to EPS estimates.")
    toc = "Table of Contents Earnings & Revenue Scorecard Earnings Growth Forward Estimates & Valuation"
    document = projected_factset_pdf.__class__(
        **{**projected_factset_pdf.__dict__, "pages": (
            PDFPage(1, first, 0, len(first), "Earnings Growth"),
            PDFPage(2, toc, len(first) + 2, len(first) + 2 + len(toc), "Table of Contents"),
        )})
    run = new_extraction_run(
        document, document_id="doc", version_id="doc@digits",
        known_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
    candidate = extract_revision_breadth(document, run).candidates[0]
    assert candidate.value == 10
    assert candidate.dimensions["sector_total"] == 11
    assert candidate.dimensions["comparison_date"] == "2026-06-30"


def _validation_candidate(metric, value, *, unit="ratio", dimensions=None):
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    return FactSetCandidate(
        run_id="run", entity_id="SP500", provider_field=metric, metric_id=metric,
        metric_group=MetricGroup.SCORECARD,
        period=ReportPeriod(value="2026Q2", basis="target_quarter"),
        estimate_state=EstimateState.BLENDED, unit=unit,
        raw_token=str(value), raw_value=str(value), value=value,
        report_date=date(2026, 8, 28), known_at=now,
        dimensions=dimensions or {}, evidence=[FactSetEvidenceAnchor(
            document_id="doc", version_id="version", page_number=1,
            char_start=1, char_end=2)])


def test_candidate_validation_checks_composition_counts_and_missing_values():
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    candidates = [
        _validation_candidate("earnings.eps.above_estimate_share", 0.8),
        _validation_candidate("earnings.eps.inline_estimate_share", 0.2),
        _validation_candidate("earnings.eps.below_estimate_share", 0.2),
        _validation_candidate("earnings.guidance.negative_count", -1, unit="count"),
        _validation_candidate(
            "earnings.revision.improved_sector_count", 12, unit="count",
            dimensions={"comparison_date": "2026-06-30",
                        "revision_direction": "upward", "sector_total": 11}),
    ]
    run = FactSetExtractionRun(
        run_id="run", document_id="doc", version_id="version",
        report_date=date(2026, 8, 28), known_at=now,
        phase=ReportPhase.SUBSTANTIALLY_COMPLETE, template_status="recognized",
        candidates=candidates,
        reason_codes=["scorecard:earnings.revenue.inline_estimate_share:text_pattern_not_found"])
    report = validate_index_candidates(run)
    by_metric = {candidate.metric_id: candidate for candidate in report.candidates}
    assert all(by_metric[metric].status == CandidateStatus.QUARANTINED for metric in (
        "earnings.eps.above_estimate_share", "earnings.eps.inline_estimate_share",
        "earnings.eps.below_estimate_share"))
    assert "eps_scorecard_composition_total_mismatch" in report.reason_counts
    assert "count_out_of_range" in by_metric[
        "earnings.guidance.negative_count"].reason_codes
    assert "revision_breadth_out_of_range" in by_metric[
        "earnings.revision.improved_sector_count"].reason_codes
    assert report.missing["earnings.revenue.inline_estimate_share"] == {
        "value": None, "reason": "text_pattern_not_found"}
    assert "earnings.revenue.inline_estimate_share" not in by_metric


def test_duplicate_evidence_merges_but_distinct_source_values_conflict():
    first = _validation_candidate("earnings.revenue.yoy_growth", 0.075)
    duplicate = first.model_copy(deep=True)
    duplicate.evidence = [FactSetEvidenceAnchor(
        document_id="doc", version_id="version", page_number=2,
        char_start=10, char_end=20)]
    merged = merge_candidate_evidence([first, duplicate])
    assert merged.merged_duplicates == 1
    assert len(merged.candidates) == 1
    assert len(merged.candidates[0].evidence) == 2

    contradictory = _validation_candidate("earnings.revenue.yoy_growth", 0.077)
    conflict = merge_candidate_evidence([first, contradictory])
    assert conflict.merged_duplicates == 0
    assert len(conflict.conflict_identities) == 1
    assert {candidate.value for candidate in conflict.candidates} == {0.075, 0.077}
    assert all(candidate.status == CandidateStatus.CONFLICT
               for candidate in conflict.candidates)
    assert all("source_internal_conflict" in candidate.reason_codes
               for candidate in conflict.candidates)


def test_index_pipeline_persists_vintage_evidence_and_effective_manifest(
        tmp_path, projected_factset_pdf):
    first = (
        "EARNINGS INSIGHT FactSet August 28, 2026 Key Metrics Earnings Growth: For Q2 2026, "
        "with 97% of S&P 500 companies reporting actual results. The blended "
        "(year-over-year) earnings growth rate for the S&P 500 is 52.0%.")
    toc = "Table of Contents Earnings & Revenue Scorecard Earnings Growth Forward Estimates & Valuation"
    document = projected_factset_pdf.__class__(
        **{**projected_factset_pdf.__dict__, "pages": (
            PDFPage(1, first, 0, len(first), "Key Metrics"),
            PDFPage(2, toc, len(first) + 2, len(first) + 2 + len(toc), "Table of Contents"),
        ), "text": f"{first}\n\n{toc}"})
    known_at = datetime(2026, 9, 2, 1, 23, tzinfo=timezone.utc)
    repository = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog()
    artifact = repository.put_artifact(
        b"%PDF fixture",
        ArtifactDescriptor(
            source_id="factset_earnings_insight_metrics",
            dataset_id="sp500_earnings_insight", fetched_at=known_at,
            source_url="https://example.test/EarningsInsight_082826.pdf",
            media_type="application/pdf", retention="licensed_internal_research"))
    required = {
        ReportPhase.SUBSTANTIALLY_COMPLETE: {"earnings.reporting.coverage"}}
    pipeline = FactSetIndexPipeline(repository, required_metrics=required)
    first_run = pipeline.run(
        document, document_id="doc", version_id="doc@fixture",
        artifact_id=artifact.id, known_at=known_at)
    repeated = pipeline.run(
        document, document_id="doc", version_id="doc@fixture",
        artifact_id=artifact.id, known_at=known_at)

    assert first_run["release_status"] == "platform"
    assert first_run["quality"]["passed"] is True
    assert first_run["accepted"] == 2
    assert repeated["status"] == "no_change"
    observations = repository.observations(
        dataset_id="sp500_earnings_insight",
        metric_id="earnings.reporting.coverage", entity_id="SP500",
        latest_only=False)
    assert len(observations) == 1
    evidence = repository.evidence_links(
        observation_id=observations[0]["observation_id"])
    assert evidence[0]["anchor_kind"] == "text_span"
    assert evidence[0]["verification_status"] == "accepted"
    manifest = repository.latest_release(
        dataset_id="sp500_earnings_insight", partition="index_core")
    assert manifest["version_id"] == "doc@fixture"
    assert observations[0]["observation_id"] in manifest["observation_ids"]
    assert len(manifest["observation_ids"]) == 2
    assert manifest["quality"]["groups"]["coverage"]["admitted"] == 1


def _seed_factset_release(repository, *, artifact_id, document_id, version_id,
                          report_date, known_at, growth, coverage=0.50):
    observation_ids = []
    values = {
        "earnings.eps.yoy_growth": (growth, "ratio", "blended", "2026Q2"),
        "earnings.reporting.coverage": (coverage, "ratio", "actual", "2026Q2"),
        "earnings.revision.improved_sector_count": (10, "count", "blended", "2026Q2"),
        "earnings.guidance.negative_count": (35, "count", "estimated", "2026Q3"),
        "earnings.guidance.positive_count": (63, "count", "estimated", "2026Q3"),
        "valuation.forward_pe": (19.6, "multiple", "not_applicable", report_date),
    }
    for metric, (value, unit, state, period) in values.items():
        dimensions = {"estimate_state": state}
        if metric == "earnings.revision.improved_sector_count":
            dimensions.update({
                "comparison_date": "2026-06-30", "revision_direction": "upward",
                "sector_total": 11})
        saved = repository.save_observation(ObservationInput(
            series=SeriesIdentity(
                source_id="factset_earnings_insight_metrics",
                dataset_id="sp500_earnings_insight", entity_id="SP500",
                metric_id=metric, unit=unit,
                period_basis="snapshot" if period == report_date else "target_quarter",
                dimensions=dimensions),
            period=period, value=value,
            event_time=datetime.fromisoformat(f"{report_date}T00:00:00+00:00"),
            known_at=known_at, fetched_at=known_at, artifact_id=artifact_id,
            quality_status=QualityStatus.ACCEPTED,
            raw={"document_id": document_id, "version_id": version_id}))
        observation_ids.append(saved.id)
        repository.save_evidence_link(EvidenceLink(
            observation_id=saved.id, document_id=document_id, version_id=version_id,
            page_number=1, char_start=10, char_end=30,
            extraction_method="fixture", source_tier="licensed_primary"))
    repository.save_release_manifest(
        source_id="factset_earnings_insight_metrics",
        dataset_id="sp500_earnings_insight", partition="index_core",
        report_date=report_date, document_id=document_id, version_id=version_id,
        artifact_id=artifact_id, known_at=known_at, extractor_version="fixture-v1",
        status="shadow", passed=True, quality={"passed": True},
        observation_ids=observation_ids)
    return observation_ids


def test_typed_snapshot_as_of_vintages_lineage_staleness_and_compatibility(tmp_path):
    repository = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog()
    documents = PlatformUnstructuredRepository(tmp_path / "documents.sqlite", writable=True)
    first_known = datetime(2026, 7, 25, tzinfo=timezone.utc)
    second_known = datetime(2026, 8, 1, tzinfo=timezone.utc)
    artifact = repository.put_artifact(
        b"%PDF product fixture",
        ArtifactDescriptor(
            source_id="factset_earnings_insight_metrics",
            dataset_id="sp500_earnings_insight", fetched_at=first_known,
            source_url="https://example.test/EarningsInsight_072426.pdf",
            media_type="application/pdf", retention="licensed_internal_research"))
    first_ids = _seed_factset_release(
        repository, artifact_id=artifact.id, document_id="factset:first",
        version_id="factset:first@hash", report_date="2026-07-24",
        known_at=first_known, growth=0.075, coverage=0.27)
    _seed_factset_release(
        repository, artifact_id=artifact.id, document_id="factset:second",
        version_id="factset:second@hash", report_date="2026-07-31",
        known_at=second_known, growth=0.077, coverage=0.61)
    products = DataProducts(
        structured_repository=repository, unstructured_repository=documents)

    historical = products.earnings_insight_snapshot(
        as_of=datetime(2026, 7, 30, tzinfo=timezone.utc))
    latest = products.earnings_insight_snapshot(
        as_of=datetime(2026, 8, 2, tzinfo=timezone.utc))
    assert historical.report.version_id == "factset:first@hash"
    assert latest.report.version_id == "factset:second@hash"
    assert historical.index["2026Q2"]["earnings.eps.yoy_growth"].value == 0.075
    assert latest.index["2026Q2"]["earnings.eps.yoy_growth"].value == 0.077
    assert first_ids[0] in historical.lineage.selected_observation_ids
    assert historical.index["2026Q2"]["earnings.eps.yoy_growth"].evidence[0].page_number == 1
    assert len(products.earnings_insight_vintages(
        as_of=datetime(2026, 8, 2, tzinfo=timezone.utc))) == 2

    backdrop = to_earnings_backdrop(latest)
    assert backdrop.growth_pct == pytest.approx(7.7)
    assert backdrop.growth_basis == "blended"
    assert backdrop.sectors_higher == 10
    assert backdrop.prior_as_of == "June 30"
    assert backdrop.guidance_negative == 35
    assert backdrop.fwd_pe == 19.6

    stale = products.earnings_insight_snapshot(
        as_of=datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert stale.status.state == "stale"
    assert stale.status.age_days == 20
    assert "report_age_exceeds_10_days" in stale.status.warnings


def test_analysis_packet_groups_all_25_metrics_and_keeps_page_cited_narrative(tmp_path):
    repository = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog()
    documents = PlatformUnstructuredRepository(tmp_path / "documents.sqlite", writable=True)
    known_at = datetime(2026, 8, 29, tzinfo=timezone.utc)
    report_date = "2026-08-28"
    document_id = "factset:analysis"
    version_id = "factset:analysis@hash"
    artifact = repository.put_artifact(
        b"%PDF analysis packet fixture",
        ArtifactDescriptor(
            source_id="factset_earnings_insight_metrics",
            dataset_id="sp500_earnings_insight", fetched_at=known_at,
            source_url="https://example.test/EarningsInsight_082826.pdf",
            media_type="application/pdf", retention="licensed_internal_research"))
    values = {
        "earnings.reporting.coverage": (0.97, "ratio"),
        "earnings.eps.above_estimate_share": (0.86, "ratio"),
        "earnings.eps.inline_estimate_share": (0.03, "ratio"),
        "earnings.eps.below_estimate_share": (0.10, "ratio"),
        "earnings.revenue.above_estimate_share": (0.77, "ratio"),
        "earnings.revenue.inline_estimate_share": (0.00, "ratio"),
        "earnings.revenue.below_estimate_share": (0.23, "ratio"),
        "earnings.eps.surprise_pct": (0.265, "ratio"),
        "earnings.revenue.surprise_pct": (0.032, "ratio"),
        "earnings.eps.yoy_growth": (0.52, "ratio"),
        "earnings.revenue.yoy_growth": (0.155, "ratio"),
        "earnings.net_profit_margin": (0.17, "ratio"),
        "earnings.revision.improved_sector_count": (10, "count"),
        "earnings.guidance.positive_count": (63, "count"),
        "earnings.guidance.negative_count": (35, "count"),
        "valuation.forward_pe": (19.6, "multiple"),
        "valuation.forward_pe.average_5y": (19.9, "multiple"),
        "valuation.forward_pe.average_10y": (19.0, "multiple"),
        "valuation.trailing_pe": (26.4, "multiple"),
        "valuation.trailing_pe.average_5y": (24.4, "multiple"),
        "valuation.trailing_pe.average_10y": (23.5, "multiple"),
        "consensus.rating.buy_share": (0.592, "ratio"),
        "consensus.rating.hold_share": (0.359, "ratio"),
        "consensus.rating.sell_share": (0.049, "ratio"),
        "consensus.target.upside": (0.191, "ratio"),
    }
    observation_ids = []
    for metric_id, (value, unit) in values.items():
        period = ("2026Q3" if "guidance" in metric_id else
                  report_date if metric_id.startswith(("valuation", "consensus")) else
                  "2026Q2")
        dimensions = {"estimate_state": "blended"}
        if metric_id == "earnings.revision.improved_sector_count":
            dimensions.update({
                "comparison_date": "2026-08-28", "revision_direction": "upward",
                "sector_total": 11})
        saved = repository.save_observation(ObservationInput(
            series=SeriesIdentity(
                source_id="factset_earnings_insight_metrics",
                dataset_id="sp500_earnings_insight", entity_id="SP500",
                metric_id=metric_id, unit=unit,
                period_basis="snapshot" if period == report_date else "target_quarter",
                dimensions=dimensions),
            period=period, value=value,
            event_time=datetime(2026, 8, 28, tzinfo=timezone.utc),
            known_at=known_at, fetched_at=known_at, artifact_id=artifact.id,
            quality_status=QualityStatus.ACCEPTED,
            raw={"document_id": document_id, "version_id": version_id}))
        observation_ids.append(saved.id)
    repository.save_release_manifest(
        source_id="factset_earnings_insight_metrics",
        dataset_id="sp500_earnings_insight", partition="index_core",
        report_date=report_date, document_id=document_id, version_id=version_id,
        artifact_id=artifact.id, known_at=known_at, extractor_version="fixture-v1",
        status="platform", passed=True, quality={"passed": True},
        observation_ids=observation_ids)
    page_texts = (
        "Excluding Alphabet and Amazon, the earnings growth rate would be 33.8%, rather than 52.0%.",
        "The report compares GAAP earnings with Non-GAAP earnings and explains the difference.",
        "Information Technology was the largest contributor to the increase in blended revenues.",
        "The net profit margin increased because of lower costs and stronger pricing.",
        "The forward 12-month P/E is near its five-year average. Buy ratings and target price imply optimism.",
    )
    pages = []
    cursor = 0
    for number, text in enumerate(page_texts, start=3):
        pages.append(PDFPage(number, text, cursor, cursor + len(text), "Fixture"))
        cursor += len(text) + 2
    documents.save_document_pages(version_id, pages)

    packet = DataProducts(
        structured_repository=repository,
        unstructured_repository=documents).earnings_insight_analysis_packet(
            as_of=datetime(2026, 9, 3, tzinfo=timezone.utc))

    assert packet.observation_count == 25
    assert set(packet.observation_groups) == {
        "reporting_progress", "earnings_revenue_surprises",
        "earnings_revenue_growth", "profit_margin", "company_guidance",
        "estimate_revision_breadth", "valuation", "ratings_and_target_price"}
    diagnostics = {item.diagnostic_id: item for item in packet.diagnostics}
    assert diagnostics["eps_minus_revenue_growth"].value == pytest.approx(36.5)
    assert diagnostics["eps_minus_revenue_surprise"].value == pytest.approx(23.3)
    assert diagnostics["positive_negative_guidance_ratio"].value == pytest.approx(1.8)
    assert diagnostics["positive_minus_negative_guidance"].value == 28
    assert diagnostics["forward_pe_vs_5y_average"].value == pytest.approx(-1.5075)
    assert diagnostics["trailing_pe_vs_10y_average"].value == pytest.approx(12.3404)
    assert 1 <= len(packet.narrative_evidence) <= 6
    assert {item.topic for item in packet.narrative_evidence} >= {
        "earnings_concentration", "gaap_non_gaap", "sector_contribution",
        "margin_drivers", "valuation_and_sentiment"}
    assert all(item.page_number >= 3 and item.char_end > item.char_start
               for item in packet.narrative_evidence)


def test_snapshot_returns_registered_no_data_instead_of_none(tmp_path):
    repository = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog()
    products = DataProducts(
        structured_repository=repository,
        unstructured_repository=PlatformUnstructuredRepository(
            tmp_path / "documents.sqlite", writable=True))
    snapshot = products.earnings_insight_snapshot(
        as_of=datetime(2026, 8, 2, tzinfo=timezone.utc))
    assert snapshot.status.state == "registered_no_data"
    assert snapshot.index == {}
    assert to_earnings_backdrop(snapshot).degraded is True


@pytest.mark.parametrize("state", ["estimated", "blended", "actual"])
def test_compatibility_mapper_preserves_estimate_state_and_warnings(state):
    observation = EarningsInsightObservation(
        observation_id=f"growth-{state}", entity_id="SP500",
        metric_id="earnings.eps.yoy_growth", period="2026Q2",
        period_basis="target_quarter", estimate_state=state, value=0.08,
        unit="ratio", known_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        quality_status="conflict")
    snapshot = EarningsInsightSnapshot(
        report=EarningsInsightReport(report_date=date(2026, 7, 31)),
        index={"2026Q2": {observation.metric_id: observation}},
        status=EarningsInsightStatus(
            state="shadow", freshness="fresh",
            warnings=["source_internal_conflict_quarantined"]))
    backdrop = to_earnings_backdrop(snapshot)
    assert backdrop.growth_basis == state
    assert backdrop.growth_pct == pytest.approx(8.0)
    assert "source_internal_conflict_quarantined" in backdrop.notes


def test_macro_and_sector_platform_consumers_use_products_without_legacy_io(
        tmp_path, monkeypatch):
    from ats.data import factset

    repository = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog()
    documents = PlatformUnstructuredRepository(tmp_path / "documents.sqlite", writable=True)
    known_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    artifact = repository.put_artifact(
        b"%PDF consumer fixture",
        ArtifactDescriptor(
            source_id="factset_earnings_insight_metrics",
            dataset_id="sp500_earnings_insight", fetched_at=known_at,
            source_url="https://example.test/EarningsInsight_073126.pdf",
            media_type="application/pdf", retention="licensed_internal_research"))
    _seed_factset_release(
        repository, artifact_id=artifact.id, document_id="factset:consumer",
        version_id="factset:consumer@hash", report_date="2026-07-31",
        known_at=known_at, growth=0.077, coverage=0.61)
    products = DataProducts(
        structured_repository=repository, unstructured_repository=documents)
    monkeypatch.setenv("ATS_STRUCTURED_MACRO_FACTSET_MODE", "platform")
    monkeypatch.setenv("ATS_STRUCTURED_SECTOR_FACTSET_MODE", "platform")
    monkeypatch.setattr(
        factset, "fetch_earnings_insight",
        lambda _cfg: (_ for _ in ()).throw(AssertionError("legacy I/O called")))

    block, source, backdrop = factset.fetch_macro_context({}, products=products)
    assert "数据状态:" in block
    assert source == "factset:factset:consumer@hash"
    assert backdrop.growth_basis == "blended"
    # No sector release is a typed omission, not a review failure.
    assert factset.fetch_sector_context(products=products) == ""


def test_sector_matrix_is_explicitly_top_down_and_degrades_to_omission():
    from ats.data import factset

    observation = EarningsInsightObservation(
        observation_id="sector", entity_id="GICS_45",
        metric_id="valuation.forward_pe", period="2026-08-28",
        period_basis="snapshot", value=21.8, unit="multiple",
        known_at=datetime(2026, 8, 29, tzinfo=timezone.utc))
    snapshot = EarningsInsightSnapshot(
        report=EarningsInsightReport(
            report_date=date(2026, 8, 28), version_id="doc@sector"),
        sectors={"GICS_45": {"2026-08-28": {
            "valuation.forward_pe": observation}}},
        status=EarningsInsightStatus(state="platform", freshness="fresh"))
    rendered = factset._render_sector_snapshot(snapshot)
    assert "top-down 市场背景" in rendered
    assert "不是 AI Hardware L1-L8、个股基本面或 Chain 独立证据" in rendered
    assert "GICS_45" in rendered and "21.8 multiple" in rendered
    assert factset._render_sector_snapshot(EarningsInsightSnapshot()) == ""


def test_sector_material_explains_shadow_instead_of_silently_omitting(monkeypatch):
    from ats.data import factset

    observation = EarningsInsightObservation(
        observation_id="sector", entity_id="GICS_45",
        metric_id="earnings.eps.yoy_growth", period="2026Q2",
        period_basis="target_quarter", value=0.25, unit="ratio",
        known_at=datetime(2026, 8, 29, tzinfo=timezone.utc))
    snapshot = EarningsInsightSnapshot(
        report=EarningsInsightReport(
            report_date=date(2026, 8, 28), version_id="doc@sector"),
        sectors={"GICS_45": {"2026Q2": {observation.metric_id: observation}}},
        status=EarningsInsightStatus(state="platform", freshness="fresh"))
    monkeypatch.setenv("ATS_STRUCTURED_SECTOR_FACTSET_MODE", "shadow")
    monkeypatch.setattr(factset, "_platform_snapshot", lambda products=None: snapshot)

    material = factset.fetch_sector_material()

    assert material["text"] == ""
    assert material["state"] == "shadow"
    assert material["report_date"] == "2026-08-28"
    assert "影子验证阶段" in material["reason"]


def test_macro_shadow_comparison_records_two_vintages(monkeypatch):
    import sys
    from types import SimpleNamespace

    from ats.data import factset
    from ats.schemas.macro_strategy import EarningsBackdrop

    recorded = []
    monkeypatch.setitem(sys.modules, "ats.data.cutover", SimpleNamespace(
        record_consumer_comparison=lambda **payload: recorded.append(payload)))
    monkeypatch.setattr("ats.data.runtime.platform_data_db_path", lambda: ":memory:")
    for day, growth, state in ((24, 7.5, "blended"), (31, 7.7, "actual")):
        observation = EarningsInsightObservation(
            observation_id=f"growth-{day}", entity_id="SP500",
            metric_id="earnings.eps.yoy_growth", period="2026Q2",
            period_basis="target_quarter", estimate_state=state,
            value=growth / 100, unit="ratio",
            known_at=(datetime(2026, 7, 25, tzinfo=timezone.utc)
                      if day == 24 else datetime(2026, 8, 1, tzinfo=timezone.utc)))
        snapshot = EarningsInsightSnapshot(
            report=EarningsInsightReport(
                report_date=date(2026, 7, day), version_id=f"doc@{day}"),
            index={"2026Q2": {observation.metric_id: observation}},
            status=EarningsInsightStatus(state="shadow", freshness="fresh"))
        factset._record_factset_shadow(
            legacy=EarningsBackdrop(
                report_date=date(2026, 7, day), quarter="Q2 2026",
                growth_pct=growth, growth_basis=state),
            platform_snapshot=snapshot)
    assert len(recorded) == 2
    assert [row["details"]["platform"]["report_date"] for row in recorded] == [
        "2026-07-24", "2026-07-31"]
    assert [row["details"]["platform"]["growth_basis"] for row in recorded] == [
        "blended", "actual"]
    assert all("freshness" in row["details"]["platform"] for row in recorded)
    assert all("rendered_review_text" in row["details"]["platform"]
               for row in recorded)


def test_non_owner_agents_do_not_directly_consume_factset_product():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "ats" / "agents"
    for package in ("chief", "risk", "pead", "technical"):
        text = "\n".join(path.read_text(encoding="utf-8")
                         for path in (root / package).glob("*.py"))
        assert "earnings_insight_snapshot" not in text
        assert "fetch_sector_context" not in text


def test_weekly_pipeline_has_one_entrypoint_and_unchanged_report_is_idempotent(
        tmp_path, monkeypatch, projected_factset_pdf):
    import ats.data.pipelines.factset_earnings_insight as pipeline_module

    local_pdf = tmp_path / "EarningsInsight_082826.pdf"
    local_pdf.write_bytes(b"%PDF weekly fixture")
    monkeypatch.setattr(
        pipeline_module, "inspect_pdf", lambda _source: projected_factset_pdf)
    known_at = datetime(2026, 9, 2, 1, 23, tzinfo=timezone.utc)
    retry_at = datetime(2026, 9, 3, 1, 23, tzinfo=timezone.utc)
    structured = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    documents = PlatformUnstructuredRepository(tmp_path / "documents.sqlite", writable=True)
    pipeline = FactSetWeeklyPipeline(
        structured, documents, clock=lambda: next(iteration))
    iteration = iter((known_at, retry_at))

    first = pipeline.run(local_pdf=local_pdf)
    first_observation_count = len(structured.observations(
        dataset_id="sp500_earnings_insight", latest_only=False))
    repeated = pipeline.run(local_pdf=local_pdf)
    assert first["document"]["status"] == "succeeded"
    assert repeated["document"]["status"] == "no_change"
    assert repeated["document"]["known_at"] == known_at.isoformat()
    assert repeated["index_core"]["status"] in {"no_change", "zero_match"}
    assert first["sector_core"]["release_status"] == "shadow"
    assert first["sector_core"]["quality"]["annotated_cells_ok"] is False
    assert set(first["counters"]) == {
        "index_candidates", "index_admitted", "index_conflicts",
        "index_quarantined", "sector_candidates", "sector_admitted",
        "sector_quarantined"}
    assert "?" not in first["provenance"]["final_url"]
    assert len(structured.release_manifests(
        dataset_id="sp500_earnings_insight", partition="index_core")) == 1
    observations = structured.observations(
        dataset_id="sp500_earnings_insight", latest_only=False)
    assert len(observations) == first_observation_count


def test_failed_refresh_with_previous_release_marks_snapshot_stale(tmp_path):
    repository = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog()
    documents = PlatformUnstructuredRepository(tmp_path / "documents.sqlite", writable=True)
    known_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    artifact = repository.put_artifact(
        b"%PDF previous release",
        ArtifactDescriptor(
            source_id="factset_earnings_insight_metrics",
            dataset_id="sp500_earnings_insight", fetched_at=known_at,
            media_type="application/pdf", retention="licensed_internal_research"))
    _seed_factset_release(
        repository, artifact_id=artifact.id, document_id="previous",
        version_id="previous@hash", report_date="2026-07-31",
        known_at=known_at, growth=0.077)
    failed_at = datetime(2026, 8, 3, tzinfo=timezone.utc)
    run_id = repository.begin_ingestion(
        source_id="factset_earnings_insight_metrics",
        dataset_id="sp500_earnings_insight", query_scope={"refresh": "weekly"})
    repository.finish_ingestion(
        run_id, status="unreachable", reason_codes={"unreachable": 1},
        at=failed_at)
    snapshot = DataProducts(
        structured_repository=repository,
        unstructured_repository=documents).earnings_insight_snapshot(
            as_of=datetime(2026, 8, 4, tzinfo=timezone.utc))
    assert snapshot.report.report_date == date(2026, 7, 31)
    assert snapshot.status.state == "stale"
    assert snapshot.status.latest_refresh_failure == "unreachable"
    assert "latest_refresh_failure:unreachable" in snapshot.status.warnings
    status = DataProducts(
        structured_repository=repository,
        unstructured_repository=documents).earnings_insight_status(
            as_of=datetime(2026, 8, 4, tzinfo=timezone.utc))
    assert status["source_validation"]["registered"] is True
    assert status["release_check"]["index_core"]["latest_passing"][
        "version_id"] == "previous@hash"
    assert status["release_check"]["sector_core"]["latest_passing"] is None
    assert status["health"]["latest_attempt_failure"] == "unreachable"
    assert status["snapshot_manifest"]["report_date"] == "2026-07-31"
    assert status["lineage"]["evidence_counts"]["text_span"] > 0


def test_failed_refresh_with_no_release_is_typed_unavailable(tmp_path):
    repository = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog()
    documents = PlatformUnstructuredRepository(tmp_path / "documents.sqlite", writable=True)
    not_pdf = tmp_path / "EarningsInsight_090426.pdf"
    not_pdf.write_text("authentication challenge", encoding="utf-8")
    result = FactSetWeeklyPipeline(repository, documents).run(local_pdf=not_pdf)
    assert result["status"] == "not_pdf"
    assert result["index_core"]["status"] == "unavailable"
    snapshot = DataProducts(
        structured_repository=repository,
        unstructured_repository=documents).earnings_insight_snapshot()
    assert snapshot.status.state == "unavailable"
    assert snapshot.status.latest_refresh_failure == "not_pdf"


def test_factset_schedule_requires_order_and_matching_timezone():
    from types import SimpleNamespace
    from ats.runtime.scheduler import _validate_factset_schedule

    valid = SimpleNamespace(
        factset_refresh_at="08:10", weekly_review_at="08:50",
        factset_refresh_tz="Asia/Shanghai", weekly_review_tz="Asia/Shanghai")
    _validate_factset_schedule(valid)
    with pytest.raises(ValueError, match="precede"):
        _validate_factset_schedule(SimpleNamespace(
            **{**valid.__dict__, "factset_refresh_at": "09:00"}))
    with pytest.raises(ValueError, match="must match"):
        _validate_factset_schedule(SimpleNamespace(
            **{**valid.__dict__, "factset_refresh_tz": "UTC"}))


def _write_synthetic_factset_pdf(path):
    from pypdf import PdfWriter
    from pypdf.generic import (
        DecodedStreamObject, DictionaryObject, NameObject)

    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    texts = [
        "EARNINGS INSIGHT FactSet August 28, 2026 Key Metrics",
        "Table of Contents Earnings & Revenue Scorecard Earnings Growth Forward Estimates & Valuation",
        *[f"Synthetic acceptance page {number}" for number in range(3, 11)],
    ]
    for text_value in texts:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        stream = DecodedStreamObject()
        escaped = text_value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as handle:
        writer.write(handle)


def test_synthetic_pdf_fixture_and_corpus_skip_reason(tmp_path):
    from ats.data.acceptance.factset_earnings_insight import run_corpus
    from ats.data.sources.factset_earnings_insight import inspect_pdf

    corpus = tmp_path / "corpus"
    manifests = tmp_path / "manifests"
    corpus.mkdir()
    manifests.mkdir()
    pdf = corpus / "EarningsInsight_082826.pdf"
    _write_synthetic_factset_pdf(pdf)
    fetch = FactSetFetch(
        stable_url="local", final_url=pdf.as_uri(), status_code=200,
        etag="", last_modified="", mime_type="application/pdf",
        body=pdf.read_bytes(), fetched_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
    document = inspect_pdf(fetch)
    assert document.page_count == 10
    assert document.report_date == date(2026, 8, 28)
    (manifests / "082826.yaml").write_text(yaml.safe_dump({
        "report_id": "082826", "report_date": "2026-08-28",
        "pdf_sha256": document.pdf_hash, "text_sha256": document.text_hash,
        "page_count": 10, "expected_phase": "pre_reporting",
        "requires_chart_inventory": False,
        "revision_breadth": {"applicable": False}, "chart_pages": {},
        "sector_annotation_status": "pending", "expected_sector_cells": [],
    }), encoding="utf-8")
    result = run_corpus(manifests, corpus)
    assert result["summary"]["document_passed"] == 1
    assert result["reports"][0]["sector"]["reason"] == (
        "manual_sector_cell_annotations_incomplete")

    pdf.unlink()
    missing = run_corpus(manifests, corpus)
    assert missing["reports"][0]["status"] == "skipped"
    assert missing["reports"][0]["skip_reason"] == "licensed_artifact_unavailable"


def test_consumer_flag_rollback_preserves_all_governed_data(
        tmp_path, monkeypatch, projected_factset_pdf):
    import ats.data.pipelines.factset_earnings_insight as pipeline_module

    local_pdf = tmp_path / "EarningsInsight_082826.pdf"
    local_pdf.write_bytes(b"%PDF rollback fixture")
    monkeypatch.setattr(
        pipeline_module, "inspect_pdf", lambda _source: projected_factset_pdf)
    known_at = datetime(2026, 9, 2, tzinfo=timezone.utc)
    structured = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    documents = PlatformUnstructuredRepository(tmp_path / "documents.sqlite", writable=True)
    projected = FactSetDocumentPipeline(
        structured, documents, clock=lambda: known_at).run(local_pdf=local_pdf)
    _seed_factset_release(
        structured, artifact_id=projected["artifact_id"],
        document_id=projected["document_id"],
        version_id=projected["document_version_id"],
        report_date="2026-08-28", known_at=known_at, growth=0.52)
    run_id = structured.begin_ingestion(
        source_id="factset_earnings_insight_metrics",
        dataset_id="sp500_earnings_insight", query_scope={"rollback": True})
    structured.save_candidate(
        candidate_id="rollback-candidate", run_id=run_id,
        source_id="factset_earnings_insight_metrics",
        dataset_id="sp500_earnings_insight", entity_id="SP500",
        provider_field="eps_yoy_growth", metric_id="earnings.eps.yoy_growth",
        period="2026Q2", value=0.52, unit="ratio", currency="",
        status="accepted", reason_codes=[], artifact_id=projected["artifact_id"],
        raw={"version_id": projected["document_version_id"]}, at=known_at)

    def inventory():
        return {
            "artifacts": structured.conn.execute(
                "SELECT COUNT(*) FROM structured_artifacts").fetchone()[0],
            "documents": documents.conn.execute(
                "SELECT COUNT(*) FROM data_documents").fetchone()[0],
            "versions": documents.conn.execute(
                "SELECT COUNT(*) FROM data_document_versions").fetchone()[0],
            "candidates": structured.conn.execute(
                "SELECT COUNT(*) FROM structured_candidates").fetchone()[0],
            "evidence": structured.conn.execute(
                "SELECT COUNT(*) FROM structured_evidence_links").fetchone()[0],
            "observations": structured.conn.execute(
                "SELECT COUNT(*) FROM structured_observations").fetchone()[0],
            "releases": structured.conn.execute(
                "SELECT COUNT(*) FROM structured_release_manifests").fetchone()[0],
        }

    before = inventory()
    monkeypatch.setenv("ATS_STRUCTURED_MACRO_FACTSET_MODE", "platform")
    assert read_mode("macro_factset") == "platform"
    monkeypatch.setenv("ATS_STRUCTURED_MACRO_FACTSET_MODE", "shadow")
    assert read_mode("macro_factset") == "shadow"
    assert inventory() == before
    assert all(value > 0 for value in before.values())


def test_chart_registry_uses_title_anchors_and_phase_not_page_number():
    registry = ChartRegistry()
    matched = registry.classify(
        title="Q2 2026 Earnings & Revenue Scorecard",
        legend=("Above", "In-Line", "Below"),
        phase=ReportPhase.SUBSTANTIALLY_COMPLETE)
    assert matched is not None
    assert matched.chart_id == "earnings_revenue_scorecard"
    assert registry.classify(
        title="", legend=("Above", "In-Line", "Below"),
        phase=ReportPhase.SUBSTANTIALLY_COMPLETE) is None
    assert registry.classify(
        title="Q2 2026 Earnings & Revenue Scorecard",
        legend=("Above", "In-Line", "Below"),
        phase=ReportPhase.PRE_REPORTING) is None


def test_missing_local_ocr_is_an_explicit_non_throwing_status(monkeypatch):
    monkeypatch.setattr(
        LocalOCRAdapter, "discover",
        staticmethod(lambda: OCRDependencyStatus(
            available=False, missing=("tesseract", "Pillow"))))
    result = LocalOCRAdapter().extract(b"not-decoded", media_type="image/png")
    assert result.status == OCRStatus.EXTRACTOR_UNAVAILABLE
    assert result.missing_dependencies == ["tesseract", "Pillow"]
    assert "unavailable" in result.reason


def test_local_ocr_discovers_explicit_binary_outside_path(monkeypatch, tmp_path):
    import ats.data.sources.factset_earnings_charts as chart_module

    binary = tmp_path / "tesseract"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("ATS_TESSERACT_PATH", str(binary))
    monkeypatch.setattr(chart_module.shutil, "which", lambda _name: None)

    assert chart_module._tesseract_command() == str(binary)
    assert LocalOCRAdapter.discover().available is True


def _chart_run():
    return FactSetExtractionRun(
        run_id="chart-run", document_id="doc", version_id="doc@chart",
        report_date=date(2026, 8, 28),
        known_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        phase=ReportPhase.SUBSTANTIALLY_COMPLETE, template_status="recognized")


def _sector_table(definition, *, shares=False):
    labels = [
        "Energy", "Materials", "Industrials", "Consumer Discretionary",
        "Consumer Staples", "Healthcare", "Financials", "Technology",
        "Communication Services", "Utilities", "Real Estate",
    ]
    cells = []
    for row_number, label in enumerate(labels):
        for column_number, column in enumerate(definition.expected_columns):
            if shares:
                values = [0.7, 0.3] if len(definition.expected_columns) == 2 else [1.0]
                value = values[column_number]
                unit, token = "ratio", f"{value * 100:.0f}%"
            else:
                value, unit, token = 18.0 + row_number / 10, "multiple", "18.0"
            cells.append(ChartCell(
                sector_label=label, column=column, raw_token=token,
                value=value, unit=unit,
                region=(column_number / 10, row_number / 20,
                        column_number / 10 + 0.05, row_number / 20 + 0.04)))
    return ChartTable(
        chart_id=definition.chart_id, title=definition.title_aliases[0],
        page_number=30,
        period=ReportPeriod(value="2026-08-28", basis="snapshot"),
        estimate_state=EstimateState.NOT_APPLICABLE, cells=cells)


def test_missing_ocr_skips_chart_without_blocking_index(monkeypatch):
    monkeypatch.setattr(
        LocalOCRAdapter, "discover",
        staticmethod(lambda: OCRDependencyStatus(False, ("Pillow",))))
    image = ChartImage(
        artifact_id="image", page_number=30, data=b"raw", media_type="image/png",
        title="Geographic Revenue Exposure",
        legend=("United States", "International"))
    result = extract_chart(
        image, phase=ReportPhase.SUBSTANTIALLY_COMPLETE,
        run=_chart_run(), table=None)
    assert result.status == OCRStatus.EXTRACTOR_UNAVAILABLE
    assert result.candidates == []
    assert result.index_processing_allowed is True


def test_chart_cells_emit_image_region_candidates(monkeypatch):
    import ats.data.sources.factset_earnings_charts as chart_module

    definition = next(item for item in ChartRegistry().definitions
                      if item.chart_id == "forward_pe")
    table = _sector_table(definition)
    image = ChartImage(
        artifact_id="image", page_number=30, data=b"png", media_type="image/png",
        title="Forward 12-Month P/E Ratio")
    monkeypatch.setattr(
        chart_module, "apply_layout_crop",
        lambda image, definition: ChartCropResult(
            status=OCRStatus.SUCCEEDED, data=b"crop", region=definition.crop))

    class _OCR:
        def extract(self, image, *, media_type):
            return OCRResult(status=OCRStatus.SUCCEEDED, tokens=[])

    result = extract_chart(
        image, phase=ReportPhase.SUBSTANTIALLY_COMPLETE,
        run=_chart_run(), table=table, ocr_adapter=_OCR())
    assert result.status == OCRStatus.SUCCEEDED
    assert len(result.candidates) == 11
    first = result.candidates[0]
    assert first.entity_id == "GICS_10"
    assert first.evidence[0].anchor_kind == "image_region"
    assert first.evidence[0].page_number == 30
    assert first.evidence[0].chart_id == "forward_pe"


def test_sector_aliases_and_exact_eleven_row_gate():
    assert normalize_sector_label("Health Care") == "GICS_35"
    assert normalize_sector_label("Healthcare") == "GICS_35"
    assert normalize_sector_label("Information Technology") == "GICS_45"
    assert len(set(GICS_ALIASES.values())) == 11
    definition = next(item for item in ChartRegistry().definitions
                      if item.chart_id == "forward_pe")
    table = _sector_table(definition)
    emitted = emit_chart_candidates(definition, table, _chart_run())
    valid = validate_sector_table(definition, table, emitted.candidates)
    assert valid.passed is True
    assert len(valid.entities) == 11

    incomplete = table.model_copy(deep=True)
    incomplete.cells = incomplete.cells[:-1]
    emitted = emit_chart_candidates(definition, incomplete, _chart_run())
    rejected = validate_sector_table(definition, incomplete, emitted.candidates)
    assert rejected.passed is False
    assert "sector_rows_incomplete" in rejected.reason_codes
    assert all(candidate.status == CandidateStatus.QUARANTINED
               for candidate in rejected.candidates)


def test_sector_table_composition_failure_quarantines_entire_table():
    definition = next(item for item in ChartRegistry().definitions
                      if item.chart_id == "geographic_revenue_exposure")
    table = _sector_table(definition, shares=True)
    table.cells[0].value = 0.8
    table.cells[1].value = 0.4
    emitted = emit_chart_candidates(definition, table, _chart_run())
    result = validate_sector_table(definition, table, emitted.candidates)
    assert result.passed is False
    assert "composition_total_mismatch:GICS_10" in result.reason_codes
    assert len(result.candidates) == 22
    assert all(candidate.status == CandidateStatus.QUARANTINED
               for candidate in result.candidates)


def test_scorecard_column_composition_and_count_reconciliation():
    definition = next(item for item in ChartRegistry().definitions
                      if item.chart_id == "earnings_revenue_scorecard")
    labels = [
        "Energy", "Materials", "Industrials", "Consumer Discretionary",
        "Consumer Staples", "Health Care", "Financials", "Information Technology",
        "Communication Services", "Utilities", "Real Estate",
    ]
    values = {
        "eps_above": 0.8, "eps_inline": 0.1, "eps_below": 0.1,
        "revenue_above": 0.7, "revenue_inline": 0.0, "revenue_below": 0.3,
    }
    cells = [ChartCell(
        sector_label=label, column=column, raw_token=f"{value:.0%}",
        value=value, unit="ratio", region=(0.1, 0.1, 0.2, 0.2))
        for label in labels for column, value in values.items()]
    table = ChartTable(
        chart_id=definition.chart_id, title=definition.title_aliases[0],
        page_number=17,
        period=ReportPeriod(value="2026Q2", basis="target_quarter"),
        estimate_state=EstimateState.ACTUAL, cells=cells,
        reported_total=10,
        reported_column_counts={"above": 8, "inline": 1, "below": 1})
    emitted = emit_chart_candidates(definition, table, _chart_run())
    assert validate_sector_table(definition, table, emitted.candidates).passed is True
    table.reported_total = 9
    rejected = validate_sector_table(definition, table, emitted.candidates)
    assert "scorecard_count_reconciliation_failed" in rejected.reason_codes
    assert all(candidate.status == CandidateStatus.QUARANTINED
               for candidate in rejected.candidates)


def test_sector_core_releases_independently_only_after_full_table_gate(
        tmp_path, projected_factset_pdf):
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    repository = SQLiteStructuredRepository(
        tmp_path / "structured.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog()
    artifact = repository.put_artifact(
        b"%PDF chart fixture", ArtifactDescriptor(
            source_id="factset_earnings_insight_metrics",
            dataset_id="sp500_earnings_insight", fetched_at=now,
            media_type="application/pdf", retention="licensed_internal_research"))
    definition = next(item for item in ChartRegistry().definitions
                      if item.chart_id == "forward_pe")
    table = _sector_table(definition)
    pipeline = FactSetSectorPipeline(
        repository, required_chart_ids={"forward_pe"})
    first = projected_factset_pdf.pages[0].text + (
        " with 97% of S&P 500 companies reporting actual results")
    contents = (
        "Table of Contents Earnings & Revenue Scorecard Earnings Growth "
        "Forward Estimates & Valuation")
    recognized_document = projected_factset_pdf.__class__(
        **{**projected_factset_pdf.__dict__, "pages": (
            PDFPage(1, first, 0, len(first), "Key Metrics"),
            PDFPage(2, contents, len(first) + 2,
                    len(first) + 2 + len(contents), "Table of Contents"),
        ), "text": f"{first}\n\n{contents}"})
    released = pipeline.run(
        recognized_document, tables=[table], document_id="doc",
        version_id="doc@sector-pass", artifact_id=artifact.id, known_at=now,
        annotated_cells_ok=True)
    assert released["quality"]["passed"] is True
    assert released["release_status"] == "platform"
    assert released["accepted"] == 11
    evidence = repository.evidence_links(
        observation_id=released["observation_ids"][0])
    assert evidence[0]["anchor_kind"] == "image_region"
    assert evidence[0]["chart_id"] == "forward_pe"

    incomplete = table.model_copy(deep=True)
    incomplete.cells = incomplete.cells[:-1]
    rejected = pipeline.run(
        recognized_document, tables=[incomplete], document_id="doc",
        version_id="doc@sector-fail", artifact_id=artifact.id, known_at=now,
        annotated_cells_ok=True)
    assert rejected["quality"]["passed"] is False
    assert rejected["release_status"] == "shadow"
    assert rejected["quarantined"] == 10
    manifest = repository.latest_release(
        dataset_id="sp500_earnings_insight", partition="sector_core",
        passed_only=False)
    assert manifest["version_id"] == "doc@sector-fail"
    assert manifest["passed"] is False
