"""Read-only product repository over migrated unstructured data tables."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3


class PlatformUnstructuredRepository:
    """Expose consumer read contracts without falling through to ``memory.store``.

    Collection/extraction writers remain on the legacy path until their own source
    cutover is published.  This class is intentionally read-only and only serves
    reconciled document/evidence history from the migrated data database.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def _rows(self, sql: str, args: list) -> list[dict]:
        return [dict(row) for row in self.conn.execute(sql, args).fetchall()]

    def observations(self, *, entity: str | None = None, metric: str | None = None,
                     since: datetime | None = None, limit: int = 500,
                     include_superseded: bool = False) -> list[dict]:
        sql, args = "SELECT * FROM data_evidence_observations WHERE 1=1", []
        if not include_superseded:
            sql += " AND superseded_at IS NULL"
        if entity:
            sql += " AND entity=?"; args.append(entity.upper())
        if metric:
            sql += " AND metric=?"; args.append(metric)
        if since:
            sql += " AND observed_at>=?"; args.append(since.isoformat())
        return self._rows(sql + " ORDER BY observed_at DESC LIMIT ?", [*args, limit])

    def observation_failures(self, limit: int = 50) -> list[dict]:
        """Return persisted extraction gaps without falling back to Workflow memory."""
        return self._rows(
            "SELECT * FROM data_evidence_failures ORDER BY at DESC LIMIT ?", [limit])

    def facts(self, *, entity: str | None = None, document_id: str | None = None,
              since: datetime | None = None, include_superseded: bool = False,
              limit: int = 500) -> list[dict]:
        sql, args = "SELECT * FROM data_evidence_facts WHERE 1=1", []
        if not include_superseded:
            sql += " AND superseded_at IS NULL"
        for column, value in (("entity", entity.upper() if entity else None),
                              ("document_id", document_id)):
            if value:
                sql += f" AND {column}=?"; args.append(value)
        if since:
            sql += " AND observed_at>=?"; args.append(since.isoformat())
        return self._rows(sql + " ORDER BY observed_at DESC LIMIT ?", [*args, limit])

    def fact_projections(self, *, fact_id: str | None = None,
                         profile: str | None = None, concept: str | None = None,
                         include_superseded: bool = False,
                         limit: int = 500) -> list[dict]:
        sql, args = "SELECT * FROM data_evidence_projections WHERE 1=1", []
        if not include_superseded:
            sql += " AND superseded_at IS NULL"
        for column, value in (("fact_id", fact_id), ("profile", profile), ("concept", concept)):
            if value:
                sql += f" AND {column}=?"; args.append(value)
        return self._rows(sql + " ORDER BY created_at DESC LIMIT ?", [*args, limit])

    def task_projections(self, *, profile: str | None = None,
                         target_type: str | None = None, target_id: str | None = None,
                         input_ref: str | None = None, limit: int = 500) -> list[dict]:
        sql, args = "SELECT * FROM data_task_projections WHERE 1=1", []
        for column, value in (("profile", profile), ("target_type", target_type),
                              ("target_id", target_id), ("input_ref", input_ref)):
            if value:
                sql += f" AND {column}=?"; args.append(value)
        return self._rows(sql + " ORDER BY created_at DESC LIMIT ?", [*args, limit])

    def documents(self, entity: str | None = None, *, ok_only: bool = True,
                  doc_type: str | None = None, source_contains: str | None = None,
                  published_since: str | None = None, limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM data_documents", []
        where: list[str] = []
        if entity:
            where.append("(entity=? OR EXISTS (SELECT 1 FROM data_document_entities de "
                         "WHERE de.document_id=data_documents.document_id AND de.entity=?))")
            args.extend([entity.upper(), entity.upper()])
        if ok_only:
            where.append("ok=1")
        if doc_type:
            where.append("doc_type=?"); args.append(doc_type)
        if source_contains:
            where.append("lower(source) LIKE ?"); args.append(f"%{source_contains.lower()}%")
        if published_since:
            where.append("published_at>=?"); args.append(published_since)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self._rows(sql + " ORDER BY fetched_at DESC LIMIT ?", [*args, limit])

    def latest_document_version(self, document_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM data_document_versions WHERE document_id=? "
            "ORDER BY fetched_at DESC,created_at DESC LIMIT 1", (document_id,)).fetchone()
        return dict(row) if row else None

    def search_document_chunks(self, query: str, *, entity: str | None = None,
                               source_contains: str | None = None,
                               published_since: str | None = None,
                               limit: int = 20) -> list[dict]:
        terms = [term for term in (query or "").split() if term]
        if not terms:
            return []
        sql = ("SELECT c.chunk_id,c.version_id,c.ordinal,c.char_start,c.char_end,c.text,"
               "v.document_id,d.entity,d.source,d.source_url,d.title,d.published_at "
               "FROM data_document_chunks c JOIN data_document_versions v ON v.version_id=c.version_id "
               "JOIN data_documents d ON d.document_id=v.document_id WHERE d.ok=1")
        args: list = []
        for term in terms:
            sql += " AND lower(c.text) LIKE ?"; args.append(f"%{term.lower()}%")
        if entity:
            sql += " AND (d.entity=? OR EXISTS (SELECT 1 FROM data_document_entities de WHERE de.document_id=d.document_id AND de.entity=?))"
            args.extend([entity.upper(), entity.upper()])
        if source_contains:
            sql += " AND lower(d.source) LIKE ?"; args.append(f"%{source_contains.lower()}%")
        if published_since:
            sql += " AND d.published_at>=?"; args.append(published_since)
        return self._rows(sql + " ORDER BY d.published_at DESC,c.ordinal LIMIT ?", [*args, limit])

    def document_processing(self, *, limit: int = 200) -> list[dict]:
        return self._rows(
            "SELECT p.*,v.document_id,v.content_hash FROM data_document_processing_runs p "
            "JOIN data_document_versions v ON v.version_id=p.version_id "
            "ORDER BY p.started_at DESC LIMIT ?", [limit])


def get_platform_unstructured_repository() -> PlatformUnstructuredRepository:
    from ...runtime import platform_data_db_path

    return PlatformUnstructuredRepository(platform_data_db_path())


__all__ = ["PlatformUnstructuredRepository", "get_platform_unstructured_repository"]
