"""FactSet Earnings Insight acquisition and deterministic PDF inspection.

This module performs no filesystem writes.  It returns exact source bytes plus
HTTP provenance, then validates and inventories the PDF in memory.  Persistence
and release belong to the governed pipeline, not the source adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
import hashlib
from io import BytesIO
import re
from typing import Callable

from ..core.structured_models import (
    AdapterArtifact,
    AdapterBatch,
    AdapterFailure,
    FetchRequest,
    IngestionStatus,
)


STABLE_URL = "https://www.factset.com/earningsinsight"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REPORT_DATE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{1,2}),\s+(20\d{2})\b"
)
URL_DATE = re.compile(r"EarningsInsight_(\d{2})(\d{2})(\d{2})\.pdf", re.I)


class FactSetFailure(StrEnum):
    UNREACHABLE = "unreachable"
    UNAUTHORIZED = "unauthorized"
    NOT_PDF = "not_pdf"
    PARSE_FAILED = "parse_failed"


class FactSetSourceError(RuntimeError):
    def __init__(self, status: FactSetFailure, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class FactSetFetch:
    stable_url: str
    final_url: str
    status_code: int
    etag: str
    last_modified: str
    mime_type: str
    body: bytes
    fetched_at: datetime

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    @property
    def byte_count(self) -> int:
        return len(self.body)


@dataclass(frozen=True)
class PDFPage:
    page_number: int
    text: str
    char_start: int
    char_end: int
    section_title: str = ""


@dataclass(frozen=True)
class PDFImage:
    page_number: int
    image_number: int
    chart_id: str
    data: bytes
    media_type: str
    width: int = 0
    height: int = 0
    color_space: str = ""
    bits_per_component: int = 0
    region: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)


@dataclass(frozen=True)
class FactSetPDF:
    report_date: date
    title: str
    page_count: int
    pages: tuple[PDFPage, ...]
    images: tuple[PDFImage, ...]
    text: str
    text_hash: str
    pdf_hash: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _header(headers, name: str) -> str:
    return str((headers or {}).get(name) or (headers or {}).get(name.lower()) or "")


def fetch_report(*, url: str = STABLE_URL, client=None,
                 clock: Callable[[], datetime] = _now,
                 timeout_seconds: int = 60) -> FactSetFetch:
    """Follow the stable redirect and return source bytes without writing files."""
    import httpx

    transport = client or httpx
    try:
        response = transport.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=timeout_seconds,
            follow_redirects=True)
    except Exception as exc:
        if isinstance(exc, (TimeoutError, ConnectionError, httpx.TransportError)):
            raise FactSetSourceError(FactSetFailure.UNREACHABLE, str(exc)) from exc
        raise
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code in {401, 403}:
        raise FactSetSourceError(
            FactSetFailure.UNAUTHORIZED, f"FactSet returned HTTP {status_code}")
    if status_code >= 400:
        raise FactSetSourceError(
            FactSetFailure.UNREACHABLE, f"FactSet returned HTTP {status_code}")
    headers = getattr(response, "headers", {}) or {}
    mime = _header(headers, "content-type").split(";", 1)[0].strip().lower()
    body = bytes(getattr(response, "content", b"") or b"")
    if mime != "application/pdf" or not body.startswith(b"%PDF"):
        raise FactSetSourceError(
            FactSetFailure.NOT_PDF,
            f"expected application/pdf with %PDF signature, got {mime or 'unknown'}")
    fetched_at = clock()
    if fetched_at.tzinfo is None:
        raise ValueError("FactSet fetch clock must return a timezone-aware datetime")
    return FactSetFetch(
        stable_url=url,
        final_url=str(getattr(response, "url", url)),
        status_code=status_code,
        etag=_header(headers, "etag"),
        last_modified=_header(headers, "last-modified"),
        mime_type=mime,
        body=body,
        fetched_at=fetched_at.astimezone(timezone.utc),
    )


def _report_date(text: str, final_url: str = "") -> date | None:
    match = REPORT_DATE.search(text)
    if match:
        return datetime.strptime(" ".join(match.groups()), "%B %d %Y").date()
    match = URL_DATE.search(final_url)
    if match:
        month, day, year = (int(value) for value in match.groups())
        return date(2000 + year, month, day)
    return None


def _section_title(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    ignored = {"EARNINGS INSIGHT"}
    for line in lines:
        if not line or line in ignored or line.isdigit() or line.startswith("Copyright ©"):
            continue
        if REPORT_DATE.fullmatch(line):
            continue
        return line[:160]
    return ""


def _image_media(name: str) -> str:
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "jp2": "image/jp2", "tif": "image/tiff", "tiff": "image/tiff",
    }.get(suffix, "application/octet-stream")


def _raw_page_images(page, page_number: int) -> list[PDFImage]:
    """Inventory embedded image XObjects without requiring Pillow.

    Flate-decoded samples are retained as internal evidence with their raster
    metadata.  A later optional image adapter may convert them to PNG for OCR.
    """
    resources = page.get("/Resources") or {}
    objects = resources.get("/XObject") or {}
    out: list[PDFImage] = []
    for image_number, (name, reference) in enumerate(objects.items(), start=1):
        obj = reference.get_object()
        if str(obj.get("/Subtype")) != "/Image":
            continue
        width, height = int(obj.get("/Width") or 0), int(obj.get("/Height") or 0)
        # Every page carries a small FactSet logo. It is not a chart or evidence region.
        if width < 300 or height < 200:
            continue
        try:
            data = bytes(obj.get_data())
        except Exception:
            data = bytes(getattr(obj, "_data", b"") or b"")
        out.append(PDFImage(
            page_number=page_number,
            image_number=image_number,
            chart_id=f"page_{page_number}_{str(name).strip('/').lower()}",
            data=data,
            media_type="application/x-pdf-image-samples",
            width=width,
            height=height,
            color_space=str(obj.get("/ColorSpace") or ""),
            bits_per_component=int(obj.get("/BitsPerComponent") or 0)))
    return out


def inspect_pdf(source: FactSetFetch, *, min_pages: int = 10,
                max_pages: int = 80) -> FactSetPDF:
    """Validate title/date/page bounds and extract all page text/image inventory."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(source.body), strict=False)
        page_count = len(reader.pages)
    except Exception as exc:
        raise FactSetSourceError(FactSetFailure.PARSE_FAILED, str(exc)) from exc
    if not min_pages <= page_count <= max_pages:
        raise FactSetSourceError(
            FactSetFailure.PARSE_FAILED,
            f"unexpected FactSet report page count: {page_count}")

    pages: list[PDFPage] = []
    images: list[PDFImage] = []
    offset = 0
    try:
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            start = offset
            end = start + len(text)
            pages.append(PDFPage(
                page_number=page_number, text=text, char_start=start, char_end=end,
                section_title=_section_title(text)))
            offset = end + 2
            try:
                page_images = list(page.images)
            except ImportError:
                images.extend(_raw_page_images(page, page_number))
            except Exception:
                images.extend(_raw_page_images(page, page_number))
            else:
                for image_number, image in enumerate(page_images, start=1):
                    name = str(getattr(image, "name", f"image-{image_number}.bin"))
                    images.append(PDFImage(
                        page_number=page_number,
                        image_number=image_number,
                        chart_id=f"page_{page_number}_image_{image_number}",
                        data=bytes(getattr(image, "data", b"")),
                        media_type=_image_media(name)))
    except FactSetSourceError:
        raise
    except Exception as exc:
        raise FactSetSourceError(FactSetFailure.PARSE_FAILED, str(exc)) from exc

    full_text = "\n\n".join(page.text for page in pages)
    normalized = re.sub(r"\s+", " ", full_text)
    if "EARNINGS INSIGHT" not in normalized.upper() or "FactSet" not in normalized:
        raise FactSetSourceError(
            FactSetFailure.PARSE_FAILED, "FactSet Earnings Insight title anchors missing")
    report_date = _report_date(pages[0].text if pages else full_text, source.final_url)
    if report_date is None:
        raise FactSetSourceError(FactSetFailure.PARSE_FAILED, "report date unresolved")
    return FactSetPDF(
        report_date=report_date,
        title="FactSet Earnings Insight",
        page_count=page_count,
        pages=tuple(pages),
        images=tuple(image for image in images if image.data),
        text=full_text,
        text_hash=hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
        pdf_hash=source.content_hash,
    )


class FactSetEarningsInsightAdapter:
    """Controlled registry adapter; candidate extraction is added by the pipeline."""

    source_id = "factset_earnings_insight_metrics"
    dataset_id = "sp500_earnings_insight"

    def __init__(self, *, client=None, clock: Callable[[], datetime] = _now):
        self.client = client
        self.clock = clock
        self.last_source: FactSetFetch | None = None
        self.last_document: FactSetPDF | None = None

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        try:
            source = fetch_report(
                url=str(request.query_scope.get("url") or STABLE_URL),
                client=self.client, clock=self.clock,
                timeout_seconds=int(request.query_scope.get("timeout_seconds") or 60))
            document = inspect_pdf(source)
        except FactSetSourceError as exc:
            status = {
                FactSetFailure.UNREACHABLE: IngestionStatus.UNREACHABLE,
                FactSetFailure.UNAUTHORIZED: IngestionStatus.UNAUTHORIZED,
                FactSetFailure.NOT_PDF: IngestionStatus.NOT_PDF,
                FactSetFailure.PARSE_FAILED: IngestionStatus.PARSE_FAILED,
            }[exc.status]
            return AdapterBatch(
                source_id=request.source_id, dataset_id=request.dataset_id,
                status=status, fetched_at=self.clock().astimezone(timezone.utc),
                failures=[AdapterFailure(status=status, message=str(exc))],
                provider_metadata={"source_failure": exc.status.value})
        self.last_source, self.last_document = source, document
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id,
            status=IngestionStatus.ZERO_MATCH, fetched_at=source.fetched_at,
            artifacts=[AdapterArtifact(
                payload=source.body,
                query_scope={"stable_url": source.stable_url,
                             "final_url": source.final_url,
                             "pdf_sha256": source.content_hash},
                source_url=source.final_url,
                source_version=source.etag or source.last_modified or source.content_hash,
                media_type="application/pdf",
                retention="licensed_internal_research",
                metadata={
                    "stable_url": source.stable_url,
                    "final_url": source.final_url,
                    "etag": source.etag,
                    "last_modified": source.last_modified,
                    "first_seen_at": source.fetched_at.isoformat(),
                    "fetched_at": source.fetched_at.isoformat(),
                    "mime_type": source.mime_type,
                    "bytes": source.byte_count,
                    "pdf_sha256": source.content_hash,
                    "report_date": document.report_date.isoformat(),
                    "page_count": document.page_count,
                    "text_sha256": document.text_hash,
                    "usage": "internal_only",
                })],
            provider_metadata={
                "report_date": document.report_date.isoformat(),
                "pdf_sha256": source.content_hash,
                "text_sha256": document.text_hash,
                "source_version": source.etag or source.last_modified or source.content_hash,
            })


__all__ = [
    "FactSetEarningsInsightAdapter", "FactSetFailure", "FactSetFetch",
    "FactSetPDF", "FactSetSourceError", "PDFImage", "PDFPage", "STABLE_URL",
    "fetch_report", "inspect_pdf",
]
