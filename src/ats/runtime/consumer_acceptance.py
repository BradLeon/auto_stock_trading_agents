"""Explicit, side-effect-contained acceptance runs for direct data consumers.

These checks are deliberately invoked by an operator rather than being folded into
normal Agent execution. A transient provider failure must not create a durable
cutover mismatch merely because someone ran a daily workflow at the wrong moment.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterator


def _point_rows(points) -> list[dict[str, Any]]:
    return [{
        "period": point.period,
        "value": point.value,
        "unit": point.unit,
        "yoy": point.yoy,
        "mom": point.mom,
        "published_at": point.published_at.isoformat() if point.published_at else "",
    } for point in points]


def _observation_rows(source, points) -> list[dict[str, Any]]:
    """Render Chain's deterministic output without Workflow-memory fields."""
    from ..chain.sources import to_observations

    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [{
        "id": row.id, "document_id": row.document_id, "entity": row.entity,
        "metric": row.metric, "concept": row.concept, "period": row.period,
        "direction": row.direction, "value": row.value, "unit": row.unit,
        "evidence_span": row.evidence_span,
    } for row in to_observations(source, points, now=stamp)]


@contextmanager
def _environment(values: dict[str, str]) -> Iterator[None]:
    before = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, previous in before.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def _regional_platform_lineage(source, *, lookback_months: int) -> list[dict[str, Any]]:
    """Read accepted IDs separately from Chain's presentation implementation."""
    from ..data.products import DataProducts
    from ..data.runtime import get_platform_structured_repository
    from ..data.sources import kr_ecos, tw_mof

    if source.adapter == "tw_mof":
        source_id, dataset, entity, metric = (
            "tw_mof_exports", "regional_tw_exports", "TW_IC_EXPORT", tw_mof.METRIC_ID)
    else:
        source_id, dataset, entity, metric = (
            "kr_ecos_exports", "regional_kr_exports", "KR_SEMI_EXPORT", kr_ecos.METRIC_ID)
    repository = get_platform_structured_repository()
    try:
        result = DataProducts(structured_repository=repository).metric_series(
            metric=metric, entity=entity, dataset=dataset, source_id=source_id, quality="loose")
    finally:
        repository.close()
    return [{key: row.get(key) for key in (
        "observation_id", "artifact_id", "period", "value", "unit", "published_at", "known_at",
    )} for row in result["rows"][-lookback_months:]]


def accept_chain_regional(*, data_db: str | Path, lookback_months: int = 2,
                          record: bool = False) -> dict[str, Any]:
    """Compare the actual Chain regional reader against its legacy adapters."""
    from ..chain import sources
    from ..data.cutover import record_consumer_comparison, record_consumer_release_verification

    selected = [source for source in sources.load_sources()
                if source.adapter in {"tw_mof", "kr_ecos"}]
    runs: list[dict[str, Any]] = []
    unexplained, governed = [], []
    for source in selected:
        platform = sources._platform_fetch(source, lookback_months=lookback_months)
        legacy_error = ""
        try:
            legacy = sources._legacy_fetch(source, lookback_months=lookback_months)
        except Exception as exc:  # failure is evidence, not a false zero observation
            legacy, legacy_error = [], f"{type(exc).__name__}: {exc}"
        platform_rows, legacy_rows = _point_rows(platform), _point_rows(legacy)
        equivalent = bool(platform_rows) and platform_rows == legacy_rows
        unavailable = bool(platform_rows) and not legacy_rows
        if equivalent:
            classification = "equivalent_regional_observation_output"
        elif unavailable:
            classification = "governed_legacy_unavailable"
            governed.append(source.id)
        else:
            classification = "platform_regional_output_mismatch"
            unexplained.append(source.id)
        runs.append({
            "source": source.id, "adapter": source.adapter,
            "legacy": legacy_rows, "platform": platform_rows,
            "legacy_error": legacy_error,
            "output": {
                "legacy_observations": _observation_rows(source, legacy),
                "platform_observations": _observation_rows(source, platform),
            },
            "lineage": _regional_platform_lineage(source, lookback_months=lookback_months),
            "classification": classification,
        })

    status = "reconciled" if runs and not unexplained else "mismatch"
    reconciliation_kind = (
        "equivalent_regional_consumer_output" if not governed else "governed_regional_source_upgrade"
    )
    details = {
        "reconciliation": {"kind": reconciliation_kind, "lookback_months": lookback_months},
        "legacy": {row["source"]: row["legacy"] for row in runs},
        "platform": {row["source"]: row["platform"] for row in runs},
        "output": {row["source"]: row["output"] for row in runs},
        "lineage": {row["source"]: row["lineage"] for row in runs},
        "sources": runs,
        "reason": "" if status == "reconciled" else "unexplained:" + ",".join(unexplained),
    }
    result: dict[str, Any] = {
        "consumer": "chain_regional", "entity": "REGIONAL:TW+KR", "status": status,
        "category": reconciliation_kind, "details": details, "recorded": False,
    }
    if record:
        result["comparison"] = record_consumer_comparison(
            consumer="chain_regional", entity="REGIONAL:TW+KR", data_db=data_db,
            status=status, details=details)
        result["recorded"] = True
        if status == "reconciled" and governed:
            result["verification"] = record_consumer_release_verification(
                consumer="chain_regional", entity="REGIONAL:TW+KR", data_db=data_db,
                details={
                    "lineage": details["lineage"],
                    "period_unit_definition": "Taiwan electronic-components and Korea semiconductor monthly levels; Chain derives YoY/MoM from accepted levels.",
                    "freshness": {row["source"]: [item["period"] for item in row["platform"]]
                                  for row in runs},
                    "output": details["output"],
                    "review_basis": "legacy adapter unavailable; governed official observations and deterministic Chain output inspected independently",
                })
    return result


def _monitor_run(*, symbol: str, mode: str, lookback_days: int, memory_db: Path) -> dict[str, Any]:
    from ..agents.pead import monitor
    from ..config import load_pead_config
    from ..memory import get_store, reset_store_cache

    with _environment({
        "ATS_STRUCTURED_PEAD_MONITOR_MODE": mode,
        "ATS_DB_PATH": str(memory_db),
        # Legacy news adapters can maintain a shared source cache.  Acceptance
        # must not pollute the user's Obsidian document library with that cache.
        "ATS_DOCS_ROOT": str(memory_db.parent / "documents"),
    }):
        reset_store_cache()
        try:
            update = monitor.run(symbol, use_llm=False, lookback_days=lookback_days)
            store = get_store()
            fiscal_label = load_pead_config(symbol).fiscal_label
            dossier = store.get_dossier(symbol, fiscal_label)
            events = [{key: row.get(key) for key in (
                "id", "source", "published_at", "headline", "url", "triage_score", "triage_category",
            )} for row in store.recent_events(symbol, limit=500)]
            return {
                "update": update.model_dump(mode="json"),
                "events": events,
                "dossier": {
                    "fiscal_label": fiscal_label,
                    "phase": dossier.phase if dossier else "",
                    "narrative": (dossier.expectation_set.narrative if dossier and dossier.expectation_set else ""),
                },
            }
        finally:
            try:
                get_store().close()
            finally:
                reset_store_cache()


def _platform_news_lineage(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from ..data.stores.unstructured import get_platform_unstructured_repository

    repository = get_platform_unstructured_repository()
    try:
        documents = {
            row["document_id"]: row
            for row in repository.documents(ok_only=True, limit=5_000)
        }
        output = []
        for event in events:
            document_id = str(event["id"])
            document = documents.get(document_id, {})
            version = repository.latest_document_version(document_id) or {}
            output.append({
                "document_id": document_id, "source": document.get("source"),
                "published_at": document.get("published_at"),
                "version_id": version.get("version_id"), "source_url": version.get("source_url"),
                "chars": version.get("chars"),
            })
        return output
    finally:
        repository.close()


def accept_pead_monitor(*, symbol: str, data_db: str | Path, lookback_days: int = 7,
                        record: bool = False) -> dict[str, Any]:
    """Run legacy and governed monitor reads in isolated Workflow-memory databases."""
    from ..data.cutover import record_consumer_comparison, record_consumer_release_verification

    symbol = symbol.upper()
    with tempfile.TemporaryDirectory(prefix="ats-pead-monitor-acceptance-") as temporary:
        root = Path(temporary)
        legacy = _monitor_run(symbol=symbol, mode="legacy", lookback_days=lookback_days,
                              memory_db=root / "legacy.sqlite")
        platform = _monitor_run(symbol=symbol, mode="platform", lookback_days=lookback_days,
                                memory_db=root / "platform.sqlite")
    platform_events, legacy_events = platform["events"], legacy["events"]
    platform_lineage = _platform_news_lineage(platform_events)
    valid_platform = bool(platform_events) and all(
        str(event["source"]).startswith("platform:") for event in platform_events
    ) and all(row.get("version_id") and int(row.get("chars") or 0) > 0
              for row in platform_lineage)
    equivalent = legacy_events == platform_events
    if equivalent and valid_platform:
        reconciliation_kind, status = "equivalent_monitor_event_and_dossier_output", "reconciled"
    elif valid_platform:
        # Legacy's Finnhub/RSS aggregate is not an authoritative completeness benchmark
        # for the deliberately narrowed IBKR-first, Yahoo-on-failure-only contract.
        reconciliation_kind, status = "governed_news_source_policy_upgrade", "reconciled"
    else:
        reconciliation_kind, status = "platform_news_output_incomplete", "mismatch"
    details = {
        "reconciliation": {"kind": reconciliation_kind, "lookback_days": lookback_days},
        "legacy": legacy, "platform": platform,
        "output": {
            "legacy_event_ids": [row["id"] for row in legacy_events],
            "platform_event_ids": [row["id"] for row in platform_events],
            "legacy_update": legacy["update"], "platform_update": platform["update"],
            "legacy_dossier": legacy["dossier"], "platform_dossier": platform["dossier"],
        },
        "lineage": platform_lineage,
        "reason": "" if status == "reconciled" else "no_complete_accepted_platform_news_events",
    }
    result: dict[str, Any] = {
        "consumer": "pead_monitor", "entity": symbol, "status": status,
        "category": reconciliation_kind, "details": details, "recorded": False,
    }
    if record:
        result["comparison"] = record_consumer_comparison(
            consumer="pead_monitor", entity=symbol, data_db=data_db,
            status=status, details=details)
        result["recorded"] = True
        if status == "reconciled" and reconciliation_kind.startswith("governed_"):
            result["verification"] = record_consumer_release_verification(
                consumer="pead_monitor", entity=symbol, data_db=data_db,
                details={
                    "lineage": platform_lineage,
                    "period_unit_definition": "ticker-associated accepted IBKR News; Yahoo is only a scoped IBKR-failure fallback, not a parallel source",
                    "freshness": {"lookback_days": lookback_days,
                                  "published_at": [row["published_at"] for row in platform_events]},
                    "output": details["output"],
                    "review_basis": "isolated no-LLM monitor run plus direct immutable document/version inspection",
                })
    return result


def _platform_research_lineage(article_ids: set[str]) -> list[dict[str, Any]]:
    """Resolve selected research inputs independently from the workflow reader."""
    from ..data.stores.unstructured import get_platform_unstructured_repository

    repository = get_platform_unstructured_repository()
    try:
        rows: dict[str, dict] = {}
        for row in repository.documents(doc_type="article", limit=5_000):
            # Pre-metadata migration rows have no article-native ID.  The reader
            # deliberately exposes their stable document ID instead, so both are
            # valid lineage keys for this independent check.
            for key in (row.get("external_id"), row.get("document_id")):
                if key:
                    rows[str(key)] = row
        output = []
        for article_id in sorted(article_ids):
            document = rows.get(article_id, {})
            version = repository.latest_document_version(document["document_id"]) if document else None
            output.append({
                "article_id": article_id,
                "document_id": document.get("document_id"),
                "version_id": (version or {}).get("version_id"),
                "source": document.get("source"),
                "published_at": document.get("published_at"),
                "completeness": document.get("completeness"),
                "truncation_reason": document.get("truncation_reason"),
                "chars": (version or {}).get("chars"),
            })
        return output
    finally:
        repository.close()


def _snapshot_memory_database(destination: Path) -> None:
    """Create an isolated SQLite snapshot without changing Workflow memory."""
    from ..memory import get_store

    source = get_store()
    destination.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(destination)
    try:
        source.conn.backup(target)
    finally:
        target.close()


def _research_platform_smoke(*, lookback_days: int) -> dict[str, Any]:
    """Exercise the real platform selection and memory processing loop without an LLM.

    The copied memory database carries the legacy compatibility document index,
    while the selected immutable inputs always come from the platform reader.
    Thus this verifies the actual mixed boundary without adding production
    insights or re-fetching any source.
    """
    from ..agents.pead import research
    from ..data import research as research_src
    from ..memory import get_store, reset_store_cache

    with tempfile.TemporaryDirectory(prefix="ats-pead-research-acceptance-") as temporary:
        root = Path(temporary)
        snapshot = root / "workflow-memory.sqlite"
        _snapshot_memory_database(snapshot)
        with _environment({
            "ATS_STRUCTURED_PEAD_RESEARCH_MODE": "platform",
            "ATS_DB_PATH": str(snapshot),
        }):
            reset_store_cache()
            try:
                store = get_store()
                since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
                candidates = [
                    article for article in research_src.stored_articles(
                        since, store=store, allow_incomplete=True)
                    if research_src.is_pead_research_article(article)
                ]
                output = research.run(use_llm=False, since=since)
                processed = store.document_processing(
                    consumer="pead", processor_version=research.PROCESSOR_VERSION, limit=5_000)
                article_ids = {article.id for article in candidates}
                outputs = [row for row in store.recent_insights(limit=5_000)
                           if row.get("article_id") in article_ids]
                return {
                    "candidates": [
                        {"article_id": article.id, "source": article.source,
                         "published_at": article.published_at.isoformat(),
                         "completeness": article.completeness,
                         "truncation_reason": article.truncation_reason}
                        for article in candidates
                    ],
                    "no_llm_output_count": len(output),
                    "processing_runs": processed,
                    "existing_insight_outputs": outputs,
                }
            finally:
                try:
                    get_store().close()
                finally:
                    reset_store_cache()


def accept_pead_research(*, data_db: str | Path, lookback_days: int = 30,
                         record: bool = False) -> dict[str, Any]:
    """Verify governed research input selection and the isolated memory workflow.

    This is intentionally a platform-only acceptance: the old reader is not an
    authority on which third-party research is useful, especially for an
    unsubscribed SemiAnalysis preview.  The acceptance instead proves that the
    selected inputs, completeness labels, immutable versions and memory output
    lineage form one auditable processing path.
    """
    from ..data.cutover import record_consumer_comparison, record_consumer_release_verification

    smoke = _research_platform_smoke(lookback_days=lookback_days)
    candidates = smoke["candidates"]
    lineage = _platform_research_lineage({row["article_id"] for row in candidates})
    valid_lineage = bool(candidates) and len(lineage) == len(candidates) and all(
        row.get("document_id") and row.get("version_id") and int(row.get("chars") or 0) > 0
        for row in lineage)
    partial_ids = {row["article_id"] for row in candidates if row["completeness"] == "partial"}
    partial_valid = all(
        row.get("source") and "semianalysis" in str(row["source"]).lower()
        and row.get("completeness") == "partial"
        for row in lineage if row["article_id"] in partial_ids)
    output_lineage_valid = all(
        row.get("article_id") in {item["article_id"] for item in lineage}
        for row in smoke["existing_insight_outputs"])
    status = "reconciled" if valid_lineage and partial_valid and output_lineage_valid else "mismatch"
    category = "governed_platform_research_processing_upgrade"
    details = {
        "reconciliation": {"kind": category, "lookback_days": lookback_days},
        "platform": {"inputs": candidates, "processing": smoke, "lineage": lineage},
        "output": {
            "no_llm_output_count": smoke["no_llm_output_count"],
            "existing_insight_outputs": smoke["existing_insight_outputs"],
        },
        "lineage": lineage,
        "reason": "" if status == "reconciled" else "platform_research_input_or_lineage_incomplete",
    }
    result: dict[str, Any] = {
        "consumer": "pead_research", "entity": "RESEARCH", "status": status,
        "category": category, "details": details, "recorded": False,
    }
    if record:
        result["comparison"] = record_consumer_comparison(
            consumer="pead_research", entity="RESEARCH", data_db=data_db,
            status=status, details=details)
        result["recorded"] = True
        if status == "reconciled":
            result["verification"] = record_consumer_release_verification(
                consumer="pead_research", entity="RESEARCH", data_db=data_db,
                details={
                    "lineage": lineage,
                    "period_unit_definition": (
                        "Immutable third-party research versions; SemiAnalysis partial previews "
                        "retain completeness/truncation metadata and are never full articles."),
                    "freshness": {"lookback_days": lookback_days,
                                  "published_at": [row["published_at"] for row in candidates]},
                    "output": details["output"],
                    "review_basis": (
                        "platform-only input selection plus isolated no-LLM workflow-memory "
                        "processing smoke; legacy equivalence is not required for this governed policy."),
                })
    return result


def _chain_report_entities(cfg) -> set[str]:
    """Return every entity whose evidence can affect the configured Chain report."""
    from ..chain.sources import source_entities_for

    entities: set[str] = set()
    for layer in cfg.layers:
        for claim in layer.claims:
            entities |= claim.expected_witnesses()
            entities |= {w.entity.upper() for w in claim.witnesses}
            entities |= {entity.upper() for entity in claim.entities}
            entities |= {entity.upper() for entity in source_entities_for(claim)}
    return {entity.upper() for entity in entities if entity}


def _evidence_chain_platform_smoke(*, sector: str) -> dict[str, Any]:
    """Render the real Chain report against platform evidence in copied memory.

    Claim assessments are Workflow memory and therefore intentionally live only in
    the temporary copy.  The report's immutable document/evidence reads are routed
    to platform by the consumer flag.  ``allow_llm=False`` makes the exercise
    deterministic and represents unresolved semantics explicitly.
    """
    from ..chain import report as chain_report
    from ..config import load_pead_global, load_sector_config
    from ..memory import get_store, reset_store_cache

    with tempfile.TemporaryDirectory(prefix="ats-evidence-chain-acceptance-") as temporary:
        root = Path(temporary)
        snapshot = root / "workflow-memory.sqlite"
        _snapshot_memory_database(snapshot)
        with _environment({
            "ATS_STRUCTURED_EVIDENCE_CHAIN_MODE": "platform",
            "ATS_DB_PATH": str(snapshot),
        }):
            reset_store_cache()
            try:
                store = get_store()
                cfg = load_sector_config(sector)
                as_of = datetime.now(timezone.utc)
                text = chain_report.render(
                    cfg, store, as_of=as_of,
                    ind_cfg=load_pead_global().get("induction", {}), allow_llm=False)
                day = as_of.date().isoformat()
                assessments = []
                for row in store.conn.execute(
                    "SELECT claim_id,layer,verdict,evidence_clusters,stance_classes,payload "
                    "FROM claim_assessments WHERE as_of=? ORDER BY layer,claim_id", (day,)
                ):
                    payload = json.loads(row[5])
                    assessments.append({
                        "claim_id": row[0], "layer": row[1], "verdict": row[2],
                        "evidence_clusters": row[3], "stance_classes": row[4],
                        "observation_ids": payload.get("observation_ids") or [],
                    })
                return {
                    "sector": sector, "as_of": as_of.isoformat(),
                    "entities": sorted(_chain_report_entities(cfg)),
                    "report": {
                        "chars": len(text),
                        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "no_llm_marker": "未运行 LLM 判读（no-LLM 验收）" in text,
                        "heading": text.splitlines()[0] if text else "",
                    },
                    "assessments": assessments,
                    "observation_ids": sorted({
                        observation_id for assessment in assessments
                        for observation_id in assessment["observation_ids"]
                    }),
                }
            finally:
                try:
                    get_store().close()
                finally:
                    reset_store_cache()


def _platform_evidence_chain_lineage(observation_ids: set[str]) -> dict[str, Any]:
    """Resolve each claim-used platform observation to its available provenance.

    Earlier Chain extraction persisted a frozen evidence observation before the
    document-version store existed. Those rows are still real platform inputs:
    their ID, source URL, document reference, entity, timestamp and quoted span
    are preserved in ``data_evidence_observations``. Keep them explicitly marked
    as ``evidence_snapshot`` rather than pretending they are full document versions.
    """
    from ..data.stores.unstructured import get_platform_unstructured_repository

    repository = get_platform_unstructured_repository()
    try:
        observations = {
            str(row.get("id") or ""): row for row in repository.observations(limit=10_000)
        }
        documents = {
            str(row.get("document_id") or ""): row for row in repository.documents(limit=10_000)
        }
        facts = repository.facts(limit=10_000)
        fact_documents = {str(row.get("document_id") or "") for row in facts}
        rows = []
        for observation_id in sorted(observation_ids):
            observation = observations.get(observation_id, {})
            document_id = str(observation.get("document_id") or "")
            document = documents.get(document_id, {})
            version = repository.latest_document_version(document_id) if document_id else None
            has_document_version = bool(
                document_id and document and version and int((version or {}).get("chars") or 0) > 0)
            snapshot_fields = {
                "source_url": observation.get("source_url"),
                "entity": observation.get("entity"),
                "source_entity": observation.get("source_entity"),
                "observed_at": observation.get("observed_at"),
                "evidence_span": observation.get("evidence_span"),
            }
            rows.append({
                "observation_id": observation_id,
                "document_id": document_id,
                "version_id": (version or {}).get("version_id"),
                "lineage_kind": "document_version" if has_document_version else "evidence_snapshot",
                **snapshot_fields,
                "document_source": document.get("source"),
                "document_published_at": document.get("published_at"),
                "document_chars": (version or {}).get("chars"),
                "has_fact_lineage": document_id in fact_documents,
            })
        return {
            "observations": rows,
            "failures": repository.observation_failures(limit=1_000),
        }
    finally:
        repository.close()


def accept_evidence_chain(*, data_db: str | Path, sector: str = "ai_hardware",
                          record: bool = False) -> dict[str, Any]:
    """Accept the actual governed Chain report, not unrelated table-level hashes."""
    from ..data.cutover import record_consumer_comparison, record_consumer_release_verification

    smoke = _evidence_chain_platform_smoke(sector=sector)
    lineage = _platform_evidence_chain_lineage(set(smoke["observation_ids"]))
    evidence_rows = lineage["observations"]
    report = smoke["report"]
    valid_report = bool(report["chars"] > 0 and report["heading"] and report["no_llm_marker"])
    valid_assessments = bool(smoke["assessments"])
    def _lineage_is_replayable(row: dict[str, Any]) -> bool:
        if row.get("lineage_kind") == "document_version":
            return bool(row.get("document_id") and row.get("version_id")
                        and int(row.get("document_chars") or 0) > 0)
        # Historical rows predate the document-version store. The stored evidence
        # snapshot is still replayable at the exact citation level used by Chain.
        return bool(row.get("observation_id") and row.get("document_id")
                    and row.get("source_url") and row.get("entity")
                    and row.get("source_entity") and row.get("observed_at")
                    and row.get("evidence_span"))

    valid_lineage = bool(evidence_rows) and all(_lineage_is_replayable(row) for row in evidence_rows)
    lineage_summary = {
        "cited_observations": len(evidence_rows),
        "document_version": sum(row.get("lineage_kind") == "document_version" for row in evidence_rows),
        "evidence_snapshot": sum(row.get("lineage_kind") == "evidence_snapshot" for row in evidence_rows),
        "unreplayable": sum(not _lineage_is_replayable(row) for row in evidence_rows),
    }
    status = "reconciled" if valid_report and valid_assessments and valid_lineage else "mismatch"
    category = "governed_platform_evidence_report_upgrade"
    details = {
        "reconciliation": {"kind": category, "sector": sector},
        "platform": {
            "entities": smoke["entities"], "report": report,
            "assessments": smoke["assessments"], "observation_ids": smoke["observation_ids"],
            "failures": lineage["failures"], "lineage_summary": lineage_summary,
        },
        "output": {
            "report": report,
            "claim_assessment_count": len(smoke["assessments"]),
            "claim_ids": [row["claim_id"] for row in smoke["assessments"]],
            "no_llm": True,
        },
        "lineage": evidence_rows, "lineage_summary": lineage_summary,
        "reason": "" if status == "reconciled" else "platform_evidence_report_or_lineage_incomplete",
    }
    result: dict[str, Any] = {
        "consumer": "evidence_chain", "entity": sector.upper(), "status": status,
        "category": category, "details": details, "recorded": False,
    }
    if record:
        result["comparison"] = record_consumer_comparison(
            consumer="evidence_chain", entity=sector.upper(), data_db=data_db,
            status=status, details=details)
        result["recorded"] = True
        if status == "reconciled":
            result["verification"] = record_consumer_release_verification(
                consumer="evidence_chain", entity=sector.upper(), data_db=data_db,
                details={
                    "lineage": evidence_rows,
                    "period_unit_definition": (
                        "Chain report reads governed non-structured evidence observations. "
                        "It resolves current assets to immutable document/version lineage and "
                        "retains migrated pre-version assets as explicit evidence snapshots; "
                        "structured_observations are outside this consumer contract."),
                    "freshness": {"as_of": smoke["as_of"], "observed_at": [
                        row["observed_at"] for row in evidence_rows if row.get("observed_at")]},
                    "output": details["output"],
                    "review_basis": (
                        "actual platform-routed Chain report rendered in isolated Workflow memory "
                        "with explicit no-LLM unknowns; report output and every cited evidence "
                        "observation were independently resolved to document/version lineage."),
                })
    return result


def run_consumer_acceptance(*, consumer: str, entity: str, data_db: str | Path,
                            lookback_days: int = 7, record: bool = False) -> dict[str, Any]:
    normalized = consumer.strip().lower().replace("-", "_")
    if normalized == "chain_regional":
        return accept_chain_regional(data_db=data_db, record=record)
    if normalized == "pead_monitor":
        return accept_pead_monitor(symbol=entity or "NVDA", data_db=data_db,
                                   lookback_days=lookback_days, record=record)
    if normalized == "pead_research":
        return accept_pead_research(data_db=data_db, lookback_days=lookback_days, record=record)
    if normalized == "evidence_chain":
        return accept_evidence_chain(data_db=data_db, sector=entity or "ai_hardware", record=record)
    raise ValueError(
        "consumer-acceptance supports chain_regional, pead_monitor, pead_research, and evidence_chain only")


__all__ = [
    "accept_chain_regional", "accept_pead_monitor", "accept_pead_research",
    "accept_evidence_chain", "run_consumer_acceptance",
]
