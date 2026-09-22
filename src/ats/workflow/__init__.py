"""Workflow package: run contracts, legacy retirement, baseline measurement and guards.

Phase A of the workflow/dataflow migration (see ``docs/TARGET_WORKFLOW_DATAFLOW.md``)
freezes the contracts that Phases B–E build on. Nothing here is wired into the
scheduler yet: this package defines structures, validation and measurement helpers.
"""

from __future__ import annotations
