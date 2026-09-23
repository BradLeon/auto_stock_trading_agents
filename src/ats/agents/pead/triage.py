"""Materiality triage — Phase D migration shim (task 4.3).

The capability moved to `agents/information/triage`. Two deliberate changes
there: `enrich` reads only ADMITTED document bodies through the Workflow-Memory
bridge (no URL fetches from inside an agent), and the module belongs to the
information analyst. Re-exported here so existing imports keep working.
"""

from __future__ import annotations

from ..information.triage import BATCH_SIZE, enrich, score_items

__all__ = ["BATCH_SIZE", "score_items", "enrich"]
