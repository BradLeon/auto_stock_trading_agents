"""Manifest-driven FactSet corpus checks without checked-in licensed PDFs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from ..sources.factset_earnings_insight import FactSetFetch, inspect_pdf
from ..sources.factset_earnings_text import (
    classify_document,
    extract_index_text,
    extract_revision_breadth,
    merge_candidate_evidence,
    new_extraction_run,
    validate_index_candidates,
)
from ..sources.factset_earnings_charts import (
    ChartTable,
    decode_082826_sector_tables,
    default_082826_cell_region,
    normalize_sector_label,
)


class GoldenSectorCell(BaseModel):
    """A human-reviewed, source-located sector value for one report."""

    chart_id: str
    page_number: int = Field(ge=1)
    sector_label: str
    column: str
    value: float
    unit: str
    raw_token: str
    period: str
    period_basis: str
    estimate_state: str
    region: tuple[float, float, float, float]
    image_number: int = Field(ge=1)
    comparison_date: str | None = None
    review_status: str

    @model_validator(mode="after")
    def reviewed_source_cell(self):
        if not normalize_sector_label(self.sector_label):
            raise ValueError("golden sector cell must use a canonical GICS alias")
        if normalize_sector_label(self.sector_label) == "SP500":
            raise ValueError("SP500 aggregate cannot be a Sector golden cell")
        if self.review_status not in {"accepted", "corrected", "unreadable"}:
            raise ValueError("golden sector cell review status is invalid")
        x0, y0, x1, y1 = self.region
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("golden sector cell region must be normalized")
        area = (x1 - x0) * (y1 - y0)
        if area >= 0.15 or (x0 == 0 and x1 == 1) or (y0 == 0 and y1 == 1):
            raise ValueError("golden sector cell region must not be a placeholder")
        if not self.raw_token.strip():
            raise ValueError("golden sector cell requires the source raw token")
        return self

    @property
    def key(self) -> tuple[str, str, str, str, str, str]:
        return (self.chart_id, normalize_sector_label(self.sector_label), self.column,
                self.period, self.period_basis, self.unit)


def compare_golden_sector_cells(expected_cells: list[dict[str, Any]],
                                tables: list[ChartTable]) -> dict[str, Any]:
    """Compare independently decoded cells with reviewed golden cells.

    A complete/non-empty manifest is deliberately insufficient: every decoded
    cell must match a reviewed cell by identity, state and value.
    """
    golden = [GoldenSectorCell.model_validate(cell) for cell in expected_cells]
    unresolved = [cell for cell in golden
                  if cell.review_status not in {"accepted", "corrected"}]
    golden_by_key: dict[tuple[str, str, str, str, str, str], GoldenSectorCell] = {}
    duplicates: list[tuple] = []
    for cell in golden:
        if not normalize_sector_label(cell.sector_label):
            duplicates.append(("unknown_sector", cell.sector_label))
        elif cell.key in golden_by_key:
            duplicates.append(cell.key)
        else:
            golden_by_key[cell.key] = cell
    actual_by_key: dict[tuple, Any] = {}
    actual_duplicates: list[tuple] = []
    for table in tables:
        for cell in table.cells:
            key = (table.chart_id, normalize_sector_label(cell.sector_label), cell.column,
                   table.period.value, table.period.basis, cell.unit)
            if key in actual_by_key:
                actual_duplicates.append(key)
            else:
                actual_by_key[key] = (table, cell)
    missing = sorted(set(golden_by_key) - set(actual_by_key))
    extra = sorted(set(actual_by_key) - set(golden_by_key))
    mismatches = []
    for key in sorted(set(golden_by_key) & set(actual_by_key)):
        expected = golden_by_key[key]
        table, actual = actual_by_key[key]
        region_match = tuple(actual.region) == tuple(expected.region)
        if (abs(float(actual.value) - expected.value) > 1e-9
                or table.estimate_state.value != expected.estimate_state
                or table.page_number != expected.page_number
                or actual.image_number != expected.image_number
                or not region_match
                or table.comparison_date != expected.comparison_date):
            mismatches.append({"key": key, "expected": expected.value,
                               "actual": actual.value,
                               "expected_state": expected.estimate_state,
                               "actual_state": table.estimate_state.value,
                               "expected_page": expected.page_number,
                               "actual_page": table.page_number,
                               "expected_image_number": expected.image_number,
                               "actual_image_number": actual.image_number,
                               "expected_region": expected.region,
                               "actual_region": actual.region,
                               "expected_comparison_date": expected.comparison_date,
                               "actual_comparison_date": table.comparison_date})
    passed = bool(golden) and not (unresolved or duplicates or actual_duplicates
                                   or missing or extra or mismatches)
    return {"passed": passed, "expected": len(golden), "actual": len(actual_by_key),
            "missing": missing, "extra": extra, "mismatches": mismatches,
            "golden_duplicates": duplicates, "actual_duplicates": actual_duplicates,
            "unresolved": [cell.model_dump(mode="json") for cell in unresolved]}


def load_manifests(path: str | Path) -> list[dict[str, Any]]:
    root = Path(path)
    manifests = []
    for file in sorted(root.glob("*.yaml")):
        payload = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        payload["manifest_path"] = str(file)
        manifests.append(payload)
    return manifests


def expand_sector_golden_tables(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand the review-friendly YAML matrix into one auditable cell per value."""
    cells = list(manifest.get("expected_sector_cells") or [])
    for table in manifest.get("sector_golden_tables") or []:
        for column, payload in (table.get("columns") or {}).items():
            if isinstance(payload, dict) and "values" in payload:
                unit, values = payload["unit"], payload["values"]
            else:
                unit, values = table["unit"], payload
            for sector, value in values.items():
                layout = default_082826_cell_region(table["chart_id"], sector, column)
                cells.append({
                    "chart_id": table["chart_id"], "page_number": table["page_number"],
                    "sector_label": sector, "column": column, "value": value,
                    "unit": unit, "raw_token": _source_token(value, unit),
                    "period": table["period"], "period_basis": table["period_basis"],
                    "estimate_state": table["estimate_state"],
                    "region": layout.region, "image_number": layout.image_number,
                    "comparison_date": table.get("comparison_date"),
                    "review_status": table.get("review_status", "accepted"),
                })
    return cells


def _source_token(value: float | int, unit: str) -> str:
    """Render the token format visibly printed in the reviewed source chart."""
    numeric = float(value)
    if unit == "ratio":
        return f"{numeric * 100:.0f}%"
    if unit == "percent":
        return f"{numeric:.1f}%"
    if unit == "count":
        return str(int(numeric))
    if unit == "multiple":
        return f"{numeric:.1f}"
    return str(value)


def validate_golden_sector_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate strict source-located golden cells before any candidate compare."""
    cells = [GoldenSectorCell.model_validate(cell)
             for cell in expand_sector_golden_tables(manifest)]
    decisions = list((manifest.get("review_provenance") or {}).get("decisions") or [])
    decision_chart_ids = [str(row.get("chart_id") or "") for row in decisions]
    decision_cells = sum(int(row.get("confirmed_cells") or 0) for row in decisions)
    decisions_complete = (
        len(decisions) == 8
        and set(decision_chart_ids) == {cell.chart_id for cell in cells}
        and len(set(decision_chart_ids)) == len(decision_chart_ids)
        and decision_cells == len(cells)
        and all(row.get("decision") == "accepted" for row in decisions)
        and bool((manifest.get("review_provenance") or {}).get("recorded_at"))
        and bool((manifest.get("review_provenance") or {}).get("release_rule"))
    )
    return {
        "passed": (len(cells) == 231 and len({cell.key for cell in cells}) == len(cells)
                   and decisions_complete),
        "cell_count": len(cells),
        "unique_cell_count": len({cell.key for cell in cells}),
        "charts": sorted({cell.chart_id for cell in cells}),
        "review_decisions_complete": decisions_complete,
        "review_decision_cell_count": decision_cells,
    }


def run_corpus(manifest_dir: str | Path, corpus_dir: str | Path, *,
               report_ids: set[str] | None = None) -> dict:
    """Validate available internal files and explain every licensed-artifact skip."""
    reports = []
    for expected in load_manifests(manifest_dir):
        report_id = str(expected["report_id"])
        if report_ids is not None and report_id not in report_ids:
            continue
        path = Path(corpus_dir) / f"EarningsInsight_{report_id}.pdf"
        if not path.is_file():
            reports.append({
                "report_id": report_id, "status": "skipped",
                "skip_reason": "licensed_artifact_unavailable",
                "document": {"passed": False}, "index": {"passed": False},
                "sector": {"passed": False, "reason": "licensed_artifact_unavailable"},
            })
            continue
        body = path.read_bytes()
        known_at = datetime.now(timezone.utc)
        document = inspect_pdf(FactSetFetch(
            stable_url="local-acceptance", final_url=path.as_uri(), status_code=200,
            etag="", last_modified="", mime_type="application/pdf",
            body=body, fetched_at=known_at))
        phase, coverage, template_reasons = classify_document(document)
        document_checks = {
            "pdf_sha256": document.pdf_hash == expected["pdf_sha256"],
            "text_sha256": document.text_hash == expected["text_sha256"],
            "report_date": document.report_date.isoformat() == expected["report_date"],
            "page_count": document.page_count == int(expected["page_count"]),
            "phase": phase.value == expected["expected_phase"],
            "page_text_complete": len(document.pages) == document.page_count,
            "chart_inventory_present": (bool(document.images)
                                        if expected.get("requires_chart_inventory", True)
                                        else True),
        }
        extraction = extract_index_text(
            document, document_id=f"acceptance:{report_id}",
            version_id=f"acceptance:{report_id}@{document.pdf_hash[:12]}",
            known_at=known_at)
        merged = merge_candidate_evidence(extraction.candidates)
        extraction.candidates = merged.candidates
        validated = validate_index_candidates(extraction)
        revision = extract_revision_breadth(
            document, new_extraction_run(
                document, document_id=f"acceptance:{report_id}",
                version_id=f"acceptance:{report_id}@{document.pdf_hash[:12]}",
                known_at=known_at))
        revision_expected = expected.get("revision_breadth") or {}
        revision_values = [candidate.value for candidate in revision.candidates]
        revision_passed = (
            not revision_expected.get("applicable", False) and not revision_values
        ) or (
            revision_expected.get("applicable", False)
            and revision_values
            and all(value == revision_expected.get("count") for value in revision_values)
            and all(candidate.dimensions.get("sector_total") ==
                    revision_expected.get("sector_total")
                    for candidate in revision.candidates)
            and all(candidate.dimensions.get("comparison_date") ==
                    revision_expected.get("comparison_date")
                    for candidate in revision.candidates)
        )
        chart_pages = expected.get("chart_pages") or {}
        headings = {page.page_number: page.section_title for page in document.pages}
        chart_identity_checks = {
            chart_id: all(int(page) in headings for page in pages)
            for chart_id, pages in chart_pages.items()
        }
        annotated = expected.get("sector_annotation_status") == "complete"
        golden = validate_golden_sector_manifest(expected)
        expected_cells = expand_sector_golden_tables(expected)
        if report_id == "082826":
            decoder = decode_082826_sector_tables(document)
            comparison = (compare_golden_sector_cells(expected_cells, decoder.tables)
                          if decoder.status.value == "succeeded" else {
                              "passed": False, "expected": len(expected_cells),
                              "actual": 0, "reason_codes": decoder.reason_codes})
        else:
            # Historical reports remain optional reprocessing inputs.  The
            # 082826 layout decoder is deliberately not applied to them.
            decoder = None
            comparison = {"passed": False, "expected": len(expected_cells),
                          "actual": 0, "reason_codes": ["not_release_corpus"]}
        sector_passed = (annotated and golden["passed"] and comparison["passed"]
                         and all(chart_identity_checks.values()))
        reports.append({
            "report_id": report_id,
            "status": "passed" if all(document_checks.values()) else "failed",
            "document": {"passed": all(document_checks.values()),
                         "checks": document_checks,
                         "template_reasons": template_reasons,
                         "coverage": coverage},
            "index": {
                "passed": revision_passed,
                "candidate_count": len(validated.candidates),
                "accepted": validated.accepted,
                "quarantined": validated.quarantined,
                "missing": validated.missing,
                "conflicts": len(merged.conflict_identities),
                "revision_breadth_passed": revision_passed,
            },
            "sector": {
                "passed": sector_passed,
                "reason": ("accepted" if sector_passed else
                           "manual_sector_cell_annotations_incomplete"),
                "annotated_cells": len(expected_cells),
                "golden": golden,
                "decoder": ({
                    "status": decoder.status.value,
                    "table_count": len(decoder.tables),
                    "reason_codes": decoder.reason_codes,
                } if decoder is not None else {"status": "not_release_corpus"}),
                "comparison": comparison,
                "chart_identity_checks": chart_identity_checks,
            },
        })
    return {
        "reports": reports,
        "summary": {
            "total": len(reports),
            "skipped": sum(row["status"] == "skipped" for row in reports),
            "document_passed": sum(row["document"]["passed"] for row in reports),
            "index_revision_passed": sum(row["index"]["passed"] for row in reports),
            "sector_passed": sum(row["sector"]["passed"] for row in reports),
        },
    }


__all__ = ["GoldenSectorCell", "compare_golden_sector_cells",
           "expand_sector_golden_tables", "load_manifests", "run_corpus",
           "validate_golden_sector_manifest"]
