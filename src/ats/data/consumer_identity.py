"""Canonical consumer identities; historical source and snapshot IDs stay intact."""

from __future__ import annotations

RETIRED_CONSUMER_ALIASES = {
    "evidence_observer": "layer",
    "evidence-observer": "layer",
    "Evidence Observer": "layer",
}


def canonical_consumer(consumer: str) -> str:
    """Resolve a legacy data consumer, never create a second runnable Agent."""
    return RETIRED_CONSUMER_ALIASES.get(consumer, consumer)
