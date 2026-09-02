"""Typed configuration objects for the unified data catalog."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CatalogLifecycle = Literal[
    "registered",
    "published",
    "current",
    "current_partial",
    "planned",
    "disabled",
    "deferred",
    "runtime/excluded",
    "runtime_excluded",
    "failure",
]


class CatalogSource(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    domain: Literal["structured", "unstructured", "runtime"] = "structured"
    provider: str = ""
    adapter: str = ""
    status: CatalogLifecycle = "registered"
    datasets: list[str] = Field(default_factory=list)
    cadence: str = ""
    request_budget: dict[str, Any] = Field(default_factory=dict)
    fallback_sources: list[str] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)


class CatalogDataset(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    domain: Literal["structured", "unstructured"] = "structured"
    status: CatalogLifecycle = "registered"
    sources: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    quality: dict[str, Any] = Field(default_factory=dict)


class CatalogValidation(BaseModel):
    valid: bool
    checks: list[dict[str, Any]] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)

