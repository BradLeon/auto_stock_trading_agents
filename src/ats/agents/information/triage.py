"""Materiality triage, migrated from agents/pead/triage.py (Phase D task 4.3).

`score_items` is unchanged in behavior. `enrich` is the one deliberate change:
the full-text path is now READ-ONLY over admitted document bodies (via the
Workflow-Memory bridge) and never fetches a URL from inside an agent — bodies
are ingested by data-layer pipelines; a brief simply notes when full text is
not yet admitted.
"""

from __future__ import annotations

import logging

from ...schemas.news import NewsItem
from ..base import run_structured
from ..pead.outputs import TriageBatchView

log = logging.getLogger("ats.agents.information.triage")

BATCH_SIZE = 40


def score_items(symbol: str, thesis: str, items: list[NewsItem]) -> dict[str, tuple[float, str]]:
    """Score every item's materiality vs the thesis. Returns {item.id: (score, category)}."""
    scores: dict[str, tuple[float, str]] = {}
    for start in range(0, len(items), BATCH_SIZE):
        batch = items[start:start + BATCH_SIZE]
        lines = "\n".join(
            f"[{i}] [{it.published_at:%Y-%m-%d} {it.source}] {it.headline}"
            + (f" — {it.summary[:200]}" if it.summary else "")
            for i, it in enumerate(batch))
        ctx = (
            f"Target: {symbol}. Current thesis:\n{thesis}\n\n"
            f"News items to triage:\n{lines}\n\n"
            "Score EVERY item (echo idx exactly)."
        )
        try:
            view: TriageBatchView = run_structured("news_triage", TriageBatchView, ctx,
                                                   skill_slug="news-triage")
        except Exception as exc:  # noqa: BLE001
            log.warning("news triage failed for %s (batch %d): %s", symbol, start, exc)
            return {}
        for r in view.items:
            if 0 <= r.idx < len(batch):
                score = max(0.0, min(1.0, float(r.materiality)))
                scores[batch[r.idx].id] = (score, r.category)
    return scores


def enrich(items: list[NewsItem], *, max_items: int, max_chars: int,
           store=None) -> list[tuple[NewsItem, str]]:
    """Full text for the given items — ADMITTED BODIES ONLY (Phase D 4.3).

    No network: an item without an admitted body is skipped (its metadata still
    enters the brief). This replaces the old path that fetched article URLs
    directly from inside the agent.
    """
    from ...memory import get_store

    out: list[tuple[NewsItem, str]] = []
    store = get_store() if store is None else store
    for it in items:
        if len(out) >= max_items:
            break
        if not it.url and not it.id:
            continue
        try:
            body = store.admitted_news_body(it)
        except Exception as exc:  # noqa: BLE001 - body read is best-effort
            log.info("information: admitted body unavailable for %s: %s", it.id, exc)
            body = ""
        if body:
            out.append((it, body[:max_chars]))
    return out
