"""Shared coercions for LLM structured output.

Models routinely return a list field as a JSON *string* (`'[{...}, {...}]'`)
instead of an actual array. Pydantic then fails list validation and the whole
call is discarded — for the macro review that meant silently falling back to the
previous week's stored review, i.e. stale macro background flowing into every
downstream agent with nothing on screen to say so.

These helpers lived only in `agents/pead/outputs.py`, so every later agent
re-hit the same failure. They belong in one place: any new `*/outputs.py` should
import from here rather than growing its own copy.
"""

from __future__ import annotations

import json
import re


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    return v


def _as_objlist(v):
    """Coerce a list-of-objects field.

    Models occasionally serialize the whole list as a JSON string
    ('[{...}, {...}]') instead of an actual array, which fails list validation
    and silently drops every row. Parse it back.
    """
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else [parsed]
    return v


def _split_jsonish_array(s: str) -> list[str] | None:
    """Best-effort parse of a '[...]'-looking string into a list of strings.

    Tries strict JSON first; on failure (models emit pseudo-JSON with UNESCAPED
    inner ASCII quotes, e.g. an item containing "demand visibility"), falls back
    to stripping the outer brackets and splitting on the '","' item boundary.
    Returns None if it doesn't look like an array at all.
    """
    s = s.strip()
    if not (s.startswith("[") and s.endswith("]")):
        return None
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except Exception:
        pass
    inner = s[1:-1].strip()
    if not inner:
        return []
    parts = re.split(r'"\s*,\s*"', inner)      # boundary: closing-quote , opening-quote
    cleaned = [p.strip().strip('"').strip() for p in parts]
    return [p for p in cleaned if p] or None


def _as_strlist(v):
    """Coerce a list-of-strings field.

    Models sometimes serialize the whole list as a JSON-array string
    ('["a", "b"]') instead of an actual array — pydantic then keeps it as a
    single-element list holding the raw blob, so the report renders one giant
    item instead of an ordered list. Parse the array back into real strings.
    """
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        arr = _split_jsonish_array(s)
        return arr if arr is not None else [s]
    # a one-element list whose sole item is itself a JSON-array string
    if isinstance(v, list) and len(v) == 1 and isinstance(v[0], str):
        arr = _split_jsonish_array(v[0])
        if arr is not None:
            return arr
    return v
