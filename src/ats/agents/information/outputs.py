"""Information model response contracts, including recorded replay types."""

from typing import Literal
from pydantic import Field, model_validator

from ..pead.outputs import ContextUpdateView
from ..pead.outputs import InsightItemView
from pydantic import BaseModel


class InformationContextUpdateView(ContextUpdateView):
    """Information's request contract requires independent factual confidence."""

    confidence: float = Field(
        ge=0,
        le=1,
        description="Required factual-summary confidence, independent of materiality.",
    )


class InformationInsightView(BaseModel):
    ticker: str
    source_fact: str = Field(min_length=1, description="Chinese faithful summary of the visible source, including attributed opinions and forecasts. Never invent omitted content.")
    source_kind: Literal['reported_fact', 'claim', 'forecast']
    source_actor: str = Field(min_length=1, description="Named speaker/analyst for claims or forecasts; publisher for reported facts. Keep attribution explicit.")
    association_type: Literal['direct_update', 'business_related', 'background', 'unrelated']
    association_reason: str = Field(min_length=1, description="Why this source information concerns this ticker. Cite an explicit event/business relationship or identify background only. No invented economic consequences.")
    information_topic: str = Field(min_length=1, description="Product, capacity, demand, competition, cost, regulation or another source topic; not bullish/bearish.")
    information_quality: Literal['substantive', 'thin']
    summary: str = Field(min_length=1,
        description="Faithful Chinese source recap, preserving attribution. No system-generated read-through or bullish/bearish conclusion.")
    evidence_span_id: str = Field(min_length=1, description="Exact ev:start:end identifier from the supplied evidence spans. Quote bytes are resolved by the system.")
    supporting_evidence_span_ids: list[str] = Field(default_factory=list, max_length=3,
        description="Up to three additional adjacent ev:start:end labels when ONE source statement crosses a span boundary. Include every intervening span. Split separate statements into separate insights; never cite distant unrelated paragraphs.")
    evidence_quote: str = Field(default='', max_length=400,
        description="Leave empty; system resolves verbatim quote from evidence_span_id. Any supplied text must exactly match that span.")
    materiality: float = Field(ge=0, le=1,
        description="Reading usefulness/information density, not investment impact; independent of confidence.")
    confidence: float = Field(default=0, ge=0, le=1)

    @model_validator(mode='after')
    def nonblank(self):
        if not all(value.strip() for value in (self.summary, self.source_fact, self.source_actor, self.evidence_span_id)):
            raise ValueError('Information text fields must not be blank')
        return self


class InformationInsightBatchView(BaseModel):
    insights: list[InformationInsightView] = Field(default_factory=list)
    empty_reason: str = Field(default='', description="When no insights are returned, explain absent relevance, insufficient visible content, or inability to extract. Do not claim no news exists.")


class InformationSourceRangeView(BaseModel):
    first_chunk: int = Field(ge=1, description="First supplied source chunk number, inclusive.")
    last_chunk: int = Field(ge=1, description="Last supplied source chunk number, inclusive.")

    @model_validator(mode='after')
    def ordered(self):
        if self.last_chunk < self.first_chunk:
            raise ValueError('Source range must be ordered')
        return self


class InformationEvidenceChoiceView(BaseModel):
    """One core statement and its source-owned interpretive context."""

    core: InformationSourceRangeView = Field(description="Source range for the complete core statement; at most 1600 source characters. Never write or reconstruct source text.")
    context_ranges: list[InformationSourceRangeView] = Field(
        default_factory=list, max_length=2,
        description="Up to two supporting source ranges, each at most 800 characters, for attribution, identity, time, units, column meaning or qualifications. No unrelated stories or inferred relationships.")
    source_kind: Literal['reported_fact', 'claim', 'forecast']
    source_actor: str = Field(min_length=1, max_length=120)
    target_role: Literal['subject', 'product_owner', 'named_comparison', 'explicit_business_party', 'incidental', 'absent'] = Field(
        description="Target role in THIS core statement, established by visible source evidence. subject: target event/data; product_owner: target's product; named_comparison: explicit comparison with target; explicit_business_party: named concrete business relationship; incidental: merely mentioned/hosting/location/general sector context; absent: no supported target role. Same industry alone never establishes a business relationship.")
    information_topic: str = Field(min_length=1, max_length=80)
    information_quality: Literal['substantive', 'thin']
    materiality: float = Field(ge=0, le=1)

    @model_validator(mode='after')
    def nonblank(self):
        if any(not getattr(self, field).strip() for field in (
                'source_actor', 'information_topic')):
            raise ValueError('Evidence selection fields must not be blank')
        return self


class InformationEvidenceBatchView(BaseModel):
    """Select facts AND assess relevance using the full admitted visible article."""

    evidence: list[InformationEvidenceChoiceView] = Field(default_factory=list, max_length=4)
    empty_reason: str = ''


class InformationEvidenceRecapView(BaseModel):
    """Recap ONE excerpt without reclassifying article-level relevance."""

    summary: str = Field(min_length=1, description="Faithful Chinese rendering of the CORE statement only. Context resolves identities, units and qualifications; never append its independent facts or expand undefined acronyms.")
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode='after')
    def nonblank(self):
        if not self.summary.strip():
            raise ValueError('Evidence recap fields must not be blank')
        return self
