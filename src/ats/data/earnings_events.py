"""Deterministic earnings-event resolution for period-bound document ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from . import fiscal


@dataclass(frozen=True)
class EventEvidence:
    source: str
    report_date: date | str | None = None
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    fiscal_label: str = ""
    reference: str = ""

    @property
    def period(self) -> tuple[int, int] | None:
        year, quarter = self.fiscal_year, self.fiscal_quarter
        if year is None or quarter is None:
            parsed_year, parsed_quarter = fiscal.parse_label(self.fiscal_label)
            year = year or parsed_year
            quarter = quarter or parsed_quarter
        return (int(year), int(quarter)) if year and quarter else None

    @property
    def date(self) -> date | None:
        value = self.report_date
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value or "")[:10])
        except ValueError:
            return None


@dataclass(frozen=True)
class EventConflict:
    field: str
    anchor_source: str
    anchor_value: str
    conflicting_source: str
    conflicting_value: str


@dataclass(frozen=True)
class EarningsEvent:
    entity: str
    report_date: date
    fiscal_year: int
    fiscal_quarter: int

    @property
    def fiscal_label(self) -> str:
        return f"Q{self.fiscal_quarter} FY{self.fiscal_year}"

    @property
    def event_id(self) -> str:
        return f"{self.entity}:{self.fiscal_year}Q{self.fiscal_quarter}:{self.report_date}"


@dataclass(frozen=True)
class EventResolution:
    status: str
    event: EarningsEvent | None
    conflicts: tuple[EventConflict, ...]
    evidence: tuple[EventEvidence, ...]
    unresolved_fields: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.status == "resolved" and self.event is not None


_PRIORITY = {"calendar": 100, "transcript": 90, "config": 60, "filing": 50}


def _priority(item: EventEvidence) -> int:
    source = item.source.lower().split(":", 1)[0]
    return _PRIORITY.get(source, 10)


def resolve_event(entity: str, evidence: Iterable[EventEvidence], *,
                  filing_window_days: int = 4) -> EventResolution:
    """Resolve one event and surface every source conflict instead of guessing latest."""
    from ..config import canonical_entity

    items = tuple(evidence)
    period_items = sorted((item for item in items if item.period), key=_priority, reverse=True)
    date_items = sorted((item for item in items if item.date), key=_priority, reverse=True)
    unresolved = []
    if not period_items:
        unresolved.append("fiscal_period")
    if not date_items:
        unresolved.append("report_date")
    if unresolved:
        return EventResolution("unresolved", None, (), items, tuple(unresolved))

    period_anchor = period_items[0]
    date_anchor = date_items[0]
    conflicts: list[EventConflict] = []
    for item in period_items[1:]:
        if item.period != period_anchor.period:
            conflicts.append(EventConflict(
                "fiscal_period", period_anchor.source,
                f"{period_anchor.period[0]}Q{period_anchor.period[1]}", item.source,
                f"{item.period[0]}Q{item.period[1]}",
            ))
    for item in date_items[1:]:
        if abs((item.date - date_anchor.date).days) > filing_window_days:
            conflicts.append(EventConflict(
                "report_date", date_anchor.source, str(date_anchor.date),
                item.source, str(item.date),
            ))

    year, quarter = period_anchor.period
    event = EarningsEvent(
        entity=canonical_entity(entity).upper(),
        report_date=date_anchor.date,
        fiscal_year=year,
        fiscal_quarter=quarter,
    )
    return EventResolution(
        "conflict" if conflicts else "resolved", event, tuple(conflicts), items)
