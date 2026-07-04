"""Macro review orchestration: assemble -> one Opus synthesis (equity-strategist)
-> persist. LLM failure never overwrites the stored latest review."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...schemas.macro_strategy import (
    SIGNALS,
    STANCES,
    MacroConfig,
    MacroReview,
    SectorTilt,
    ThemeAssess,
)
from ..base import run_structured
from . import assemble
from .outputs import MacroReviewLLMView

log = logging.getLogger("ats.agents.macro.review")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run(name: str = "macro", *, use_llm: bool = True, live_data: bool = True) -> MacroReview:
    from ...config import load_macro_config
    from ...memory import get_store

    cfg = load_macro_config(name)
    store = get_store()
    mc = assemble.build(cfg, live_data=live_data)
    log.info("macro %s: context %s", name, mc.stats())

    if not use_llm:
        review = MacroReview(name=name, as_of=_now(), regime="(no-llm)",
                             summary=f"context stats: {mc.stats()}")
        store.save_macro_review(review)
        return review

    try:
        view: MacroReviewLLMView = run_structured("macro_strategist", MacroReviewLLMView,
                                                  mc.as_context(), skill_slug="macro-strategist")
    except Exception as exc:  # noqa: BLE001
        log.warning("macro review LLM failed for %s: %s", name, exc)
        prior = store.latest_macro_review(name)
        return prior or MacroReview(name=name, as_of=_now(), regime="(LLM unavailable)")

    review = _to_review(name, cfg, view)
    store.save_macro_review(review)
    return review


def _to_review(name: str, cfg: MacroConfig, view: MacroReviewLLMView) -> MacroReview:
    valid = {t.key: t.label for t in cfg.themes}
    themes = []
    for tv in view.themes:
        if tv.key not in valid:
            log.warning("macro %s: dropped unknown theme key %r", name, tv.key)
            continue
        themes.append(ThemeAssess(
            key=tv.key, label=valid[tv.key], direction=tv.direction,
            transmission=tv.transmission,
            signal=tv.signal if tv.signal in SIGNALS else "neutral", note=tv.note))

    tilts = [SectorTilt(sector=tv.sector.strip(),
                        stance=tv.stance if tv.stance in STANCES else "中性",
                        rationale=tv.rationale)
             for tv in view.sector_tilts if tv.sector.strip()]

    return MacroReview(
        name=name, as_of=_now(), regime=view.regime, summary=view.summary,
        rate_path=view.rate_path, sector_tilts=tilts,
        asset_implications=view.asset_implications, themes=themes, top_risks=view.top_risks)
