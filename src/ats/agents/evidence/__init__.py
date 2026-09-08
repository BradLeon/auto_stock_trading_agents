"""Chain evidence — extract cross-company observations from filings/transcripts.

Read-only by construction: this package never imports the broker and never produces
a TradeDecision. See docs/CHAIN_EVIDENCE.md.
"""

from .layer_runner import (
    render_layer_evidence_markdown,
    run_registered_layer_observers,
    write_layer_evidence_markdown,
)
from .work_adoption import (
    PRODUCTION_CLAIM_ID,
    observe_ai_production_penetration,
    observe_work_adoption,
    render_ai_production_markdown,
)

__all__ = [
    "PRODUCTION_CLAIM_ID",
    "observe_ai_production_penetration",
    "observe_work_adoption",
    "render_ai_production_markdown",
    "render_layer_evidence_markdown",
    "run_registered_layer_observers",
    "write_layer_evidence_markdown",
]
