"""Independent-source counting for `kind: risk` claims (event-impact-routing D5, 2026-11-16).

An operating claim is corroborated by independent COMPANY speakers: three filers saying the same thing about
their own business. A layer-wide risk is not something the exposed companies report first — an export ban
reaches the ledger as a Commerce Department rule, a wire story and the re-tellings of both. So a risk claim is
counted by SOURCE instead:

  * one source per ORIGIN. "The Wall Street Journal（经IBD转述）" is the WSJ; five relays of one wire story are one
    source. Which strings name the same outlet is declared in config/workflow/event_impact.yaml (`origins`).
  * "pressured" / "realized" need at least `min_independent` sources, at least `min_authoritative` of them
    first-hand or authoritative (official text, a company's own statement or filing, a mainstream wire's
    original report).

Everything here is string normalisation over declared lists. What a report MEANS for the claim stays with the
adjudicator (chain clusters) and the layer analyst (event components).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

SETTINGS_PATH = Path(__file__).resolve().parents[3] / "config" / "workflow" / "event_impact.yaml"

# "（经IBD转述）", "(via Barron's)", "（经Barron's引述）": a relay names the messenger, not the origin.
_RELAY = re.compile(r"[（(][^（）()]*?(经|转述|引述|转载|via )[^（）()]*[）)]", re.IGNORECASE)
_PAREN = re.compile(r"[（(][^（）()]*[）)]")


@dataclass(frozen=True)
class Source:
    key: str
    authoritative: bool
    label: str = ""


@lru_cache(maxsize=4)
def _rules_cached(path: str, mtime: float) -> dict[str, Any]:
    import yaml

    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    raw = doc.get("risk_claims") or {}
    auth = raw.get("authoritative") or {}
    return {"min_independent": int(raw.get("min_independent", 2)),
            "min_authoritative": int(raw.get("min_authoritative", 1)),
            "origins": {str(k).upper(): [str(a).lower() for a in (v or [])]
                        for k, v in (raw.get("origins") or {}).items()},
            "authoritative_origins": {str(o).upper() for o in auth.get("origins") or []},
            "markers": [str(m).lower() for m in auth.get("markers") or []]}


def rules(path: Path | None = None) -> dict[str, Any]:
    target = path or SETTINGS_PATH
    try:
        return _rules_cached(str(target), target.stat().st_mtime)
    except FileNotFoundError:
        return {"min_independent": 2, "min_authoritative": 1, "origins": {}, "authoritative_origins": set(),
                "markers": []}


def _hit(text: str, needle: str) -> bool:
    if re.fullmatch(r"[a-z0-9.'\- ]+", needle):          # ASCII: whole words, so "sec" never matches "sector"
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", text) is not None
    return needle in text


def origin_of(actor: str, *, cfg: dict[str, Any] | None = None) -> Source | None:
    """The origin a `source_actor` string names, and whether it is first-hand or authoritative."""
    cfg = cfg or rules()
    raw = (actor or "").strip()
    if not raw:
        return None
    whole = raw.lower()
    origin_text = _RELAY.sub("", raw).strip().lower() or whole
    key = next((canon for canon, aliases in cfg["origins"].items()
                if any(_hit(origin_text, a) for a in aliases)), "")
    if not key:
        bare = _PAREN.sub("", origin_text).replace("作者", "")
        key = re.sub(r"\s+", " ", re.split(r"[/、,，]", bare)[0]).strip() or origin_text
    authoritative = key in cfg["authoritative_origins"] or any(_hit(whole, m) for m in cfg["markers"])
    return Source(key=key, authoritative=authoritative, label=raw[:80])


def component_sources(brief_payload: dict, component: dict, *, cfg: dict[str, Any] | None = None) -> list[Source]:
    """Origins behind one event component, read from the brief's own `source_evidence` rows."""
    indices = set(component.get("fact_indices") or [])
    out = []
    for row in brief_payload.get("source_evidence") or []:
        if row.get("fact_index") in indices:
            src = origin_of(str(row.get("source_actor") or ""), cfg=cfg)
            if src:
                out.append(src)
    return out


def cluster_sources(clusters: Iterable, *, cfg: dict[str, Any] | None = None) -> list[Source]:
    """Origins behind evidence-engine clusters.

    A company's own filing or a declared statistical/regulatory series is first-hand when the observation is
    PRIMARY. An article publisher (DOWJONES, TRENDFORCE, ...) is one source per publisher and is never counted
    as authoritative here: its feed mixes wire copy with re-tellings, and the origin of each sentence is only
    known on the brief side (`source_actor`).
    """
    from .articles import load_article_sources

    cfg = cfg or rules()
    publishers = {s.entity.upper() for s in load_article_sources()}
    out = []
    for c in clusters:
        speaker = (getattr(c, "speaker", "") or "").upper()
        if not speaker:
            continue
        if speaker in publishers:
            out.append(Source(key=speaker, authoritative=False, label=speaker))
        else:
            out.append(Source(key=speaker, authoritative=bool(getattr(c, "primary", False)), label=speaker))
    return out


def gate(sources: Iterable[Source], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Count distinct origins; an origin is authoritative if any of its mentions is."""
    cfg = cfg or rules()
    by_key: dict[str, bool] = {}
    for s in sources:
        by_key[s.key] = by_key.get(s.key, False) or s.authoritative
    independent = sorted(by_key)
    authoritative = sorted(k for k, v in by_key.items() if v)
    return {"independent": independent, "authoritative": authoritative,
            "ok": len(independent) >= cfg["min_independent"] and len(authoritative) >= cfg["min_authoritative"]}
