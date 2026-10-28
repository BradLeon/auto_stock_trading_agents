"""Independent entrypoint for the information analyst (Phase D task 4.9).

One pass = extraction over admitted articles + a document-identification pass
per configured target. The pass reads admitted documents and configuration
ONLY: it never reads another analyst's projection, and it terminates without
triggering the fundamental analyst, the chief, or any decision flow.
"""

from __future__ import annotations

import logging
from ...workflow.schedule_runtime import scheduled_cli

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

    # Every target owns its bounded direct-material pass. Removing publisher
    # exploration must never re-enable an unbounded historical corpus pre-pass.
    article_ids = set()
    summary["briefs"] += _brief_count(store)

    targets = [s.upper() for s in (symbols or g.get("targets", []))]
    for sym in targets:
        try:
            result = run_information_target(sym, store=store, use_llm=use_llm,
                                            run_extraction=False)
            article_ids.update(item['document_id'] for item in
                               result.get('selection', {}).get('items', []))
            summary["targets"].append(sym)
        except Exception as exc:  # noqa: BLE001 - one target must not stop the pass
            log.warning("information pass failed for %s: %s", sym, exc)
    summary["briefs"] = _brief_count(store)
    summary["articles"] = len(article_ids)
    return summary


@scheduled_cli("information-brief", kind="entity", argument="symbol")
def run_information_target(symbol: str, *, store=None, use_llm: bool = True,
                           run_extraction: bool = True, run_once=None) -> dict:
    """Publish one target brief, using bounded extraction on the native route.

    Dispatcher fan-out calls this per entity and supplies ``run_once`` so the
    The historical corpus pre-pass remains a compatibility path. Native target
    invocations use an explicit target/peer/publisher scope and own-role cache.
    """
    from ...memory import get_store
    from . import documents, extract

    store = get_store() if store is None else store
    from ...workflow.runtime_reads import current_read_context
    from ...workflow.isolation import verified_isolation_root
    context = current_read_context()
    if context is not None and (context.route == "target" or verified_isolation_root() is not None):
        # Candidate acceptance uses the native business implementation inside
        # complete isolation. Native reads still run their scope/route gates;
        # this does not activate a production route or grant qualification.
        from ...config import load_pead_global
        from . import assemble, target_research

        sym = symbol.upper()
        g = load_pead_global()
        result = target_research.run(store, sym, config=g["research"],
            since=assemble.default_cutoff(int(g.get("monitor", {}).get("lookback_days", 7))),
            use_llm=use_llm)
        return {"symbol": sym, "materiality": result.get("materiality"),
                "event_summary": result["status"], "selection": result}
    # A bound task reads only its authorized target/peer documents. The corpus
    # extraction entry owns a different scope and must not escape this task.
    if run_extraction and context is None:
        work = lambda: extract.run(use_llm=use_llm)
        if run_once is not None:
            run_once("information-research-extraction", work)
        else:
            work()
    sym = symbol.upper()
    cfg = _pead_cfg(sym)
    update = documents.run_document_pass(store, sym, cfg=cfg, fresh=[], use_llm=use_llm,
        lookback_days=int(cfg["monitor"].get("lookback_days", 7)))
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
