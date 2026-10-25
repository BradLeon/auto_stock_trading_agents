"""Read-only views of what the Layer and Sector reviews read (Phase F 10.5, 2026-10-17).

One place describes those inputs so the consumer health check (`consumer_health._layer/_sector`) and the
Dispatcher's data-vintage reference (`dispatcher.default_data_vintage`) can never disagree about them.

Everything here is a plain local read: no model call, no provider call, no network, and the Workflow memory
is only ever opened through a read-only SQLite URI (`get_store()` applies schema changes, so it is never
used). A failure to read raises; the callers decide whether that is a health problem or just "recompute".

What the reviews read, as the code stands today:

* Layer (the Dispatcher adapter): neutral evidence observations of every witness named by the layer's claims
  (`assemble.layer_assessments`, 200 newest per witness) and the layer's knowledge notes
  (`sector_inputs.fetch_named`, the governed `ai_hardware_knowledge_corpus` source). The adapter passes no
  cross-section basket, so no financials, consensus or prices reach the Layer prompt.
* Sector (the Dispatcher adapter): the eight `LayerAnalysis` projections, then per layer the quantitative
  cross-section (governed constituent financials, platform consensus, runtime prices), the claim assessments
  again, and one FactSet sector packet for the cross-layer rotation.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# The review reads each witness' 200 newest non-superseded observations; the vintage must see the same window.
EVIDENCE_LIMIT = 200


def digest(values) -> str:
    return hashlib.sha256(json.dumps(sorted(map(str, values)), ensure_ascii=False).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def configured_sectors() -> list[str]:
    """Sectors reviewed weekly (`config/pead.yaml: sector_review.sectors`)."""
    from ..config import load_pead_global

    names = (load_pead_global().get("sector_review") or {}).get("sectors") or ["ai_hardware"]
    return [str(name) for name in names]


def load_sector(name: str):
    from ..config import load_sector_config

    return load_sector_config(name)


def witness_entities(layer) -> list[str]:
    """Every entity whose observations the layer's claims read (mirrors `assemble.layer_assessments`)."""
    from ..chain.sources import source_entities_for

    entities: set[str] = set()
    for claim in layer.claims:
        entities |= (claim.expected_witnesses() | {w.entity.upper() for w in claim.witnesses}
                     | set(claim.entities) | source_entities_for(claim))
    return sorted(entities)


def note_paths(layer) -> list[str]:
    return list(dict.fromkeys(layer.structure_notes.values()))


def cohort(layer) -> list[str]:
    """One symbol per company in the layer's cross-section (mirrors `cross_section.run_layer`)."""
    from ..config import canonical_entity

    out: list[str] = []
    for symbol in [t.symbol for t in layer.tickers] + list(layer.cohort_extra):
        canon = canonical_entity(symbol)
        if canon not in out:
            out.append(canon)
    return out


# --------------------------------------------------------------------------- #
# Evidence observations and knowledge notes (Layer inputs)
# --------------------------------------------------------------------------- #
def layer_evidence(repository, layer) -> dict[str, Any]:
    """What the claim engine would see for this layer: observations per witness, read-only."""
    by_entity = {entity: repository.observations(entity=entity, limit=EVIDENCE_LIMIT)
                 for entity in witness_entities(layer)}
    rows = [row for group in by_entity.values() for row in group]
    # A cited observation needs its identity, its subject, a time and the document it came from.
    malformed = [str(row.get("id") or "?") for row in rows
                 if not (row.get("id") and row.get("entity") and row.get("observed_at")
                         and (row.get("document_id") or row.get("source_url")))]
    newest = max((str(row["observed_at"]) for row in rows if row.get("observed_at")), default="")
    return {"by_entity": by_entity, "ids": sorted({str(row["id"]) for row in rows if row.get("id")}),
            "observations": len(rows), "malformed": malformed, "newest": newest,
            "silent": [entity for entity, group in by_entity.items() if not group]}


def knowledge_notes(paths: list[str]) -> dict[str, Any]:
    """The layer's judging criteria as governed documents: `{status, notes, missing}` (no network)."""
    from ..data.products.sector_inputs import curated_knowledge_packet

    return curated_knowledge_packet(list(paths))


def layer_vintage(sector: str, layer_key: str) -> list[str]:
    """Data-vintage references of one Layer review: the observation set and the note versions it reads."""
    from ..data.stores.unstructured import get_platform_unstructured_repository

    layer = load_sector(sector).layer_by_key(layer_key)
    if layer is None:
        return []
    repository = get_platform_unstructured_repository()
    try:
        evidence = layer_evidence(repository, layer)
    finally:
        repository.close()
    packet = knowledge_notes(note_paths(layer))
    refs = [f"evidence:{layer.key}:{evidence['observations']}@{digest(evidence['ids'])}"]
    refs += [f"knowledge:{note['path']}@{str(note['content_hash'])[:16]}" for note in packet["notes"]]
    refs += [f"knowledge-missing:{item['path']}:{item['reason']}" for item in packet["missing"]]
    if getattr(layer, "evidence_observers", None):
        # The governed observers the layer analyst reads (L1).  Their datasets are fixed by code
        # (`OBSERVER_DATASETS`), and the vintage is each dataset's count of ingestion runs that admitted rows and the newest one's
        # time: a new print or a revision moves it, and nothing else does.  (Running the observers here would take ~40 s and write a
        # snapshot manifest on every call.)  An unreadable dataset contributes nothing.
        from ..agents.evidence.layer_runner import OBSERVER_DATASETS
        from ..data.products import get_platform_data_products

        wanted = sorted({dataset for ref in layer.evidence_observers if getattr(ref, "enabled", True)
                         for dataset in OBSERVER_DATASETS.get(str(getattr(ref, "claim_id", "")), ())})
        products = get_platform_data_products(readonly=True)
        try:
            for dataset in wanted:
                try:
                    # runs that admitted rows: a new print or a revision is one more such run
                    count, newest = products.structured.conn.execute(
                        "SELECT COUNT(*), MAX(completed_at) FROM structured_ingestion_runs "
                        "WHERE dataset_id=? AND accepted>0", (dataset,)).fetchone()
                except Exception:  # noqa: BLE001
                    continue
                refs.append(f"observer-data:{dataset}:{count}@{str(newest or '')[:19]}")
        finally:
            products.structured.close()
    return refs


# --------------------------------------------------------------------------- #
# Governed constituent financials, consensus and FactSet (Sector inputs)
# --------------------------------------------------------------------------- #
def financial_package(structured, symbol: str) -> dict | None:
    """The complete report package the constituent view would use (mirrors `_platform_constituent_accounting`)."""
    from ..data.fundamentals import _select_report_package

    return _select_report_package(structured, symbol=symbol.upper())


def financial_state(products, symbol: str, *, as_of: datetime) -> dict[str, Any]:
    package = financial_package(products.structured, symbol)
    if package is None:
        return {"covered": False, "source": "", "period": "", "observations": []}
    rows = products.structured.observations(dataset_id="company_financials", entity_id=symbol.upper(),
                                            as_of=as_of, accepted_only=True, latest_only=True, limit=100_000)
    return {"covered": True, "source": package["source_id"], "period": str(package["period"]),
            "observations": sorted(str(row.get("observation_id")) for row in rows)}


def session_ref(as_of: datetime, key: str) -> str:
    """Last NYSE session side that can have changed a runtime price.

    Weekends map onto Friday's close: nothing trades on a Saturday or Sunday, so a weekend re-run must reuse
    the same-day review instead of treating the calendar date as new data. Holidays are not modelled: they
    only cause an extra recompute, never a stale reuse.
    """
    local = as_of.astimezone(ZoneInfo("America/New_York"))
    if local.weekday() >= 5:
        local = (local - timedelta(days=local.weekday() - 4)).replace(hour=23, minute=59)
    side = "after-close" if (local.hour, local.minute) >= (16, 0) else "before-close"
    return f"market-session:{key}:{local.date().isoformat()}:{side}"


def sector_vintage(sector: str, as_of: datetime) -> list[str]:
    """Data-vintage references of one Sector review, beyond the Layer projections (those are input refs)."""
    from ..data import factset
    from ..data.products import get_platform_data_products

    cfg = load_sector(sector)
    symbols = sorted({symbol for layer in cfg.layers for symbol in cohort(layer)})
    products = get_platform_data_products(readonly=True)
    try:
        financials, consensus = [], []
        for symbol in symbols:
            state = financial_state(products, symbol, as_of=as_of)
            financials.append(f"{symbol}={state['source']}@{state['period']}#{digest(state['observations'])}")
            snapshot = products.consensus_snapshot(entity=symbol, as_of=as_of)
            consensus.append(f"{symbol}={snapshot.get('known_at') or 'none'}")
        material = factset.fetch_sector_material(products=products)
    finally:
        products.structured.close()
        products.unstructured.close()
    from ..config import load_pead_global

    bind = bool((load_pead_global().get("sector_review") or {}).get("bind_layer_budget", True))
    return [f"financials:{sector}:{len(symbols)}@{digest(financials)}",
            f"consensus:{sector}@{digest(consensus)}",
            f"factset:{material.get('version_id') or 'none'}:{material.get('state') or 'unavailable'}",
            session_ref(as_of, sector),
            f"layer-budget-bound:{int(bind)}"]


# --------------------------------------------------------------------------- #
# LayerAnalysis projections (the Sector dependency), over a read-only connection
# --------------------------------------------------------------------------- #
def latest_layer_projections() -> dict[str, dict]:
    """Newest published `LayerAnalysis` row per layer, with a verdict on whether it is usable.

    Deliberately not `get_store()`: opening the memory store applies pending additive schema, which would make
    a "read-only" health check write to the production file.
    """
    from ..memory import _db_path

    path = Path(_db_path()).resolve()
    if not path.is_file():
        return {}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_projection_envelopes'"
                            ).fetchone():
            return {}
        rows = conn.execute(
            "SELECT * FROM task_projection_envelopes WHERE agent_role='layer_analysis' AND scope_kind='layer' "
            "AND status='published' ORDER BY created_at DESC").fetchall()
    newest: dict[str, dict] = {}
    for row in rows:
        newest.setdefault(row["scope_id"], dict(row))
    return {key: {**row, "usable": projection_problem(row) == "", "problem": projection_problem(row)}
            for key, row in newest.items()}


def projection_problem(row) -> str:
    """Why a stored LayerAnalysis envelope is not usable by Sector, or '' when it is."""
    from ..agent.task_projection import ProjectionScope, content_hash, normalize_refs, validate_payload

    try:
        if row["schema_name"] != "LayerAnalysis" or row["schema_version"] != "v1":
            return "schema_incompatible"
        payload = validate_payload("layer_analysis", json.loads(row["payload"] or "{}"))
        expected = content_hash(
            role="layer_analysis", scope=ProjectionScope(kind="layer", id=row["scope_id"]), as_of=row["as_of"],
            schema_name=row["schema_name"], schema_version=row["schema_version"], payload=payload,
            input_refs=normalize_refs(json.loads(row["input_refs"] or "[]")),
            data_vintage_refs=normalize_refs(json.loads(row["data_vintage_refs"] or "[]")))
    except Exception as exc:  # noqa: BLE001 - any unreadable envelope is simply unusable
        return f"unreadable ({type(exc).__name__})"
    return "" if expected == row["content_hash"] else "content_hash_mismatch"


def age_days(stamp: str, now: datetime) -> float | None:
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (now - moment).total_seconds() / 86_400
