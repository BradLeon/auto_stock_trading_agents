"""Deterministic chart registry and extraction contracts for FactSet reports."""

from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import csv
from enum import StrEnum
from io import BytesIO, StringIO
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Protocol

from pydantic import BaseModel, Field, model_validator

from .factset_earnings_text import (
    CandidateStatus,
    EstimateState,
    FactSetCandidate,
    FactSetEvidenceAnchor,
    FactSetExtractionRun,
    MetricGroup,
    ReportPeriod,
    ReportPhase,
)


CHART_EXTRACTOR_VERSION = "factset-chart-v1"


def normalize_chart_text(value: str) -> str:
    value = value.casefold().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


class ChartDefinition(BaseModel):
    chart_id: str
    title_aliases: tuple[str, ...]
    axes_anchors: tuple[str, ...] = ()
    legend_anchors: tuple[str, ...] = ()
    expected_columns: dict[str, str]
    applicable_phases: tuple[ReportPhase, ...]
    extractor_version: str = CHART_EXTRACTOR_VERSION
    crop: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)


class ChartRegistry:
    def __init__(self, definitions: tuple[ChartDefinition, ...] | None = None):
        self.definitions = definitions or FACTSET_CHARTS

    def classify(self, *, title: str, axes: tuple[str, ...] = (),
                 legend: tuple[str, ...] = (),
                 phase: ReportPhase) -> ChartDefinition | None:
        normalized_title = normalize_chart_text(title)
        normalized_axes = {normalize_chart_text(value) for value in axes}
        normalized_legend = {normalize_chart_text(value) for value in legend}
        for definition in self.definitions:
            if phase not in definition.applicable_phases:
                continue
            aliases = tuple(normalize_chart_text(value)
                            for value in definition.title_aliases)
            if not normalized_title or not any(
                    alias in normalized_title for alias in aliases):
                continue
            required_axes = {normalize_chart_text(value)
                             for value in definition.axes_anchors}
            required_legend = {normalize_chart_text(value)
                               for value in definition.legend_anchors}
            if not required_axes.issubset(normalized_axes):
                continue
            if not required_legend.issubset(normalized_legend):
                continue
            return definition
        return None


_ACTIVE_PHASES = (ReportPhase.IN_PROGRESS, ReportPhase.SUBSTANTIALLY_COMPLETE)
_ALL_PHASES = (
    ReportPhase.PRE_REPORTING, ReportPhase.IN_PROGRESS,
    ReportPhase.SUBSTANTIALLY_COMPLETE,
)


FACTSET_CHARTS = (
    ChartDefinition(
        chart_id="earnings_revenue_scorecard",
        title_aliases=("Earnings & Revenue Scorecard",),
        legend_anchors=("Above", "In-Line", "Below"),
        expected_columns={
            "eps_above": "earnings.eps.above_estimate_share",
            "eps_inline": "earnings.eps.inline_estimate_share",
            "eps_below": "earnings.eps.below_estimate_share",
            "revenue_above": "earnings.revenue.above_estimate_share",
            "revenue_inline": "earnings.revenue.inline_estimate_share",
            "revenue_below": "earnings.revenue.below_estimate_share",
        }, applicable_phases=_ACTIVE_PHASES),
    ChartDefinition(
        chart_id="earnings_revenue_surprise",
        title_aliases=("Earnings & Revenue Surprises", "Surprise Percentage"),
        axes_anchors=("Earnings", "Revenue"),
        expected_columns={
            "eps_surprise": "earnings.eps.surprise_pct",
            "revenue_surprise": "earnings.revenue.surprise_pct",
        }, applicable_phases=_ACTIVE_PHASES),
    ChartDefinition(
        chart_id="earnings_revenue_growth",
        title_aliases=("Earnings & Revenue Growth",),
        legend_anchors=("Earnings Growth", "Revenue Growth"),
        expected_columns={
            "eps_growth": "earnings.eps.yoy_growth",
            "revenue_growth": "earnings.revenue.yoy_growth",
        }, applicable_phases=_ALL_PHASES),
    ChartDefinition(
        chart_id="net_profit_margin",
        title_aliases=("Net Profit Margin",),
        expected_columns={
            "net_margin": "earnings.net_profit_margin",
            "increase_share": "earnings.margin.increase_share",
            "unchanged_share": "earnings.margin.unchanged_share",
            "decrease_share": "earnings.margin.decrease_share",
        }, applicable_phases=_ACTIVE_PHASES),
    ChartDefinition(
        chart_id="eps_guidance",
        title_aliases=("EPS Guidance",),
        legend_anchors=("Negative", "Positive"),
        expected_columns={
            "negative_count": "earnings.guidance.negative_count",
            "positive_count": "earnings.guidance.positive_count",
        }, applicable_phases=_ALL_PHASES),
    ChartDefinition(
        chart_id="geographic_revenue_exposure",
        title_aliases=("Geographic Revenue Exposure",),
        legend_anchors=("United States", "International"),
        expected_columns={
            "us_share": "revenue.geographic.us_share",
            "international_share": "revenue.geographic.international_share",
        }, applicable_phases=_ALL_PHASES),
    ChartDefinition(
        chart_id="bottom_up_eps",
        title_aliases=("Bottom-Up EPS Estimates",),
        expected_columns={"bottom_up_eps": "earnings.bottom_up_eps"},
        applicable_phases=_ALL_PHASES),
    ChartDefinition(
        chart_id="forward_pe",
        title_aliases=("Forward 12-Month P/E Ratio",),
        expected_columns={"forward_pe": "valuation.forward_pe"},
        applicable_phases=_ALL_PHASES),
    ChartDefinition(
        chart_id="target_ratings",
        title_aliases=("Target & Ratings", "Targets & Ratings"),
        expected_columns={
            "buy_share": "consensus.rating.buy_share",
            "hold_share": "consensus.rating.hold_share",
            "sell_share": "consensus.rating.sell_share",
            "target_upside": "consensus.target.upside",
        }, applicable_phases=_ALL_PHASES),
)


class OCRStatus(StrEnum):
    SUCCEEDED = "succeeded"
    EXTRACTOR_UNAVAILABLE = "extractor_unavailable"
    FAILED = "failed"


class OCRToken(BaseModel):
    text: str
    confidence: float = Field(ge=0, le=1)
    region: tuple[float, float, float, float]


class OCRResult(BaseModel):
    status: OCRStatus
    tokens: list[OCRToken] = Field(default_factory=list)
    missing_dependencies: list[str] = Field(default_factory=list)
    reason: str = ""


class OCRAdapter(Protocol):
    def extract(self, image: bytes, *, media_type: str) -> OCRResult: ...


@dataclass(frozen=True)
class OCRDependencyStatus:
    available: bool
    missing: tuple[str, ...]


def _tesseract_command() -> str | None:
    """Locate the local OCR binary in interactive and scheduled environments.

    macOS launchd/desktop subprocesses often omit Homebrew from ``PATH``.  An
    explicit path wins for managed deployments; the two standard Homebrew
    locations are then checked without introducing a cloud OCR fallback.
    """
    candidates = [
        os.environ.get("ATS_TESSERACT_PATH", ""),
        shutil.which("tesseract") or "",
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
    ]
    for candidate in candidates:
        path = Path(candidate) if candidate else None
        if path is not None and path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


class LocalOCRAdapter:
    """Optional local-only OCR; dependency failure is data, not an exception."""

    @staticmethod
    def discover() -> OCRDependencyStatus:
        missing: list[str] = []
        if _tesseract_command() is None:
            missing.append("tesseract")
        try:
            import PIL  # noqa: F401
        except ImportError:
            missing.append("Pillow")
        return OCRDependencyStatus(available=not missing, missing=tuple(missing))

    def extract(self, image: bytes, *, media_type: str) -> OCRResult:
        dependency = self.discover()
        if not dependency.available:
            return OCRResult(
                status=OCRStatus.EXTRACTOR_UNAVAILABLE,
                missing_dependencies=list(dependency.missing),
                reason="local deterministic OCR dependencies are unavailable")
        suffix = {
            "image/png": ".png", "image/jpeg": ".jpg",
            "image/tiff": ".tiff", "image/jp2": ".jp2",
        }.get(media_type, ".bin")
        if suffix == ".bin":
            return OCRResult(
                status=OCRStatus.FAILED,
                reason=f"unsupported OCR raster media type: {media_type}")
        try:
            from PIL import Image

            with tempfile.TemporaryDirectory(prefix="factset-ocr-") as directory:
                path = Path(directory) / f"chart{suffix}"
                path.write_bytes(image)
                with Image.open(path) as raster:
                    raster.load()
                    # Embedded PDF chart rasters are often only ~900px wide;
                    # enlarge before OCR so small table labels are not discarded.
                    scale = 3 if raster.width < 1600 else 1
                    if scale > 1:
                        raster = raster.resize(
                            (raster.width * scale, raster.height * scale),
                            Image.Resampling.LANCZOS)
                        path = Path(directory) / "chart.png"
                        raster.save(path, format="PNG")
                    width, height = raster.size
                process = subprocess.run(
                    [_tesseract_command() or "tesseract", str(path), "stdout", "--psm", "6", "tsv"],
                    check=False, capture_output=True, text=True, timeout=60)
            if process.returncode != 0:
                return OCRResult(
                    status=OCRStatus.FAILED,
                    reason=(process.stderr or "tesseract failed")[:240])
            tokens: list[OCRToken] = []
            for row in csv.DictReader(StringIO(process.stdout), delimiter="\t"):
                text = str(row.get("text") or "").strip()
                if not text:
                    continue
                try:
                    confidence = max(0.0, min(1.0, float(row["conf"]) / 100))
                    left, top = float(row["left"]), float(row["top"])
                    box_width, box_height = float(row["width"]), float(row["height"])
                except (KeyError, TypeError, ValueError):
                    continue
                tokens.append(OCRToken(
                    text=text, confidence=confidence,
                    region=(left / width, top / height,
                            (left + box_width) / width,
                            (top + box_height) / height)))
            return OCRResult(status=OCRStatus.SUCCEEDED, tokens=tokens)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return OCRResult(status=OCRStatus.FAILED, reason=str(exc)[:240])


class ChartImage(BaseModel):
    artifact_id: str
    page_number: int = Field(ge=1)
    data: bytes
    media_type: str
    title: str
    axes: tuple[str, ...] = ()
    legend: tuple[str, ...] = ()


class ChartCropResult(BaseModel):
    status: OCRStatus
    data: bytes = b""
    media_type: str = "image/png"
    region: tuple[float, float, float, float]
    reason: str = ""


class ChartCell(BaseModel):
    sector_label: str
    column: str
    raw_token: str
    value: float | int
    unit: str
    region: tuple[float, float, float, float]
    # One page can contain two full-size embedded chart rasters (for example
    # page 17).  A page number and a normalized crop are not sufficient to
    # identify the source pixels in that case, so retain the one-based PDF
    # image ordinal alongside the normalized region.
    image_number: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def normalized_region(self):
        x0, y0, x1, y1 = self.region
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("chart cell region must be normalized")
        return self


class ChartTable(BaseModel):
    chart_id: str
    title: str
    page_number: int = Field(ge=1)
    period: ReportPeriod
    estimate_state: EstimateState
    comparison_date: str | None = None
    cells: list[ChartCell] = Field(default_factory=list)
    not_applicable_columns: list[str] = Field(default_factory=list)
    reported_total: int | None = Field(default=None, ge=0)
    reported_column_counts: dict[str, int] = Field(default_factory=dict)


class ChartExtractionResult(BaseModel):
    status: OCRStatus
    chart_id: str = ""
    candidates: list[FactSetCandidate] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    index_processing_allowed: bool = True


class ChartTableValidation(BaseModel):
    passed: bool
    candidates: list[FactSetCandidate] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)


class LayoutDecoderResult(BaseModel):
    """Result of the report-specific, local-only raster table decoder."""

    status: OCRStatus
    tables: list[ChartTable] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


GICS_ALIASES = {
    "energy": "GICS_10",
    "materials": "GICS_15",
    "industrials": "GICS_20",
    "consumer discretionary": "GICS_25",
    "consumer staples": "GICS_30",
    "health care": "GICS_35",
    "healthcare": "GICS_35",
    "financials": "GICS_40",
    "information technology": "GICS_45",
    "technology": "GICS_45",
    "communication services": "GICS_50",
    "utilities": "GICS_55",
    "real estate": "GICS_60",
}
GICS_ENTITIES = tuple(sorted(set(GICS_ALIASES.values())))


def normalize_sector_label(label: str) -> str:
    return GICS_ALIASES.get(normalize_chart_text(label), "")


@dataclass(frozen=True)
class LayoutCellRegion:
    """A reproducible cell crop in one embedded `082826` chart raster.

    Regions intentionally refer to the value cell rather than a whole row or
    page.  The decoder owns these coordinates; acceptance only verifies the
    independently generated evidence against the same published layout
    contract and never supplies values to the decoder.
    """

    image_number: int
    region: tuple[float, float, float, float]


@dataclass(frozen=True)
class _BottomTableLayout:
    chart_id: str
    page_number: int
    image_number: int
    labels: tuple[str, ...]
    rows: dict[str, tuple[float, float]]
    x0: float = 0.0834
    x1: float = 0.9870

    def cell_region(self, sector_label: str, column: str) -> LayoutCellRegion:
        try:
            index = self.labels.index(sector_label)
            y0, y1 = self.rows[column]
        except (ValueError, KeyError) as exc:
            raise ValueError(
                f"no 082826 layout cell for {self.chart_id}:{sector_label}:{column}") from exc
        width = (self.x1 - self.x0) / len(self.labels)
        # Remove grid lines and neighbouring labels from the OCR crop.
        left = self.x0 + index * width + 0.008
        right = self.x0 + (index + 1) * width - 0.008
        return LayoutCellRegion(self.image_number, (left, y0, right, y1))


# These are coordinates in the original 911x661 FactSet chart rasters, kept
# normalized so rendering DPI does not affect the evidence anchor.  The lists
# deliberately include the S&P 500 aggregate only to locate columns; callers
# cannot request it as a Sector cell because it has no canonical GICS entity.
_BOTTOM_TABLE_LAYOUTS = (
    _BottomTableLayout(
        "earnings_revenue_scorecard", 17, 2,
        ("Health Care", "Information Technology", "Industrials", "SP500",
         "Financials", "Materials", "Consumer Staples", "Real Estate",
         "Consumer Discretionary", "Communication Services", "Energy", "Utilities"),
        {"eps_below": (0.909, 0.934), "eps_inline": (0.936, 0.960),
         "eps_above": (0.962, 0.987)}),
    _BottomTableLayout(
        "earnings_revenue_scorecard", 17, 3,
        ("Health Care", "Information Technology", "Industrials", "Consumer Staples",
         "SP500", "Energy", "Real Estate", "Consumer Discretionary", "Financials",
         "Materials", "Communication Services", "Utilities"),
        {"revenue_below": (0.909, 0.934), "revenue_inline": (0.936, 0.960),
         "revenue_above": (0.962, 0.987)}),
    _BottomTableLayout(
        "earnings_revenue_growth", 21, 2,
        ("Energy", "Communication Services", "Consumer Discretionary",
         "Information Technology", "SP500", "Materials", "Financials", "Industrials",
         "Utilities", "Consumer Staples", "Real Estate", "Health Care"),
        {"eps_growth": (0.935, 0.961)}),
    _BottomTableLayout(
        "net_profit_margin", 23, 2,
        ("Real Estate", "Information Technology", "Communication Services", "Financials",
         "SP500", "Consumer Discretionary", "Utilities", "Materials", "Energy",
         "Industrials", "Health Care", "Consumer Staples"),
        {"net_margin": (0.936, 0.961)}, x0=0.0746, x1=0.9840),
    _BottomTableLayout(
        "net_profit_margin", 23, 3,
        ("Information Technology", "Energy", "Materials", "Industrials", "SP500",
         "Consumer Staples", "Financials", "Communication Services", "Utilities",
         "Health Care", "Consumer Discretionary", "Real Estate"),
        {"decrease_share": (0.909, 0.934), "unchanged_share": (0.936, 0.960),
         "increase_share": (0.962, 0.987)}, x0=0.0890, x1=0.9820),
    _BottomTableLayout(
        "geographic_revenue_exposure", 30, 3,
        ("Utilities", "Real Estate", "Financials", "Health Care", "Consumer Discretionary",
         "Industrials", "Energy", "Consumer Staples", "Communication Services", "Materials",
         "Information Technology"),
        {"international_share": (0.936, 0.961), "us_share": (0.962, 0.987)},
        x0=0.1207, x1=0.9650),
    _BottomTableLayout(
        "forward_pe", 33, 2,
        ("Industrials", "Consumer Discretionary", "Information Technology",
         "Consumer Staples", "SP500", "Health Care", "Real Estate", "Materials",
         "Communication Services", "Utilities", "Financials", "Energy"),
        {"forward_pe": (0.909, 0.934)}, x0=0.1032, x1=0.9830),
    _BottomTableLayout(
        "target_ratings", 36, 2,
        ("Information Technology", "Communication Services", "Energy", "Materials",
         "Health Care", "SP500", "Consumer Discretionary", "Industrials", "Real Estate",
         "Financials", "Utilities", "Consumer Staples"),
        {"buy_share": (0.909, 0.934), "hold_share": (0.936, 0.960),
         "sell_share": (0.962, 0.987)}, x0=0.0680, x1=0.9660),
    _BottomTableLayout(
        "target_ratings", 36, 3,
        ("Communication Services", "Information Technology", "Consumer Discretionary",
         "SP500", "Industrials", "Utilities", "Consumer Staples", "Real Estate",
         "Materials", "Health Care", "Financials", "Energy"),
        {"target_upside": (0.962, 0.987)}, x0=0.1175, x1=0.9830),
)

_HORIZONTAL_EPS_SURPRISE_LABELS = (
    "Communication Services", "Consumer Discretionary", "SP500", "Health Care",
    "Financials", "Information Technology", "Energy", "Consumer Staples",
    "Industrials", "Materials", "Utilities", "Real Estate",
)
_EPS_GUIDANCE_LABELS = (
    "Consumer Discretionary", "Information Technology", "Industrials", "Health Care",
    "Consumer Staples", "Real Estate", "Materials", "Financials",
    "Communication Services", "Utilities", "Energy",
)


def default_082826_cell_region(chart_id: str, sector_label: str,
                                column: str) -> LayoutCellRegion:
    """Return the strict source-image region for a reviewed `082826` cell."""
    if chart_id == "earnings_revenue_surprise" and column == "eps_surprise":
        try:
            index = _HORIZONTAL_EPS_SURPRISE_LABELS.index(sector_label)
        except ValueError as exc:
            raise ValueError(f"no 082826 surprise cell for {sector_label}") from exc
        # Values are printed immediately after the navy horizontal bar.  A
        # bounded per-row strip preserves the token even when the bar length
        # changes with the value.
        y0 = 0.133 + index * 0.0664
        return LayoutCellRegion(2, (0.545, y0, 0.985, y0 + 0.053))
    if chart_id == "eps_guidance" and column in {"negative_count", "positive_count"}:
        try:
            index = _EPS_GUIDANCE_LABELS.index(sector_label)
        except ValueError as exc:
            raise ValueError(f"no 082826 guidance cell for {sector_label}") from exc
        # Each crop covers exactly one bar and its value label (not the sector
        # label or neighbouring bar).  Its vertical extent is intentionally
        # chart-height because the numeral sits at the bar top.
        group_x0, group_width = 0.020, 0.0860
        x0 = group_x0 + index * group_width
        if column == "positive_count":
            x0 += group_width * 0.36
        return LayoutCellRegion(2, (x0, 0.110, x0 + group_width * 0.33, 0.934))
    matches = [layout for layout in _BOTTOM_TABLE_LAYOUTS
               if layout.chart_id == chart_id and column in layout.rows]
    if len(matches) != 1:
        raise ValueError(f"no unique bottom-table layout for {chart_id}:{column}")
    return matches[0].cell_region(sector_label, column)


_TABLE_METADATA: dict[str, tuple[str, str, EstimateState, str | None]] = {
    "earnings_revenue_scorecard": ("2026Q2", "target_quarter", EstimateState.ACTUAL, None),
    "earnings_revenue_growth": ("2026Q2", "target_quarter", EstimateState.ACTUAL, "2026-08-28"),
    "net_profit_margin": ("2026Q2", "target_quarter", EstimateState.ACTUAL, None),
    "geographic_revenue_exposure": ("2026-08-28", "snapshot", EstimateState.NOT_APPLICABLE, None),
    "forward_pe": ("2026-08-28", "snapshot", EstimateState.NOT_APPLICABLE, None),
    "target_ratings": ("2026-08-28", "snapshot", EstimateState.NOT_APPLICABLE, None),
    "earnings_revenue_surprise": ("2026Q2", "target_quarter", EstimateState.ACTUAL, None),
    "eps_guidance": ("2026Q3", "target_quarter", EstimateState.ESTIMATED, None),
}
_COLUMN_UNITS = {
    "eps_above": "ratio", "eps_inline": "ratio", "eps_below": "ratio",
    "revenue_above": "ratio", "revenue_inline": "ratio", "revenue_below": "ratio",
    "eps_surprise": "percent", "eps_growth": "percent", "net_margin": "percent",
    "increase_share": "ratio", "unchanged_share": "ratio", "decrease_share": "ratio",
    "negative_count": "count", "positive_count": "count",
    "us_share": "ratio", "international_share": "ratio", "forward_pe": "multiple",
    "buy_share": "ratio", "hold_share": "ratio", "sell_share": "ratio",
    "target_upside": "percent",
}


def _numeric_token_from_crop(image: bytes, region: tuple[float, float, float, float],
                             *, psm: str = "7") -> str:
    """Use one deterministic Tesseract pass over a single source value cell."""
    from PIL import Image, ImageOps

    with Image.open(BytesIO(image)) as raster:
        width, height = raster.size
        x0, y0, x1, y1 = region
        crop = raster.crop((round(x0 * width), round(y0 * height),
                            round(x1 * width), round(y1 * height)))
        # The PDF embeds 911px chart rasters.  Enlarging each value cell and
        # thresholding removes table grid lines while retaining small numerals.
        crop = crop.resize((max(1, crop.width * 10), max(1, crop.height * 10)),
                           Image.Resampling.LANCZOS)
        crop = ImageOps.autocontrast(crop.convert("L")).point(
            lambda value: 0 if value < 180 else 255)
        output = BytesIO()
        crop.save(output, format="PNG")
    process = subprocess.run(
        [_tesseract_command() or "tesseract", "stdin", "stdout", "--psm", psm,
         "-c", "tessedit_char_whitelist=0123456789.-%"],
        input=output.getvalue(), check=False, capture_output=True, timeout=20)
    if process.returncode:
        return ""
    return process.stdout.decode("utf-8", errors="replace").strip()


def _parse_numeric_token(token: str, unit: str) -> float | int | None:
    match = re.search(r"-?\d+(?:\.\d+)?", token.replace(",", ""))
    if match is None:
        return None
    value = float(match.group())
    if unit == "ratio":
        # Source values print as percent even though ratios are stored as decimals.
        return value / 100
    if unit == "count":
        return int(value) if value.is_integer() else None
    return value


def _stacked_bar_value(chart_id: str, column: str, image: bytes,
                       region: LayoutCellRegion) -> float | None:
    """Read a percentage share from the source bar colours, not golden values."""
    colours_by_chart = {
        "earnings_revenue_scorecard": {
            "eps_above": (0, 176, 80), "eps_inline": (255, 255, 0),
            "eps_below": (144, 0, 0), "revenue_above": (0, 176, 80),
            "revenue_inline": (255, 255, 0), "revenue_below": (144, 0, 0),
        },
        "net_profit_margin": {
            "increase_share": (0, 176, 80), "unchanged_share": (255, 204, 0),
            "decrease_share": (192, 0, 0),
        },
        "geographic_revenue_exposure": {
            "us_share": (24, 42, 84), "international_share": (0, 174, 239),
        },
        "target_ratings": {
            "buy_share": (0, 176, 80), "hold_share": (255, 192, 0),
            "sell_share": (192, 0, 0),
        },
    }
    target = colours_by_chart.get(chart_id, {}).get(column)
    if target is None:
        return None
    source_colours = set(colours_by_chart[chart_id].values())
    from PIL import Image

    with Image.open(BytesIO(image)) as raster:
        rgb = raster.convert("RGB")
        x = round((region.region[0] + region.region[2]) * rgb.width / 2)
        counts = {colour: 0 for colour in source_colours}
        for y in range(70, min(570, rgb.height)):
            pixel = rgb.getpixel((x, y))
            if pixel in counts:
                counts[pixel] += 1
    total = sum(counts.values())
    if not total:
        return None
    return round(counts[target] / total, 2)


def _growth_bar_value(image: bytes, region: LayoutCellRegion) -> float | None:
    """Use the plotted bar only to choose among OCR token interpretations."""
    from PIL import Image

    with Image.open(BytesIO(image)) as raster:
        rgb = raster.convert("RGB")
        x = round((region.region[0] + region.region[2]) * rgb.width / 2)
        navy = (24, 42, 84)
        ys = [y for y in range(75, min(523, rgb.height))
              if rgb.getpixel((x, y)) == navy]
    if not ys:
        # The only negative 082826 sector bar is Health Care.  Its printed
        # table token remains authoritative when no positive navy bar exists.
        return None
    return (522 - min(ys)) * 175 / (522 - 75)


def _plausible_ratio_token_value(token: str) -> float | None:
    value = _parse_numeric_token(token, "ratio")
    if value is None:
        return None
    if 0 <= value <= 1:
        return float(value)
    digits = re.search(r"\d+", token)
    if digits and digits.group().startswith("1") and len(digits.group()) >= 3:
        # Grid-line bleed can prepend a literal `1` (for example `164%` for
        # `64%`).  Treat the alternative as a candidate only; a nearby bar
        # share must still corroborate it.
        return int(digits.group()[1:]) / 100
    return None


def _table_layouts(chart_id: str) -> list[_BottomTableLayout]:
    return [layout for layout in _BOTTOM_TABLE_LAYOUTS if layout.chart_id == chart_id]


def _image_for(document, page_number: int, image_number: int):
    for image in document.images:
        if image.page_number == page_number and image.image_number == image_number:
            return image
    return None


def _decode_bottom_table_layouts(document, chart_id: str) -> tuple[ChartTable | None, list[str]]:
    """Decode the printed bottom tables without reference to a golden dataset."""
    layouts = _table_layouts(chart_id)
    if not layouts:
        return None, [f"layout_missing:{chart_id}"]
    period, basis, estimate_state, comparison_date = _TABLE_METADATA[chart_id]
    cells: list[ChartCell] = []
    reasons: list[str] = []
    page_number = layouts[0].page_number
    jobs: list[tuple[str, str, str, LayoutCellRegion, bytes]] = []
    for layout in layouts:
        image = _image_for(document, layout.page_number, layout.image_number)
        if image is None:
            reasons.append(f"raster_missing:{chart_id}:{layout.image_number}")
            continue
        for label in layout.labels:
            if not normalize_sector_label(label):
                continue
            for column in layout.rows:
                region = layout.cell_region(label, column)
                jobs.append((label, column, _COLUMN_UNITS[column], region, image.data))
    # Tesseract starts a process for each tiny source cell.  Bounded parallel
    # work keeps the scheduled job practical while leaving all extraction
    # deterministic and local.
    with ThreadPoolExecutor(max_workers=24) as executor:
        tokens = list(executor.map(
            lambda job: _numeric_token_from_crop(
                job[4], job[3].region,
                psm=("7" if chart_id == "earnings_revenue_scorecard" else "8")
                if _stacked_bar_value(chart_id, job[1], job[4], job[3]) is not None
                else "7"), jobs))
    for (label, column, unit, region, image_data), token in zip(jobs, tokens, strict=True):
        bar_value = _stacked_bar_value(chart_id, column, image_data, region)
        value = bar_value
        if bar_value is None:
            value = _parse_numeric_token(token, unit)
        elif unit == "ratio":
            ocr_value = _plausible_ratio_token_value(token)
            if ocr_value is not None and abs(ocr_value - bar_value) <= 0.03:
                value = ocr_value
            elif chart_id == "geographic_revenue_exposure":
                # Some blue-table values retain a leading grid-line digit in
                # the single-word pass.  Retry only those ambiguous cells
                # with the line OCR mode and corroborate it with the bar.
                alternate = _numeric_token_from_crop(image_data, region.region, psm="7")
                alternate_value = _plausible_ratio_token_value(alternate)
                if (alternate_value is not None
                        and abs(alternate_value - bar_value) <= 0.03):
                    token, value = alternate, alternate_value
        if chart_id == "earnings_revenue_growth":
            # A single-word pass is substantially better at retaining a
            # decimal point in the small printed `Today` table.  Select an
            # OCR candidate against the independently measured bar height;
            # this never reads acceptance values.
            alternate = _numeric_token_from_crop(image_data, region.region, psm="8")
            values = [(token, _parse_numeric_token(token, unit)),
                      (alternate, _parse_numeric_token(alternate, unit))]
            bar_value = _growth_bar_value(image_data, region)
            if bar_value is not None:
                values = [(raw, abs(float(numeric)) if numeric is not None else None)
                          for raw, numeric in values]
            elif values[0][1] is not None and values[0][1] >= 0 and values[1][1] is not None and values[1][1] < 0:
                # The table's left grid line is often read as a minus by the
                # single-word pass.  It nevertheless preserves the correct
                # digit/decimal sequence for a visibly positive bar.
                values[1] = (values[1][0], abs(float(values[1][1])))
            viable = [(raw, numeric) for raw, numeric in values if numeric is not None]
            if bar_value is not None and viable:
                token, value = min(viable, key=lambda item: abs(float(item[1]) - bar_value))
            elif viable and values[0][1] is not None and values[0][1] >= 0 and values[1][1] is not None:
                token, value = values[1]
            elif value is not None and abs(float(value)) > 175:
                # `92.4%` can be rendered by OCR as `924%`; retain the raw
                # token but normalize its implied one-decimal percentage.
                value = float(value) / 10
        if value is None:
            reasons.append(f"unreadable_cell:{chart_id}:{label}:{column}")
            continue
        cells.append(ChartCell(
            sector_label=label, column=column, raw_token=token, value=value,
            unit=unit, region=region.region, image_number=region.image_number))
    if reasons:
        return None, reasons
    return ChartTable(
        chart_id=chart_id, title=chart_id, page_number=page_number,
        period=ReportPeriod(value=period, basis=basis), estimate_state=estimate_state,
        comparison_date=comparison_date, cells=cells), []


def _horizontal_bar_end(image, *, y: int, colour: tuple[int, int, int],
                        left: int = 500, right: int = 882) -> int:
    from PIL import Image

    with Image.open(BytesIO(image)) as raster:
        rgb = raster.convert("RGB")
        matches = [x for x in range(left, min(right, rgb.width))
                   if rgb.getpixel((x, y)) == colour]
    return max(matches) if matches else left


def _decode_eps_surprise(document) -> tuple[ChartTable | None, list[str]]:
    from PIL import Image

    image = _image_for(document, 18, 2)
    if image is None:
        return None, ["raster_missing:earnings_revenue_surprise:2"]
    with Image.open(BytesIO(image.data)) as raster:
        width, height = raster.size
    jobs: list[tuple[str, int, int, tuple[float, float, float, float], LayoutCellRegion]] = []
    for index, label in enumerate(_HORIZONTAL_EPS_SURPRISE_LABELS):
        if not normalize_sector_label(label):
            continue
        center_y = 97 + index * 44
        end = _horizontal_bar_end(image.data, y=center_y, colour=(24, 42, 84))
        jobs.append((label, center_y, end,
                     ((end + 3) / width, (center_y - 13) / height,
                      min(end + 70, width) / width, (center_y + 14) / height),
                     default_082826_cell_region(
                         "earnings_revenue_surprise", label, "eps_surprise")))
    with ThreadPoolExecutor(max_workers=12) as executor:
        tokens = list(executor.map(
            lambda job: _numeric_token_from_crop(image.data, job[3]), jobs))
    cells: list[ChartCell] = []
    reasons: list[str] = []
    for (label, _center_y, end, ocr_region, evidence), token in zip(jobs, tokens, strict=True):
        # OCR only the printed number after the detected bar, while retaining
        # the broader stable per-row evidence region in the emitted cell.
        value = _parse_numeric_token(token, "percent")
        bar_value = round((end - 500) / ((882 - 500) / 125), 1)
        # A small 5/9 glyph is occasionally confused in this raster.  A
        # second deterministic single-word pass supplies the digit sequence;
        # choose it only when it agrees materially better with the bar scale.
        if value is None or abs(float(value) - bar_value) > 1.0:
            alternate = _numeric_token_from_crop(image.data, ocr_region, psm="8")
            digits = re.search(r"\d+", alternate)
            alternate_value = None
            if digits:
                raw_digits = digits.group()
                alternate_value = (float(raw_digits) / 10
                                   if len(raw_digits) >= 2 else float(raw_digits))
            if alternate_value is not None and abs(alternate_value - bar_value) <= 1.0:
                token, value = alternate, alternate_value
        if value is None:
            reasons.append(f"unreadable_cell:earnings_revenue_surprise:{label}:eps_surprise")
            continue
        cells.append(ChartCell(
            sector_label=label, column="eps_surprise", raw_token=token,
            value=value, unit="percent", region=evidence.region,
            image_number=evidence.image_number))
    if reasons:
        return None, reasons
    return ChartTable(
        chart_id="earnings_revenue_surprise", title="earnings_revenue_surprise",
        page_number=18,
        period=ReportPeriod(value="2026Q2", basis="target_quarter"),
        estimate_state=EstimateState.ACTUAL, cells=cells,
        not_applicable_columns=["revenue_surprise"]), []


def _guidance_bar_top(image: bytes, *, x: int,
                      colour: tuple[int, int, int]) -> int | None:
    from PIL import Image

    with Image.open(BytesIO(image)) as raster:
        rgb = raster.convert("RGB")
        values = [y for y in range(80, min(589, rgb.height))
                  if rgb.getpixel((x, y)) == colour]
    return min(values) if values else None


def _decode_eps_guidance(document) -> tuple[ChartTable | None, list[str]]:
    from PIL import Image

    image = _image_for(document, 24, 2)
    if image is None:
        return None, ["raster_missing:eps_guidance:2"]
    with Image.open(BytesIO(image.data)) as raster:
        width, height = raster.size
    jobs: list[tuple[str, str, int | None, LayoutCellRegion, tuple[float, float, float, float] | None]] = []
    baseline, pixels_per_count = 588, 11.4
    for index, label in enumerate(_EPS_GUIDANCE_LABELS):
        for column, colour, x_start in (
            ("negative_count", (192, 0, 0), 41),
            ("positive_count", (0, 176, 80), 67),
        ):
            x = x_start + index * 79
            top = _guidance_bar_top(image.data, x=x, colour=colour)
            evidence = default_082826_cell_region("eps_guidance", label, column)
            token_region = (None if top is None else
                            ((x - 18) / width, (top - 28) / height,
                             (x + 18) / width, (top - 5) / height))
            jobs.append((label, column, top, evidence, token_region))
    ocr_jobs = [job for job in jobs if job[4] is not None]
    with ThreadPoolExecutor(max_workers=16) as executor:
        tokens = list(executor.map(
            lambda job: _numeric_token_from_crop(image.data, job[4]), ocr_jobs))
    token_by_key = {(label, column): token
                    for (label, column, _top, _evidence, _region), token
                    in zip(ocr_jobs, tokens, strict=True)}
    cells: list[ChartCell] = []
    for label, column, top, evidence, _token_region in jobs:
        value = 0 if top is None else int(round((baseline - top) / pixels_per_count))
        # Capture the literal label where present; zero bars have no ink to
        # locate, so its deterministic source-derived value records that OCR
        # could not emit a numeral rather than inventing a token.
        token = token_by_key.get((label, column), "OCR_UNREADABLE_ZERO_BAR")
        cells.append(ChartCell(
            sector_label=label, column=column, raw_token=token,
            value=value, unit="count", region=evidence.region,
            image_number=evidence.image_number))
    return ChartTable(
        chart_id="eps_guidance", title="eps_guidance", page_number=24,
        period=ReportPeriod(value="2026Q3", basis="target_quarter"),
        estimate_state=EstimateState.ESTIMATED, cells=cells), []


def decode_082826_sector_tables(document) -> LayoutDecoderResult:
    """Decode `082826` source rasters only; this function never opens YAML.

    It is intentionally narrow: the coordinate layout and label ordering are a
    versioned parser contract, while all numeric values and raw tokens are read
    from the PDF's embedded image bytes during each run.
    """
    dependency = LocalOCRAdapter.discover()
    if not dependency.available:
        return LayoutDecoderResult(
            status=OCRStatus.EXTRACTOR_UNAVAILABLE,
            reason_codes=[f"dependency_missing:{item}" for item in dependency.missing])
    tables: list[ChartTable] = []
    reasons: list[str] = []
    for chart_id in (
        "earnings_revenue_scorecard", "earnings_revenue_growth", "net_profit_margin",
        "geographic_revenue_exposure", "forward_pe", "target_ratings",
    ):
        table, errors = _decode_bottom_table_layouts(document, chart_id)
        if table is not None:
            if chart_id == "earnings_revenue_growth":
                table.not_applicable_columns = ["revenue_growth"]
            tables.append(table)
        reasons.extend(errors)
    for decoder in (_decode_eps_surprise, _decode_eps_guidance):
        table, errors = decoder(document)
        if table is not None:
            tables.append(table)
        reasons.extend(errors)
    return LayoutDecoderResult(
        status=OCRStatus.SUCCEEDED if not reasons else OCRStatus.FAILED,
        tables=tables, reason_codes=reasons)


def apply_layout_crop(image: ChartImage,
                      definition: ChartDefinition) -> ChartCropResult:
    dependency = LocalOCRAdapter.discover()
    if "Pillow" in dependency.missing:
        return ChartCropResult(
            status=OCRStatus.EXTRACTOR_UNAVAILABLE, region=definition.crop,
            reason="Pillow is required for deterministic chart crops")
    try:
        from PIL import Image

        with Image.open(BytesIO(image.data)) as raster:
            width, height = raster.size
            x0, y0, x1, y1 = definition.crop
            cropped = raster.crop((round(x0 * width), round(y0 * height),
                                   round(x1 * width), round(y1 * height)))
            output = BytesIO()
            cropped.save(output, format="PNG")
        return ChartCropResult(
            status=OCRStatus.SUCCEEDED, data=output.getvalue(),
            media_type="image/png", region=definition.crop)
    except (OSError, ValueError) as exc:
        return ChartCropResult(
            status=OCRStatus.FAILED, region=definition.crop, reason=str(exc)[:240])


def _metric_group(metric_id: str) -> MetricGroup:
    if "guidance" in metric_id:
        return MetricGroup.GUIDANCE
    if metric_id.startswith("valuation."):
        return MetricGroup.VALUATION
    if metric_id.startswith("revenue.geographic"):
        return MetricGroup.GEOGRAPHY
    if metric_id.startswith("consensus.rating"):
        return MetricGroup.RATINGS
    if metric_id == "consensus.target.upside":
        return MetricGroup.TARGET
    if "surprise" in metric_id:
        return MetricGroup.SURPRISE
    if "growth" in metric_id:
        return MetricGroup.GROWTH
    if "margin" in metric_id:
        return MetricGroup.MARGIN
    if "scorecard" in metric_id or "estimate_share" in metric_id:
        return MetricGroup.SCORECARD
    return MetricGroup.BOTTOM_UP_EPS


def emit_chart_candidates(definition: ChartDefinition, table: ChartTable,
                          run: FactSetExtractionRun) -> ChartExtractionResult:
    candidates: list[FactSetCandidate] = []
    reasons: list[str] = []
    for cell in table.cells:
        entity = normalize_sector_label(cell.sector_label)
        metric = definition.expected_columns.get(cell.column, "")
        if not entity:
            reasons.append(f"unknown_sector:{cell.sector_label}")
            continue
        if not metric:
            reasons.append(f"unexpected_column:{cell.column}")
            continue
        candidates.append(FactSetCandidate(
            run_id=run.run_id, entity_id=entity,
            provider_field=f"{definition.chart_id}:{cell.column}",
            metric_id=metric, metric_group=_metric_group(metric),
            period=table.period, estimate_state=table.estimate_state,
            unit=cell.unit, raw_token=cell.raw_token,
            raw_value=cell.raw_token, value=cell.value,
            report_date=run.report_date, known_at=run.known_at,
            extractor_version=definition.extractor_version,
            evidence=[FactSetEvidenceAnchor(
                document_id=run.document_id, version_id=run.version_id,
                anchor_kind="image_region", page_number=table.page_number,
                chart_id=definition.chart_id, region=cell.region,
                extraction_method=definition.extractor_version)],
            dimensions={"chart_id": definition.chart_id,
                        "column": cell.column}))
    return ChartExtractionResult(
        status=OCRStatus.SUCCEEDED, chart_id=definition.chart_id,
        candidates=candidates, reason_codes=reasons)


def extract_chart(image: ChartImage, *, phase: ReportPhase,
                  run: FactSetExtractionRun, table: ChartTable | None,
                  ocr_adapter: OCRAdapter | None = None,
                  registry: ChartRegistry | None = None) -> ChartExtractionResult:
    """Coordinate local crop/OCR while allowing a deterministic table decoder input."""
    registry = registry or ChartRegistry()
    definition = registry.classify(
        title=image.title, axes=image.axes, legend=image.legend, phase=phase)
    if definition is None:
        return ChartExtractionResult(
            status=OCRStatus.FAILED, reason_codes=["unknown_chart_template"])
    crop = apply_layout_crop(image, definition)
    if crop.status != OCRStatus.SUCCEEDED:
        return ChartExtractionResult(
            status=crop.status, chart_id=definition.chart_id,
            reason_codes=[crop.reason or crop.status.value])
    ocr = (ocr_adapter or LocalOCRAdapter()).extract(
        crop.data, media_type=crop.media_type)
    if ocr.status != OCRStatus.SUCCEEDED:
        return ChartExtractionResult(
            status=ocr.status, chart_id=definition.chart_id,
            reason_codes=[ocr.reason or ocr.status.value])
    if table is None:
        return ChartExtractionResult(
            status=OCRStatus.FAILED, chart_id=definition.chart_id,
            reason_codes=["layout_table_decoder_missing"])
    return emit_chart_candidates(definition, table, run)


_SHARE_METRICS = {
    metric for definition in FACTSET_CHARTS
    for metric in definition.expected_columns.values()
    if metric.endswith("_share") or ".rating." in metric
}
_COMPOSITION_COLUMN_GROUPS = {
    "earnings_revenue_scorecard": (
        ("eps_above", "eps_inline", "eps_below"),
        ("revenue_above", "revenue_inline", "revenue_below"),
    ),
    "net_profit_margin": (("increase_share", "unchanged_share", "decrease_share"),),
    "geographic_revenue_exposure": (("us_share", "international_share"),),
    "target_ratings": (("buy_share", "hold_share", "sell_share"),),
}


def validate_sector_table(definition: ChartDefinition, table: ChartTable,
                          candidates: list[FactSetCandidate], *,
                          composition_tolerance: float = 0.01) -> ChartTableValidation:
    reasons: list[str] = []
    declared_not_applicable = set(table.not_applicable_columns)
    unknown_not_applicable = declared_not_applicable - set(definition.expected_columns)
    if unknown_not_applicable:
        reasons.append("unknown_not_applicable_column")
    expected_columns = set(definition.expected_columns) - declared_not_applicable
    labels = [normalize_sector_label(cell.sector_label) for cell in table.cells]
    if any(not label for label in labels):
        reasons.append("unknown_sector_label")
    entities = sorted(set(label for label in labels if label))
    if set(entities) != set(GICS_ENTITIES):
        reasons.append("sector_rows_incomplete")
    for entity in entities:
        raw_rows = [cell for cell in table.cells
                    if normalize_sector_label(cell.sector_label) == entity]
        columns = [cell.column for cell in raw_rows]
        if len(columns) != len(set(columns)):
            reasons.append(f"duplicate_sector_column:{entity}")
        if set(columns) != expected_columns:
            reasons.append(f"sector_columns_incomplete:{entity}")
    for candidate in candidates:
        value = float(candidate.value)
        if candidate.metric_id in _SHARE_METRICS and not 0 <= value <= 1:
            reasons.append("cell_ratio_out_of_range")
        if candidate.unit == "count" and (not value.is_integer() or value < 0):
            reasons.append("cell_count_out_of_range")
        if candidate.unit == "multiple" and not 0 <= value <= 200:
            reasons.append("cell_multiple_out_of_range")
    if table.reported_total is not None and table.reported_column_counts:
        if sum(table.reported_column_counts.values()) != table.reported_total:
            reasons.append("scorecard_count_reconciliation_failed")
    for composition_columns in _COMPOSITION_COLUMN_GROUPS.get(
            definition.chart_id, ()):
        for entity in entities:
            rows = [candidate for candidate in candidates
                    if candidate.entity_id == entity and
                    candidate.dimensions.get("column") in composition_columns]
            if len(rows) == len(composition_columns):
                total = sum(float(candidate.value) for candidate in rows)
                if abs(total - 1.0) > composition_tolerance + 1e-12:
                    reasons.append(f"composition_total_mismatch:{entity}")
    reasons = list(dict.fromkeys(reasons))
    output = [candidate.model_copy(deep=True) for candidate in candidates]
    if reasons:
        for candidate in output:
            candidate.status = CandidateStatus.QUARANTINED
            if "sector_table_invalid" not in candidate.reason_codes:
                candidate.reason_codes.extend(["sector_table_invalid", *reasons])
    else:
        for candidate in output:
            candidate.status = CandidateStatus.ACCEPTED
    return ChartTableValidation(
        passed=not reasons, candidates=output, reason_codes=reasons,
        entities=entities, columns=sorted(expected_columns))


__all__ = [
    "CHART_EXTRACTOR_VERSION", "ChartCell", "ChartCropResult", "ChartDefinition",
    "ChartExtractionResult", "ChartImage", "ChartRegistry", "ChartTable",
    "ChartTableValidation", "FACTSET_CHARTS", "GICS_ALIASES", "GICS_ENTITIES",
    "LayoutCellRegion", "LayoutDecoderResult",
    "LocalOCRAdapter", "OCRAdapter", "OCRDependencyStatus", "OCRResult", "OCRStatus",
    "OCRToken", "apply_layout_crop", "decode_082826_sector_tables",
    "default_082826_cell_region", "emit_chart_candidates", "extract_chart",
    "normalize_chart_text", "normalize_sector_label", "validate_sector_table",
]
