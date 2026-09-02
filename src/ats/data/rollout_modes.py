"""Post-cutover guard for the sole supported data read/write path."""

from __future__ import annotations

import os

READ_MODES = {"platform"}


def _mode(value: str, *, context: str) -> str:
    normalized = value.strip().lower()
    if normalized not in READ_MODES:
        raise ValueError(f"invalid structured read mode {normalized!r} for {context}")
    return normalized


def source_mode(source_id: str) -> str:
    """Return the only allowed persistent-source path after retirement."""
    normalized = source_id.upper().replace("-", "_")
    env_name = f"ATS_STRUCTURED_SOURCE_{normalized}_MODE"
    if os.environ.get(env_name, "").strip():
        return _mode(os.environ[env_name], context=env_name)
    return "platform"


def read_mode(consumer: str, *, source_id: str = "") -> str:
    normalized_consumer = consumer.upper().replace("-", "_")
    normalized_source = source_id.upper().replace("-", "_")
    candidates = []
    if normalized_source:
        candidates.append(f"ATS_STRUCTURED_{normalized_consumer}_{normalized_source}_MODE")
    candidates.extend([
        f"ATS_STRUCTURED_{normalized_consumer}_MODE",
        "ATS_STRUCTURED_DEFAULT_MODE",
    ])
    for name in candidates:
        value = os.environ.get(name, "").strip().lower()
        if value:
            return _mode(value, context=name)
    return "platform"
