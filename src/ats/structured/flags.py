"""Consumer/source read modes with environment overrides and safe legacy default."""

from __future__ import annotations

import os

from .catalog import StructuredCatalog


READ_MODES = {"legacy", "shadow", "platform", "fallback"}


def _mode(value: str, *, context: str) -> str:
    normalized = value.strip().lower()
    if normalized not in READ_MODES:
        raise ValueError(f"invalid structured read mode {normalized!r} for {context}")
    return normalized


def source_mode(source_id: str) -> str:
    """Independent ingestion/source rollout gate, separate from consumer reads."""
    normalized = source_id.upper().replace("-", "_")
    env_name = f"ATS_STRUCTURED_SOURCE_{normalized}_MODE"
    if os.environ.get(env_name, "").strip():
        return _mode(os.environ[env_name], context=env_name)
    from .release import overlay_mode

    overlay = overlay_mode("source", source_id)
    if overlay:
        return _mode(overlay, context=f"source release overlay {source_id}")
    config = StructuredCatalog.load().raw.get("feature_flags", {})
    value = str(config.get("sources", {}).get(
        source_id, config.get("default_mode", "legacy")))
    return _mode(value, context=f"source {source_id}")


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
    from .release import overlay_mode

    overlay = overlay_mode("consumer", consumer)
    if overlay:
        return _mode(overlay, context=f"consumer release overlay {consumer}")
    config = StructuredCatalog.load().raw.get("feature_flags", {})
    value = str(
        config.get("consumer_sources", {}).get(consumer, {}).get(
            source_id,
            config.get("consumers", {}).get(
                consumer, config.get("default_mode", "legacy")),
        )
    ).lower()
    return _mode(value, context=consumer)
