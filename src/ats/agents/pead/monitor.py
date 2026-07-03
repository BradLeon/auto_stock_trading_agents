"""Continuous PEAD monitor: ingest news/signal-chain events between earnings and
incrementally update the living dossier (narrative + expectations) in memory.

This is *analysis*, so it runs autonomously (no approval). It is the technical
realization of "investing is dynamic and continuous": every session it folds new
target/supply-chain developments into the thesis, so by earnings day the
expectations are current.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ...schemas.news import ContextUpdate, ExpectationChange, NewsItem
from ...schemas.pead import ExpectationSet, PeadDossier
from ..base import run_structured
from .outputs import ContextUpdateView

log = logging.getLogger("ats.agents.pead.monitor")

MAX_EVENTS_IN_CONTEXT = 25   # bound the LLM context (memory management)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run(symbol: str, *, use_llm: bool = True, lookback_days: int = 7) -> ContextUpdate:
    """Fetch new events, triage, fold the material ones into the dossier."""
    from ...config import load_pead_config, load_pead_global
    from ...data import news as news_src
    from ...memory import get_store

    symbol = symbol.upper()
    cfg = load_pead_config(symbol)
    store = get_store()
    since = _now() - timedelta(days=lookback_days)

    # Gather news on the target + signal-chain peers; store deduped.
    collected: list[NewsItem] = list(news_src.fetch_news(symbol, since))
    for sc in cfg.signal_chain:
        collected += news_src.fetch_news(sc.symbol, since)
    fresh = store.append_events(symbol, collected)
    log.info("monitor %s: %d fetched, %d new", symbol, len(collected), len(fresh))

    dossier = store.get_dossier(symbol, cfg.fiscal_label)

    if not use_llm or not fresh:
        update = ContextUpdate(symbol=symbol, as_of=_now(), materiality=0.0,
                               event_summary=f"{len(fresh)} new events" if fresh else "no new events")
        _apply(store, cfg, dossier, update)
        return update

    # Cheap-LLM triage: score, persist, filter noise, fetch bodies of hot items.
    thesis = (dossier.expectation_set.narrative
              if dossier and dossier.expectation_set else cfg.narrative_seed)
    material, articles = fresh, []
    tcfg = load_pead_global()["monitor"]["triage"]
    if tcfg["enabled"]:
        from . import triage

        scores = triage.score_items(symbol, thesis, fresh)
        store.set_triage(scores)
        if scores:
            # Unscored items (triage miss) pass through; scored noise is dropped.
            material = [i for i in fresh
                        if scores.get(i.id, (1.0, ""))[0] >= tcfg["min_score"]]
            material.sort(key=lambda i: scores.get(i.id, (1.0, ""))[0], reverse=True)
            hot = [i for i in material
                   if scores.get(i.id, (0.0, ""))[0] >= tcfg["fulltext_score"]]
            articles = [(it, body, scores[it.id][0]) for it, body in
                        triage.enrich(hot, max_items=tcfg["max_fulltext"],
                                      max_chars=tcfg["fulltext_chars"])]
            log.info("monitor %s: triage kept %d/%d, %d bodies fetched",
                     symbol, len(material), len(fresh), len(articles))

    if not material:
        update = ContextUpdate(symbol=symbol, as_of=_now(), materiality=0.0,
                               event_summary=f"{len(fresh)} new events, all triaged as noise")
        _apply(store, cfg, dossier, update)
        return update

    update = _llm_update(symbol, cfg, dossier, material, articles)
    _apply(store, cfg, dossier, update)
    return update


def _llm_update(symbol, cfg, dossier, fresh: list[NewsItem],
                articles: list[tuple[NewsItem, str, float]] = ()) -> ContextUpdate:
    thesis = (dossier.expectation_set.narrative
              if dossier and dossier.expectation_set else cfg.narrative_seed)
    events_txt = "\n".join(f"  - {e.one_line()}" for e in fresh[:MAX_EVENTS_IN_CONTEXT])
    articles_txt = "\n\n".join(
        f"=== [{it.published_at:%Y-%m-%d} {it.source}] {it.headline} (materiality {score:.2f}) ===\n{body}"
        for it, body, score in articles)
    ctx = (
        f"Living dossier for {symbol}. Current thesis:\n{thesis}\n\n"
        f"New events since last update (target + supply-chain peers):\n{events_txt}\n\n"
        + (f"Full text of the most material articles:\n{articles_txt}\n\n" if articles_txt else "")
        + "Decide materiality (0=noise, 1=thesis-changing), summarize what's genuinely new, "
        "and state any change to the thesis or to specific scorecard expectations. Most days "
        "are low-materiality noise — say so."
    )
    try:
        view: ContextUpdateView = run_structured("context_monitor", ContextUpdateView, ctx,
                                                 skill_slug="pead-monitor")
        return ContextUpdate(
            symbol=symbol, as_of=_now(),
            materiality=max(0.0, min(1.0, float(view.materiality))),
            event_summary=view.event_summary, narrative_delta=view.narrative_delta,
            expectation_changes=[ExpectationChange(dim_key=c.dim_key, change=c.change)
                                 for c in view.expectation_changes],
            sources=[e.source for e in fresh[:MAX_EVENTS_IN_CONTEXT]])
    except Exception as exc:  # noqa: BLE001
        log.warning("monitor LLM failed for %s: %s", symbol, exc)
        return ContextUpdate(symbol=symbol, as_of=_now(), materiality=0.0,
                             event_summary=f"{len(fresh)} new events (LLM unavailable)")


def _apply(store, cfg, dossier: PeadDossier | None, update: ContextUpdate) -> None:
    """Merge the update into the dossier and persist (creating one if needed)."""
    if dossier is None:
        es = ExpectationSet(symbol=cfg.symbol, fiscal_label=cfg.fiscal_label, as_of=_now(),
                            narrative=cfg.narrative_seed)
        dossier = PeadDossier(symbol=cfg.symbol, fiscal_label=cfg.fiscal_label, phase="prep",
                              updated_at=_now(), expectation_set=es)

    if update.narrative_delta and update.materiality > 0:
        es = dossier.expectation_set or ExpectationSet(
            symbol=cfg.symbol, fiscal_label=cfg.fiscal_label, as_of=_now())
        stamp = update.as_of.strftime("%Y-%m-%d")
        es.narrative = (es.narrative + f"\n\n[update {stamp}] {update.narrative_delta}").strip()
        dossier.expectation_set = es

    dossier.updated_at = _now()
    store.save_dossier(dossier)
