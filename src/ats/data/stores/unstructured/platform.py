"""Read-only product repository over migrated unstructured data tables."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3


class PlatformUnstructuredRepository:
    """Expose consumer read contracts without falling through to ``memory.store``.

    Collection/extraction writers remain on the legacy path until their own source
    cutover is published.  This class is intentionally read-only and only serves
    reconciled document/evidence history from the migrated data database.
    """

    def __init__(self, path: str | Path, *, writable: bool = False):
        self.path = Path(path).expanduser().resolve()
        self.writable = writable
        if writable:
            self.conn = sqlite3.connect(self.path)
            self._bootstrap_writer_schema()
        else:
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def _bootstrap_writer_schema(self) -> None:
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS data_documents (document_id TEXT PRIMARY KEY,entity TEXT NOT NULL,period TEXT NOT NULL,doc_type TEXT NOT NULL,source TEXT NOT NULL,source_url TEXT NOT NULL,local_path TEXT NOT NULL,sha256 TEXT NOT NULL,chars INTEGER NOT NULL,ok INTEGER NOT NULL DEFAULT 1,note TEXT NOT NULL DEFAULT '',fetched_at TEXT NOT NULL,external_id TEXT NOT NULL DEFAULT '',title TEXT NOT NULL DEFAULT '',published_at TEXT NOT NULL DEFAULT '',completeness TEXT NOT NULL DEFAULT 'full',truncation_reason TEXT NOT NULL DEFAULT '',carrier_format TEXT NOT NULL DEFAULT '',mime_source TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS data_document_versions (version_id TEXT PRIMARY KEY,document_id TEXT NOT NULL,content_hash TEXT NOT NULL,local_path TEXT NOT NULL,chars INTEGER NOT NULL,source_url TEXT NOT NULL,fetched_at TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(document_id,content_hash));
        CREATE INDEX IF NOT EXISTS idx_data_document_version_document ON data_document_versions(document_id,fetched_at);
        CREATE TABLE IF NOT EXISTS data_document_entities (document_id TEXT NOT NULL,entity TEXT NOT NULL,relation TEXT NOT NULL DEFAULT 'mentioned',PRIMARY KEY(document_id,entity,relation));
        CREATE TABLE IF NOT EXISTS data_document_aliases (alias_id TEXT PRIMARY KEY,document_id TEXT NOT NULL,source TEXT NOT NULL,source_url TEXT NOT NULL,external_id TEXT NOT NULL,title TEXT NOT NULL,published_at TEXT NOT NULL,metadata_json TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS data_document_chunks (chunk_id TEXT PRIMARY KEY,version_id TEXT NOT NULL,ordinal INTEGER NOT NULL,char_start INTEGER NOT NULL,char_end INTEGER NOT NULL,text TEXT NOT NULL,content_hash TEXT NOT NULL,UNIQUE(version_id,ordinal));
        CREATE TABLE IF NOT EXISTS data_document_candidates (candidate_id TEXT PRIMARY KEY,document_id TEXT NOT NULL,status TEXT NOT NULL,expected_entity TEXT NOT NULL,claimed_entity TEXT NOT NULL,target_period TEXT NOT NULL,claimed_period TEXT NOT NULL,expected_semantic TEXT NOT NULL,claimed_semantic TEXT NOT NULL,carrier_format TEXT NOT NULL,completeness TEXT NOT NULL,source TEXT NOT NULL,source_url TEXT NOT NULL,external_id TEXT NOT NULL,title TEXT NOT NULL,published_at TEXT NOT NULL,discovered_at TEXT NOT NULL,content_hash TEXT NOT NULL,chars INTEGER NOT NULL,raw_path TEXT NOT NULL,reason_codes TEXT NOT NULL,validation_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS data_sources (source_id TEXT PRIMARY KEY,kind TEXT NOT NULL,label TEXT NOT NULL,adapter TEXT NOT NULL,cadence TEXT NOT NULL,entity TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS data_ingestion_runs (run_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,kind TEXT NOT NULL,started_at TEXT NOT NULL,completed_at TEXT NOT NULL DEFAULT '',status TEXT NOT NULL,discovered INTEGER NOT NULL DEFAULT 0,accepted INTEGER NOT NULL DEFAULT 0,quarantined INTEGER NOT NULL DEFAULT 0,reason_codes TEXT NOT NULL DEFAULT '{}',snapshot_updated_at TEXT NOT NULL DEFAULT '',snapshot_lag_hours REAL,note TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS data_newsletter_cursors (mailbox TEXT NOT NULL,folder TEXT NOT NULL,sender TEXT NOT NULL,uidvalidity TEXT NOT NULL,last_uid INTEGER NOT NULL,last_message_id TEXT NOT NULL,watermark TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(mailbox,folder,sender));
        CREATE TABLE IF NOT EXISTS data_document_artifacts (document_version_id TEXT NOT NULL,artifact_id TEXT NOT NULL,role TEXT NOT NULL,page_number INTEGER NOT NULL DEFAULT 0,region_json TEXT NOT NULL DEFAULT '',media_type TEXT NOT NULL,content_hash TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(document_version_id,artifact_id,role,page_number,region_json));
        CREATE INDEX IF NOT EXISTS idx_data_document_artifact_version ON data_document_artifacts(document_version_id,role,page_number);
        CREATE TABLE IF NOT EXISTS data_document_pages (document_version_id TEXT NOT NULL,page_number INTEGER NOT NULL,char_start INTEGER NOT NULL,char_end INTEGER NOT NULL,section_title TEXT NOT NULL DEFAULT '',text TEXT NOT NULL,content_hash TEXT NOT NULL,PRIMARY KEY(document_version_id,page_number));
        CREATE INDEX IF NOT EXISTS idx_data_document_page_version ON data_document_pages(document_version_id,page_number);
        CREATE TABLE IF NOT EXISTS data_document_processing_runs (version_id TEXT NOT NULL,consumer TEXT NOT NULL,processor_version TEXT NOT NULL,status TEXT NOT NULL,started_at TEXT NOT NULL,completed_at TEXT NOT NULL DEFAULT '',outputs INTEGER NOT NULL DEFAULT 0,note TEXT NOT NULL DEFAULT '',PRIMARY KEY(version_id,consumer,processor_version));
        -- ── Evidence write side ────────────────────────────────────────────────
        -- The evidence twin tables were migrated read-side only: the repository could
        -- query them but nothing could create them, so neutral evidence facts kept
        -- being written to Workflow memory tables that the boundary classifies as
        -- data-layer-owned (and therefore drops). Column-for-column identical to the
        -- Workflow memory originals — this is a redirect, not a redesign.
        CREATE TABLE IF NOT EXISTS data_evidence_facts (fact_id TEXT PRIMARY KEY,document_id TEXT,document_version_id TEXT,source_url TEXT,entity TEXT,source_entity TEXT,metric TEXT,period TEXT,observation_type TEXT,value REAL,unit TEXT,evidence_span TEXT,observed_at TEXT,extraction_confidence REAL DEFAULT 1.0,discovery_evidence INTEGER DEFAULT 0,superseded_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_data_evidence_fact_entity ON data_evidence_facts(entity,metric);
        CREATE INDEX IF NOT EXISTS idx_data_evidence_fact_document ON data_evidence_facts(document_id,source_entity);
        CREATE TABLE IF NOT EXISTS data_evidence_projections (projection_id TEXT PRIMARY KEY,fact_id TEXT,legacy_observation_id TEXT,profile TEXT,profile_version TEXT,concept TEXT,stance TEXT,direction TEXT,payload TEXT,created_at TEXT,superseded_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_data_evidence_projection ON data_evidence_projections(profile,profile_version,concept,fact_id);
        CREATE TABLE IF NOT EXISTS data_evidence_observations (id TEXT PRIMARY KEY,document_id TEXT,source_url TEXT,entity TEXT,source_entity TEXT,metric TEXT,concept TEXT,period TEXT,observation_type TEXT,stance TEXT,direction TEXT,value REAL,unit TEXT,evidence_span TEXT,observed_at TEXT,discovery_evidence INTEGER DEFAULT 0,extraction_confidence REAL DEFAULT 1.0,superseded_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_data_evidence_obs_entity ON data_evidence_observations(entity,metric);
        CREATE INDEX IF NOT EXISTS idx_data_evidence_obs_at ON data_evidence_observations(observed_at);
        CREATE TABLE IF NOT EXISTS data_evidence_failures (document_id TEXT,entity TEXT,reason TEXT,at TEXT,PRIMARY KEY(document_id,entity));
        CREATE TABLE IF NOT EXISTS data_task_projections (projection_id TEXT PRIMARY KEY,profile TEXT,profile_version TEXT,input_kind TEXT,input_ref TEXT,target_type TEXT,target_id TEXT,payload TEXT,created_at TEXT,expires_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_data_task_projection ON data_task_projections(profile,target_type,target_id,created_at);
        CREATE TABLE IF NOT EXISTS data_measurement_series (series_id TEXT PRIMARY KEY,source_id TEXT,series TEXT,label TEXT,entity TEXT,unit TEXT,cadence TEXT,updated_at TEXT,UNIQUE(source_id,series));
        CREATE TABLE IF NOT EXISTS data_measurement_points (point_id TEXT PRIMARY KEY,series_id TEXT,period TEXT,value REAL,unit TEXT,published_at TEXT,fetched_at TEXT,content_hash TEXT,raw_payload TEXT,UNIQUE(series_id,period,content_hash));
        CREATE INDEX IF NOT EXISTS idx_data_measurement_period ON data_measurement_points(series_id,period,fetched_at);
        """)
        self.conn.commit()

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

    def observations_by_id(self, ids: list[str]) -> dict[str, dict]:
        """Return exactly the cited observations, keyed by their stable ids."""
        if not ids:
            return {}
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT * FROM data_evidence_observations WHERE id IN ({placeholders})",
                chunk).fetchall()
            for row in rows:
                out[row["id"]] = dict(row)
        return out

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
                  published_since: str | None = None, limit: int = 200,
                  doc_type_in: tuple[str, ...] | None = None) -> list[dict]:
        # `doc_type_in` lets a caller pass an already-resolved set of equivalent type
        # values (current + legacy) instead of a single string.
        if doc_type_in:
            doc_type = list(doc_type_in)
        sql, args = "SELECT * FROM data_documents", []
        where: list[str] = []
        if entity:
            where.append("(entity=? OR EXISTS (SELECT 1 FROM data_document_entities de "
                         "WHERE de.document_id=data_documents.document_id AND de.entity=?))")
            args.extend([entity.upper(), entity.upper()])
        if ok_only:
            where.append("ok=1")
        if doc_type:
            if isinstance(doc_type, (list, tuple, set)):
                values = [str(v) for v in doc_type]
                where.append("doc_type IN (%s)" % ",".join("?" * len(values)))
                args.extend(values)
            else:
                where.append("doc_type=?"); args.append(doc_type)
        if source_contains:
            where.append("lower(source) LIKE ?"); args.append(f"%{source_contains.lower()}%")
        if published_since:
            where.append("published_at>=?"); args.append(published_since)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self._rows(sql + " ORDER BY fetched_at DESC LIMIT ?", [*args, limit])

    def documents_by_id(self, ids: list[str]) -> dict[str, dict]:
        """Resolve exactly the source documents referenced by cited observations."""
        if not ids:
            return {}
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT * FROM data_documents WHERE document_id IN ({placeholders})",
                chunk).fetchall()
            for row in rows:
                out[row["document_id"]] = dict(row)
        return out

    def latest_document_version(self, document_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM data_document_versions WHERE document_id=? "
            "ORDER BY fetched_at DESC,created_at DESC LIMIT 1", (document_id,)).fetchone()
        return dict(row) if row else None

    def document_versions(self, document_id: str) -> list[dict]:
        return self._rows(
            "SELECT * FROM data_document_versions WHERE document_id=? "
            "ORDER BY fetched_at DESC,created_at DESC", [document_id])

    def link_document_artifact(self, document_version_id: str, artifact_id: str, *,
                               role: str, media_type: str, content_hash: str,
                               page_number: int | None = None,
                               region: tuple[float, float, float, float] | None = None) -> bool:
        if role not in {"source_pdf", "page_image", "chart_crop"}:
            raise ValueError(f"invalid document artifact role: {role}")
        if page_number is not None and page_number < 1:
            raise ValueError("page_number must be one-based")
        if region is not None:
            x0, y0, x1, y1 = region
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError("region must be normalized [x0,y0,x1,y1]")
        before = self.conn.total_changes
        self._write(
            "INSERT OR IGNORE INTO data_document_artifacts "
            "(document_version_id,artifact_id,role,page_number,region_json,media_type,"
            "content_hash,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (document_version_id, artifact_id, role, page_number or 0,
             json.dumps(region or (), separators=(",", ":")), media_type, content_hash,
             datetime.now().astimezone().isoformat(timespec="seconds")))
        return self.conn.total_changes > before

    def document_artifacts(self, document_version_id: str, *,
                           role: str | None = None) -> list[dict]:
        sql = "SELECT * FROM data_document_artifacts WHERE document_version_id=?"
        args: list = [document_version_id]
        if role:
            sql += " AND role=?"
            args.append(role)
        return self._rows(sql + " ORDER BY role,page_number,artifact_id", args)

    def save_document_pages(self, document_version_id: str, pages) -> int:
        before = self.conn.total_changes
        self.conn.executemany(
            "INSERT OR REPLACE INTO data_document_pages "
            "(document_version_id,page_number,char_start,char_end,section_title,text,"
            "content_hash) VALUES (?,?,?,?,?,?,?)",
            [(document_version_id, int(page.page_number), int(page.char_start),
              int(page.char_end), str(page.section_title), str(page.text),
              hashlib.sha256(str(page.text).encode("utf-8")).hexdigest())
             for page in pages])
        self.conn.commit()
        return self.conn.total_changes - before

    def document_pages(self, document_version_id: str) -> list[dict]:
        return self._rows(
            "SELECT * FROM data_document_pages WHERE document_version_id=? "
            "ORDER BY page_number", [document_version_id])

    def begin_document_processing(self, document_id: str, consumer: str,
                                  processor_version: str = "v1", *,
                                  at: str | None = None) -> str | None:
        latest = self.latest_document_version(document_id)
        if latest is None:
            return None
        stamp = at or datetime.now().astimezone().isoformat(timespec="seconds")
        cur = self._write(
            "INSERT OR IGNORE INTO data_document_processing_runs "
            "(version_id,consumer,processor_version,status,started_at,outputs,note) "
            "VALUES (?,?,?,'running',?,0,'')",
            (latest["version_id"], consumer, processor_version, stamp))
        return latest["version_id"] if cur.rowcount else None

    def finish_document_processing(self, version_id: str, consumer: str,
                                   processor_version: str = "v1", *, ok: bool,
                                   outputs: int = 0, note: str = "",
                                   at: str | None = None) -> None:
        stamp = at or datetime.now().astimezone().isoformat(timespec="seconds")
        self._write(
            "UPDATE data_document_processing_runs SET status=?,completed_at=?,outputs=?,"
            "note=? WHERE version_id=? AND consumer=? AND processor_version=?",
            ("succeeded" if ok else "failed", stamp, outputs, note,
             version_id, consumer, processor_version))

    def documents_by_alias_source(self, source_contains: str, *,
                                  entity: str | None = None,
                                  published_since: str | None = None,
                                  limit: int = 1000) -> list[dict]:
        sql = ("SELECT DISTINCT d.* FROM data_documents d "
               "JOIN data_document_aliases a ON a.document_id=d.document_id "
               "WHERE d.ok=1 AND lower(a.source) LIKE ?")
        args: list = [f"%{source_contains.lower()}%"]
        if entity:
            sql += (" AND (d.entity=? OR EXISTS (SELECT 1 FROM data_document_entities de "
                    "WHERE de.document_id=d.document_id AND de.entity=?))")
            args.extend([entity.upper(), entity.upper()])
        if published_since:
            sql += " AND d.published_at>=?"
            args.append(published_since)
        return self._rows(sql + " ORDER BY d.published_at DESC LIMIT ?", [*args, limit])

    def document_candidates(self, *, status: str | None = None,
                            source: str | None = None,
                            limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM data_document_candidates", []
        where: list[str] = []
        if status:
            where.append("status=?")
            args.append(status)
        if source:
            where.append("source=?")
            args.append(source)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self._rows(sql + " ORDER BY discovered_at DESC LIMIT ?", [*args, limit])

    def data_source_health(self) -> list[dict]:
        return self._rows(
            "SELECT s.*,r.status,r.started_at,r.completed_at,r.discovered,r.accepted,"
            "r.quarantined,r.reason_codes,r.snapshot_updated_at,r.snapshot_lag_hours,r.note "
            "FROM data_sources s LEFT JOIN data_ingestion_runs r ON r.run_id=("
            " SELECT r2.run_id FROM data_ingestion_runs r2 WHERE r2.source_id=s.source_id "
            " ORDER BY r2.started_at DESC LIMIT 1) ORDER BY s.source_id", [])

    def document_aliases(self, document_id: str | None = None, *,
                         limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM data_document_aliases", []
        if document_id:
            sql += " WHERE document_id=?"
            args.append(document_id)
        return self._rows(sql + " ORDER BY created_at DESC LIMIT ?", [*args, limit])

    def unmapped_observations(self, *, limit: int = 500) -> list[dict]:
        """Facts we stored but could file under no declared claim dimension.

        Raw material for induction; discovery-frozen rows are excluded — they already
        triggered a proposal and must not trigger another.
        """
        return self._rows(
            "SELECT * FROM data_evidence_observations "
            "WHERE (concept IS NULL OR concept = '') AND COALESCE(discovery_evidence,0)=0 "
            "AND superseded_at IS NULL ORDER BY observed_at DESC LIMIT ?", [limit])

    def processing_runs(self, document_id: str | None = None, *,
                        consumer: str | None = None,
                        processor_version: str | None = None,
                        limit: int = 200) -> list[dict]:
        sql = ("SELECT p.*, v.document_id, v.content_hash FROM data_document_processing_runs p "
               "JOIN data_document_versions v ON v.version_id=p.version_id WHERE 1=1")
        args: list = []
        if document_id:
            sql += " AND v.document_id=?"
            args.append(document_id)
        if consumer:
            sql += " AND p.consumer=?"
            args.append(consumer)
        if processor_version:
            sql += " AND p.processor_version=?"
            args.append(processor_version)
        return self._rows(sql + " ORDER BY p.started_at DESC LIMIT ?", [*args, limit])

    def save_document_failure(self, entity: str, period: str, doc_type: str, *,
                              source: str = "", source_url: str = "", note: str = "",
                              at: str | None = None) -> None:
        """Record a fetch that failed a guard so it is not retried blindly (ok=0)."""
        stamp = at or datetime.now().astimezone().isoformat(timespec="seconds")
        self._write(
            "INSERT OR REPLACE INTO data_documents (document_id,entity,period,doc_type,"
            "source,source_url,local_path,sha256,chars,ok,note,fetched_at,external_id,"
            "title,published_at,completeness,truncation_reason,carrier_format,mime_source) "
            "VALUES (?,?,?,?,?,?,'','',0,0,?,?,'','','','full','','','')",
            (f"{entity.upper()}:{period or 'unknown'}:{doc_type}", entity.upper(), period,
             doc_type, source, source_url, note, stamp))

    def has_document(self, document_ids: list[str]) -> bool:
        """True when any of these logical documents exists and is accepted (ok=1)."""
        if not document_ids:
            return False
        row = self.conn.execute(
            "SELECT ok FROM data_documents WHERE document_id IN (%s)"
            % ",".join("?" * len(document_ids)), document_ids).fetchone()
        return bool(row and row["ok"])

    def processed_document_ids(self, consumer: str, processor_version: str, *,
                               entity: str | None = None) -> set[str]:
        """Documents whose processing ledger says a consumer already read them."""
        sql = ("SELECT DISTINCT v.document_id FROM data_document_processing_runs p "
               "JOIN data_document_versions v ON v.version_id=p.version_id "
               "LEFT JOIN data_documents d ON d.document_id=v.document_id "
               "WHERE p.consumer=? AND p.processor_version=?")
        args: list = [consumer, processor_version]
        if entity:
            sql += " AND d.entity=?"
            args.append(entity.upper())
        return {row["document_id"] for row in self.conn.execute(sql, args).fetchall()}

    def ingestion_runs(self, source_id: str | None = None, *, limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM data_ingestion_runs", []
        if source_id:
            sql += " WHERE source_id=?"
            args.append(source_id)
        return self._rows(sql + " ORDER BY started_at DESC LIMIT ?", [*args, limit])

    def save_document_chunks(self, version_id: str, chunks) -> int:
        """Persist pre-computed chunks (used when chunking is done outside save_document)."""
        before = self.conn.total_changes
        self.conn.executemany(
            "INSERT OR IGNORE INTO data_document_chunks "
            "(chunk_id,version_id,ordinal,char_start,char_end,text,content_hash) "
            "VALUES (?,?,?,?,?,?,?)",
            [(hashlib.sha1(f"{version_id}|{int(c['ordinal'])}|{hashlib.sha256(str(c['text']).encode()).hexdigest()}".encode()).hexdigest()[:20],
              version_id, int(c["ordinal"]), int(c["char_start"]), int(c["char_end"]),
              str(c["text"]), hashlib.sha256(str(c["text"]).encode()).hexdigest())
             for c in chunks])
        self.conn.commit()
        return self.conn.total_changes - before

    def document_source_health(self) -> list[dict]:
        return self._rows(
            "SELECT source,count(*) AS documents,"
            "sum(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS failures,"
            "max(fetched_at) AS latest_fetch FROM data_documents "
            "GROUP BY source ORDER BY source", [])

    def document_candidate_health(self) -> list[dict]:
        rows = self._rows(
            "SELECT source,status,reason_codes,count(*) AS candidates "
            "FROM data_document_candidates GROUP BY source,status,reason_codes "
            "ORDER BY source,status", [])
        for row in rows:
            try:
                row["reason_codes"] = json.loads(row.get("reason_codes") or "[]")
            except json.JSONDecodeError:
                row["reason_codes"] = ["invalid_reason_code_payload"]
        return rows

    def document_quality_inventory(self) -> list[dict]:
        return self._rows(
            "SELECT source,doc_type,coalesce(completeness,'full') AS completeness,"
            "count(*) AS documents,sum(chars) AS chars,max(published_at) AS latest_published,"
            "max(fetched_at) AS latest_fetch FROM data_documents WHERE ok=1 "
            "GROUP BY source,doc_type,coalesce(completeness,'full') "
            "ORDER BY source,doc_type,completeness", [])

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

    # --- evidence write side --------------------------------------------- #
    # Neutral evidence facts are authored here and read back through the read methods
    # above; Workflow memory keeps only opinionated conclusions plus a lineage
    # reference to these rows.
    @staticmethod
    def observation_fact_id(obs) -> str:
        """Deterministic fact id: (document, entity, metric, period)."""
        raw = f"{obs.document_id}|{obs.entity.upper()}|{obs.metric.lower()}|{obs.period}"
        return hashlib.sha1(raw.encode()).hexdigest()[:20]

    @staticmethod
    def observation_projection_id(obs, profile: str, version: str) -> str:
        raw = f"{obs.id}|{profile}|{version}"
        return hashlib.sha1(raw.encode()).hexdigest()[:20]

    def save_evidence_observation(self, obs, *, projection_profile: str = "evidence_observer",
                                  projection_version: str = "v1") -> bool:
        """Idempotent upsert of one neutral observation; True when it is new.

        `discovery_evidence` is STICKY: it is set by a different step (when an
        induction pass used this row to notice a proposition), so a plain
        INSERT OR REPLACE would clear it — and the material that discovered a claim
        would become eligible to confirm that same claim.
        """
        existing = self.conn.execute(
            "SELECT discovery_evidence FROM data_evidence_observations WHERE id=?",
            (obs.id,)).fetchone()
        frozen = 1 if (getattr(obs, "discovery_evidence", 0) or (existing and existing[0])) else 0
        fact_id = self.observation_fact_id(obs)
        prior_fact = self.conn.execute(
            "SELECT discovery_evidence FROM data_evidence_facts WHERE fact_id=?",
            (fact_id,)).fetchone()
        fact_frozen = 1 if (frozen or (prior_fact and prior_fact[0])) else 0
        document_version = self.latest_document_version(obs.document_id)
        self._write(
            "INSERT OR REPLACE INTO data_evidence_facts "
            "(fact_id,document_id,document_version_id,source_url,entity,source_entity,metric,"
            " period,observation_type,value,unit,evidence_span,observed_at,"
            " extraction_confidence,discovery_evidence,superseded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (fact_id, obs.document_id,
             document_version["version_id"] if document_version else "",
             obs.source_url, obs.entity.upper(),
             (obs.source_entity or obs.entity).upper(), obs.metric, obs.period,
             obs.observation_type, obs.value, obs.unit, obs.evidence_span,
             obs.observed_at.isoformat(), obs.extraction_confidence, fact_frozen))
        projection_id = self.observation_projection_id(
            obs, projection_profile, projection_version)
        self._write(
            "INSERT OR REPLACE INTO data_evidence_projections "
            "(projection_id,fact_id,legacy_observation_id,profile,profile_version,"
            " concept,stance,direction,payload,created_at,superseded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,NULL)",
            (projection_id, fact_id, obs.id, projection_profile, projection_version,
             obs.concept, obs.stance, obs.direction,
             json.dumps({"observation_id": obs.id}, ensure_ascii=False),
             obs.observed_at.isoformat()))
        self._write(
            "INSERT OR REPLACE INTO data_evidence_observations "
            "(id,document_id,source_url,entity,source_entity,metric,concept,period,"
            " observation_type,stance,direction,value,unit,evidence_span,observed_at,"
            " discovery_evidence,extraction_confidence) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (obs.id, obs.document_id, obs.source_url, obs.entity.upper(),
             (obs.source_entity or obs.entity).upper(), obs.metric,
             obs.concept, obs.period, obs.observation_type, obs.stance, obs.direction,
             obs.value, obs.unit, obs.evidence_span, obs.observed_at.isoformat(),
             frozen, obs.extraction_confidence))
        return existing is None

    def save_evidence_failure(self, fail) -> None:
        """Record a failed extraction — "could not read" ≠ "says nothing"."""
        at = getattr(fail, "at", None)
        self._write(
            "INSERT OR REPLACE INTO data_evidence_failures (document_id,entity,reason,at) "
            "VALUES (?,?,?,?)",
            (fail.document_id, (fail.entity or "").upper(), fail.reason,
             at.isoformat() if hasattr(at, "isoformat") else str(at or "")))

    def supersede_document_observations(self, document_id: str, source_entity: str, *,
                                        at=None) -> int:
        """Retire the previous extraction of one document by one speaker (rows kept)."""
        stamp = (at or datetime.now().astimezone()).isoformat(timespec="seconds")
        source = (source_entity or "").upper()
        cur = self._write(
            "UPDATE data_evidence_observations SET superseded_at=? "
            "WHERE document_id=? AND source_entity=? AND superseded_at IS NULL",
            (stamp, document_id, source))
        fact_ids = [r["fact_id"] for r in self.conn.execute(
            "SELECT fact_id FROM data_evidence_facts WHERE document_id=? AND source_entity=? "
            "AND superseded_at IS NULL", (document_id, source)).fetchall()]
        self._write(
            "UPDATE data_evidence_facts SET superseded_at=? WHERE document_id=? "
            "AND source_entity=? AND superseded_at IS NULL", (stamp, document_id, source))
        if fact_ids:
            self._write(
                "UPDATE data_evidence_projections SET superseded_at=? WHERE fact_id IN (%s) "
                "AND superseded_at IS NULL" % ",".join("?" * len(fact_ids)),
                [stamp, *fact_ids])
        return cur.rowcount

    def freeze_observations_as_discovery(self, observation_ids: list[str]) -> int:
        """Mark observations as discovery material (sticky across later rewrites)."""
        ids = [i for i in observation_ids if i]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        cur = self._write(
            f"UPDATE data_evidence_observations SET discovery_evidence=1 "
            f"WHERE id IN ({placeholders})", ids)
        self._write(
            f"UPDATE data_evidence_facts SET discovery_evidence=1 WHERE fact_id IN ("
            f" SELECT fact_id FROM data_evidence_projections "
            f" WHERE legacy_observation_id IN ({placeholders}))", ids)
        return cur.rowcount

    def has_observations_for_document(self, document_id: str) -> bool:
        """Document-level idempotence: never re-fetch a filing already extracted."""
        return self.conn.execute(
            "SELECT 1 FROM data_evidence_observations WHERE document_id=? LIMIT 1",
            (document_id,)).fetchone() is not None

    # --- measurement write side ------------------------------------------ #
    def save_measurement_points(self, source, points, *, fetched_at=None) -> int:
        """Persist immutable raw point vintages. Returns newly accepted versions."""
        stamp = (fetched_at or datetime.now().astimezone()).isoformat(timespec="seconds")
        saved = 0
        for point in points:
            series = point.series or "value"
            series_id = f"{source.id}:{series}"
            self._write(
                "INSERT INTO data_measurement_series "
                "(series_id,source_id,series,label,entity,unit,cadence,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(series_id) DO UPDATE SET "
                "label=excluded.label,entity=excluded.entity,unit=excluded.unit,"
                "cadence=excluded.cadence,updated_at=excluded.updated_at",
                (series_id, source.id, series, getattr(source, "label", source.id),
                 getattr(source, "entity", ""), point.unit,
                 getattr(source, "cadence", ""), stamp))
            published = point.published_at.isoformat() if point.published_at else ""
            # Exclude yoy/mom by construction: those are transformations, even when a
            # provider happens to include them in the response DTO.
            raw = point.model_dump(mode="json", exclude={"yoy", "mom"})
            payload = json.dumps(raw, ensure_ascii=False, sort_keys=True)
            content_hash = hashlib.sha256(payload.encode()).hexdigest()
            point_id = hashlib.sha1(
                f"{series_id}|{point.period}|{content_hash}".encode()).hexdigest()[:20]
            cur = self._write(
                "INSERT OR IGNORE INTO data_measurement_points "
                "(point_id,series_id,period,value,unit,published_at,fetched_at,"
                " content_hash,raw_payload) VALUES (?,?,?,?,?,?,?,?,?)",
                (point_id, series_id, point.period, point.value, point.unit,
                 published, stamp, content_hash, payload))
            saved += max(0, cur.rowcount)
        return saved

    def measurements(self, *, source_id: str | None = None, series: str | None = None,
                     since: str | None = None, entity: str | None = None,
                     as_of=None, latest_only: bool = True,
                     limit: int = 5000) -> list[dict]:
        """Query point vintages, optionally as they were knowable at `as_of`."""
        sql = ("SELECT p.*,s.source_id,s.series,s.label,s.entity,s.cadence "
               "FROM data_measurement_points p JOIN data_measurement_series s "
               "ON s.series_id=p.series_id WHERE 1=1")
        args: list = []
        if source_id:
            sql += " AND s.source_id=?"; args.append(source_id)
        if series:
            sql += " AND s.series=?"; args.append(series)
        if entity:
            sql += " AND s.entity=?"; args.append(entity.upper())
        if since:
            sql += " AND p.period>=?"; args.append(since)
        cutoff = as_of.isoformat(timespec="seconds") if as_of else None
        if cutoff:
            # Both conditions matter: a backdated published_at does not mean this
            # system possessed the point before fetched_at.
            sql += " AND p.fetched_at<=? AND (p.published_at='' OR p.published_at<=?)"
            args.extend([cutoff, cutoff])
        if latest_only:
            sql += (" AND NOT EXISTS (SELECT 1 FROM data_measurement_points newer "
                    "WHERE newer.series_id=p.series_id AND newer.period=p.period "
                    "AND newer.fetched_at>p.fetched_at)")
            if cutoff:
                # The newer-version exclusion must use the same historical knowledge
                # boundary, otherwise a future revision hides the point known then.
                sql = sql[:-1] + " AND newer.fetched_at<=?)"
                args.append(cutoff)
        return self._rows(sql + " ORDER BY p.period, p.fetched_at LIMIT ?", [*args, limit])

    def projection_lineage(self, projection_id: str, *,
                           task_projection: dict | None = None) -> dict | None:
        """Resolve either projection family back to its fact/document/source input.

        `task_projection` is the row from Workflow memory's `task_projections` (that
        table is NOT data-layer-owned, so the caller hands it in); evidence
        projections, facts, versions and documents all live here.
        """
        def version_and_document(version_id: str, document_id: str) -> tuple[dict | None, dict | None]:
            version = self.conn.execute(
                "SELECT * FROM data_document_versions WHERE version_id=?",
                (version_id,)).fetchone() if version_id else None
            doc = self.conn.execute(
                "SELECT * FROM data_documents WHERE document_id=?",
                (document_id,)).fetchone() if document_id else None
            return (dict(version) if version else None, dict(doc) if doc else None)

        if task_projection:
            out = {"projection": dict(task_projection), "fact": None,
                   "document_version": None, "document": None}
            if task_projection.get("input_kind") == "document_version":
                version, doc = version_and_document(
                    task_projection.get("input_ref", ""), "")
                if version:
                    doc = self.conn.execute(
                        "SELECT * FROM data_documents WHERE document_id=?",
                        (version["document_id"],)).fetchone()
                    doc = dict(doc) if doc else None
                out["document_version"], out["document"] = version, doc
            return out
        projected = self.conn.execute(
            "SELECT * FROM data_evidence_projections WHERE projection_id=?",
            (projection_id,)).fetchone()
        if not projected:
            return None
        fact = self.conn.execute(
            "SELECT * FROM data_evidence_facts WHERE fact_id=?",
            (projected["fact_id"],)).fetchone()
        out = {"projection": dict(projected), "fact": dict(fact) if fact else None,
               "document_version": None, "document": None}
        if fact:
            version, doc = version_and_document(
                fact["document_version_id"] or "", fact["document_id"])
            out["document_version"], out["document"] = version, doc
        return out

    # Writer methods deliberately cover only reusable data assets. Workflow
    # projections, decisions and trades stay in ats.memory.
    @staticmethod
    def document_version_id(document_id: str, content_hash: str) -> str:
        return f"{document_id}@{content_hash[:16]}"

    def _write(self, sql: str, args=()) -> sqlite3.Cursor:
        if not self.writable:
            raise RuntimeError("platform repository is read-only")
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur

    def save_document(self, doc, *, ok: bool = True, note: str = "") -> None:
        stamp = doc.fetched_at or datetime.now().astimezone().isoformat(timespec="seconds")
        self._write("INSERT OR REPLACE INTO data_documents (document_id,entity,period,doc_type,source,source_url,local_path,sha256,chars,ok,note,fetched_at,external_id,title,published_at,completeness,truncation_reason,carrier_format,mime_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (doc.document_id,doc.symbol.upper(),doc.period,doc.doc_type,doc.source,doc.source_url,str(doc.path) if doc.path else "",doc.sha256,len(doc.text or ""),int(ok),note,stamp,getattr(doc,"external_id",""),getattr(doc,"title",""),getattr(doc,"published_at",""),getattr(doc,"completeness","full"),getattr(doc,"truncation_reason",""),getattr(doc,"carrier_format",""),getattr(doc,"mime_source","")))
        if ok and doc.sha256:
            version_id = self.document_version_id(doc.document_id, doc.sha256)
            self._write("INSERT OR IGNORE INTO data_document_versions (version_id,document_id,content_hash,local_path,chars,source_url,fetched_at,created_at) VALUES (?,?,?,?,?,?,?,?)", (version_id,doc.document_id,doc.sha256,str(getattr(doc,"version_path",None) or doc.path or ""),len(doc.text or ""),doc.source_url,stamp,stamp))
            for ordinal, start in enumerate(range(0, len(doc.text or ""), 2400)):
                text = (doc.text or "")[start:start + 2400]
                digest = hashlib.sha256(text.encode()).hexdigest()
                chunk_id = hashlib.sha1(f"{version_id}|{ordinal}|{digest}".encode()).hexdigest()[:20]
                self._write("INSERT OR IGNORE INTO data_document_chunks (chunk_id,version_id,ordinal,char_start,char_end,text,content_hash) VALUES (?,?,?,?,?,?,?)",(chunk_id,version_id,ordinal,start,start+len(text),text,digest))
        entities = {doc.symbol.upper()}
        entities.update(e.upper() for e in getattr(doc, "related_entities", ()) if e)
        self.link_document_entities(
            doc.document_id,
            [(entity, "primary" if entity == doc.symbol.upper() else "mentioned")
             for entity in sorted(entities)])

    def document_by_external_id(self, external_id: str) -> dict | None:
        row=self.conn.execute("SELECT * FROM data_documents WHERE external_id=? AND ok=1 ORDER BY fetched_at DESC LIMIT 1",(external_id,)).fetchone(); return dict(row) if row else None

    def document_by_content_hash(self, content_hash: str, *, entity: str | None = None) -> dict | None:
        sql=("SELECT d.*,v.version_id,v.content_hash AS version_hash "
             "FROM data_document_versions v JOIN data_documents d ON d.document_id=v.document_id "
             "WHERE v.content_hash=? AND d.ok=1"); args=[content_hash]
        if entity: sql+=(" AND (d.entity=? OR EXISTS (SELECT 1 FROM data_document_entities de "
                         "WHERE de.document_id=d.document_id AND de.entity=?))"); args.extend([entity.upper(),entity.upper()])
        row=self.conn.execute(sql+" ORDER BY v.fetched_at DESC LIMIT 1",args).fetchone(); return dict(row) if row else None

    def document_by_story(self, title: str, published_at: str = "") -> dict | None:
        key=" ".join("".join(ch if ch.isalnum() else " " for ch in (title or "").lower()).split())
        for row in self.conn.execute("SELECT * FROM data_documents WHERE ok=1 AND substr(published_at,1,10)=? ORDER BY chars DESC",((published_at or "")[:10],)):
            value=" ".join("".join(ch if ch.isalnum() else " " for ch in (row["title"] or "").lower()).split())
            if value==key: return dict(row)
        return None

    def link_document_entities(self, document_id: str, entities, *, relation: str = "mentioned") -> int:
        # Accepts either a flat iterable of entity symbols or (entity, relation) pairs,
        # so the document writer can mark the document's own symbol as `primary` in one
        # call — exactly as Workflow memory did before the cutover.
        pairs: list[tuple[str, str]] = []
        for item in entities or ():
            if isinstance(item, (tuple, list)) and len(item) == 2:
                pairs.append((str(item[0]).upper(), str(item[1])))
            elif item:
                pairs.append((str(item).upper(), relation))
        if not pairs:
            return 0
        before=self.conn.total_changes; self.conn.executemany("INSERT OR IGNORE INTO data_document_entities (document_id,entity,relation) VALUES (?,?,?)",[(document_id,entity,rel) for entity,rel in pairs]); self.conn.commit(); return self.conn.total_changes-before

    def save_document_alias(self, document_id: str, *, source: str, source_url: str="", external_id: str="", title: str="", published_at: str="", metadata: dict|None=None) -> str:
        identity=external_id or source_url or f"{title}|{published_at}"; alias_id=hashlib.sha1(f"{source}|{identity}".encode()).hexdigest()[:24]
        self._write("INSERT OR REPLACE INTO data_document_aliases (alias_id,document_id,source,source_url,external_id,title,published_at,metadata_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",(alias_id,document_id,source,source_url,external_id,title,published_at,json.dumps(metadata or {},ensure_ascii=False,sort_keys=True),datetime.now().astimezone().isoformat(timespec="seconds"))); return alias_id

    def save_document_candidate(self, candidate, validation, *, raw_path: str="", document_id: str="") -> None:
        from ...admission import result_json
        from ...document_types import semantic_type
        def semantic(value):
            try: return semantic_type(value).value
            except (KeyError, ValueError): return str(value or "")
        self._write("INSERT OR REPLACE INTO data_document_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(candidate.candidate_id,document_id,validation.status,candidate.expected_entity.upper(),candidate.claimed_entity.upper(),candidate.target_period,candidate.claimed_period,semantic(candidate.expected_semantic),semantic(candidate.claimed_semantic),str(candidate.carrier_format),candidate.completeness,candidate.source,candidate.source_url,candidate.external_id,candidate.title,candidate.published_at,candidate.discovered_at,candidate.content_hash,len(candidate.text or ""),raw_path,json.dumps(validation.reason_codes,ensure_ascii=False),result_json(validation)))

    def register_data_source(self, source, *, kind: str="unstructured", at=None) -> None:
        stamp=(at or datetime.now().astimezone()).isoformat(timespec="seconds"); self._write("INSERT OR REPLACE INTO data_sources VALUES (?,?,?,?,?,?,?)",(source.id,kind,getattr(source,"label",""),getattr(source,"adapter",""),getattr(source,"cadence",""),getattr(source,"entity",""),stamp))

    def begin_ingestion(self, source_id: str, *, kind: str, at=None) -> str:
        import uuid
        stamp=(at or datetime.now().astimezone()).isoformat(timespec="microseconds")
        # uuid4 keeps two runs started in the same microsecond from colliding.
        run_id=hashlib.sha1(f"{source_id}|{kind}|{stamp}|{uuid.uuid4().hex}".encode()).hexdigest()[:20]
        self._write("INSERT INTO data_ingestion_runs (run_id,source_id,kind,started_at,status) VALUES (?,?,?,?,'running')",(run_id,source_id,kind,stamp)); return run_id

    def finish_ingestion(self, run_id: str, *, status: str, discovered:int=0, accepted:int=0, quarantined:int=0, reason_codes=None, snapshot_updated_at:str="", snapshot_lag_hours=None, note:str="", at=None) -> None:
        stamp=(at or datetime.now().astimezone()).isoformat(timespec="seconds"); self._write("UPDATE data_ingestion_runs SET completed_at=?,status=?,discovered=?,accepted=?,quarantined=?,reason_codes=?,snapshot_updated_at=?,snapshot_lag_hours=?,note=? WHERE run_id=?",(stamp,status,discovered,accepted,quarantined,json.dumps(reason_codes or {},ensure_ascii=False,sort_keys=True),snapshot_updated_at,snapshot_lag_hours,note,run_id))

    def newsletter_cursor(self, mailbox:str, folder:str, sender:str) -> dict|None:
        row=self.conn.execute("SELECT * FROM data_newsletter_cursors WHERE mailbox=? AND folder=? AND sender=?",(mailbox,folder,sender)).fetchone(); return dict(row) if row else None

    def save_newsletter_cursor(self, *, mailbox:str, folder:str, sender:str, uidvalidity:str, last_uid:int, last_message_id:str, watermark:str) -> None:
        self._write("INSERT OR REPLACE INTO data_newsletter_cursors VALUES (?,?,?,?,?,?,?,?)",(mailbox,folder,sender,uidvalidity,last_uid,last_message_id,watermark,datetime.now().astimezone().isoformat(timespec="seconds")))


def get_platform_unstructured_repository() -> PlatformUnstructuredRepository:
    """Open the released data repository read-only.

    "Nothing ingested yet" is a valid state, not an error: a read path must not need
    a writer to have run first. An absent database is therefore bootstrapped to the
    empty schema before the read handle is opened, instead of failing with
    "unable to open database file" — which is what the chain report hit on a fresh
    checkout, where every read precedes the first write.
    """
    from ...runtime import platform_data_db_path

    path = platform_data_db_path()
    if not Path(path).exists():
        PlatformUnstructuredRepository(path, writable=True).close()
    return PlatformUnstructuredRepository(path)


def get_platform_unstructured_store() -> PlatformUnstructuredRepository:
    """Open the platform writer used by document/news/research ingestion pipelines."""
    from ...runtime import platform_data_db_path
    return PlatformUnstructuredRepository(platform_data_db_path(), writable=True)


__all__ = ["PlatformUnstructuredRepository", "get_platform_unstructured_repository",
           "get_platform_unstructured_store"]
