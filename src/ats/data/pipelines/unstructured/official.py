"""Official-disclosure acquisition steps used by workflow entrypoints."""

from __future__ import annotations

from datetime import date

from ...base import safe_fetch


def earnings_release_record(symbol: str, *, near: str = "") -> dict | None:
    """Return the validated event-bound official release record, if available.

    The existing official-document facade owns SEC access and parsing; this pipeline
    owns retrying an acquisition step for a consumer.  Call-time forwarding retains
    the compatibility seam until the flat facade is retired.  It deliberately
    receives an event date, so a generic latest 8-K can never be mistaken for this
    quarter's release.
    """
    return safe_fetch(
        lambda: _legacy_earnings_release_record(symbol, near=near),
        source=f"sec-8k:{symbol.upper()}", attempts=2,
    )


def _legacy_earnings_release_record(symbol: str, *, near: str) -> dict | None:
    """Compatibility-only forwarding point; keep the SEC implementation singular."""
    from ats.data import documents

    return documents.sec_8k_release(symbol, near=near)


def release_filed_on_or_after(symbol: str, *, expected_date: date) -> tuple[bool, str]:
    """Check the official fallback used when a calendar has no actual result."""
    record = earnings_release_record(symbol, near=expected_date.isoformat())
    if not record:
        return False, "无实际 EPS，且未取到 8-K"
    filed = record.get("filed")
    if filed is None:
        return False, "8-K 无申报日期，无法确认是本季"
    if filed < expected_date:
        return False, f"最新 8-K 申报于 {filed}，早于财报日 {expected_date}（上一季）"
    return True, f"8-K 申报于 {filed}（≥ 财报日 {expected_date}）"


__all__ = ["earnings_release_record", "release_filed_on_or_after"]
