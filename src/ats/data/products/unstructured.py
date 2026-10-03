"""Consumer-facing, read-only products for governed document assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from .base import DataProducts


_ROLE_TYPES = {
    "earnings_release": {"company_release", "release"},
    "regulatory_filing": {"regulatory_filing", "filing"},
    "earnings_transcript": {"earnings_transcript", "transcript"},
}
_ROLE_ORDER = {role: index for index, role in enumerate(_ROLE_TYPES)}


@dataclass(frozen=True)
class EarningsDocument:
    """One exact immutable document version selected for an earnings event."""

    role: str
    document_id: str
    version_id: str
    source: str
    source_url: str
    published_at: str
    title: str
    text: str

    @property
    def lineage(self) -> dict[str, str]:
        return {
            "role": self.role,
            "document_id": self.document_id,
            "version_id": self.version_id,
            "source": self.source,
            "source_url": self.source_url,
            "published_at": self.published_at,
        }


@dataclass(frozen=True)
class EarningsDocumentPackage:
    """Event-bound official documents, never a live Provider fetch."""

    entity: str
    period: str
    documents: tuple[EarningsDocument, ...]
    repository: str

    @property
    def transcript(self) -> EarningsDocument | None:
        return next((item for item in self.documents
                     if item.role == "earnings_transcript"), None)

    @property
    def official_documents(self) -> tuple[EarningsDocument, ...]:
        return tuple(item for item in self.documents
                     if item.role != "earnings_transcript")

    @property
    def scoreable(self) -> bool:
        return bool(self.transcript or self.official_documents)

    def official_text(self, *, per_document_chars: int = 25_000) -> str:
        return "\n\n".join(
            f"### {item.title or item.role}\n{item.text[:per_document_chars]}"
            for item in self.official_documents if item.text.strip())


def _period_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _role(doc_type: str) -> str:
    normalized = str(doc_type or "").lower()
    return next((role for role, values in _ROLE_TYPES.items() if normalized in values), "")


def _matches_period(row: dict[str, Any], period: str) -> bool:
    """Require an explicit event-period token; do not revive ``unknown`` assets."""
    target = _period_token(period)
    if not target:
        return False
    haystack = " ".join(str(row.get(key) or "") for key in (
        "document_id", "title", "source_url", "published_at"))
    return target in _period_token(haystack)


def _read_version_text(version: dict[str, Any]) -> str:
    path = Path(str(version.get("local_path") or ""))
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def earnings_document_package(repository, *, entity: str,
                              period: str) -> EarningsDocumentPackage:
    """Select the latest complete body for each official role in one event.

    The repository is intentionally supplied by the caller.  That keeps product
    selection independent from migration routing and makes it impossible for a PEAD
    read to trigger SEC, transcript, RSS, or web collection as a hidden side effect.
    """
    selected: dict[str, EarningsDocument] = {}
    for row in repository.documents(entity=entity, ok_only=True, limit=1000):
        role = _role(row.get("doc_type", ""))
        if not role or not _matches_period(row, period):
            continue
        version = repository.latest_document_version(row["document_id"])
        if not version:
            continue
        text = _read_version_text(version)
        if not text.strip():
            continue
        item = EarningsDocument(
            role=role,
            document_id=row["document_id"],
            version_id=str(version.get("version_id") or ""),
            source=str(row.get("source") or ""),
            source_url=str(version.get("source_url") or row.get("source_url") or ""),
            published_at=str(row.get("published_at") or ""),
            title=str(row.get("title") or role),
            text=text,
        )
        existing = selected.get(role)
        if existing is None or item.version_id > existing.version_id:
            selected[role] = item
    return EarningsDocumentPackage(
        entity=entity.upper(), period=period,
        documents=tuple(sorted(selected.values(), key=lambda item: _ROLE_ORDER[item.role])),
        repository=type(repository).__name__,
    )


@dataclass(frozen=True)
class AdmittedDocument:
    """A published immutable document version exposed through Data Products."""

    document_id: str
    version_id: str
    source: str
    source_url: str
    document_type: str
    entity: str
    title: str
    published_at: str
    fetched_at: str
    completeness: str
    content_hash: str
    text: str


def admitted_documents(*, entities: tuple[str, ...] | list[str] | None = None,
                       document_types: tuple[str, ...] | list[str] | None = None,
                       published_since: str | None = None,
                       limit: int = 200) -> list[AdmittedDocument]:
    """Read only accepted, versioned document material; never triggers collection.

    Missing/stale data is represented by an empty result.  Each returned row carries
    an exact immutable version and completeness marker so consumers cannot confuse
    a subscriber preview with a full transcript or article.
    """
    from ats.data.stores.unstructured import get_platform_unstructured_repository

    repo = get_platform_unstructured_repository()
    try:
        rows: dict[str, dict[str, Any]] = {}
        entity_filter = tuple(str(item).upper() for item in (entities or ()))
        type_filter = set(str(item).lower() for item in (document_types or ()))
        for entity in entity_filter or (None,):
            for row in repo.documents(
                entity=entity, ok_only=True,
                doc_type_in=tuple(type_filter) if type_filter else None,
                published_since=published_since, limit=max(1, min(int(limit), 1000)),
            ):
                rows[str(row["document_id"])] = row
        result: list[AdmittedDocument] = []
        for row in rows.values():
            version = repo.latest_document_version(str(row["document_id"]))
            if not version:
                continue
            text = _read_version_text(version)
            if not text.strip():
                continue
            result.append(AdmittedDocument(
                document_id=str(row["document_id"]),
                version_id=str(version.get("version_id") or ""),
                source=str(row.get("source") or ""),
                source_url=str(version.get("source_url") or row.get("source_url") or ""),
                document_type=str(row.get("doc_type") or ""),
                entity=str(row.get("entity") or ""),
                title=str(row.get("title") or ""),
                published_at=str(row.get("published_at") or ""),
                fetched_at=str(version.get("fetched_at") or row.get("fetched_at") or ""),
                completeness=str(row.get("completeness") or "full"),
                content_hash=str(version.get("content_hash") or ""),
                text=text,
            ))
        return sorted(result, key=lambda item: (item.published_at, item.fetched_at), reverse=True)
    finally:
        repo.close()


def platform_earnings_document_package(*, entity: str,
                                       period: str) -> EarningsDocumentPackage:
    """Read the migrated platform database and its immutable asset references."""
    from ats.data.stores.unstructured import get_platform_unstructured_repository

    repository = get_platform_unstructured_repository()
    try:
        return earnings_document_package(repository, entity=entity, period=period)
    finally:
        repository.close()


def platform_news_items(*, entity: str, since, until=None) -> list:
    """Return ticker-associated IBKR/Yahoo news from accepted platform assets.

    Association is read from ``document_entities``.  Publisher-only witnesses (for
    example an IBKR article linked solely to ``DOWJONES``) are intentionally absent:
    they remain Chain material and cannot leak into a ticker's PEAD monitor.
    """
    from datetime import datetime, timezone

    from ats.data.stores.unstructured import get_platform_unstructured_repository
    from ats.schemas.news import NewsItem

    repository = get_platform_unstructured_repository()
    try:
        items = []
        for row in repository.documents(entity=entity, ok_only=True, limit=1000):
            source = str(row.get("source") or "")
            if source not in {"ibkr_news", "yfinance_live_news"}:
                continue
            stamp = str(row.get("published_at") or row.get("fetched_at") or "")
            try:
                published = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published < since or (until is not None and published > until):
                continue
            version = repository.latest_document_version(row["document_id"])
            body = _read_version_text(version or {})
            if not body.strip():
                continue
            items.append(NewsItem(
                id=row["document_id"], source=f"platform:{source}",
                headline=str(row.get("title") or row["document_id"]),
                summary=body[:800], url=str((version or {}).get("source_url") or row.get("source_url") or ""),
                published_at=published, tickers=[entity.upper()], publisher=source,
            ))
        return sorted(items, key=lambda item: item.published_at, reverse=True)
    finally:
        repository.close()


UnstructuredDataProducts = DataProducts


def get_unstructured_products() -> UnstructuredDataProducts:
    return UnstructuredDataProducts()


__all__ = [
    "AdmittedDocument", "EarningsDocument", "EarningsDocumentPackage", "UnstructuredDataProducts",
    "earnings_document_package", "get_unstructured_products",
    "platform_earnings_document_package", "platform_news_items", "admitted_documents",
]
