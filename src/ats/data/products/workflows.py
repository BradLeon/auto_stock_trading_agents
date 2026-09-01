"""Declared data-input boundaries for orchestration consumers.

This module describes a Workflow's *input* contract only.  Dossiers, reports,
claims, decisions, approvals, trades and run state remain workflow memory and are
not made into data products merely because an orchestrator reads them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowDataBoundary:
    consumer: str
    mode: str
    persistent_inputs: tuple[str, ...]
    runtime_inputs: tuple[str, ...]
    memory_outputs: tuple[str, ...]


_BOUNDARIES = {
    "chief_graph": {
        "persistent_inputs": (
            "upstream Agent products: company financials, consensus, documents, evidence",
        ),
        "runtime_inputs": ("broker portfolio and execution state",),
        "memory_outputs": (
            "Chief context, decision, approval, trade and run records",
        ),
    },
    "runtime_scheduler": {
        "persistent_inputs": (
            "official releases, research/news documents and Chain source products",
        ),
        "runtime_inputs": ("earnings calendar and market-session calendar",),
        "memory_outputs": (
            "workflow runs, scores, observations, reports and decision state",
        ),
    },
}


def workflow_data_boundary(consumer: str) -> WorkflowDataBoundary:
    """Return the configured rollout mode plus the non-overlapping I/O boundary."""
    normalized = consumer.strip().lower().replace("-", "_")
    if normalized not in _BOUNDARIES:
        raise ValueError(f"unknown workflow data boundary: {consumer}")
    from ..rollout_modes import read_mode

    body = _BOUNDARIES[normalized]
    return WorkflowDataBoundary(
        consumer=normalized,
        mode=read_mode(normalized),
        persistent_inputs=body["persistent_inputs"],
        runtime_inputs=body["runtime_inputs"],
        memory_outputs=body["memory_outputs"],
    )


__all__ = ["WorkflowDataBoundary", "workflow_data_boundary"]
