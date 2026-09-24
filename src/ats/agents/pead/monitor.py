"""PEAD monitor bridge (Phase D task 4.4).

The analysis side of the monitor moved to `agents/information/documents` —
this module keeps only the pre-Phase-D INGESTION seam (fetching new events for
the target + its signal chain into the shared, deduplicated event store, plus
triage scoring) and then delegates identification + summarization + brief
publication to the information analyst.

What is GONE: `_apply` and every dossier write. The dossier narrative and
expectations are no longer updated by the monitor; the fundamental analyst
consumes InformationBrief projections instead. A monitor pass therefore never
mutates the dossier, by construction.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ...schemas.news import ContextUpdate
from . import triage

log = logging.getLogger("ats.agents.pead.monitor")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run(symbol: str, *, use_llm: bool = True, lookback_days: int = 7) -> ContextUpdate:
    """Ingest new events, triage, then delegate the brief pass to information."""
    from ...config import load_pead_config, load_pead_global
    from ...data.products import news_inputs
    from ...memory import get_store
    from ..information import documents

    symbol = symbol.upper()
    cfg = load_pead_config(symbol)
    store = get_store()
    since = _now() - timedelta(days=lookback_days)

    # Ingestion seam: fetch (via the data-product entry) and dedupe into the
    # shared event store.
    collected: list = list(news_inputs.monitor_news(symbol, since, consumer="pead_monitor"))
    for sc in cfg.signal_chain:
        collected += news_inputs.monitor_news(sc.symbol, since, consumer="pead_monitor")
    fresh = store.append_events(symbol, collected)
    log.info("monitor %s: %d fetched, %d new", symbol, len(collected), len(fresh))

    if not use_llm or not fresh:
        update = ContextUpdate(symbol=symbol, as_of=_now(), materiality=0.0,
                               event_summary=f"{len(fresh)} new events" if fresh else "no new events")
        return update

    tcfg = load_pead_global()["monitor"]["triage"]
    material = fresh
    articles: list[tuple] = []
    if tcfg["enabled"]:
        scores = triage.score_items(symbol, cfg.narrative_seed, fresh)
        store.set_triage(scores)
        if scores:
            material = [i for i in fresh
                        if scores.get(i.id, (1.0, ""))[0] >= tcfg["min_score"]]
            material.sort(key=lambda i: scores.get(i.id, (1.0, ""))[0], reverse=True)
            hot = [i for i in material
                   if scores.get(i.id, (0.0, ""))[0] >= tcfg["fulltext_score"]]
            # Full texts are ADMITTED BODIES ONLY (information.triage.enrich) —
            # an un-ingested article stays metadata-only, never a live fetch.
            articles = triage.enrich(hot, max_items=tcfg["max_fulltext"],
                                     max_chars=tcfg["fulltext_chars"], store=store)

    if not material:
        return ContextUpdate(symbol=symbol, as_of=_now(), materiality=0.0,
                             event_summary=f"{len(fresh)} new events, all triaged as noise")

    return documents.run_document_pass(
        store, symbol, cfg={"monitor": load_pead_global().get("monitor", {})},
        fresh=material, use_llm=use_llm, lookback_days=lookback_days,
        articles=articles)
