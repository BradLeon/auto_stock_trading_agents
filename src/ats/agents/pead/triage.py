"""Cheap-LLM news triage: batch-score items for materiality before the manager
LLM sees them. Noise is stored but excluded from context; the hottest items get
their article bodies fetched for depth.

Degrades safely: on any LLM failure `score_items` returns {} and the caller
treats unscored items as pass-through (current behavior).
"""

from __future__ import annotations

import logging

from ...schemas.news import NewsItem
from ..base import run_structured
from .outputs import TriageBatchView

log = logging.getLogger("ats.agents.pead.triage")

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


def enrich(items: list[NewsItem], *, max_items: int, max_chars: int) -> list[tuple[NewsItem, str]]:
    """Fetch article bodies for the given items (already sorted by importance)."""
    from ...data.web import fetch_article_text

    out: list[tuple[NewsItem, str]] = []
    for it in items:
        if len(out) >= max_items:
            break
        if not it.url:
            continue
        body = fetch_article_text(it.url)
        if body:
            out.append((it, body[:max_chars]))
    return out
