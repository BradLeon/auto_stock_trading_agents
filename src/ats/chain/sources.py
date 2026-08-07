"""Third-party evidence sources — statistical series turned into observations.

The one kind of witness that is not party to anything. Every observation the ledger
held before this came from a company's own filing, so the stance-diversity gate could
only ever be satisfied between customers and suppliers — both of them interested
parties. A customs bureau has no position to talk up.

**No LLM runs here.** A numeric series has no prose to read: `direction` comes from a
formula and `evidence_span` is rendered deterministically into a line a person can go
and check:

    2026-06 韩国半导体出口 148.7 亿美元，同比 +32.1%（KR_CUSTOMS 8542，发布 2026-07-15）

What a series MEANS still goes through the adjudicator, exactly like company evidence.
"Exports up 32%" could be demand strengthening or supply loosening — the same ambiguity
that made `supports_when` the wrong shape for capex. The engine computes the fact; the
model judges the fact; neither does the other's job.

Adapters live in data/sources/<adapter>.py. That layer is deliberately NOT abstracted:
every agency's API differs, and pretending otherwise would produce a config language
that is just Python with worse errors. What IS uniform is the declaration and the
output — see config/sources.yaml.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..schemas.chain import Observation, SeriesPoint, SourceDef

log = logging.getLogger("ats.chain.sources")

# Below this a change is noise, not a reading. Monthly trade data is volatile enough
# that a ±1% print says nothing about supply tightness, and emitting `up` for it would
# hand the adjudicator a fact that is really a rounding artefact.
FLAT_BAND = 0.02


def load_sources() -> list[SourceDef]:
    """Read config/sources.yaml. Missing file is fine — no third-party sources."""
    from ..config import _config_dir, _load_yaml

    raw = _load_yaml(_config_dir() / "sources.yaml").get("sources", {}) or {}
    out = []
    for sid, body in raw.items():
        try:
            out.append(SourceDef(id=sid, **(body or {})))
        except Exception as exc:  # noqa: BLE001 - one bad entry must not hide the rest
            log.warning("sources: skipping %r — %s", sid, exc)
    return out


def sources_for_concepts(concepts: set[str]) -> list[SourceDef]:
    """Sources entitled to speak to any of these dimensions."""
    return [s for s in load_sources() if set(s.concepts) & concepts]


def _direction(change: float | None) -> str:
    if change is None:
        return "flat"
    if change > FLAT_BAND:
        return "up"
    if change < -FLAT_BAND:
        return "down"
    return "flat"


def _span(source: SourceDef, point: SeriesPoint, basis: str, change: float | None) -> str:
    """The re-checkable rendering. Carries the agency and the release date on purpose:
    an observation nobody can go and verify is not evidence, and that rule does not
    relax just because the number came from a spreadsheet instead of a transcript."""
    unit = f" {point.unit}" if point.unit else ""
    move = f"{change * 100:+.1f}%" if change is not None else "n/a"
    label = {"yoy": "同比", "mom": "环比", "level_vs_ma": "较均值"}.get(basis, basis)
    published = f"，发布 {point.published_at}" if point.published_at else ""
    return (f"{point.period} {source.label or source.id} {point.value:,.1f}{unit}，"
            f"{label} {move}（{source.adapter} {point.period}{published}）")


def to_observations(source: SourceDef, points: list[SeriesPoint], *,
                    now: datetime | None = None) -> list[Observation]:
    """One observation per (point, direction basis). Never raises.

    Trend and turning point are emitted separately because they are separate facts —
    a series can be up year-on-year while turning down month-on-month, and that
    divergence is precisely the leading signal a blended number would erase.
    """
    now = now or datetime.now(timezone.utc)
    out: list[Observation] = []
    for point in points:
        for basis in source.direction_from:
            change = getattr(point, basis, None)
            if change is None:
                continue
            metric = f"{source.id}_{basis}"
            try:
                out.append(Observation(
                    document_id=f"{source.id}:{point.period}",
                    source_url=source.adapter,
                    entity=source.entity,
                    # The source speaks for itself: it is both the subject and the
                    # discloser, which is what makes its stance `regulator` rather than
                    # borrowed from whoever it happens to be reporting on.
                    source_entity=source.entity,
                    metric=metric,
                    concept="",           # assigned below, per declared concept
                    period=point.period,
                    observation_type=source.observation_type,
                    stance=source.stance,
                    direction=_direction(change),
                    value=point.value, unit=point.unit,
                    evidence_span=_span(source, point, basis, change),
                    observed_at=now))
            except Exception as exc:  # noqa: BLE001
                log.warning("sources: %s %s rejected by schema: %s", source.id,
                            point.period, exc)
    return out


def fetch(source: SourceDef, *, lookback_months: int = 6) -> list[SeriesPoint]:
    """Run the source's adapter. Returns [] when it is unavailable — never raises.

    A source we cannot reach is a gap, and the caller records it as one. It must not
    become "the series says nothing", which is a statement about the world.
    """
    import importlib

    try:
        mod = importlib.import_module(f"..data.sources.{source.adapter}", __package__)
    except ImportError as exc:
        log.warning("sources: no adapter %r for %s (%s)", source.adapter, source.id, exc)
        return []
    try:
        return list(mod.fetch(lookback_months=lookback_months, **source.params))
    except Exception as exc:  # noqa: BLE001 - an agency outage must not break a window
        log.warning("sources: %s fetch failed — %s", source.id, exc)
        return []


def collect(store, *, lookback_months: int = 6, concepts: set[str] | None = None,
            now: datetime | None = None) -> dict[str, int]:
    """Fetch every declared source and persist its observations. Returns {id: saved}.

    One observation is emitted per declared concept: the same customs print is evidence
    on `supply_tightness` and on `hbm_demand`, and filing it once under a blank concept
    would leave it in the unmapped pool where no claim can reach it.
    """
    now = now or datetime.now(timezone.utc)
    out: dict[str, int] = {}
    for source in load_sources():
        if concepts and not (set(source.concepts) & concepts):
            continue
        points = fetch(source, lookback_months=lookback_months)
        if not points:
            out[source.id] = 0
            try:
                store.save_document_failure(source.entity, "", "series",
                                            source=source.adapter,
                                            note=f"{source.id} 本轮取不到数据")
            except Exception:  # noqa: BLE001
                pass
            continue
        saved = 0
        for obs in to_observations(source, points, now=now):
            for concept in source.concepts:
                row = obs.model_copy(update={"concept": concept})
                # Re-derive the id: it is deterministic over (doc, entity, metric,
                # period), and two concepts off one print must not collide.
                row = row.model_copy(update={"id": Observation.deterministic_id(
                    row.document_id, row.entity, f"{row.metric}:{concept}", row.period)})
                if store.save_observation(row):
                    saved += 1
        out[source.id] = saved
        log.info("sources: %s -> %d new observations", source.id, saved)
    return out


def source_entities_for(claim) -> set[str]:
    """Entities of the third-party sources entitled to speak to this claim.

    Callers that assemble `rows_by_entity` themselves need this: a source is never
    named in a claim, so iterating the claim's declared witnesses alone would silently
    drop every non-company witness.
    """
    return {s.entity for s in sources_for_concepts({c.key for c in claim.concepts})}
