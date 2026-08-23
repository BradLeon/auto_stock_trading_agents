"""Single entry point for accepted non-structured research documents.

Adapters remain responsible for fetching and source-specific validation. Once a body
is accepted, every adapter calls this module so immutable storage, catalog metadata,
entity associations, chunks and lineage are applied consistently.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path

from . import source_cache

log = logging.getLogger("ats.data.document_assets")


def stable_key(value: str, *, prefix: str = "doc") -> str:
    """Readable, bounded key with a hash suffix to avoid slug collisions."""
    raw = (value or "").strip()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")[:80] or prefix
    digest = hashlib.sha1(raw.encode()).hexdigest()[:12]
    return f"{slug}-{digest}"


def ingest(*, entity: str, key: str, doc_type: str, text: str,
           source: str = "", source_url: str = "", external_id: str = "",
           title: str = "", published_at: str = "",
           related_entities: tuple[str, ...] = (), min_chars: int = source_cache.MIN_CHARS,
           completeness: str = "full", truncation_reason: str = "",
           carrier_format: str = "plain_text",
           mime_source: str = "",
           now: datetime | None = None, note: str = "", store=None):
    """Persist one accepted body and return its ``CachedDoc`` compatibility object."""
    from ..memory import get_store

    store = store or get_store()
    doc = source_cache.store(
        entity, key, doc_type, text, source=source, source_url=source_url,
        external_id=external_id, title=title, published_at=published_at,
        related_entities=related_entities, completeness=completeness,
        truncation_reason=truncation_reason, carrier_format=carrier_format,
        mime_source=mime_source,
        min_chars=min_chars, now=now,
    )
    if doc is not None:
        store.save_document(doc, note=note)
    return doc


def read_document(document_id: str, *, store=None) -> str:
    """Read the latest immutable body for a catalog document without network access."""
    from ..memory import get_store

    store = store or get_store()
    version = store.latest_document_version(document_id)
    if not version:
        return ""
    path = Path(version.get("local_path") or "")
    if not path.is_file():
        return ""
    try:
        _, body = source_cache._split_frontmatter(
            path.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return ""
    body = body.strip()
    if not source_cache.body_is_consistent(
        body,
        expected_hash=version.get("content_hash", ""),
        expected_chars=version.get("chars"),
    ):
        log.error("document asset integrity mismatch: %s (%s)", document_id, path)
        return ""
    return body


def read_external(external_id: str, *, store=None) -> tuple[dict | None, str]:
    """Resolve a provider id to its shared document and body."""
    from ..memory import get_store

    store = store or get_store()
    row = store.document_by_external_id(external_id)
    return (row, read_document(row["document_id"], store=store) if row else "")


def identify(text: str, *, entity: str | None = None, store=None) -> dict | None:
    """Resolve exact accepted text to its catalog asset for downstream lineage."""
    from ..memory import get_store

    body = (text or "").strip()
    if not body:
        return None
    store = store or get_store()
    return store.document_by_content_hash(
        hashlib.sha256(body.encode("utf-8")).hexdigest(), entity=entity)
