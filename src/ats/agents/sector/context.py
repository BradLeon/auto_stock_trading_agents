"""Compact sector-review blocks for injection into PEAD prep/monitor.

Import-light on purpose (store reads only) so graph/pead.py and monitor.py can
call it without pulling the assembly stack. Returns "" on any failure.
"""

from __future__ import annotations

import logging

log = logging.getLogger("ats.agents.sector.context")


def prep_block(sector: str, symbol: str, max_chars: int = 1500) -> str:
    """regime + summary + rotation + this symbol's layer assessment + its call."""
    try:
        review, cfg = _load(sector)
        if review is None:
            return ""
        parts = [f"[行业评审 {review.as_of:%Y-%m-%d}] {review.regime}", review.summary]
        if review.rotation_advice:
            parts.append(f"轮动: {review.rotation_advice}")
        parts.append(_layer_line(review, cfg, symbol))
        c = review.call_for(symbol)
        if c:
            parts.append(f"本票 call: {c.stance} (conviction {c.conviction:.2f}) — {c.rationale}")
        return "\n".join(p for p in parts if p)[:max_chars]
    except Exception as exc:  # noqa: BLE001 - never break prep
        log.warning("sector prep_block failed: %s", exc)
        return ""


def monitor_hint(symbol: str, sector: str = "ai_hardware", max_chars: int = 280) -> str:
    """1-3 lines: regime + this symbol's layer signal, for materiality calibration."""
    try:
        review, cfg = _load(sector)
        if review is None:
            return ""
        hint = f"行业评审 {review.as_of:%Y-%m-%d}: {review.regime}"
        key = cfg.layer_of(symbol) if cfg else None
        v = review.verdict_for(key)
        if v is not None:
            hint += f" | {_label(cfg, key)}: {v.allocation}"
        else:
            a = review.layer_assessment(key)
            if a:
                hint += f" | {a.label}: {a.signal}, 景气 {a.boom_score:.0f}"
        return hint[:max_chars]
    except Exception as exc:  # noqa: BLE001
        log.warning("sector monitor_hint failed: %s", exc)
        return ""


def _load(sector: str):
    from ...config import load_sector_config
    from ...memory import get_store

    review = get_store().latest_sector_review(sector)
    if review is None or review.regime.startswith("("):   # stub/no-llm reviews aren't useful
        return None, None
    try:
        cfg = load_sector_config(sector)
    except FileNotFoundError:
        cfg = None
    return review, cfg


def _label(cfg, key: str | None) -> str:
    if not cfg or not key:
        return key or ""
    layer = cfg.layer_by_key(key)
    return layer.label if layer else key


def _layer_line(review, cfg, symbol: str) -> str:
    """This symbol's layer, as the allocation call — falling back to the old assessment.

    The verdict is what a downstream reader can act on ("this layer is 低配"); the old
    `boom_score` could not be turned into a position. Reviews stored before 2026-08-20
    only have the assessment, so both shapes stay readable.
    """
    key = cfg.layer_of(symbol) if cfg else None
    if key is None:
        return ""
    v = review.verdict_for(key)
    if v is not None:
        bits = [f"本层 {_label(cfg, key)}: **{v.allocation}**（信心 {v.confidence:.2f}）"]
        if v.cycle_position:
            bits.append(f"周期 {v.cycle_position}")
        if not v.has_claims:
            bits.append("⚠️ 本层无命题，该结论没有议题证据支撑")
        if v.reversal_triggers:
            bits.append("反转看: " + "；".join(v.reversal_triggers[:2]))
        return "; ".join(bits)
    a = review.layer_assessment(key)
    if a:
        return (f"本层 {a.label}: 景气 {a.boom_score:.0f}, {a.supply_demand}; "
                f"定价权: {a.pricing_power}; 周期: {a.cycle_position} ({a.signal})")
    return ""
