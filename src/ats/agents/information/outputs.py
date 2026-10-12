"""Information model response contracts, including recorded replay types."""

from pydantic import Field

from ..pead.outputs import ContextUpdateView


class InformationContextUpdateView(ContextUpdateView):
    """Information's request contract requires independent factual confidence."""

    confidence: float = Field(
        ge=0,
        le=1,
        description="Required factual-summary confidence, independent of materiality.",
    )
