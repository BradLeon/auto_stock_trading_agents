"""Document identification pass, migrated from agents/pead/monitor.py
(Phase D task 4.4).

The monitor's ANALYSIS side (identify what is new among admitted events,
triage, summarize) survives; its WRITE side is gone: `_apply` — which merged
narrative deltas and expectation changes straight into the PEAD dossier — is
retired. The output of this pass is an InformationBrief projection per target;
the fundamental analyst reads briefs, never receives dossier writes.

Event ingestion stays in the PEAD runtime bridge for now — it is the
pre-Phase-D ingestion seam scheduled for the provider-direct cleanup, and it
feeds the shared admitted-events table that this pass reads.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...schemas.news import ContextUpdate
from ..base import run_structured
from ..pead.outputs import ContextUpdateView
from . import assemble
from .briefs import (cluster_key, cluster_summary, clocks_for,
                     publish_information_brief)

log = logging.getLogger("ats.agents.information.documents")

MAX_EVENTS_IN_CONTEXT = 25


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_document_pass(store, symbol: str, *, cfg, fresh: list, use_llm: bool = True,
                      lookback_days: int = 7, articles: list[tuple] | None = None) -> ContextUpdate:
    """Identify material admitted events for one target and publish a brief.

    `fresh` is the newly-ingested event list supplied by the runtime bridge
    (ingestion is not this module's concern); `articles` is the admitted full
    texts the bridge enriched for the hottest items. Returns a ContextUpdate
    summary (CLI compatibility); the contract output is the published brief.
    """
    symbol = symbol.upper()
    cutoff = assemble.default_cutoff(lookback_days)
    admitted = assemble.admitted_events(store, symbol, cutoff=cutoff)

    if not use_llm or (not admitted and not fresh):
        return ContextUpdate(symbol=symbol, as_of=_now(), materiality=0.0,
                             event_summary=f"{len(admitted)} admitted events"
                             if admitted else "no new events")

    # The bridge's `fresh` list is ALREADY triage-filtered and ranked — it is the
    # authoritative material for this pass. Admitted events are the fallback for
    # standalone runs (entry.py) where no bridge ran.
    material = [_as_record(e) for e in fresh] if fresh else admitted
    # 简报保持 thesis-free：基本面叙事是基本面的观点，不由信息角色读取。

    if not material:
        return ContextUpdate(symbol=symbol, as_of=_now(), materiality=0.0,
                             event_summary="all triaged as noise")

    update = _llm_brief(symbol, material, articles or [])
    _publish_target_brief(store, symbol, update, material)
    return update


def _as_record(event) -> dict:
    """Normalize a NewsItem or dict into the dict shape the brief helpers use."""
    if isinstance(event, dict):
        return event
    published = getattr(event, "published_at", None)
    return {
        "id": getattr(event, "id", ""),
        "source": getattr(event, "source", ""),
        "headline": getattr(event, "headline", ""),
        "published_at": (published.isoformat(timespec="seconds")
                         if hasattr(published, "isoformat") else str(published or "")),
    }


def _llm_brief(symbol: str, material: list, articles: list[tuple]) -> ContextUpdate:
    """Summarize what is genuinely new. Read-only over the target's thesis text."""
    events_txt = "\n".join(
        f"  - [{e.get('published_at', '')[:10]}] {e.get('headline') or ''}"
        for e in material[:MAX_EVENTS_IN_CONTEXT])
    articles_txt = "\n\n".join(
        f"=== {it.headline} ===\n{body}" for it, body in articles)
    ctx = (
        f"Target: {symbol}. Admitted events (target + signal-chain peers):\n"
        f"{events_txt}\n\n"
        + (f"Full text of the most material articles:\n{articles_txt}\n\n"
           if articles_txt else "")
        + "Summarize what is factually NEW (no advice, no position language): "
        "list the fact changes, who they might impact, what remains unverified, "
        "and an overall confidence. Most days are low-materiality noise — say so."
    )
    try:
        view: ContextUpdateView = run_structured("context_monitor", ContextUpdateView, ctx,
                                                 skill_slug="pead-monitor")
        return ContextUpdate(
            symbol=symbol, as_of=_now(),
            materiality=max(0.0, min(1.0, float(view.materiality))),
            event_summary=view.event_summary, narrative_delta=view.narrative_delta,
            sources=[str(e.get("source") or "") for e in material[:MAX_EVENTS_IN_CONTEXT]])
    except Exception as exc:  # noqa: BLE001
        log.warning("information monitor LLM failed for %s: %s", symbol, exc)
        return ContextUpdate(symbol=symbol, as_of=_now(), materiality=0.0,
                             event_summary=f"{len(material)} new events (LLM unavailable)")


def _publish_target_brief(store, symbol: str, update: ContextUpdate,
                          material: list[dict]) -> str | None:
    """One InformationBrief projection per target pass (best-effort, 4.4/4.10/4.11)."""
    from ...agent.task_projection import ProjectionScope

    if not material:
        return None
    clocks = clocks_for({"published_at": material[0].get("published_at") or ""},
                        extracted_at=update.as_of)
    stats = cluster_summary([
        {"source": e.get("source") or "", "confidence": update.materiality}
        for e in material])
    fact_changes = [
        f"[{str(e.get('published_at') or '')[:10]}] {e.get('headline') or ''}"
        for e in material[:MAX_EVENTS_IN_CONTEXT]]
    payload = {
        "entity": symbol,
        "headline": update.event_summary[:200] or f"{symbol} 事件更新",
        "summary": (update.narrative_delta or update.event_summary or "")[:400],
        "relevance": ("high" if update.materiality >= 0.7
                      else "medium" if update.materiality >= 0.4 else "low"),
        "sources": sorted(stats["sources"]) or ["unknown"],
        "fact_changes": fact_changes,
        "impact_candidates": ["supply_chain"] if any(
            e.get("symbol", symbol) != symbol for e in material) else ["direct"],
        "entities": [symbol],
        "confidence": update.materiality,
        "freshness": f"published {clocks['published_at'][:10]}",
        "unverified": ["事件影响幅度与持续性待后续材料核验"],
        "event_time": clocks["event_time"],
        "published_at": clocks["published_at"],
        "extracted_at": clocks["extracted_at"],
        "cluster_key": cluster_key(fact_changes[0]) if fact_changes else "",
        "source_count": stats["document_count"],
        "independent_sources": stats["independent_sources"],
    }
    try:
        return publish_information_brief(
            store, payload,
            scope=ProjectionScope(kind="entity", id=symbol),
            as_of=clocks["extracted_at"], input_refs=[])
    except Exception as exc:  # noqa: BLE001 - contract violations must fail loudly in logs
        log.warning("information target brief failed for %s: %s", symbol, exc)
        return None
