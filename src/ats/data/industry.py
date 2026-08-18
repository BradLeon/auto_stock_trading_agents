"""Curated industry / supply-chain knowledge (Obsidian notes).

Stable, slow-changing sector background (AI-hardware supply chain: positioning,
moats, cycle, pricing power) injected into PEAD prep's thesis building. Distinct
from `documents` (per-ticker official filings, score phase) — this is one shared
sector brief. Missing/unset root -> [] (feature silently skipped). Never raises.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .base import safe_fetch
from .documents import _read_doc

log = logging.getLogger("ats.data.industry")

name = "industry"

# --------------------------------------------------------------------------- #
# Locating the criteria section of a knowledge note
# --------------------------------------------------------------------------- #
# Both consumers of that section — `chain.kb_audit` (is the KB being used?) and
# `agents.sector.kb_perturb` (are the criteria load-bearing?) — used to find it by
# splitting on the literal "## 三". That encodes the four-section template
# (分工/技术曲线/判据/误判) as a promise about EVERY note's section NUMBERING, and nothing
# enforced it. Two notes outgrew the template — 芯片设计 to fifteen sections with the
# criteria at §八, 半导体设备 to six with them at §四 — and both tools silently began
# reading the wrong section:
#
#   kb_audit   → `_criteria_of` returned [], so `criteria_total` was 0, the coverage
#                line was suppressed (`if rep.criteria_total`), `uncited` was empty, and
#                the layer printed 「无异常」 for a layer it had measured nothing in.
#   kb_perturb → ablate deleted 技术曲线 / Fabless 边界 while writing a heading claiming
#                the criteria had been removed; poison found nothing to reverse and
#                returned the note unchanged, i.e. ran a control arm labelled 投毒.
#
# Anchoring on the heading's MEANING survives renumbering, which is the only thing about
# these notes that is actually stable. Both current spellings ("护城河判据" in the eight
# sub-layer notes, "判据" in 资本开支链) contain 判据; 常见误判 does not.
CRITERIA_HEADING = re.compile(r"^##[^\n]*判据[^\n]*$", re.M)
_ANY_HEADING = re.compile(r"^##(?!#)", re.M)


def criteria_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of every criteria section (heading line included).

    A list rather than one span so that a note may grow a second criteria section
    without either consumer quietly reading only the first. Empty means the note has
    none — a defect the caller must surface, never swallow.
    """
    spans: list[tuple[int, int]] = []
    for m in CRITERIA_HEADING.finditer(text):
        nxt = _ANY_HEADING.search(text, m.end())
        spans.append((m.start(), nxt.start() if nxt else len(text)))
    return spans


def criteria_text(text: str) -> str:
    """Every criteria section concatenated — for parsing the criteria out of a note."""
    return "".join(text[a:b] for a, b in criteria_spans(text))


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
