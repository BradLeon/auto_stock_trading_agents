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

    from . import documents, extract

    insights = extract.run(use_llm=use_llm)
    summary["articles"] = len({i.article_id for i in insights})
    summary["briefs"] += _brief_count(store)

    targets = [s.upper() for s in (symbols or g.get("targets", []))]
    for sym in targets:
        try:
            cfg = _pead_cfg(sym)
            documents.run_document_pass(store, sym, cfg=cfg, fresh=[],
                                        use_llm=use_llm)
            summary["targets"].append(sym)
        except Exception as exc:  # noqa: BLE001 - one target must not stop the pass
            log.warning("information pass failed for %s: %s", sym, exc)
    summary["briefs"] = _brief_count(store)
    return summary


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
