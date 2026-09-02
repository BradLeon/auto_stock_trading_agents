"""Read-only regional demand products backed by governed monthly observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .base import DataProducts


@dataclass(frozen=True)
class RegionalPoint:
    id: str
    label: str
    period: str
    value: float
    unit: str
    yoy: float | None
    mom: float | None
    known_at: str
    source_id: str
    dataset_id: str
    observation_id: str


@dataclass(frozen=True)
class RegionalSnapshot:
    points: tuple[RegionalPoint, ...]
    as_of: datetime

    def render(self) -> str:
        if not self.points:
            return "(无已发布的区域月度数据)"
        lines = []
        for point in self.points:
            def pct(value: float | None) -> str:
                return "n/a" if value is None else f"{value * 100:+.1f}%"
            lines.append(
                f"- {point.label}: {point.period} {point.value:,.0f} {point.unit} "
                f"(MoM {pct(point.mom)}, YoY {pct(point.yoy)}; source={point.source_id}; "
                f"known_at={point.known_at}; observation={point.observation_id})")
        return "\n".join(lines)


_SERIES = (
    ("tw_ic_exports", "台湾 IC 出口", "regional.tw_ic_exports.value", "TW_IC_EXPORT",
     "regional_tw_exports", "tw_mof_exports"),
    ("kr_semi_exports", "韩国半导体出口指数", "regional.kr_semiconductor_exports.index",
     "KR_SEMI_EXPORT", "regional_kr_exports", "kr_ecos_exports"),
)


class RegionalProducts:
    """Discover regional levels and shared YoY/MoM derivations from products only."""

    def __init__(self, products: DataProducts):
        self.products = products

    def snapshot(self, *, source_ids: frozenset[str] | None = None) -> RegionalSnapshot:
        points: list[RegionalPoint] = []
        for identifier, label, metric, entity, dataset, source in _SERIES:
            if source_ids is not None and source not in source_ids:
                continue
            result = self.products.metric_series(
                metric=metric, entity=entity, dataset=dataset, source_id=source, quality="loose")
            rows = sorted(result["rows"], key=lambda row: row["period"])
            if not rows:
                continue
            yoy = {row["period"]: row["value"] for row in self.products.derive(
                operation="yoy", query_result={"metric_id": metric, "rows": rows})["rows"]}
            mom = {row["period"]: row["value"] for row in self.products.derive(
                operation="mom", query_result={"metric_id": metric, "rows": rows})["rows"]}
            latest = rows[-1]
            points.append(RegionalPoint(
                id=identifier, label=label, period=latest["period"], value=float(latest["value"]),
                unit=str(latest["unit"]), yoy=yoy.get(latest["period"]),
                mom=mom.get(latest["period"]), known_at=str(latest["known_at"]),
                source_id=source, dataset_id=dataset,
                observation_id=str(latest["observation_id"])))
        return RegionalSnapshot(points=tuple(points), as_of=datetime.now(timezone.utc))


__all__ = ["RegionalPoint", "RegionalProducts", "RegionalSnapshot"]
