"""Compact technical block for injection into the Chief's context.

Import-light on purpose (store read only), and every entry point swallows its
own errors: a technical reading is an input to a decision, never a reason the
decision cannot be made.
"""

from __future__ import annotations

import logging

log = logging.getLogger("ats.agents.technical.context")


def chief_block(name: str = "technical", max_chars: int = 1200) -> str:
    """Latest readings, trimmed to what the Chief actually needs to act on."""
    try:
        from ...memory import get_store

        review = get_store().latest_technical_review(name)
        return review.chief_block(max_chars) if review else ""
    except Exception as exc:  # noqa: BLE001 - never break the Chief
        log.warning("technical chief_block failed: %s", exc)
        return ""
