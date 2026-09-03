"""Typed, deterministic text extraction for FactSet Earnings Insight reports."""

from __future__ import annotations

from datetime import date, datetime
from collections import Counter
from enum import StrEnum
import hashlib
import json
import math
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from .factset_earnings_insight import FactSetPDF, PDFPage


EXTRACTOR_VERSION = "factset-text-v1"


class ReportPhase(StrEnum):
    PRE_REPORTING = "pre_reporting"
    IN_PROGRESS = "in_progress"
    SUBSTANTIALLY_COMPLETE = "substantially_complete"
    UNKNOWN_TEMPLATE = "unknown_template"


class EstimateState(StrEnum):
    ESTIMATED = "estimated"
    BLENDED = "blended"
    ACTUAL = "actual"
    NOT_APPLICABLE = "not_applicable"


class MetricGroup(StrEnum):
    COVERAGE = "coverage"
    SCORECARD = "scorecard"
    SURPRISE = "surprise"
    GROWTH = "growth"
    MARGIN = "margin"
    GUIDANCE = "guidance"
    BOTTOM_UP_EPS = "bottom_up_eps"
    VALUATION = "valuation"
    GEOGRAPHY = "geography"
    RATINGS = "ratings"
    TARGET = "target"
    REVISION_BREADTH = "revision_breadth"


class CandidateStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    QUARANTINED = "quarantined"
    CONFLICT = "conflict"
    MISSING = "missing"


class ReportPeriod(BaseModel):
    value: str
    basis: str

    @field_validator("basis")
    @classmethod
    def known_basis(cls, value: str) -> str:
        if value not in {"target_quarter", "target_year", "snapshot"}:
            raise ValueError("unsupported FactSet period basis")
        return value


class FactSetEvidenceAnchor(BaseModel):
    document_id: str
    version_id: str
    anchor_kind: str = "text_span"
    page_number: int = Field(ge=1)
    char_start: int = Field(default=0, ge=0)
    char_end: int = Field(default=0, ge=0)
    chart_id: str = ""
    region: tuple[float, float, float, float] | None = None
    extraction_method: str = EXTRACTOR_VERSION

    @model_validator(mode="after")
    def valid_anchor(self):
        if self.anchor_kind == "text_span" and self.char_end <= self.char_start:
            raise ValueError("text evidence requires a non-empty span")
        if self.anchor_kind == "image_region" and self.region is None:
            raise ValueError("image evidence requires a normalized region")
        return self


class FactSetCandidate(BaseModel):
    candidate_id: str = ""
    run_id: str
    entity_id: str
    provider_field: str
    metric_id: str
    metric_group: MetricGroup
    period: ReportPeriod
    estimate_state: EstimateState
    unit: str
    raw_token: str
    raw_value: str
    value: float | int | None
    source_id: str = "factset_earnings_insight_metrics"
    dataset_id: str = "sp500_earnings_insight"
    report_date: date
    known_at: datetime
    extractor_version: str = EXTRACTOR_VERSION
    evidence: list[FactSetEvidenceAnchor] = Field(default_factory=list)
    status: CandidateStatus = CandidateStatus.PENDING
    reason_codes: list[str] = Field(default_factory=list)
    dimensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("known_at")
    @classmethod
    def aware_known_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("FactSet known_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def assign_identity(self):
        if not self.candidate_id:
            payload = "|".join((
                self.run_id, self.entity_id, self.metric_id, self.period.value,
                self.estimate_state.value, self.unit, self.raw_value,
                self.extractor_version,
            ))
            self.candidate_id = hashlib.sha256(payload.encode()).hexdigest()[:24]
        if self.value is None and not self.reason_codes:
            raise ValueError("missing candidates require a reason code")
        return self


class FactSetExtractionRun(BaseModel):
    run_id: str
    document_id: str
    version_id: str
    report_date: date
    known_at: datetime
    extractor_version: str = EXTRACTOR_VERSION
    phase: ReportPhase
    template_status: str
    reporting_coverage: float | None = None
    candidates: list[FactSetCandidate] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)

    @field_validator("known_at")
    @classmethod
    def aware_run_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("FactSet extraction known_at must be timezone-aware")
        return value


_QUARTER = re.compile(r"Q\s*([1-4])\s*(20\s*\d{2})", re.I)
_COVERAGE = re.compile(
    r"(?:with|Overall,?)\s+(\d{1,3})\s*%\s+of\s+(?:the\s+)?(?:companies\s+in\s+the\s+)?"
    r"S&P\s*500\s+(?:companies\s+)?(?:reporting|have\s+reported)\s+actual\s+results",
    re.I,
)
_CONTENTS_ANCHORS = (
    "Table of Contents", "Earnings & Revenue Scorecard", "Earnings Growth",
    "Forward Estimates & Valuation",
)
_WORD_NUMBER = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11,
}
_NUMBER_TOKEN = r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|\d{1,2})"


def normalize_quarter(token: str) -> str:
    match = _QUARTER.search(token)
    if not match:
        return ""
    year = "".join(match.group(2).split())
    return f"{year}Q{match.group(1)}"


def classify_document(document: FactSetPDF) -> tuple[ReportPhase, float | None, list[str]]:
    """Classify a known template and infer season phase from reporting coverage."""
    first = document.pages[0].text if document.pages else ""
    contents = document.pages[1].text if len(document.pages) > 1 else ""
    reasons: list[str] = []
    if "EARNINGS INSIGHT" not in first.upper() or "FactSet" not in first:
        reasons.append("title_anchor_missing")
    missing_contents = [anchor for anchor in _CONTENTS_ANCHORS if anchor not in contents]
    if len(missing_contents) > 1:
        reasons.append("table_of_contents_unrecognized")
    if reasons:
        return ReportPhase.UNKNOWN_TEMPLATE, None, reasons
    flat = re.sub(r"\s+", " ", first)
    match = _COVERAGE.search(flat)
    coverage = float(match.group(1)) / 100 if match else None
    if coverage is None or coverage < 0.05:
        phase = ReportPhase.PRE_REPORTING
    elif coverage < 0.90:
        phase = ReportPhase.IN_PROGRESS
    else:
        phase = ReportPhase.SUBSTANTIALLY_COMPLETE
    return phase, coverage, []


def new_extraction_run(document: FactSetPDF, *, document_id: str,
                       version_id: str, known_at: datetime,
                       extractor_version: str = EXTRACTOR_VERSION) -> FactSetExtractionRun:
    phase, coverage, reasons = classify_document(document)
    identity = "|".join((version_id, extractor_version, known_at.isoformat()))
    return FactSetExtractionRun(
        run_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
        document_id=document_id, version_id=version_id,
        report_date=document.report_date, known_at=known_at,
        extractor_version=extractor_version, phase=phase,
        template_status="quarantined" if reasons else "recognized",
        reporting_coverage=coverage, reason_codes=reasons)


def page_for_section(document: FactSetPDF, *anchors: str) -> PDFPage | None:
    for page in document.pages:
        text = re.sub(r"\s+", " ", page.text)
        if all(anchor.lower() in text.lower() for anchor in anchors):
            return page
    return None


class TextExtractorResult(BaseModel):
    name: str
    candidates: list[FactSetCandidate] = Field(default_factory=list)
    missing_metrics: dict[str, str] = Field(default_factory=dict)


class CandidateValidationReport(BaseModel):
    candidates: list[FactSetCandidate] = Field(default_factory=list)
    accepted: int = 0
    quarantined: int = 0
    missing: dict[str, dict[str, Any]] = Field(default_factory=dict)
    reason_counts: dict[str, int] = Field(default_factory=dict)


class CandidateConflictReport(BaseModel):
    candidates: list[FactSetCandidate] = Field(default_factory=list)
    merged_duplicates: int = 0
    conflict_identities: list[str] = Field(default_factory=list)


def _flat_page(page: PDFPage) -> tuple[str, list[int]]:
    """Collapse PDF whitespace while preserving a map to original character offsets."""
    output: list[str] = []
    offsets: list[int] = []
    pending_space = False
    for index, char in enumerate(page.text):
        if char.isspace():
            pending_space = bool(output)
            continue
        if pending_space:
            output.append(" ")
            offsets.append(index)
            pending_space = False
        output.append(char)
        offsets.append(index)
    return "".join(output), offsets


def _state(value: str, default: EstimateState) -> EstimateState:
    token = value.lower().strip()
    return EstimateState(token) if token in {item.value for item in EstimateState} else default


def _candidate(run: FactSetExtractionRun, page: PDFPage, flat: str,
               offsets: list[int], match: re.Match, *, provider_field: str,
               metric_id: str, group: MetricGroup, period: str, basis: str,
               state: EstimateState, unit: str, percent: bool = False,
               integer: bool = False, value_group: str = "value",
               dimensions: dict[str, Any] | None = None) -> FactSetCandidate:
    raw_value = re.sub(r"\s+", "", match.group(value_group))
    numeric = float(raw_value.replace(",", ""))
    value: float | int = int(numeric) if integer else numeric / (100 if percent else 1)
    start = page.char_start + offsets[match.start()]
    end_index = max(match.start(), match.end() - 1)
    end = page.char_start + offsets[end_index] + 1
    return FactSetCandidate(
        run_id=run.run_id, entity_id="SP500", provider_field=provider_field,
        metric_id=metric_id, metric_group=group,
        period=ReportPeriod(value=period, basis=basis), estimate_state=state,
        unit=unit, raw_token=match.group(0), raw_value=raw_value, value=value,
        report_date=run.report_date, known_at=run.known_at,
        extractor_version=run.extractor_version,
        evidence=[FactSetEvidenceAnchor(
            document_id=run.document_id, version_id=run.version_id,
            page_number=page.page_number, char_start=start, char_end=end,
            extraction_method=run.extractor_version)],
        dimensions=dimensions or {})


def _current_quarter(document: FactSetPDF) -> str:
    first = document.pages[0].text if document.pages else ""
    match = re.search(r"Earnings Growth:\s*For\s*(Q\s*[1-4]\s*20\s*\d{2})", first, re.I)
    return normalize_quarter(match.group(1)) if match else ""


def _number(token: str) -> int:
    normalized = token.strip().lower()
    return _WORD_NUMBER.get(normalized, int(normalized) if normalized.isdigit() else -1)


def _comparison_date(token: str, report_date: date) -> str:
    match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|"
        r"November|December)\s+(\d{1,2})(?:,\s*(20\d{2}))?", token, re.I)
    if not match:
        return ""
    month = datetime.strptime(match.group(1).title(), "%B").month
    year = int(match.group(3)) if match.group(3) else report_date.year
    candidate = date(year, month, int(match.group(2)))
    if not match.group(3) and candidate > report_date:
        candidate = date(year - 1, month, int(match.group(2)))
    return candidate.isoformat()


def _current_growth_state(document: FactSetPDF) -> EstimateState:
    for page in document.pages[:16]:
        flat = re.sub(r"\s+", " ", page.text)
        match = re.search(
            r"(?P<state>estimated|blended|actual)\s*\(year\s*-?\s*over\s*-?\s*year\)\s*"
            r"earnings\s+growth\s+rate(?:\s+for\s+the\s+S&P\s*500)?\s+is", flat, re.I)
        if match:
            return _state(match.group("state"), EstimateState.BLENDED)
    return EstimateState.BLENDED


def extract_revision_breadth(document: FactSetPDF,
                             run: FactSetExtractionRun) -> TextExtractorResult:
    """Extract explicit sector-improvement counts without inferring absent totals."""
    result = TextExtractorResult(name="revision_breadth")
    pattern = re.compile(
        rf"(?P<count>{_NUMBER_TOKEN})\s+of\s+(?:the\s+)?(?P<total>{_NUMBER_TOKEN})\s+"
        r"sectors\s+(?P<body>.{0,420}?)(?P<direction>upward|downward)\s+revisions",
        re.I,
    )
    quarter = _current_quarter(document)
    state = _current_growth_state(document)
    for page in document.pages[:16]:
        flat, offsets = _flat_page(page)
        for match in pattern.finditer(flat):
            body = match.group("body").lower()
            if "earnings" not in body or not any(
                    word in body for word in ("higher", "increase", "improve")):
                continue
            count = _number(match.group("count"))
            total = _number(match.group("total"))
            context = flat[max(0, match.start() - 260):match.end()]
            comparison = ""
            comparison_match = re.search(
                r"(?:compared\s+to|since)[^.;]{0,80}?"
                r"((?:January|February|March|April|May|June|July|August|September|"
                r"October|November|December)\s+\d{1,2}(?:,\s*20\d{2})?)",
                context, re.I)
            if comparison_match:
                comparison = _comparison_date(comparison_match.group(1), run.report_date)
            start = page.char_start + offsets[match.start()]
            end = page.char_start + offsets[match.end() - 1] + 1
            reasons = [] if comparison else ["comparison_date_unresolved"]
            result.candidates.append(FactSetCandidate(
                run_id=run.run_id, entity_id="SP500",
                provider_field="revision_improved_sector_count",
                metric_id="earnings.revision.improved_sector_count",
                metric_group=MetricGroup.REVISION_BREADTH,
                period=ReportPeriod(value=quarter, basis="target_quarter"),
                estimate_state=state, unit="count",
                raw_token=match.group(0), raw_value=match.group("count"), value=count,
                report_date=run.report_date, known_at=run.known_at,
                extractor_version=run.extractor_version,
                evidence=[FactSetEvidenceAnchor(
                    document_id=run.document_id, version_id=run.version_id,
                    page_number=page.page_number, char_start=start, char_end=end,
                    extraction_method=run.extractor_version)],
                reason_codes=reasons,
                dimensions={
                    "comparison_date": comparison,
                    "revision_direction": match.group("direction").lower(),
                    "sector_total": total,
                    "raw_sector_count_token": match.group("count"),
                    "raw_sector_total_token": match.group("total"),
                }))
    if not result.candidates:
        result.missing_metrics[
            "earnings.revision.improved_sector_count"] = "explicit_count_not_found"
    return result


def extract_scorecard(document: FactSetPDF, run: FactSetExtractionRun) -> TextExtractorResult:
    result = TextExtractorResult(name="scorecard")
    quarter = _current_quarter(document)
    specs = (
        ("reporting_coverage", "earnings.reporting.coverage", MetricGroup.COVERAGE,
         r"(?P<value>\d{1,3})\s*%\s+of\s+(?:the\s+)?(?:companies\s+in\s+the\s+)?S&P\s*500\s+(?:companies\s+)?(?:reporting|have\s+reported)\s+actual\s+results"),
        ("eps_above_estimate_share", "earnings.eps.above_estimate_share", MetricGroup.SCORECARD,
         r"(?P<value>\d{1,3})\s*%\s+(?:of\s+the\s+companies\s+)?have\s+reported\s+actual\s+EPS\s+above"),
        ("eps_inline_estimate_share", "earnings.eps.inline_estimate_share", MetricGroup.SCORECARD,
         r"(?P<value>\d{1,3})\s*%\s+(?:of\s+the\s+companies\s+)?have\s+reported\s+actual\s+EPS\s+equal"),
        ("eps_below_estimate_share", "earnings.eps.below_estimate_share", MetricGroup.SCORECARD,
         r"(?P<value>\d{1,3})\s*%\s+(?:of\s+the\s+companies\s+)?have\s+reported\s+actual\s+EPS\s+below"),
        ("revenue_above_estimate_share", "earnings.revenue.above_estimate_share", MetricGroup.SCORECARD,
         r"(?P<value>\d{1,3})\s*%\s+of\s+the\s+companies\s+have\s+reported\s+actual\s+revenues\s+above"),
        ("revenue_inline_estimate_share", "earnings.revenue.inline_estimate_share", MetricGroup.SCORECARD,
         r"(?P<value>\d{1,3})\s*%\s+of\s+the\s+companies\s+have\s+reported\s+actual\s+revenues\s+equal"),
        ("revenue_below_estimate_share", "earnings.revenue.below_estimate_share", MetricGroup.SCORECARD,
         r"(?P<value>\d{1,3})\s*%\s+of\s+the\s+companies\s+have\s+reported\s+actual\s+revenues\s+below"),
    )
    for field, metric, group, pattern in specs:
        found = False
        for page in document.pages[:10]:
            flat, offsets = _flat_page(page)
            match = re.search(pattern, flat, re.I)
            if match:
                result.candidates.append(_candidate(
                    run, page, flat, offsets, match, provider_field=field,
                    metric_id=metric, group=group, period=quarter,
                    basis="target_quarter", state=EstimateState.ACTUAL,
                    unit="ratio", percent=True))
                found = True
                break
        if not found:
            result.missing_metrics[metric] = "text_pattern_not_found"
    return result


def extract_growth_and_surprise(document: FactSetPDF,
                                run: FactSetExtractionRun) -> TextExtractorResult:
    result = TextExtractorResult(name="growth_and_surprise")
    quarter = _current_quarter(document)
    specs = (
        ("eps_yoy_growth", "earnings.eps.yoy_growth", MetricGroup.GROWTH,
         r"(?P<state>estimated|blended|actual)\s*\(year\s*-?\s*over\s*-?\s*year\)\s*earnings\s+growth\s+rate\s+(?:for\s+the\s+S&P\s*500\s+)?(?:for\s+Q\s*[1-4]\s*20\s*\d{2}\s+)?is\s+(?P<value>[+-]?\d+(?:\.\d+)?)\s*%"),
        ("revenue_yoy_growth", "earnings.revenue.yoy_growth", MetricGroup.GROWTH,
         r"(?P<state>estimated|blended|actual)\s*\(year\s*-?\s*over\s*-?\s*year\)\s*revenue\s+growth\s+rate\s+(?:for\s+the\s+S&P\s*500\s+)?(?:for\s+Q\s*[1-4]\s*20\s*\d{2}\s+)?is\s+(?P<value>[+-]?\d+(?:\.\d+)?)\s*%"),
        ("eps_surprise_pct", "earnings.eps.surprise_pct", MetricGroup.SURPRISE,
         r"companies\s+are\s+reporting\s+earnings\s+that\s+are\s+(?P<value>[+-]?\d+(?:\.\d+)?)\s*%\s+above\s+(?:expectations|estimates)"),
        ("revenue_surprise_pct", "earnings.revenue.surprise_pct", MetricGroup.SURPRISE,
         r"companies\s+are\s+reporting\s+revenues\s+that\s+are\s+(?P<value>[+-]?\d+(?:\.\d+)?)\s*%\s+above\s+(?:the\s+)?(?:expectations|estimates)"),
    )
    for field, metric, group, pattern in specs:
        found = False
        for page in document.pages[:16]:
            flat, offsets = _flat_page(page)
            match = re.search(pattern, flat, re.I)
            if not match:
                continue
            default = EstimateState.BLENDED if group == MetricGroup.GROWTH else EstimateState.ACTUAL
            state = _state(match.groupdict().get("state", ""), default)
            result.candidates.append(_candidate(
                run, page, flat, offsets, match, provider_field=field,
                metric_id=metric, group=group, period=quarter,
                basis="target_quarter", state=state, unit="ratio", percent=True))
            found = True
            break
        if not found:
            result.missing_metrics[metric] = "text_pattern_not_found"
    return result


def extract_margin(document: FactSetPDF, run: FactSetExtractionRun) -> TextExtractorResult:
    result = TextExtractorResult(name="margin")
    quarter = _current_quarter(document)
    patterns = (
        ("net_profit_margin", "earnings.net_profit_margin",
         r"(?P<state>estimated|blended|actual)\s+net\s+profit\s+margin\s+for\s+the\s+S&P\s*500\s+for\s+Q\s*[1-4]\s*20\s*\d{2}\s+is\s+(?P<value>\d+(?:\.\d+)?)\s*%"),
    )
    for field, metric, pattern in patterns:
        for page in document.pages[:16]:
            flat, offsets = _flat_page(page)
            match = re.search(pattern, flat, re.I)
            if match:
                result.candidates.append(_candidate(
                    run, page, flat, offsets, match, provider_field=field,
                    metric_id=metric, group=MetricGroup.MARGIN, period=quarter,
                    basis="target_quarter", state=_state(match.group("state"), EstimateState.BLENDED),
                    unit="ratio", percent=True))
                break
        else:
            result.missing_metrics[metric] = "text_pattern_not_found"
    return result


def extract_guidance(document: FactSetPDF, run: FactSetExtractionRun) -> TextExtractorResult:
    result = TextExtractorResult(name="guidance")
    joined = " ".join(re.sub(r"\s+", " ", page.text) for page in document.pages[:16])
    period_match = re.search(r"Earnings Guidance:\s*For\s*(Q\s*[1-4]\s*20\s*\d{2})", joined, re.I)
    period = normalize_quarter(period_match.group(1)) if period_match else ""
    specs = (
        ("guidance_negative_count", "earnings.guidance.negative_count", "negative"),
        ("guidance_positive_count", "earnings.guidance.positive_count", "positive"),
    )
    for field, metric, direction in specs:
        pattern = rf"(?P<value>\d+)\s+S&P\s*500\s+companies\s+have\s+issued\s+{direction}\s+EPS\s+guidance"
        for page in document.pages[:16]:
            flat, offsets = _flat_page(page)
            match = re.search(pattern, flat, re.I)
            if match:
                result.candidates.append(_candidate(
                    run, page, flat, offsets, match, provider_field=field,
                    metric_id=metric, group=MetricGroup.GUIDANCE, period=period,
                    basis="target_quarter", state=EstimateState.NOT_APPLICABLE,
                    unit="count", integer=True))
                break
        else:
            result.missing_metrics[metric] = "text_pattern_not_found"
    return result


def extract_valuation(document: FactSetPDF, run: FactSetExtractionRun) -> TextExtractorResult:
    result = TextExtractorResult(name="valuation")
    snapshot = run.report_date.isoformat()
    specs = (
        ("forward_pe", "valuation.forward_pe", r"forward\s+12\s*-?\s*month\s+P/E\s+ratio\s+for\s+the\s+S&P\s*500\s+is\s+(?P<value>\d+(?:\.\d+)?)"),
        ("trailing_pe", "valuation.trailing_pe", r"trailing\s+12\s*-?\s*month\s+P/E\s+ratio\s+is\s+(?P<value>\d+(?:\.\d+)?)"),
    )
    for field, metric, pattern in specs:
        for page in document.pages[:16]:
            flat, offsets = _flat_page(page)
            match = re.search(pattern, flat, re.I)
            if match:
                result.candidates.append(_candidate(
                    run, page, flat, offsets, match, provider_field=field,
                    metric_id=metric, group=MetricGroup.VALUATION, period=snapshot,
                    basis="snapshot", state=EstimateState.NOT_APPLICABLE,
                    unit="multiple"))
                break
        else:
            result.missing_metrics[metric] = "text_pattern_not_found"
    # Reference averages are accepted only from the corresponding valuation sentence.
    for horizon, prefix in (("forward", "forward"), ("trailing", "trailing")):
        for page in document.pages[:16]:
            flat, offsets = _flat_page(page)
            block = re.search(
                rf"{horizon}\s+12\s*-?\s*month\s+P/E\s+ratio(?:\s+for\s+the\s+S&P\s*500)?\s+is\s+"
                r"\d+(?:\.\d+)?[.,]?\s+(?:(?:This\s+P/E\s+ratio|which)\s+)?is\s+"
                r"(?:below|above)\s+(?:the\s+)?5\s*-\s*year\s+average(?:\s+of)?\s*\(?(?P<five>\d+(?:\.\d+)?)\)?\s+"
                r"(?:but|and)\s+above\s+(?:the\s+)?10\s*-\s*year\s+average(?:\s+of)?\s*\(?(?P<ten>\d+(?:\.\d+)?)\)?",
                flat, re.I)
            if block:
                for field, metric, value_group in (
                    (f"{prefix}_pe_average_5y", f"valuation.{prefix}_pe.average_5y", "five"),
                    (f"{prefix}_pe_average_10y", f"valuation.{prefix}_pe.average_10y", "ten"),
                ):
                    result.candidates.append(_candidate(
                        run, page, flat, offsets, block, provider_field=field,
                        metric_id=metric, group=MetricGroup.VALUATION, period=snapshot,
                        basis="snapshot", state=EstimateState.NOT_APPLICABLE,
                        unit="multiple", value_group=value_group))
                break
    for metric in ("valuation.forward_pe.average_5y", "valuation.forward_pe.average_10y",
                   "valuation.trailing_pe.average_5y", "valuation.trailing_pe.average_10y"):
        if not any(item.metric_id == metric for item in result.candidates):
            result.missing_metrics[metric] = "text_pattern_not_found"
    return result


def extract_ratings_and_target(document: FactSetPDF,
                               run: FactSetExtractionRun) -> TextExtractorResult:
    result = TextExtractorResult(name="ratings_and_target")
    snapshot = run.report_date.isoformat()
    rating_pattern = re.compile(
        r"(?P<buy>\d+(?:\.\d+)?)\s*%\s+are\s+Buy\s+ratings,\s+"
        r"(?P<hold>\d+(?:\.\d+)?)\s*%\s+are\s+Hold\s+ratings,\s+and\s+"
        r"(?P<sell>\d+(?:\.\d+)?)\s*%\s+are\s+Sell\s+ratings", re.I)
    for page in document.pages[:16]:
        flat, offsets = _flat_page(page)
        match = rating_pattern.search(flat)
        if match:
            for field, metric, value_group in (
                ("rating_buy_share", "consensus.rating.buy_share", "buy"),
                ("rating_hold_share", "consensus.rating.hold_share", "hold"),
                ("rating_sell_share", "consensus.rating.sell_share", "sell"),
            ):
                result.candidates.append(_candidate(
                    run, page, flat, offsets, match, provider_field=field,
                    metric_id=metric, group=MetricGroup.RATINGS, period=snapshot,
                    basis="snapshot", state=EstimateState.NOT_APPLICABLE,
                    unit="ratio", percent=True, value_group=value_group))
            break
    target_pattern = re.compile(
        r"bottom\s*-?\s*up\s+target\s+price\s+for\s+the\s+S&P\s*500\s+is\s+"
        r"[\d,.]+,\s+which\s+is\s+(?P<value>[+-]?\d+(?:\.\d+)?)\s*%\s+above", re.I)
    for page in document.pages[:16]:
        flat, offsets = _flat_page(page)
        match = target_pattern.search(flat)
        if match:
            result.candidates.append(_candidate(
                run, page, flat, offsets, match, provider_field="target_upside",
                metric_id="consensus.target.upside", group=MetricGroup.TARGET,
                period=snapshot, basis="snapshot", state=EstimateState.NOT_APPLICABLE,
                unit="ratio", percent=True))
            break
    for metric in ("consensus.rating.buy_share", "consensus.rating.hold_share",
                   "consensus.rating.sell_share", "consensus.target.upside"):
        if not any(item.metric_id == metric for item in result.candidates):
            result.missing_metrics[metric] = "text_pattern_not_found"
    return result


def extract_chart_only_index_fields(document: FactSetPDF,
                                    run: FactSetExtractionRun) -> TextExtractorResult:
    """Declare V1 index fields whose authoritative value is normally raster-only."""
    metrics = {
        "earnings.bottom_up_eps": "chart_extractor_required",
        "revenue.geographic.us_share": "chart_extractor_required",
        "revenue.geographic.international_share": "chart_extractor_required",
        "earnings.margin.increase_share": "chart_or_composition_derivation_required",
        "earnings.margin.unchanged_share": "chart_or_composition_derivation_required",
        "earnings.margin.decrease_share": "chart_or_composition_derivation_required",
    }
    return TextExtractorResult(name="chart_only_index_fields", missing_metrics=metrics)


def extract_index_text(document: FactSetPDF, *, document_id: str, version_id: str,
                       known_at: datetime,
                       extractor_version: str = EXTRACTOR_VERSION) -> FactSetExtractionRun:
    """Run isolated section extractors; an unrecognized template emits no candidates."""
    run = new_extraction_run(
        document, document_id=document_id, version_id=version_id,
        known_at=known_at, extractor_version=extractor_version)
    if run.phase == ReportPhase.UNKNOWN_TEMPLATE:
        return run
    results = (
        extract_scorecard(document, run),
        extract_growth_and_surprise(document, run),
        extract_revision_breadth(document, run),
        extract_margin(document, run),
        extract_guidance(document, run),
        extract_valuation(document, run),
        extract_ratings_and_target(document, run),
        extract_chart_only_index_fields(document, run),
    )
    run.candidates = [candidate for result in results for candidate in result.candidates]
    for result in results:
        run.reason_codes.extend(
            f"{result.name}:{metric}:{reason}"
            for metric, reason in result.missing_metrics.items())
    return run


_BOUNDED_SHARES = {
    "earnings.reporting.coverage",
    "earnings.eps.above_estimate_share",
    "earnings.eps.inline_estimate_share",
    "earnings.eps.below_estimate_share",
    "earnings.revenue.above_estimate_share",
    "earnings.revenue.inline_estimate_share",
    "earnings.revenue.below_estimate_share",
    "earnings.margin.increase_share",
    "earnings.margin.unchanged_share",
    "earnings.margin.decrease_share",
    "revenue.geographic.us_share",
    "revenue.geographic.international_share",
    "consensus.rating.buy_share",
    "consensus.rating.hold_share",
    "consensus.rating.sell_share",
}
_RATIO_METRICS = _BOUNDED_SHARES | {
    "earnings.eps.surprise_pct", "earnings.revenue.surprise_pct",
    "earnings.eps.yoy_growth", "earnings.revenue.yoy_growth",
    "earnings.net_profit_margin", "consensus.target.upside",
}
_COUNT_METRICS = {
    "earnings.guidance.positive_count", "earnings.guidance.negative_count",
    "earnings.revision.improved_sector_count",
}
_MULTIPLE_METRICS = {
    "valuation.forward_pe", "valuation.trailing_pe",
    "valuation.forward_pe.average_5y", "valuation.forward_pe.average_10y",
    "valuation.trailing_pe.average_5y", "valuation.trailing_pe.average_10y",
}
_COMPOSITIONS = {
    "eps_scorecard": {
        "earnings.eps.above_estimate_share", "earnings.eps.inline_estimate_share",
        "earnings.eps.below_estimate_share",
    },
    "revenue_scorecard": {
        "earnings.revenue.above_estimate_share",
        "earnings.revenue.inline_estimate_share",
        "earnings.revenue.below_estimate_share",
    },
    "margin_breadth": {
        "earnings.margin.increase_share", "earnings.margin.unchanged_share",
        "earnings.margin.decrease_share",
    },
    "geography": {"revenue.geographic.us_share", "revenue.geographic.international_share"},
    "ratings": {
        "consensus.rating.buy_share", "consensus.rating.hold_share",
        "consensus.rating.sell_share",
    },
}


def _add_reason(candidate: FactSetCandidate, reason: str) -> None:
    if reason not in candidate.reason_codes:
        candidate.reason_codes.append(reason)
    candidate.status = CandidateStatus.QUARANTINED


def validate_index_candidates(run: FactSetExtractionRun, *,
                              composition_tolerance: float = 0.01) -> CandidateValidationReport:
    """Apply deterministic candidate and composition validation without imputation."""
    candidates = [candidate.model_copy(deep=True) for candidate in run.candidates]
    missing: dict[str, dict[str, Any]] = {}
    for reason in run.reason_codes:
        parts = reason.split(":", 2)
        if len(parts) == 3:
            missing[parts[1]] = {"value": None, "reason": parts[2]}
    for candidate in candidates:
        if candidate.value is None:
            _add_reason(candidate, "value_missing")
            continue
        if not candidate.period.value:
            _add_reason(candidate, "period_unresolved")
        if candidate.entity_id != "SP500":
            _add_reason(candidate, "entity_unresolved")
        value = float(candidate.value)
        if not math.isfinite(value):
            _add_reason(candidate, "value_not_finite")
        if candidate.metric_id in _RATIO_METRICS and candidate.unit != "ratio":
            _add_reason(candidate, "unit_mismatch")
        if candidate.metric_id in _COUNT_METRICS:
            if candidate.unit != "count":
                _add_reason(candidate, "unit_mismatch")
            if not value.is_integer() or value < 0:
                _add_reason(candidate, "count_out_of_range")
        if candidate.metric_id in _MULTIPLE_METRICS:
            if candidate.unit != "multiple":
                _add_reason(candidate, "unit_mismatch")
            if not 0 <= value <= 200:
                _add_reason(candidate, "multiple_out_of_range")
        if candidate.metric_id in _BOUNDED_SHARES and not 0 <= value <= 1:
            _add_reason(candidate, "ratio_out_of_range")
        if candidate.metric_id in _RATIO_METRICS - _BOUNDED_SHARES and not -5 <= value <= 5:
            _add_reason(candidate, "ratio_out_of_range")
        if candidate.metric_id == "earnings.revision.improved_sector_count":
            required = ("comparison_date", "revision_direction", "sector_total")
            if any(candidate.dimensions.get(key) in {None, ""} for key in required):
                _add_reason(candidate, "required_dimension_missing")
            total = candidate.dimensions.get("sector_total")
            if not isinstance(total, int) or total <= 0 or value > total:
                _add_reason(candidate, "revision_breadth_out_of_range")
        if not candidate.evidence:
            _add_reason(candidate, "evidence_missing")

    by_metric = {candidate.metric_id: candidate for candidate in candidates}
    for group, expected in _COMPOSITIONS.items():
        present = expected & set(by_metric)
        if not present:
            continue
        if present != expected:
            for metric in present:
                _add_reason(by_metric[metric], f"{group}_composition_incomplete")
            continue
        total = sum(float(by_metric[metric].value) for metric in expected)
        if not math.isclose(total, 1.0, rel_tol=0, abs_tol=composition_tolerance + 1e-12):
            for metric in expected:
                _add_reason(by_metric[metric], f"{group}_composition_total_mismatch")

    for candidate in candidates:
        if candidate.status == CandidateStatus.PENDING:
            candidate.status = CandidateStatus.ACCEPTED
    counts = Counter(
        reason for candidate in candidates for reason in candidate.reason_codes)
    return CandidateValidationReport(
        candidates=candidates,
        accepted=sum(c.status == CandidateStatus.ACCEPTED for c in candidates),
        quarantined=sum(c.status == CandidateStatus.QUARANTINED for c in candidates),
        missing=missing, reason_counts=dict(counts))


def observation_identity(candidate: FactSetCandidate) -> str:
    semantic_dimensions = {
        key: value for key, value in candidate.dimensions.items()
        if not key.startswith("raw_") and key != "supporting_raw_tokens"
    }
    payload = {
        "entity_id": candidate.entity_id,
        "metric_id": candidate.metric_id,
        "period": candidate.period.model_dump(mode="json"),
        "estimate_state": candidate.estimate_state.value,
        "unit": candidate.unit,
        "source_id": candidate.source_id,
        "dimensions": semantic_dimensions,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def merge_candidate_evidence(
        candidates: list[FactSetCandidate]) -> CandidateConflictReport:
    """Merge identical values and quarantine every side of source-internal conflicts."""
    grouped: dict[str, list[FactSetCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(observation_identity(candidate), []).append(
            candidate.model_copy(deep=True))
    output: list[FactSetCandidate] = []
    merged = 0
    conflicts: list[str] = []
    for identity, rows in grouped.items():
        values = {
            json.dumps(row.value, sort_keys=True, separators=(",", ":"))
            for row in rows
        }
        if len(values) > 1:
            conflicts.append(identity)
            for row in rows:
                if "source_internal_conflict" not in row.reason_codes:
                    row.reason_codes.append("source_internal_conflict")
                row.status = CandidateStatus.CONFLICT
                output.append(row)
            continue
        base = rows[0]
        evidence_seen: set[str] = set()
        evidence: list[FactSetEvidenceAnchor] = []
        for row in rows:
            for anchor in row.evidence:
                key = json.dumps(anchor.model_dump(mode="json"), sort_keys=True)
                if key not in evidence_seen:
                    evidence_seen.add(key)
                    evidence.append(anchor)
            for reason in row.reason_codes:
                if reason not in base.reason_codes:
                    base.reason_codes.append(reason)
            if row.status == CandidateStatus.QUARANTINED:
                base.status = CandidateStatus.QUARANTINED
        raw_tokens = list(dict.fromkeys(row.raw_token for row in rows))
        if len(raw_tokens) > 1:
            base.dimensions["supporting_raw_tokens"] = raw_tokens
        base.evidence = evidence
        output.append(base)
        merged += len(rows) - 1
    return CandidateConflictReport(
        candidates=output, merged_duplicates=merged,
        conflict_identities=conflicts)


__all__ = [
    "CandidateConflictReport", "CandidateStatus", "CandidateValidationReport",
    "EstimateState",
    "EXTRACTOR_VERSION", "FactSetCandidate",
    "FactSetEvidenceAnchor", "FactSetExtractionRun", "MetricGroup", "ReportPeriod",
    "ReportPhase", "TextExtractorResult", "classify_document", "extract_guidance",
    "extract_growth_and_surprise", "extract_index_text", "extract_margin",
    "extract_ratings_and_target", "extract_scorecard", "extract_valuation",
    "extract_revision_breadth",
    "merge_candidate_evidence", "new_extraction_run", "normalize_quarter",
    "observation_identity", "page_for_section",
    "validate_index_candidates",
]
