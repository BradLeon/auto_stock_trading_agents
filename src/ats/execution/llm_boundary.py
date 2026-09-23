"""The LLM/ledger boundary, enforced in code (§11.2, Phase C group 7).

LLM output may only land in ANNOTATION fields — booleans/labels the LLM judged
— and each such write must carry its source and the time it was generated.
Money, quantity, trade identity, position and attribution values are forever
beyond an LLM's write reach (§16.3: LLMs have no write-back to broker or
deterministic results).

The whitelist is DATA (this module), not convention: the invalidation checker
calls :func:`assert_llm_update_allowed` on every update dict, and the
architecture guard (tests/test_llm_boundary.py) verifies both sides.
"""

from __future__ import annotations

from typing import Mapping

# Annotation fields an LLM branch MAY write. Everything else — amounts,
# quantities, identities, positions, attribution — is forbidden.
LLM_ANNOTATION_FIELDS = frozenset({"invalidation_triggered"})

# Every allowed field must be accompanied by provenance fields, so an
# annotation can always be told apart from a deterministic result.
SOURCE_FIELDS = {"invalidation_triggered": "invalidation_source"}
TIMESTAMP_FIELDS = {"invalidation_triggered": "invalidation_checked_at"}

# Where LLM modules are allowed to persist: annotations land on
# `trade_episodes` via save_episode only. Anything else in the store that
# carries money/identity is out of reach.
LLM_ALLOWED_WRITES = frozenset({"save_episode"})

# Store methods that are ledger write paths — never callable from an LLM
# module (checked textually by the architecture guard).
LEDGER_WRITE_METHODS = frozenset({
    "save_trades", "upsert_fills", "record_snapshot", "save_chief_run",
    "set_cycle_approval", "record_ledger_exception",
})

# Raw SQL targets that would be a ledger write from an LLM module.
LEDGER_SQL_TARGETS = frozenset({
    "INSERT INTO trades", "UPDATE trades", "INSERT INTO fills",
    "UPDATE fills", "INSERT INTO ledger_read_models",
    "INSERT INTO ledger_exceptions", "INSERT INTO performance",
})


class LlmLedgerWriteError(Exception):
    """An LLM branch attempted to write outside the annotation whitelist."""


def assert_llm_update_allowed(updates: Mapping) -> None:
    """Raise :class:`LlmLedgerWriteError` unless EVERY key is whitelisted.

    Also requires the provenance pair (source + checked-at) for each
    annotation actually being written — an unmarked annotation is treated as
    tampering with the ledger's determinism, not an omission.
    """
    # Provenance companions are part of an annotation write, not separate
    # claims — they may only appear NEXT TO their annotation field.
    provenance = set(SOURCE_FIELDS.values()) | set(TIMESTAMP_FIELDS.values())
    for key in updates:
        if key in LLM_ANNOTATION_FIELDS or key in provenance:
            continue
        raise LlmLedgerWriteError(
            f"LLM branches may only write {sorted(LLM_ANNOTATION_FIELDS)} "
            f"(+ provenance {sorted(provenance)}); got {key!r} "
            f"(money/quantity/identity/attribution fields are never "
            f"LLM-writable)")
    for key in updates:
        if key not in LLM_ANNOTATION_FIELDS:
            continue
        src = SOURCE_FIELDS.get(key)
        stamp = TIMESTAMP_FIELDS.get(key)
        if src and not updates.get(src):
            raise LlmLedgerWriteError(
                f"LLM annotation {key!r} must carry its source in {src!r}")
        if stamp and not updates.get(stamp):
            raise LlmLedgerWriteError(
                f"LLM annotation {key!r} must carry its generation time in "
                f"{stamp!r}")
