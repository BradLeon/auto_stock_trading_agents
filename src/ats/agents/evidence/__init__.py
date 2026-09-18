"""Chain evidence — extract cross-company observations from filings/transcripts.

Read-only by construction: this package never imports the broker and never produces
a TradeDecision. See docs/CHAIN_EVIDENCE.md.
"""

from .commercialization import (
    COMMERCIALIZATION_CLAIM_ID,
    observe_ai_commercialization,
    render_ai_commercialization_markdown,
)
from .raw_capability import (
    CLAIM_ID as RAW_CAPABILITY_CLAIM_ID,
    observe_ai_raw_capability,
    render_ai_raw_capability_markdown,
)
from .layer_runner import (
    COMMERCIALIZATION_CLAIM_ID as _LAYER_COMMERCIALIZATION_CLAIM_ID,
    render_layer_evidence_markdown,
    run_registered_layer_observers,
    write_layer_evidence_markdown,
    write_layer_evidence_outputs,
)
from .work_adoption import (
    PRODUCTION_CLAIM_ID,
    observe_ai_production_penetration,
    observe_work_adoption,
    render_ai_production_markdown,
)

__all__ = [
    "COMMERCIALIZATION_CLAIM_ID",
    "RAW_CAPABILITY_CLAIM_ID",
    "PRODUCTION_CLAIM_ID",
    "observe_ai_commercialization",
    "observe_ai_raw_capability",
    "observe_ai_production_penetration",
    "observe_work_adoption",
    "render_ai_commercialization_markdown",
    "render_ai_raw_capability_markdown",
    "render_ai_production_markdown",
    "render_layer_evidence_markdown",
    "run_registered_layer_observers",
    "write_layer_evidence_markdown",
    "write_layer_evidence_outputs",
]
