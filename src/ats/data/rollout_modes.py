"""Resolve independent governed-source and consumer rollout modes.

Resolution order is environment override, mutable release overlay, checked-in
baseline, then the catalog default.  Source publication and consumer routing are
separate on purpose: a source may collect in shadow before any Agent reads it.
"""

from __future__ import annotations

import os

import yaml

READ_MODES = {"legacy", "shadow", "platform", "fallback", "off"}


def _mode(value: str, *, context: str) -> str:
    normalized = value.strip().lower()
    if normalized not in READ_MODES:
        raise ValueError(f"invalid structured read mode {normalized!r} for {context}")
    return normalized


def source_mode(source_id: str) -> str:
    """Return the effective collection/release mode for one persistent source."""
    normalized = source_id.upper().replace("-", "_")
    env_name = f"ATS_STRUCTURED_SOURCE_{normalized}_MODE"
    if os.environ.get(env_name, "").strip():
        return _mode(os.environ[env_name], context=env_name)
    from .release import overlay_mode

    overlay = overlay_mode("source", source_id)
    if overlay:
        return _mode(overlay, context=f"release source {source_id}")
    raw = _feature_flags()
    value = (raw.get("sources") or {}).get(source_id, raw.get("default_mode", "legacy"))
    return _mode(str(value), context=f"catalog source {source_id}")


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
        return _mode(overlay, context=f"release consumer {consumer}")
    raw = _feature_flags()
    source_overrides = raw.get("consumer_sources") or {}
    if source_id:
        value = (source_overrides.get(consumer) or {}).get(source_id, "")
        if value:
            return _mode(str(value), context=f"catalog consumer/source {consumer}/{source_id}")
    value = (raw.get("consumers") or {}).get(consumer, raw.get("default_mode", "legacy"))
    return _mode(str(value), context=f"catalog consumer {consumer}")


def _feature_flags() -> dict:
    from ..config import REPO_ROOT

    path = REPO_ROOT / "config" / "data" / "structured.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(raw.get("feature_flags") or {})
