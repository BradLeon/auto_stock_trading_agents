"""Research/newsletter extraction, migrated from agents/pead/research.py
(Phase D task 4.2).

What changed against the pre-Phase-D flow:

* the extraction product is an InformationBrief PROJECTION per processed
  article — the dossier is never touched, and the old `_inject_events` path
  (material insights becoming synthetic pead_events with pre-seeded triage
  under each affected target) is gone: information does not reach the
  fundamental analyst's event stream, only its brief projections;
* the universe card is built from CONFIG, not from dossier narratives;
* idempotency stays on the document-processing ledger: the same document
  version + extractor version reuses the earlier brief; a new document version
  publishes a second projection superseding the first (4.8).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ...schemas.research import Article, Insight
from ..base import run_structured
from ..pead.outputs import InsightBatchView
from . import assemble
from .briefs import (cluster_key, cluster_summary, clocks_for,
                     publish_information_brief)

log = logging.getLogger("ats.agents.information.extract")

_DIRECTIONS = {"bullish", "bearish", "neutral"}
_IMPACT_PATHS = {"direct", "supply_chain", "competitive", "demand", "macro"}
_MAX_QUOTE_CHARS = 400
PROCESSOR_VERSION = assemble.PROCESSOR_VERSION


def run(*, use_llm: bool = True, since: datetime | None = None) -> list[Insight]:
    """One research pass over admitted articles: extract insights, publish briefs.

    Returns the extracted insights (raw extraction product, persisted to the
    insights table as a neutral data product); the CONTRACT output is the set
    of InformationBrief projections written alongside them.
    """
    from ...config import load_pead_global
    from ...memory import get_store

    g = load_pead_global()
    rcfg = g["research"]
    store = get_store()

    since = since or (datetime.now(timezone.utc) - timedelta(days=rcfg["lookback_days"]))
    candidates = assemble.admitted_articles(store, since=since)
    log.info("information: %d admitted article candidates", len(candidates))
    if not candidates:
        return []

    universe_card, ticker_to_targets = assemble.signal_chain_universe(g.get("targets", []))
    universe = set(ticker_to_targets)
    all_insights: list[Insight] = []
    claimed = 0
    for art in candidates:
        entity = _publisher_entity(art.source)
        catalog = store.document_by_external_id(art.id)
        legacy_document = store.latest_document_version(art.id)
        doc_id = ((catalog or {}).get("document_id") or
                  (art.id if legacy_document else "") or
                  f"{entity}:{_article_slug(art.id)}:research_article")

        # Preserve the deployed seen-set without re-spending on the first
        # migration run (same policy the PEAD research path used).
        if store.article_seen(art.id):
            version_id = store.begin_document_processing(doc_id, "information",
                                                         PROCESSOR_VERSION)
            if version_id:
                store.finish_document_processing(
                    version_id, "information", PROCESSOR_VERSION, ok=True,
                    outputs=store.insight_count(art.id),
                    note="migrated from research_articles")
            continue

        if claimed >= rcfg["max_articles_per_run"]:
            break
        version_id = store.begin_document_processing(doc_id, "information",
                                                     PROCESSOR_VERSION)
        if not version_id:
            continue  # already processed at this extractor version — reuse (4.8)
        claimed += 1
        insights = _extract(art, universe_card, universe,
                            rcfg["article_chars"]) if use_llm else []
        store.save_article(art)
        store.save_insights(art.id, insights)
        store.finish_document_processing(
            version_id, "information", PROCESSOR_VERSION, ok=True, outputs=len(insights))
        _publish_article_brief(store, art, insights, doc_id)
        _maybe_push(insights, art, rcfg)
        all_insights += insights
    return all_insights


def _extract(art: Article, universe_card: str, universe: set[str],
             max_chars: int) -> list[Insight]:
    ctx = (
        f"Universe (targets and their signal-chain members):\n{universe_card}\n\n"
        f"Article from {art.source} ({art.published_at:%Y-%m-%d}; "
        f"completeness={art.completeness}): {art.title}\n"
        f"---\n{art.body[:max_chars]}\n---\n\n"
        "Extract per-ticker insights (direct AND second-order read-throughs). "
        "Only universe tickers. An empty list is a valid answer. "
        "If completeness is partial, use only information visible in this preview; "
        "do not infer omitted content or describe it as the full article."
    )
    try:
        view: InsightBatchView = run_structured("research_extract", InsightBatchView, ctx,
                                                skill_slug="research-insight")
    except Exception as exc:  # noqa: BLE001
        log.warning("information extraction failed for %s: %s", art.id, exc)
        return []

    out = []
    for r in view.insights:
        ticker = r.ticker.strip().upper()
        if ticker not in universe:
            log.info("information: dropped non-universe ticker %s from %s",
                     ticker, art.id)
            continue
        out.append(Insight(
            article_id=art.id, ticker=ticker,
            direction=r.direction if r.direction in _DIRECTIONS else "neutral",
            impact_path=r.impact_path if r.impact_path in _IMPACT_PATHS else "direct",
            summary=r.summary.strip(),
            evidence_quote=r.evidence_quote.strip()[:_MAX_QUOTE_CHARS],
            confidence=max(0.0, min(1.0, float(r.confidence)))))
    return out


def _publish_article_brief(store, art: Article, insights: list[Insight],
                           doc_id: str) -> str | None:
    """One InformationBrief projection per processed article (best-effort)."""
    from ...agent.task_projection import ProjectionScope

    if not insights:
        return None
    clocks = clocks_for({"published_at": art.published_at.isoformat()
                         if hasattr(art.published_at, "isoformat")
                         else str(art.published_at)})
    ckey = cluster_key(art.title)
    stats = cluster_summary([
        {"source": art.source, "confidence": i.confidence} for i in insights])
    previous = store.reusable_task_projection(
        agent_role="information_brief",
        scope=ProjectionScope(kind="entity", id=doc_id),
        schema_name="InformationBrief") if hasattr(store, "reusable_task_projection") else None
    fact_changes = [
        f"{i.ticker}: {i.summary}（方向 {i.direction}，影响路径 {i.impact_path}，"
        f"证据「{i.evidence_quote[:120]}」）" for i in insights]
    unverified = [
        f"{i.ticker}: 单一来源陈述，置信 {i.confidence:.2f}，待核验"
        for i in insights if i.confidence < 0.7] or [
        "全文已核读，无额外待核验项" ]
    payload = {
        "entity": _publisher_entity(art.source),
        "headline": art.title,
        "summary": (insights[0].summary if insights else art.title)[:300],
        "relevance": "high" if stats["confidence"] and stats["confidence"] >= 0.7
        else ("medium" if stats["confidence"] and stats["confidence"] >= 0.4 else "low"),
        "sources": [art.source],
        "fact_changes": fact_changes,
        "impact_candidates": sorted({i.impact_path for i in insights}),
        "entities": sorted({i.ticker for i in insights}),
        "confidence": stats["confidence"],
        "freshness": f"published {clocks['published_at'][:10]}",
        "unverified": unverified,
        "event_time": clocks["event_time"],
        "published_at": clocks["published_at"],
        "extracted_at": clocks["extracted_at"],
        "cluster_key": ckey,
        "source_count": stats["document_count"],
        "independent_sources": stats["independent_sources"],
    }
    try:
        return publish_information_brief(
            store, payload,
            scope=ProjectionScope(kind="entity", id=doc_id),
            as_of=clocks["extracted_at"],
            input_refs=[art.id],
            supersedes_projection_id=previous.projection_id if previous else "")
    except Exception as exc:  # noqa: BLE001 - brief publication is contract-checked
        log.warning("information brief publication failed for %s: %s", art.id, exc)
        return None


def _publisher_entity(source: str) -> str:
    return (source or "").split(":", 1)[-1] or source or "unknown"


def _article_slug(article_id: str) -> str:
    """Stable filesystem identity shared by every consumer of a research article
    (inlined from the data layer so agents never import a provider module)."""
    import re as _re

    return _re.sub(r"[^A-Za-z0-9]+", "-", article_id or "").strip("-")[:120]


def _maybe_push(insights: list[Insight], art: Article, rcfg: dict) -> None:
    """Hot-insight Feishu card — a human notification, not an analysis output."""
    hot = [i for i in insights if i.confidence >= rcfg["push_threshold"]]
    if not hot:
        return
    try:
        from ...channel import get_channel
        from ...schemas.channel import Notification

        body = "\n".join(f"[{i.direction}/{i.impact_path}] {i.ticker}: {i.summary}" for i in hot)
        get_channel("feishu").push(Notification(
            kind="info", title=f"Research insight — {art.title[:60]}", body=body))
        log.info("information: pushed %d insights to Feishu", len(hot))
    except Exception as exc:  # noqa: BLE001 - push is best-effort
        log.info("information: Feishu push skipped: %s", exc)
