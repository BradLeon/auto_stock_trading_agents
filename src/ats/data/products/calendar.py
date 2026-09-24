"""Read-only calendar data product with as-of versions and quality lineage."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..stores.schedule_calendar import ScheduleCalendarStore
from ...config import REPO_ROOT


class ScheduleCalendarProduct:
    def __init__(self, store: ScheduleCalendarStore | None = None,
                 *, config_dir: str | Path | None = None):
        self.store = store or ScheduleCalendarStore()
        self.config_dir = Path(config_dir or REPO_ROOT / "config")

    def snapshot(self, *, as_of: datetime | None = None, start: date | None = None,
                 end: date | None = None, event_types: tuple[str, ...] = (),
                 include_cancelled: bool = False, limit: int = 500) -> dict[str, Any]:
        point = as_of or datetime.now(timezone.utc)
        events = self.store.events(as_of=point, start=start, end=end,
                                   event_types=event_types,
                                   include_cancelled=include_cancelled, limit=limit)
        source_runs = self.store.source_runs(limit=100)
        conflicts = self.store.candidates(review_status="conflict", limit=10_000)
        pending = self.store.candidates(review_status="pending_identity", limit=10_000)
        completed = [run for run in source_runs if run["status"] in {"complete", "partial"}]
        latest = max(completed, key=lambda item: item["finished_at"], default=None)
        now = point.astimezone(timezone.utc)
        threshold = self._freshness_slo_seconds()
        age = None
        if latest and latest.get("finished_at"):
            age = max(0.0, (now - datetime.fromisoformat(latest["finished_at"])).total_seconds())
        quality = "ok"
        if conflicts or any(event["quality_status"] == "conflict" for event in events):
            quality = "conflict"
        elif latest is None or age is None or age > threshold:
            quality = "stale"
        elif latest["status"] == "partial" or any(run["status"] == "failed" for run in source_runs):
            quality = "degraded"
        return {
            "product": "schedule_calendar",
            "as_of": now.isoformat(),
            "events": events,
            "quality": {
                "status": quality,
                "conflicting_candidates": len(conflicts),
                "identity_quarantine": len(pending),
                "freshness_slo_seconds": threshold,
                "latest_successful_refresh": latest,
                "refresh_age_seconds": age,
            },
            "lineage": {
                "source_runs": source_runs,
                "event_sources": {
                    event["event_id"]: event["sources"] for event in events
                },
            },
        }

    def _freshness_slo_seconds(self) -> int:
        path = self.config_dir / "data" / "schedule_calendar.yaml"
        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return int(config.get("freshness_slo_hours", 48)) * 3600
        except (OSError, ValueError, TypeError):
            return 48 * 3600


def schedule_calendar_snapshot(**kwargs) -> dict[str, Any]:
    return ScheduleCalendarProduct().snapshot(**kwargs)


__all__ = ["ScheduleCalendarProduct", "schedule_calendar_snapshot"]
