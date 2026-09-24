"""Independent entrypoint for the information analyst (Phase D task 4.9).

One pass = extraction over admitted articles + a document-identification pass
per configured target. The pass reads admitted documents and configuration
ONLY: it never reads another analyst's projection, and it terminates without
triggering the fundamental analyst, the chief, or any decision flow.
"""

from __future__ import annotations

import logging

log = logging.getLogger("ats.agents.information.entry")


def run_information_pass(*, use_llm: bool = True, symbols: list[str] | None = None,
                         ingest_research: bool = False) -> dict:
    """Run one full information pass. Returns a summary dict for the CLI.

    `ingest_research` is handled by the RUNTIME caller (CLI), not here: an
    agent module must not import data-layer ingestion — this pass only reads
    already-admitted documents.
    """
    from ...config import load_pead_global
    from ...memory import get_store

    _ = ingest_research
    store = get_store()
    g = load_pead_global()
    summary: dict = {"briefs": 0, "targets": [], "articles": 0}

    from . import extract

    insights = extract.run(use_llm=use_llm)
    summary["articles"] = len({i.article_id for i in insights})
    summary["briefs"] += _brief_count(store)

    targets = [s.upper() for s in (symbols or g.get("targets", []))]
    for sym in targets:
        try:
            run_information_target(sym, store=store, use_llm=use_llm,
                                   run_extraction=False)
            summary["targets"].append(sym)
        except Exception as exc:  # noqa: BLE001 - one target must not stop the pass
            log.warning("information pass failed for %s: %s", sym, exc)
    summary["briefs"] = _brief_count(store)
    return summary


def run_information_target(symbol: str, *, store=None, use_llm: bool = True,
                           run_extraction: bool = True, run_once=None) -> dict:
    """Run the information extraction pre-pass once, then publish one target brief.

    Dispatcher fan-out calls this per entity and supplies ``run_once`` so the
    corpus-wide research extraction is shared across instances instead of being
    repeated once per ticker. All per-target documents remain admitted-data reads.
    """
    from ...memory import get_store
    from . import documents, extract

    store = get_store() if store is None else store
    if run_extraction:
        work = lambda: extract.run(use_llm=use_llm)
        if run_once is not None:
            run_once("information-research-extraction", work)
        else:
            work()
    sym = symbol.upper()
    cfg = _pead_cfg(sym)
    update = documents.run_document_pass(store, sym, cfg=cfg, fresh=[], use_llm=use_llm)
    return {"symbol": sym, "materiality": update.materiality,
            "event_summary": update.event_summary}


def _pead_cfg(sym: str) -> dict:
    """Minimal cfg view the document pass needs (no dossier reads)."""
    from ...config import load_pead_global

    g = load_pead_global()
    return {"monitor": g.get("monitor", {})}


def _brief_count(store) -> int:
    read = getattr(store, "task_projection_envelopes", None)
    if read is None:
        return 0
    try:
        return len(read(agent_role="information_brief"))
    except Exception:  # noqa: BLE001
        return 0
