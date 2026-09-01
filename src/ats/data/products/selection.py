"""Dataset-scoped primary/fallback selection without hiding parallel sources."""

from __future__ import annotations

import json

from ..core.structured_models import SourceSelection
from ..stores.structured.repository import SQLiteStructuredRepository


class SourceSelector:
    def __init__(self, repository: SQLiteStructuredRepository):
        self.repository = repository

    def select(self, dataset_id: str, rows: list[dict]) -> SourceSelection:
        dataset = self.repository.dataset(dataset_id)
        if dataset is None:
            return SourceSelection(selection_reason="dataset_unregistered", alternatives=rows)
        primary = json.loads(dataset["primary_sources_json"] or "[]")
        fallback = json.loads(dataset["fallback_sources_json"] or "[]")
        by_source = {row["source_id"]: row for row in rows}
        selected = None
        reason = "no_coverage"
        for source_id in primary:
            if source_id in by_source:
                selected = by_source[source_id]
                reason = "primary_source_available"
                break
        if selected is None:
            for source_id in fallback:
                if source_id in by_source:
                    selected = by_source[source_id]
                    reason = "fallback_source_used"
                    break
        if selected is None and rows:
            selected = rows[0]
            reason = "unranked_source_only"
        values = {row.get("value") for row in rows}
        return SourceSelection(
            selected=selected,
            selected_source=selected.get("source_id", "") if selected else "",
            selection_reason=reason,
            alternatives=[row for row in rows if row is not selected],
            conflict=len(values) > 1,
        )
