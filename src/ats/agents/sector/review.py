"""Sector review orchestration: assemble context -> one Opus synthesis -> persist.

LLM failure never overwrites the stored latest review — it returns the prior one
(or a stub) and saves nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...schemas.sector import STANCES, CompanyCall, LayerAssessment, SectorConfig, SectorReview
from ..base import run_structured
from . import assemble
from .outputs import SectorReviewLLMView

log = logging.getLogger("ats.agents.sector.review")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run(name: str = "ai_hardware", *, use_llm: bool = True, live_data: bool = True) -> SectorReview:
    from ...config import load_sector_config
    from ...memory import get_store

    cfg = load_sector_config(name)
    store = get_store()
    sc = assemble.build(cfg, live_data=live_data)
    log.info("sector %s: context %s", name, sc.stats())

    if not use_llm:
        review = SectorReview(sector=name, as_of=_now(), regime="(no-llm)",
                              summary=f"context stats: {sc.stats()}")
        store.save_sector_review(review)
        return review

    try:
        view: SectorReviewLLMView = run_structured("sector_analyst", SectorReviewLLMView,
                                                   sc.as_context(), skill_slug="sector-analyst")
    except Exception as exc:  # noqa: BLE001
        log.warning("sector review LLM failed for %s: %s", name, exc)
        prior = store.latest_sector_review(name)
        return prior or SectorReview(sector=name, as_of=_now(), regime="(LLM unavailable)")

    review = _to_review(name, cfg, view)
    store.save_sector_review(review)
    return review


def _to_review(name: str, cfg: SectorConfig, view: SectorReviewLLMView) -> SectorReview:
    valid_keys = {layer.key: layer.label for layer in cfg.layers}
    layers = []
    for lv in view.layers:
        if lv.key not in valid_keys:
            log.warning("sector %s: dropped unknown layer key %r", name, lv.key)
            continue
        layers.append(LayerAssessment(
            key=lv.key, label=valid_keys[lv.key],
            boom_score=max(0.0, min(100.0, float(lv.boom_score))),
            supply_demand=lv.supply_demand, pricing_power=lv.pricing_power,
            capital_flow=lv.capital_flow, cycle_position=lv.cycle_position,
            signal=lv.signal if lv.signal in ("bullish", "neutral", "bearish") else "neutral",
            note=lv.note))

    missing = set(valid_keys) - {a.key for a in layers}
    if missing:
        log.warning("sector %s: LLM omitted layer assessments for %s", name, sorted(missing))

    universe = set(cfg.all_symbols())
    calls = []
    for cv in view.company_calls:
        sym = cv.symbol.strip().upper()
        if sym not in universe:
            # allow lower/mixed-case echoes of KRX/TSE style symbols
            match = next((u for u in universe if u.upper() == sym), None)
            if match is None:
                log.warning("sector %s: dropped non-universe call %r", name, cv.symbol)
                continue
            sym = match
        calls.append(CompanyCall(
            symbol=sym, layer=cv.layer if cv.layer in valid_keys else (cfg.layer_of(sym) or ""),
            stance=cv.stance if cv.stance in STANCES else "持有",
            conviction=max(0.0, min(1.0, float(cv.conviction))),
            rationale=cv.rationale))

    return SectorReview(sector=name, as_of=_now(), regime=view.regime, summary=view.summary,
                        layers=layers, company_calls=calls,
                        rotation_advice=view.rotation_advice, top_risks=view.top_risks)
