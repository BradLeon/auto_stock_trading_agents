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
output — see config/data/sources.yaml.
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
    """Read config/data/sources.yaml. Missing file is fine — no third-party sources."""
    from ..config import _config_dir, _load_yaml

    raw = _load_yaml(_config_dir() / "data" / "sources.yaml").get("sources", {}) or {}
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
    what = f" {point.series}" if point.series else ""
    move = f"{change * 100:+.1f}%" if change is not None else "n/a"
    label = {"yoy": "同比", "mom": "环比", "level_vs_ma": "较均值"}.get(basis, basis)
    published = f"，发布 {point.published_at}" if point.published_at else ""
    return (f"{point.period} {source.label or source.id}{what} {point.value:,.2f}{unit}，"
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
            suffix = f"_{point.series}" if point.series else ""
            metric = f"{source.id}{suffix}_{basis}"
            try:
                out.append(Observation(
                    document_id=f"{source.id}:{point.period}{suffix}",
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
        if source.adapter not in {"tw_mof", "kr_ecos"}:
            return list(mod.fetch(lookback_months=lookback_months, **source.params))
        from ..data.structured import read_mode

        mode = read_mode("chain_regional", source_id=source.id)
        if mode == "legacy":
            return list(mod.fetch(lookback_months=lookback_months, **source.params))
        platform = _platform_fetch(source, lookback_months=lookback_months)
        if mode == "platform":
            return platform
        if mode == "fallback":
            return platform or list(mod.fetch(
                lookback_months=lookback_months, **source.params))
        legacy = list(mod.fetch(lookback_months=lookback_months, **source.params))
        if _point_signature(platform) != _point_signature(legacy):
            log.warning("sources: structured shadow mismatch for %s", source.id)
        return legacy
    except Exception as exc:  # noqa: BLE001 - an agency outage must not break a window
        log.warning("sources: %s fetch failed — %s", source.id, exc)
        return []


def _point_signature(points: list[SeriesPoint]) -> list[tuple]:
    return [(point.period, point.value, point.unit, point.yoy, point.mom)
            for point in points]


def _legacy_fetch(source: SourceDef, *, lookback_months: int) -> list[SeriesPoint]:
    """Read the pre-platform adapter contract for an explicit acceptance comparison."""
    import importlib

    mod = importlib.import_module(f"..data.sources.{source.adapter}", __package__)
    return list(mod.fetch(lookback_months=lookback_months, **source.params))


def _platform_fetch(source: SourceDef, *, lookback_months: int) -> list[SeriesPoint]:
    """Ingest accepted regional levels, then assemble the legacy Chain contract."""
    from ..data.sources import kr_ecos, tw_mof
    from ..data.products import DataProducts
    from ..data.runtime import get_platform_structured_repository
    from ..data.structured import FetchRequest, IngestionPipeline

    if source.adapter == "tw_mof":
        adapter = tw_mof.TaiwanMOFAdapter()
        source_id, dataset_id = "tw_mof_exports", "regional_tw_exports"
        entity_id, metric_id = "TW_IC_EXPORT", tw_mof.METRIC_ID
        scope = {"lookback_months": lookback_months + 12,
                 "item": source.params.get("item", "electronic_components")}
    else:
        adapter = kr_ecos.KoreaECOSAdapter()
        source_id, dataset_id = "kr_ecos_exports", "regional_kr_exports"
        entity_id, metric_id = "KR_SEMI_EXPORT", kr_ecos.METRIC_ID
        scope = {"lookback_months": lookback_months + 12,
                 "stat": source.params.get("stat", "403Y001"),
                 "item": source.params.get("item", "3091AA")}
    repository = get_platform_structured_repository()
    try:
        request = FetchRequest(
            source_id=source_id, dataset_id=dataset_id, entities=[entity_id],
            query_scope=scope)
        run = IngestionPipeline(repository).run(adapter, request)
        # ``collect`` is the scheduled refresh path, but a provider outage during
        # this invocation must not erase a still-current, accepted monthly series
        # from the Chain consumer.  Read the latest governed vintage below even
        # when this refresh failed; return [] only if the repository itself has no
        # usable level.  The ingestion run keeps the refresh gap explicit.
        if run["status"] not in {"succeeded", "no_change"}:
            log.warning("sources: %s refresh %s; reading accepted governed vintage if present",
                        source.id, run["status"])
        products = DataProducts(structured_repository=repository)
        levels = products.metric_series(
            metric=metric_id, entity=entity_id, dataset=dataset_id,
            source_id=source_id, quality="loose")
        if not levels["rows"]:
            return []
        yoy = {row["period"]: row for row in products.derive(
            operation="yoy", query_result=levels)["rows"]}
        mom = {row["period"]: row for row in products.derive(
            operation="mom", query_result=levels)["rows"]}
        if levels["rows"]:
            products.snapshot_manifest(
                consumer="chain_regional", purpose=f"regional:{source.id}",
                as_of=datetime.now(timezone.utc), rows=levels["rows"],
                metadata={
                    "source_definition": source.id,
                    "derivations": ["yoy:v1", "mom:v1"],
                    "runtime_inputs_included": False,
                })
        output = []
        for row in levels["rows"][-lookback_months:]:
            published = (datetime.fromisoformat(row["published_at"]).date()
                         if row.get("published_at") else None)
            output.append(SeriesPoint(
                period=row["period"], value=row["value"], unit=row["unit"],
                yoy=yoy[row["period"]]["value"], mom=mom[row["period"]]["value"],
                published_at=published))
        return output
    finally:
        repository.close()


def collect(store, *, lookback_months: int = 6, concepts: set[str] | None = None,
            now: datetime | None = None) -> dict[str, int]:
    """Fetch every declared source and persist its observations. Returns {id: saved}.

    One observation is emitted per declared concept: the same customs print is evidence
    on `supply_tightness` and on `hbm_demand`, and filing it once under a blank concept
    would leave it in the unmapped pool where no claim can reach it.

    The return value has two zero-like states that must not be confused, so they are
    NOT the same number:
      * ``0``  — the source was reached and every point it returned was already in the
        ledger. The data is current; nothing changed since last time. This is the
        normal steady state for a monthly source fetched more than once a month.
      * ``-1`` — the source could not be reached at all this round (network failure,
        page structure changed, rate-limited). A gap, recorded via
        `save_document_failure`. Confusing this with ``0`` is exactly the failure this
        sentinel exists to prevent: TrendForce's contract-price page returned the same
        "2H Jun" print twice in a row and both callers logged "0 new observations", but
        one CLI surface then printed "取不到数据" over data that had, in fact, just
        been fetched successfully — a live-and-current source reporting as dead.
    """
    now = now or datetime.now(timezone.utc)
    out: dict[str, int] = {}
    for source in load_sources():
        if concepts and not (set(source.concepts) & concepts):
            continue
        store.register_data_source(source, kind="structured", at=now)
        run_id = store.begin_ingestion(source.id, kind="structured", at=now)
        points = fetch(source, lookback_months=lookback_months)
        if not points:
            out[source.id] = -1
            store.finish_ingestion(run_id, status="unreachable", note="本轮取不到数据",
                                   at=now)
            try:
                store.save_document_failure(source.entity, "", "series",
                                            source=source.adapter,
                                            note=f"{source.id} 本轮取不到数据")
            except Exception:  # noqa: BLE001
                pass
            continue
        raw_saved = store.save_measurement_points(source, points, fetched_at=now)
        saved = 0
        for obs in to_observations(source, points, now=now):
            for concept in source.concepts:
                row = obs.model_copy(update={"concept": concept})
                # Re-derive the id: it is deterministic over (doc, entity, metric,
                # period), and two concepts off one print must not collide.
                row = row.model_copy(update={"id": Observation.deterministic_id(
                    row.document_id, row.entity, f"{row.metric}:{concept}", row.period)})
                if store.save_observation(
                        row, projection_profile="structured_evidence",
                        projection_version="v1"):
                    saved += 1
        out[source.id] = saved
        store.finish_ingestion(run_id, status="succeeded", discovered=len(points),
                               accepted=raw_saved, at=now)
        log.info("sources: %s -> %d raw point vintages, %d new observations",
                 source.id, raw_saved, saved)
    return out


def source_entities_for(claim) -> set[str]:
    """Entities of the third-party sources entitled to speak to this claim.

    Callers that assemble `rows_by_entity` themselves need this: a source is never
    named in a claim, so iterating the claim's declared witnesses alone would silently
    drop every non-company witness.
    """
    return {s.entity for s in sources_for_concepts({c.key for c in claim.concepts})}
