"""Compact macro-review blocks for injection into PEAD prep/monitor and the
sector review. Import-light (store reads only). Returns "" on any failure."""

from __future__ import annotations

import logging

log = logging.getLogger("ats.agents.macro.context")


def prep_block(symbol: str = "", name: str = "macro", max_chars: int = 1200) -> str:
    """regime + rate path + asset implications + sector tilts (for PEAD prep)."""
    try:
        review = _load(name)
        return review.regime_block(max_chars) if review else ""
    except Exception as exc:  # noqa: BLE001 - never break prep
        log.warning("macro prep_block failed: %s", exc)
        return ""


def monitor_hint(name: str = "macro", max_chars: int = 280) -> str:
    """1-2 lines: regime + rate path, for materiality calibration."""
    try:
        review = _load(name)
        if review is None:
            return ""
        hint = f"宏观评审 {review.as_of:%Y-%m-%d}: {review.regime}"
        if review.rate_path:
            hint += f" | 利率: {review.rate_path}"
        return hint[:max_chars]
    except Exception as exc:  # noqa: BLE001
        log.warning("macro monitor_hint failed: %s", exc)
        return ""


def sector_block(name: str = "macro", max_chars: int = 1500) -> str:
    """regime + rate path + asset implications + full sector tilts (for the sector review)."""
    try:
        review = _load(name)
        if review is None:
            return ""
        parts = [f"[宏观评审 {review.as_of:%Y-%m-%d}] {review.regime}"]
        if review.rate_path:
            parts.append(f"利率路径: {review.rate_path}")
        if review.asset_implications:
            parts.append(f"资产含义: {review.asset_implications}")
        for t in review.sector_tilts:
            parts.append(f"  · {t.sector}: {t.stance} — {t.rationale}")
        return "\n".join(parts)[:max_chars]
    except Exception as exc:  # noqa: BLE001
        log.warning("macro sector_block failed: %s", exc)
        return ""


def _load(name: str):
    from ...memory import get_store

    review = get_store().latest_macro_review(name)
    if review is None or review.regime.startswith("("):   # stub/no-llm reviews aren't useful
        return None
    return review
