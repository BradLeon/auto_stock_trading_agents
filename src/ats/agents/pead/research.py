"""Research-insight extraction — Phase D migration shim (task 4.2).

The capability moved to `agents/information/extract`: extraction products are
InformationBrief projections, the dossier is never touched, and material
insights no longer become synthetic pead_events with pre-seeded triage. This
module stays as a thin delegate so the `pead research` CLI surface and existing
imports keep working during the transition.
"""

from __future__ import annotations

from ...schemas.research import Insight
from ..information import extract as _information_extract

log = __import__("logging").getLogger("ats.agents.pead.research")

PROCESSOR_VERSION = _information_extract.PROCESSOR_VERSION


def run(*, use_llm: bool = True, since=None) -> list[Insight]:
    """One research pass — delegated to the information analyst."""
    return _information_extract.run(use_llm=use_llm, since=since)
