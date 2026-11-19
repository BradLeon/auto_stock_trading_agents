"""Event components of an information brief, rated once per brief (event-impact-routing, design D1 plan A).

Why a separate step and not a field of the per-article extraction: the extraction cache key contains the full text
of `extract.py` and the `research-insight` prompt, so folding components into it would retire every cached
extraction of the last seven days the moment the dispatcher restarted, even with the feature switched off. Rating
after the brief's facts are assembled touches neither, sees all of the company's facts at once (better clustering),
and costs one flash request per brief once switched on.

Division of labour (config declares scope, the model judges and leaves a trace, code checks):
  * the model splits facts into components and rates scope, materiality and direction, direct/contagion, ongoing/concluded and
    whether a known cluster escalated;
  * code decides first-seen vs follow-up from the 7-day cluster ledger, takes citations from the brief's own
    `source_evidence` (the model never writes them), refuses layers/tickers the chain config does not know, and
    downgrades a major component without a traceable citation to moderate.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ...config import REPO_ROOT

log = logging.getLogger("ats.agents.information.event_components")

ROLE = "event_component_rating"
SKILL = "information-event-components"
SETTINGS_PATH = REPO_ROOT / "config" / "workflow" / "event_impact.yaml"
MATERIALITY_ORDER = {"minor": 0, "moderate": 1, "major": 2, "critical": 3}


class RatedComponentView(BaseModel):
    fact_indices: list[int] = Field(min_length=1, description="Numbers of the facts this component rests on.")
    cluster_key: str = Field(min_length=1, description="Short lowercase slug of the underlying event; reuse a listed recent key for the same event.")
    content: str = Field(min_length=1, description="Chinese one-sentence description of this component. No advice.")
    scope: Literal["entity", "layer", "cross_layer"]
    entities: list[str] = Field(default_factory=list, description="Affected tickers, from the supplied list only.")
    layers: list[str] = Field(default_factory=list, description="Affected layer keys, from the supplied list only.")
    materiality: Literal["minor", "moderate", "major", "critical"] = Field(description="How much it can change expectations or the thesis, either direction.")
    direction: Literal["positive", "negative", "mixed", "unclear"] = "unclear"
    role: Literal["direct", "contagion"] = "direct"
    status: Literal["ongoing", "concluded"] = "ongoing"
    escalation: bool = Field(default=False, description="True only if a listed recent cluster got materially worse or wider.")
    rationale: str = Field(default="", description="Chinese: the nature of harm and the scale judged.")


class ComponentBatchView(BaseModel):
    components: list[RatedComponentView] = Field(default_factory=list)


# --------------------------------------------------------------------------- settings / scope


def settings(path: Path | None = None) -> dict[str, Any]:
    import yaml

    try:
        doc = yaml.safe_load((path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        doc = {}
    components = doc.get("components") or {}
    return {"enabled": bool(components.get("enabled", False)),
            "novelty_window_days": int(components.get("novelty_window_days", 7)),
            "max_facts": int(components.get("max_facts", 120))}


def chain_scope(sector: str = "ai_hardware") -> dict[str, dict[str, set[str]]]:
    """Layer key -> its members, and every ticker that speaks to one of its claims."""
    from ...config import load_sector_config

    cfg = load_sector_config(sector)
    out: dict[str, dict[str, set[str]]] = {}
    for layer in cfg.layers:
        members = {t.symbol.upper() for t in layer.tickers}
        speakers: set[str] = set()
        for claim in layer.claims:
            speakers |= {w.entity.upper() for w in claim.witnesses} | claim.expected_witnesses()
            speakers |= {e.upper() for e in claim.entities}
        out[layer.key] = {"members": members, "speakers": speakers}
    return out


def normalize_key(value: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", (value or "").lower())).strip("-")[:60]


def recent_clusters(store, *, now: datetime, window_days: int) -> dict[str, dict[str, Any]]:
    """Clusters seen in published briefs (any company) within the window: the ledger novelty is judged against."""
    cutoff = now - timedelta(days=window_days)
    seen: dict[str, dict[str, Any]] = {}
    for row in store.task_projection_envelopes(agent_role="information_brief", limit=3000):
        try:
            as_of = datetime.fromisoformat(str(row.get("as_of")))
        except ValueError:
            continue
        if as_of.tzinfo is None or as_of < cutoff or as_of > now:
            continue
        for comp in (row.get("payload") or {}).get("event_components") or []:
            key = comp.get("cluster_key")
            if not key:
                continue
            entry = seen.setdefault(key, {"max_materiality": "minor", "contents": [], "last_seen": as_of})
            if MATERIALITY_ORDER.get(comp.get("materiality"), 0) > MATERIALITY_ORDER[entry["max_materiality"]]:
                entry["max_materiality"] = comp.get("materiality")
            entry["last_seen"] = max(entry["last_seen"], as_of)
            if len(entry["contents"]) < 2:
                entry["contents"].append(str(comp.get("content") or "")[:120])
    return seen


# --------------------------------------------------------------------------- the rating call


def rating_context(symbol: str, facts: list[str], layers: dict[str, dict[str, set[str]]],
                   recent: dict[str, dict[str, Any]]) -> str:
    fact_lines = "\n".join(f"[{i}] {fact}" for i, fact in enumerate(facts))
    layer_lines = "\n".join(f"- {key}: members {', '.join(sorted(v['members']))}" for key, v in layers.items())
    recent_lines = "\n".join(
        f"- {key} (max materiality so far: {v['max_materiality']}): {' / '.join(v['contents'])}"
        for key, v in sorted(recent.items(), key=lambda kv: kv[1]["last_seen"], reverse=True)[:30]) or "(none)"
    return (f"Company: {symbol}\n\nFacts of today's brief:\n{fact_lines}\n\n"
            f"Chain layers (use these keys only):\n{layer_lines}\n\n"
            f"Event clusters seen in recent days (reuse a key for the same event):\n{recent_lines}\n\n"
            "Split the facts into event components and rate each one. An empty list is valid.")


def prompt_version() -> str:
    from ..base import execution_versions

    return execution_versions(ROLE, SKILL, template_source=__file__)["prompt_version"]


def _citations(payload: dict, indices: list[int]) -> list[str]:
    out = []
    for row in payload.get("source_evidence") or []:
        if row.get("fact_index") not in indices:
            continue
        loc = row.get("locator") or {}
        ref = row.get("document_ref")
        if ref and isinstance(loc.get("start"), int) and isinstance(loc.get("end"), int) and loc["start"] < loc["end"]:
            out.append(f"{ref}#{loc['start']}-{loc['end']}")
    return sorted(set(out))


def validate_components(symbol: str, views: list[RatedComponentView], payload: dict, *,
                        layers: dict[str, dict[str, set[str]]], recent: dict[str, dict[str, Any]]
                        ) -> tuple[list[dict], list[str]]:
    """The publication contract for components (tasks 2.2, 2.3). Returns (components, notes for `unverified`)."""
    from ...agent.task_projection import EventComponent
    from .briefs import assert_no_advice

    facts = payload.get("fact_changes") or []
    known = set().union(*(v["members"] | v["speakers"] for v in layers.values())) if layers else set()
    out, notes = [], []
    for view in views:
        key = normalize_key(view.cluster_key)
        label = f"成分「{view.content[:30]}」"
        indices = sorted({i for i in view.fact_indices if 0 <= i < len(facts)})
        if not key or not indices:
            notes.append(f"{label}未发布：缺事件簇或引用的事实编号无效")
            continue
        bad_layers = [lay for lay in view.layers if lay not in layers]
        entities = sorted({e.upper() for e in view.entities} | ({symbol} if view.scope == "entity" else set()))
        bad_entities = [e for e in entities if e != symbol and e not in known]
        if bad_layers or bad_entities:
            notes.append(f"{label}未发布：未登记的层或标的 {', '.join(bad_layers + bad_entities)}")
            continue
        if view.scope in {"layer", "cross_layer"} and not view.layers:
            notes.append(f"{label}未发布：影响面为层面但未给出涉及的层")
            continue
        if view.scope == "cross_layer" and len(set(view.layers)) < 2:
            notes.append(f"{label}未发布：跨层成分至少涉及两层")
            continue
        try:
            assert_no_advice([view.content, view.rationale])
        except Exception:  # noqa: BLE001 - EnvelopeValidationError: the component carries advice
            notes.append(f"{label}未发布：含交易建议措辞")
            continue
        citations = _citations(payload, indices)
        materiality, downgraded_from, downgrade_reason = view.materiality, None, ""
        if MATERIALITY_ORDER[materiality] >= MATERIALITY_ORDER["major"] and not citations:
            materiality, downgraded_from = "moderate", view.materiality
            downgrade_reason = "重大及以上须引用可回溯到已入库原文的片段；所引事实没有原文定位"
        if key in recent:
            novelty = "escalation" if view.escalation else "follow_up"
        else:
            novelty = "first"
        # The cited facts are part of the identity: two components of one cluster with the same scope (the board
        # probe and the market's doubt about the CEO's future) are different components (benchmark run 2026-11-14).
        digest = hashlib.sha256(f"{key}|{view.scope}|{','.join(sorted(view.layers))}|{','.join(entities)}|"
                                f"{','.join(map(str, indices))}".encode()).hexdigest()[:8]
        component = EventComponent(
            component_id=f"{symbol}-{digest}", cluster_key=key, content=view.content, scope=view.scope,
            entities=entities, layers=sorted(set(view.layers)), fact_indices=indices, materiality=materiality, direction=view.direction,
            role=view.role,
            status=view.status, novelty=novelty, citations=citations, rationale=view.rationale,
            downgraded_from=downgraded_from, downgrade_reason=downgrade_reason)
        out.append(component.model_dump(mode="json"))
    return out, notes


def attach(store, payload: dict, *, symbol: str, now: datetime | None = None,
           rate=None) -> tuple[dict, list[str]]:
    """Add rated components to a brief payload before publication. Returns (payload, extra input refs).

    Switched off (the default) this returns the payload untouched and makes no request. A failed rating never fails
    the brief: it is published without components and says why in `unverified`.
    """
    conf = settings()
    if not conf["enabled"] or not payload.get("source_evidence") or not payload.get("fact_changes"):
        return payload, []
    from ...workflow.evaluation_clock import now as evaluation_now

    now = now or evaluation_now(timezone.utc)
    payload = dict(payload)
    facts = list(payload["fact_changes"])
    truncated = len(facts) > conf["max_facts"]
    try:
        layers = chain_scope()
        recent = recent_clusters(store, now=now, window_days=conf["novelty_window_days"])
        context = rating_context(symbol, facts[:conf["max_facts"]], layers, recent)
        if rate is None:
            from ..base import run_structured

            rate = lambda ctx: run_structured(ROLE, ComponentBatchView, ctx, skill_slug=SKILL)  # noqa: E731
        view = rate(context)
        components, notes = validate_components(symbol, list(view.components), payload, layers=layers,
                                                recent=recent)
    except Exception as exc:  # noqa: BLE001 - a rating failure is disclosed, never fatal to the brief
        log.warning("event components for %s not rated: %s", symbol, exc)
        payload["unverified"] = [*payload.get("unverified", []),
                                 f"事件成分评级未完成（{type(exc).__name__}），本简报不含事件成分"]
        return payload, []
    if components:
        payload["event_components"] = components
    extra = list(notes)
    if truncated:
        extra.append(f"事件成分只评级了前 {conf['max_facts']} 条事实（共 {len(facts)} 条）")
    if extra:
        payload["unverified"] = [*payload.get("unverified", []), *extra]
    return payload, [f"event-components:{prompt_version()[:16]}"]
