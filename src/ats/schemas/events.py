"""Event-calendar contract — config-driven event triggers for the analysts."""

from __future__ import annotations

from datetime import date as date_type
from typing import Literal

from pydantic import BaseModel, Field

EventKind = Literal["fomc", "cpi", "nfp", "gdp", "industry_conf", "other"]


class CalendarEvent(BaseModel):
    date: date_type
    kind: EventKind = "other"
    label: str = ""
    triggers: list[str] = Field(default_factory=list,
                                description='macro | sector | sector:<name> | pead:<SYM>')
