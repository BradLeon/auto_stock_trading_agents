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
        CREATE TABLE IF NOT EXISTS data_sources (source_id TEXT PRIMARY KEY,kind TEXT NOT NULL,label TEXT NOT NULL,adapter TEXT NOT NULL,cadence TEXT NOT NULL,entity TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS data_ingestion_runs (run_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,kind TEXT NOT NULL,started_at TEXT NOT NULL,completed_at TEXT NOT NULL DEFAULT '',status TEXT NOT NULL,discovered INTEGER NOT NULL DEFAULT 0,accepted INTEGER NOT NULL DEFAULT 0,quarantined INTEGER NOT NULL DEFAULT 0,reason_codes TEXT NOT NULL DEFAULT '{}',snapshot_updated_at TEXT NOT NULL DEFAULT '',snapshot_lag_hours REAL,note TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS data_newsletter_cursors (mailbox TEXT NOT NULL,folder TEXT NOT NULL,sender TEXT NOT NULL,uidvalidity TEXT NOT NULL,last_uid INTEGER NOT NULL,last_message_id TEXT NOT NULL,watermark TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(mailbox,folder,sender));
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
        self.link_document_entities(doc.document_id, {doc.symbol, *getattr(doc,"related_entities",())}, relation="mentioned")

    def document_by_external_id(self, external_id: str) -> dict | None:
        row=self.conn.execute("SELECT * FROM data_documents WHERE external_id=? AND ok=1 ORDER BY fetched_at DESC LIMIT 1",(external_id,)).fetchone(); return dict(row) if row else None

    def document_by_content_hash(self, content_hash: str, *, entity: str | None = None) -> dict | None:
        sql="SELECT d.* FROM data_document_versions v JOIN data_documents d ON d.document_id=v.document_id WHERE v.content_hash=?"; args=[content_hash]
        if entity: sql+=" AND d.entity=?"; args.append(entity.upper())
        row=self.conn.execute(sql+" ORDER BY v.fetched_at DESC LIMIT 1",args).fetchone(); return dict(row) if row else None

    def document_by_story(self, title: str, published_at: str = "") -> dict | None:
        key=" ".join("".join(ch if ch.isalnum() else " " for ch in (title or "").lower()).split())
        for row in self.conn.execute("SELECT * FROM data_documents WHERE ok=1 AND substr(published_at,1,10)=? ORDER BY chars DESC",((published_at or "")[:10],)):
            value=" ".join("".join(ch if ch.isalnum() else " " for ch in (row["title"] or "").lower()).split())
            if value==key: return dict(row)
        return None

    def link_document_entities(self, document_id: str, entities, *, relation: str = "mentioned") -> int:
        before=self.conn.total_changes; self.conn.executemany("INSERT OR IGNORE INTO data_document_entities (document_id,entity,relation) VALUES (?,?,?)",[(document_id,str(x).upper(),relation) for x in entities if x]); self.conn.commit(); return self.conn.total_changes-before

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
        stamp=(at or datetime.now().astimezone()).isoformat(timespec="microseconds"); run_id=hashlib.sha1(f"{source_id}|{kind}|{stamp}".encode()).hexdigest()[:20]; self._write("INSERT INTO data_ingestion_runs (run_id,source_id,kind,started_at,status) VALUES (?,?,?,?,'running')",(run_id,source_id,kind,stamp)); return run_id

    def finish_ingestion(self, run_id: str, *, status: str, discovered:int=0, accepted:int=0, quarantined:int=0, reason_codes=None, snapshot_updated_at:str="", snapshot_lag_hours=None, note:str="", at=None) -> None:
        stamp=(at or datetime.now().astimezone()).isoformat(timespec="seconds"); self._write("UPDATE data_ingestion_runs SET completed_at=?,status=?,discovered=?,accepted=?,quarantined=?,reason_codes=?,snapshot_updated_at=?,snapshot_lag_hours=?,note=? WHERE run_id=?",(stamp,status,discovered,accepted,quarantined,json.dumps(reason_codes or {},ensure_ascii=False,sort_keys=True),snapshot_updated_at,snapshot_lag_hours,note,run_id))

    def newsletter_cursor(self, mailbox:str, folder:str, sender:str) -> dict|None:
        row=self.conn.execute("SELECT * FROM data_newsletter_cursors WHERE mailbox=? AND folder=? AND sender=?",(mailbox,folder,sender)).fetchone(); return dict(row) if row else None

    def save_newsletter_cursor(self, *, mailbox:str, folder:str, sender:str, uidvalidity:str, last_uid:int, last_message_id:str, watermark:str) -> None:
        self._write("INSERT OR REPLACE INTO data_newsletter_cursors VALUES (?,?,?,?,?,?,?,?)",(mailbox,folder,sender,uidvalidity,last_uid,last_message_id,watermark,datetime.now().astimezone().isoformat(timespec="seconds")))


def get_platform_unstructured_repository() -> PlatformUnstructuredRepository:
    from ...runtime import platform_data_db_path

    return PlatformUnstructuredRepository(platform_data_db_path())


def get_platform_unstructured_store() -> PlatformUnstructuredRepository:
    """Open the platform writer used by document/news/research ingestion pipelines."""
    from ...runtime import platform_data_db_path
    return PlatformUnstructuredRepository(platform_data_db_path(), writable=True)


__all__ = ["PlatformUnstructuredRepository", "get_platform_unstructured_repository",
           "get_platform_unstructured_store"]
