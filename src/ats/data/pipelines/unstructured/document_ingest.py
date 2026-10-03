"""Governed, fixed-source SEC and transcript acquisition (no search fallback).

DefeatBeta rows are immutable *discovery snapshots*, never SEC originals.  The
official filing body is fetched separately and must agree with its accession.
All operational calls require a persistent-queue worker lease before I/O.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from ....config import REPO_ROOT
from ... import admission, document_assets
from ...document_types import DocumentSemantic
from ...persistent_queue import require_queue_worker
from ...runtime.repository import platform_data_db_path
from ...sources.defeatbeta import query_documents as _query_hf
from ...sources.defeatbeta import snapshot as _hf_snapshot
from ...stores.unstructured import get_platform_unstructured_store
from .sec import SECSourceUnavailable, SECTransport, fetch_filing
from .sec import validate_filing as _filing_row
from .transcripts import parse_transcript as _transcript_row

DOCUMENT_SOURCE_IDS = frozenset({"defeatbeta_sec_filing_index", "sec_edgar_filing_body",
                       "defeatbeta_earnings_transcript", "ai_hardware_knowledge_corpus"})
_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_fixed_source_snapshots (
    snapshot_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, revision TEXT NOT NULL,
    spec_sha256 TEXT NOT NULL, file_sha256 TEXT NOT NULL, file_updated_at TEXT NOT NULL,
    checked_at TEXT NOT NULL, source_url TEXT NOT NULL, run_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS data_fixed_source_rows (
    row_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
    entity TEXT NOT NULL, natural_key TEXT NOT NULL, content_hash TEXT NOT NULL,
    raw_json TEXT NOT NULL, document_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', observed_at TEXT NOT NULL,
    UNIQUE(source_id,snapshot_id,natural_key,content_hash));
CREATE INDEX IF NOT EXISTS ix_fixed_source_entity
    ON data_fixed_source_rows(source_id,entity,natural_key,observed_at);
"""


def _rows() -> dict[str, dict[str, Any]]:
    registry = yaml.safe_load((REPO_ROOT / "config/data/unstructured.yaml").read_text(
        encoding="utf-8")) or {}
    return registry.get("sources") or {}


def _stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      default=str, separators=(",", ":")).encode("utf-8")


def _universe(entity: str, *, excluded_entities: tuple[str, ...] = ()) -> list[str]:
    excluded = {value.upper() for value in excluded_entities}
    if entity:
        if entity.upper() in excluded:
            raise ValueError("fixed_source_market_out_of_scope")
        return [entity.upper()]
    from ...pead_official_disclosures import active_pead_targets

    sector = yaml.safe_load((REPO_ROOT / "config/sectors/ai_hardware.yaml").read_text(
        encoding="utf-8")) or {}
    symbols = set(active_pead_targets())
    for layer in sector.get("layers") or []:
        for ticker in layer.get("tickers") or []:
            symbol = ticker.get("symbol") if isinstance(ticker, dict) else ticker
            if symbol:
                symbols.add(str(symbol).upper())
    # Market exclusions belong to the registered source policy; do not infer
    # market from punctuation (US tickers can also contain a dot).
    return sorted(symbol for symbol in symbols if symbol not in excluded)


def _record_row(store: Any, *, source_id: str, snapshot_id: str, entity: str,
                natural_key: str, row: dict[str, Any], status: str,
                document_id: str = "", publication_hash: str = "",
                reason: str = "", now: datetime) -> tuple[str, bool]:
    payload = _canonical(row)
    # A fixed index row and an official body can share the same metadata while
    # the official text is revised.  Include admission/body identity in this
    # row-version hash; the raw JSON remains separately recoverable.
    content_hash = _digest(payload + b"|" + status.encode() + b"|" +
                           publication_hash.encode() +
                           (b"|" + reason.encode() if reason else b""))
    if source_id == "defeatbeta_sec_filing_index":
        existing = store.conn.execute(
            "SELECT row_id FROM data_fixed_source_rows WHERE source_id=? AND "
            "snapshot_id=? AND natural_key=? AND raw_json=? AND status=? LIMIT 1",
            (source_id, snapshot_id, natural_key, payload.decode("utf-8"), status)).fetchone()
        if existing:
            return str(existing[0]), False
    row_id = hashlib.sha256(
        f"{source_id}|{snapshot_id}|{natural_key}|{content_hash}|"
        f"{status}|{publication_hash}".encode()).hexdigest()[:32]
    created = store.conn.execute(
        "SELECT 1 FROM data_fixed_source_rows WHERE row_id=?", (row_id,)).fetchone() is None
    store.conn.execute(
        "INSERT OR IGNORE INTO data_fixed_source_rows VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (row_id, source_id, snapshot_id, entity, natural_key, content_hash,
         payload.decode("utf-8"), document_id, status, reason, _stamp(now)))
    store.conn.commit()
    return row_id, created


def _publish_document(store: Any, candidate: admission.CandidateDocument,
                      *, source_issues: list[admission.ValidationIssue] | None = None
                      ) -> tuple[str, str]:
    result = admission.validate_candidate(candidate, extensions=[
        lambda _candidate: source_issues or []]) if candidate.target_period else None
    if result is None:
        issues = tuple(source_issues or [])
        if len(candidate.text.strip()) < candidate.min_chars:
            issues += (admission.ValidationIssue("quality", "body_too_short"),)
        if candidate.completeness != "full":
            issues += (admission.ValidationIssue("quality", "fixed_body_not_full"),)
        result = admission.ValidationResult(
            "quarantined" if issues else "accepted", issues,
            {"official_source": not issues})
    if not result.accepted:
        raw_path = admission._write_quarantine(candidate)
        store.save_document_candidate(candidate, result, raw_path=str(raw_path or ""))
        return "quarantined", ""
    prior = store.document_by_external_id(candidate.external_id)
    managed_root = Path(os.environ.get("ATS_DOCS_ROOT") or "").resolve()
    prior_path = Path(str((prior or {}).get("local_path") or ""))
    if prior and prior.get("source") == candidate.source and prior_path.is_file() and \
            prior_path.resolve().is_relative_to(managed_root) and \
            (prior.get("content_hash") or prior.get("sha256")) == candidate.content_hash:
        store.save_document_candidate(
            candidate, admission.ValidationResult("no_change", (), {"unchanged": True}),
            document_id=prior["document_id"])
        return "no_change", prior["document_id"]
    document = document_assets.ingest(
        entity=candidate.expected_entity,
        key=document_assets.stable_key(
            f"managed:{candidate.source}:{candidate.external_id}", prefix="managed"),
        doc_type=str(candidate.expected_semantic), text=candidate.text,
        source=candidate.source, source_url=candidate.source_url,
        external_id=candidate.external_id, title=candidate.title,
        published_at=candidate.published_at, min_chars=candidate.min_chars,
        completeness="full", carrier_format=str(candidate.carrier_format),
        period=candidate.target_period or "", store=store)
    if document is None:
        store.save_document_candidate(candidate, admission.ValidationResult(
            "quarantined", (admission.ValidationIssue("persistence", "write_failed"),),
            {"persistence": False}))
        return "quarantined", ""
    store.save_document_candidate(candidate, result, document_id=document.document_id)
    return "accepted", document.document_id


def ingest_documents(source_id: str, *, entity: str = "", now: datetime | None = None,
                        store: Any = None) -> dict[str, Any]:
    """Refresh one registered fixed source; external adapters are called only here."""
    if source_id not in DOCUMENT_SOURCE_IDS:
        raise ValueError("fixed_source_not_registered")
    rows = _rows()
    source = rows[source_id]
    if source.get("source_kind") not in {
            "fixed_index", "official_body", "fixed_transcript", "repository_corpus"}:
        raise ValueError("fixed_source_kind_invalid")
    store_path = getattr(store, "path", None)
    production_store = store is None or (store_path and Path(store_path).resolve() ==
                                         platform_data_db_path().resolve())
    if production_store:
        require_queue_worker(source_id)
    previous_docs_root = os.environ.get("ATS_DOCS_ROOT")
    now = now or datetime.now(UTC)
    own_store = store is None
    store = store or get_platform_unstructured_store()
    if production_store:
        # A data refresh must not deposit research assets in the human Obsidian
        # workspace selected by legacy PEAD settings.
        os.environ["ATS_DOCS_ROOT"] = str(REPO_ROOT / "var/data/fixed_documents")
    try:
        store.conn.executescript(_SCHEMA)
        store.register_data_source(SimpleNamespace(
            id=source_id, label=source["provider"], adapter=source["adapter"],
            cadence=source["cadence"], entity=""), at=now)
        run_id = store.begin_ingestion(source_id, kind="fixed_source_refresh", at=now)
        accepted = quarantined = unchanged = failed = 0
        row_ids: list[str] = []
        document_ids: list[str] = []
        coverage_missing_entities: list[str] = []
        try:
            policy = source["policy"]
            kind = source["source_kind"]
            if kind in {"fixed_index", "fixed_transcript"}:
                symbols = _universe(entity, excluded_entities=tuple(
                    str(value) for value in policy.get("excluded_entities") or ()))
                row_limit = int(source["request_budget"]["max_rows_per_run"])
                per_symbol_limit = int(source["request_budget"]["max_rows_per_symbol"])
                if len(symbols) * per_symbol_limit > row_limit:
                    raise ValueError("fixed_universe_exceeds_row_budget")
                snapshot = _hf_snapshot(policy)
                source_rows = _query_hf(snapshot, source_id, symbols=symbols,
                                        limit=row_limit,
                                        per_symbol_limit=per_symbol_limit)
                coverage_missing_entities = sorted(
                    set(symbols) - {str(row.get("symbol") or "").upper()
                                    for row in source_rows})
            elif kind == "official_body":
                transport = SECTransport(source["request_budget"])
                snapshot = {"revision": "sec_official", "spec_sha256": "",
                            "file_sha256": "", "file_updated_at": "",
                            "source_url": "https://www.sec.gov/Archives/edgar/data/"}
                index_id = str(policy["discovery_source"])
                # Each daily run has a bounded SEC request budget.  Prefer
                # never-published accessions and dedupe repeat index snapshots,
                # otherwise the same first 20 rows can starve all other filings.
                query = ("WITH latest AS (SELECT raw_json,natural_key,observed_at,"
                         "ROW_NUMBER() OVER (PARTITION BY natural_key ORDER BY observed_at DESC) rn "
                         "FROM data_fixed_source_rows WHERE source_id=? AND status='accepted'" +
                         (" AND entity=?" if entity else "") +
                         "), published AS (SELECT natural_key,MAX(observed_at) last_attempt,"
                         "MAX(CASE WHEN status='accepted' "
                         "THEN 1 ELSE 0 END) accepted FROM data_fixed_source_rows "
                         "WHERE source_id=? GROUP BY natural_key) "
                         "SELECT latest.raw_json FROM latest LEFT JOIN published USING(natural_key) "
                         "WHERE latest.rn=1 ORDER BY COALESCE(published.accepted,0), "
                         "COALESCE(published.last_attempt,''), "
                         "json_extract(latest.raw_json,'$.filing_date') DESC LIMIT ?")
                params: list[Any] = [index_id]
                if entity:
                    params.append(entity.upper())
                params.append(source_id)
                params.append(int(source["request_budget"]["max_documents_per_run"]))
                source_rows = [json.loads(row[0]) for row in store.conn.execute(query, params)]
            else:
                manifest = yaml.safe_load((REPO_ROOT / policy["manifest"]).read_text(
                    encoding="utf-8")) or {}
                expected = {str(path) for layer in manifest.get("layers") or []
                            for path in (layer.get("structure_notes") or {}).values()}
                members = set(policy["members"])
                if expected != members:
                    raise ValueError("knowledge_manifest_membership_drift")
                source_rows = [{"path": name, "text": (REPO_ROOT / name).read_text(
                    encoding="utf-8")} for name in sorted(members)]
                if len(source_rows) > int(source["request_budget"]["max_files_per_run"]):
                    raise ValueError("knowledge_file_budget_exceeded")
                snapshot = {"revision": "repository_managed", "spec_sha256": "",
                            "file_sha256": _digest(_canonical([
                                (row["path"], _digest(row["text"].encode())) for row in source_rows])),
                            "file_updated_at": "", "source_url": str(REPO_ROOT / policy["manifest"])}
            snapshot_id = _digest(_canonical([source_id, snapshot]))[:32]
            updated_at = snapshot.get("file_updated_at") or ""
            snapshot_lag_hours = None
            if updated_at:
                updated = datetime.fromisoformat(updated_at)
                if updated.tzinfo is None:
                    raise ValueError("snapshot_timestamp_unzoned")
                snapshot_lag_hours = max(0.0, (now - updated).total_seconds() / 3600)
            # _hf_snapshot resolves the current upstream commit and verifies its
            # spec/file hash.  A long quiet interval between earnings seasons is
            # not staleness; failed checks, missing coverage and bad manifests
            # are separate, explicit failures.
            snapshot_stale = False
            store.conn.execute("INSERT OR IGNORE INTO data_fixed_source_snapshots VALUES "
                               "(?,?,?,?,?,?,?,?,?)",
                               (snapshot_id, source_id, snapshot["revision"],
                                snapshot["spec_sha256"], snapshot["file_sha256"],
                                snapshot["file_updated_at"], _stamp(now),
                                snapshot["source_url"], run_id))
            store.conn.commit()
            for row in source_rows:
                issues: list[admission.ValidationIssue] = []
                publication_hash = ""
                try:
                    if kind == "fixed_index":
                        symbol, natural_key = _filing_row(row)
                        status, doc_id = "accepted", ""
                    elif kind == "fixed_transcript":
                        symbol, period, text = _transcript_row(row)
                        native_id = row.get("transcripts_id")
                        natural_key = f"{symbol}:{period}" + (
                            f":{native_id}" if native_id not in (None, "") else "")
                        candidate = admission.CandidateDocument(
                            expected_entity=symbol, claimed_entity=symbol,
                            target_period=period, claimed_period=period,
                            expected_semantic=DocumentSemantic.EARNINGS_TRANSCRIPT,
                            claimed_semantic=DocumentSemantic.EARNINGS_TRANSCRIPT,
                            text=text, source=source_id,
                            source_url=snapshot["source_url"],
                            external_id=f"{source_id}:{natural_key}",
                            title=f"{symbol} {period} earnings call",
                            published_at=str(row.get("report_date") or ""),
                            carrier_format="structured_text", min_chars=500,
                            metadata={"snapshot_id": snapshot_id,
                                      "file_sha256": snapshot["file_sha256"]})
                        publication_hash = candidate.content_hash
                        status, doc_id = _publish_document(store, candidate)
                    elif kind == "official_body":
                        symbol, natural_key = _filing_row(row)
                        result, semantic = fetch_filing(row, transport)
                        if result.status != "succeeded":
                            failure_codes = sorted({error.error_type for error in result.errors})
                            if result.status == "unreachable" or "RequestBudgetExceeded" in failure_codes:
                                raise SECSourceUnavailable("sec_" + result.status + ":" +
                                    result.stage + ":" + ",".join(failure_codes))
                            raise ValueError("sec_" + result.status + ":" +
                                             result.stage + ":" + ",".join(failure_codes))
                        official_url, text = result.url, result.text
                        row = {**row, "official_document_url": official_url,
                               "document_semantic": semantic, "extraction_stage": result.stage,
                               "declared_type": result.declared_type,
                               "body_sha256": _digest(text.encode("utf-8"))}
                        if not admission.mentions_entity(text, symbol, str(row.get("company_name") or "")):
                            issues.append(admission.ValidationIssue(
                                "identity", "official_body_entity_not_verified"))
                        candidate = admission.CandidateDocument(
                            expected_entity=symbol, claimed_entity=symbol,
                            target_period="", claimed_period="",
                            expected_semantic=semantic,
                            claimed_semantic=semantic,
                            text=text, source=source_id, source_url=official_url,
                            external_id=f"sec:{natural_key}:{semantic}",
                            title=f"{symbol} {row['form_type']} {natural_key}",
                            published_at=str(row.get("filing_date") or ""),
                            carrier_format="html", min_chars=1000,
                            metadata={"index_source_id": policy["discovery_source"],
                                      "accession": natural_key,
                                      "index_filing_url": row["filing_url"],
                                      "extraction_stage": result.stage,
                                      "declared_type": result.declared_type,
                                      "report_date": str(row.get("report_date") or "")})
                        publication_hash = candidate.content_hash
                        status, doc_id = _publish_document(store, candidate,
                                                           source_issues=issues)
                    else:
                        symbol, natural_key = "AI_HARDWARE", str(row["path"])
                        candidate = admission.CandidateDocument(
                            expected_entity=symbol, claimed_entity=symbol,
                            target_period="", claimed_period="",
                            expected_semantic=DocumentSemantic.RESEARCH_ARTICLE,
                            claimed_semantic=DocumentSemantic.RESEARCH_ARTICLE,
                            text=str(row["text"]), source=source_id,
                            source_url=f"repository:{natural_key}",
                            external_id=f"knowledge:{natural_key}", title=Path(natural_key).stem,
                            carrier_format="plain_text", min_chars=100,
                            metadata={"snapshot_id": snapshot_id})
                        publication_hash = candidate.content_hash
                        status, doc_id = _publish_document(store, candidate)
                    row_id, row_created = _record_row(
                        store, source_id=source_id, snapshot_id=snapshot_id, entity=symbol,
                        natural_key=natural_key, row=row,
                        status="accepted" if status == "no_change" else status,
                        document_id=doc_id, publication_hash=publication_hash, now=now)
                    row_ids.append(row_id)
                    if kind == "fixed_index" and not row_created and status == "accepted":
                        status = "no_change"
                    if status == "accepted":
                        accepted += 1
                    elif status == "no_change":
                        unchanged += 1
                    else:
                        quarantined += 1
                    if doc_id and status == "accepted":
                        document_ids.append(doc_id)
                except (ValueError, TypeError, KeyError, SECSourceUnavailable) as exc:
                    unavailable = isinstance(exc, SECSourceUnavailable)
                    failed += int(unavailable)
                    quarantined += int(not unavailable)
                    natural_key = str(row.get("accession_number") or
                                      row.get("transcripts_id") or row.get("path") or "invalid")
                    rejected_row_id, _ = _record_row(
                        store, source_id=source_id, snapshot_id=snapshot_id,
                        entity=str(row.get("symbol") or ""), natural_key=natural_key,
                        row=row, status="failed" if unavailable else "quarantined",
                        reason=type(exc).__name__ + ":" + str(exc),
                        now=now)
                    row_ids.append(rejected_row_id)
            status = ("partial" if quarantined and (accepted or unchanged) else
                      "quarantined" if quarantined else "succeeded" if accepted else
                      "no_change")
            if failed:
                status = "partial" if accepted or unchanged else "failed"
            if kind == "official_body" and not source_rows:
                status = "partial"
                coverage_missing_entities = [entity.upper() if entity else "SEC_INDEX"]
            if snapshot_stale or coverage_missing_entities:
                status = "partial"
            store.finish_ingestion(run_id, status=status, discovered=len(source_rows),
                                   accepted=accepted, quarantined=quarantined,
                                   reason_codes={**({"fixed_source_quarantine": quarantined}
                                                   if quarantined else {}),
                                                 **({"source_unavailable": failed} if failed else {}),
                                                 **({"snapshot_stale": 1}
                                                    if snapshot_stale else {}),
                                                 **({"coverage_missing_entities":
                                                     len(coverage_missing_entities)}
                                                    if coverage_missing_entities else {})},
                                   snapshot_updated_at=updated_at,
                                   snapshot_lag_hours=snapshot_lag_hours,
                                   note=json.dumps({"snapshot_id": snapshot_id,
                                                    "row_ids": row_ids}, sort_keys=True), at=now)
            versions = sorted({str(version.get("version_id")) for doc_id in document_ids
                               for version in [store.latest_document_version(doc_id) or {}]
                               if version.get("version_id")})
            return {"status": status, "source_id": source_id, "run_id": run_id,
                    "snapshot_id": snapshot_id, "row_ids": row_ids,
                    "document_ids": document_ids, "published_version_ids": versions,
                    "accepted": accepted, "quarantined": quarantined,
                    "failed": failed,
                    "no_change": unchanged, "discovered": len(source_rows),
                    "snapshot_stale": snapshot_stale,
                    "snapshot_lag_hours": snapshot_lag_hours,
                    "coverage_missing_entities": coverage_missing_entities,
                    "side_effects": {"llm": 0, "agent": 0, "workflow": 0, "orders": 0}}
        except Exception as exc:
            store.finish_ingestion(run_id, status="failed",
                                   reason_codes={type(exc).__name__: 1}, at=now)
            raise
    finally:
        if production_store:
            if previous_docs_root is None:
                os.environ.pop("ATS_DOCS_ROOT", None)
            else:
                os.environ["ATS_DOCS_ROOT"] = previous_docs_root
        if own_store:
            store.close()


__all__ = ["DOCUMENT_SOURCE_IDS", "ingest_documents"]
