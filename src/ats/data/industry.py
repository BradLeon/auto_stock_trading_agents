"""Curated industry / supply-chain knowledge (Obsidian notes).

Stable, slow-changing sector background (AI-hardware supply chain: positioning,
moats, cycle, pricing power) injected into PEAD prep's thesis building. Distinct
from `documents` (per-ticker official filings, score phase) — this is one shared
sector brief. Missing/unset root -> [] (feature silently skipped). Never raises.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .base import safe_fetch
from .documents import _read_doc

log = logging.getLogger("ats.data.industry")

name = "industry"


def fetch_notes() -> list[tuple[str, str]]:
    """Read the whitelisted (or all) industry notes -> [(filename, text), ...]."""
    from ..config import load_pead_global

    cfg = load_pead_global().get("industry_notes", {}) or {}
    root = cfg.get("root", "") or ""
    if not root:
        return []
    folder = Path(root)
    if not folder.is_dir():
        log.info("industry_notes root not found, skipping: %s", root)
        return []

    whitelist = cfg.get("files", []) or []
    if whitelist:
        paths = [folder / f for f in whitelist]
    else:
        paths = sorted(folder.glob("*.md"))

    cap = int(cfg.get("max_chars_per_file", 12000))
    out: list[tuple[str, str]] = []
    for p in paths:
        if not p.is_file():
            log.info("industry note missing: %s", p.name)
            continue
        text = safe_fetch(lambda p=p: _read_doc(p), source=f"industry:{p.name}", attempts=1)
        if text:
            out.append((p.name, text[:cap]))
    return out


def fetch_named(paths: list[str], cap: int = 16000) -> list[tuple[str, str]]:
    """Read specific note files (repo-relative or absolute) -> [(name, text), ...].
    Used by the structure analyst for per-subgroup KB notes. Missing -> skipped."""
    from ..config import REPO_ROOT

    out: list[tuple[str, str]] = []
    for raw in paths:
        p = Path(raw)
        if not p.is_absolute():
            p = REPO_ROOT / raw
        if not p.is_file():
            log.info("structure KB note missing: %s", p)
            continue
        text = safe_fetch(lambda p=p: _read_doc(p), source=f"kb:{p.name}", attempts=1)
        if text:
            out.append((p.stem, text[:cap]))
    return out


def as_context(notes: list[tuple[str, str]]) -> str:
    """Join notes into one background block with filename headers."""
    if not notes:
        return ""
    return "\n\n".join(f"### {name}\n{text}" for name, text in notes)
