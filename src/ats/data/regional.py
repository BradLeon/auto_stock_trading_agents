"""Compatibility read router for governed Taiwan/Korea regional demand inputs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .products import RegionalPoint, RegionalProducts, RegionalSnapshot


log = logging.getLogger("ats.data.regional")


_SOURCE_IDS = ("tw_mof_exports", "kr_ecos_exports")


def _legacy_snapshot(*, source_ids: frozenset[str] | None = None) -> RegionalSnapshot:
    from .sources import kr_ecos, tw_mof

    definitions = (
        ("tw_ic_exports", "台湾 IC 出口", tw_mof.fetch),
        ("kr_semi_exports", "韩国半导体出口指数", kr_ecos.fetch),
    )
    points: list[RegionalPoint] = []
    for identifier, label, loader in definitions:
        if identifier == "tw_ic_exports":
            source, dataset, entity = "tw_mof_exports", "regional_tw_exports", "TW_IC_EXPORT"
        else:
            source, dataset, entity = "kr_ecos_exports", "regional_kr_exports", "KR_SEMI_EXPORT"
        if source_ids is not None and source not in source_ids:
            continue
        rows = loader(lookback_months=1)
        if not rows:
            continue
        row = rows[-1]
        points.append(RegionalPoint(
            id=identifier, label=label, period=row.period, value=float(row.value), unit=row.unit,
            yoy=row.yoy, mom=row.mom,
            known_at=(row.published_at.isoformat() if row.published_at else ""),
            source_id=source, dataset_id=dataset, observation_id=f"legacy:{entity}:{row.period}"))
    return RegionalSnapshot(points=tuple(points), as_of=datetime.now(timezone.utc))


def _platform_snapshot(*, source_ids: frozenset[str] | None = None) -> RegionalSnapshot:
    from .products import DataProducts
    from .runtime import get_platform_structured_repository

    repository = get_platform_structured_repository()
    try:
        return RegionalProducts(DataProducts(structured_repository=repository)).snapshot(
            source_ids=source_ids)
    finally:
        repository.close()


def _point_signature(snapshot: RegionalSnapshot) -> dict:
    return {
        point.id: (point.period, point.value, point.unit, point.yoy, point.mom)
        for point in snapshot.points
    }


def _record(*, consumer: str, source_id: str, legacy: RegionalSnapshot,
            platform: RegionalSnapshot, matched: bool, reason: str) -> None:
    try:
        from .cutover import record_consumer_comparison
        from .runtime import platform_data_db_path
        record_consumer_comparison(
            consumer=consumer, entity=f"REGIONAL:{source_id}", data_db=platform_data_db_path(),
            status="reconciled" if matched else "mismatch",
            details={"input": "regional_monthly", "reason": reason,
                     "legacy": _point_signature(legacy),
                     "platform": _point_signature(platform), "source_id": source_id},
        )
    except Exception as exc:  # audit cannot make a research workflow unavailable
        log.warning("regional: failed to record %s comparison: %s", consumer, exc)


def _select_source(*, consumer: str, source_id: str) -> RegionalSnapshot:
    """Resolve one regional source so source-scoped overrides are never ignored."""
    from ..structured import read_mode

    mode = read_mode(consumer, source_id=source_id)
    source_ids = frozenset((source_id,))
    if mode == "legacy":
        return _legacy_snapshot(source_ids=source_ids)
    try:
        platform = _platform_snapshot(source_ids=source_ids)
    except Exception as exc:
        log.warning("regional: platform unavailable for %s/%s: %s", consumer, source_id, exc)
        if mode in {"shadow", "fallback"}:
            return _legacy_snapshot(source_ids=source_ids)
        return RegionalSnapshot(points=(), as_of=datetime.now(timezone.utc))
    if mode == "platform":
        return platform
    try:
        legacy = _legacy_snapshot(source_ids=source_ids)
    except Exception as exc:
        log.warning("regional: legacy unavailable for %s/%s: %s", consumer, source_id, exc)
        _record(consumer=consumer, source_id=source_id,
                legacy=RegionalSnapshot((), datetime.now(timezone.utc)),
                platform=platform, matched=False, reason=type(exc).__name__)
        return platform if mode == "fallback" else RegionalSnapshot((), datetime.now(timezone.utc))
    if mode == "fallback":
        return platform if platform.points else legacy
    matched = _point_signature(legacy) == _point_signature(platform)
    _record(consumer=consumer, source_id=source_id, legacy=legacy, platform=platform, matched=matched,
            reason="identical_levels_and_derivations" if matched else "regional_signature_mismatch")
    if not matched:
        log.warning("regional: structured shadow mismatch for %s/%s", consumer, source_id)
    return legacy


def fetch(*, consumer: str) -> RegionalSnapshot:
    """Return a reversible regional snapshot for a Sector/Macro consumer.

    In shadow mode the legacy provider result remains visible, while the governed
    product is independently read and recorded.  The product never fetches a
    Provider: source ingestion is an explicit operational action.
    """
    snapshots = [_select_source(consumer=consumer, source_id=source_id)
                 for source_id in _SOURCE_IDS]
    points = tuple(point for snapshot in snapshots for point in snapshot.points)
    return RegionalSnapshot(points=points, as_of=datetime.now(timezone.utc))


__all__ = ["fetch"]
