"""Sector-analyst contracts — layer/universe config and the persisted weekly review."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .chain import ClaimDef

STANCES = ("增持", "持有", "减持")
# Layer-level allocation calls. Ordered strong -> flat: index doubles as a severity rank.
ALLOCATIONS = ("超配", "标配", "低配", "清仓")


# --------------------------------------------------------------------------- #
# Config (config/sectors/<name>.yaml)
# --------------------------------------------------------------------------- #
class LayerTicker(BaseModel):
    symbol: str
    note: str = ""
    subgroup: str = ""                # e.g. 光互联/铜连接/衬底/电力冷却 — cross-section label


class SectorLayer(BaseModel):
    key: str                          # e.g. L4_interconnect — echoed verbatim by the LLM
    label: str
    question: str = ""
    # Layer keys this layer inherits history from. A rename declares its old key here;
    # a SPLIT declares the same old key on BOTH halves, so each of them can still read
    # the pre-split rows. Stored rows are NEVER rewritten — a row saying `L5_fab` was
    # judged under the merged foundry+memory lens, and relabelling it `L6_memory` would
    # claim a separation that did not exist when the call was made.
    legacy_keys: list[str] = Field(default_factory=list)
    weight_cap: float | None = None   # risk: per-chain-layer portfolio weight ceiling
    weight_cap_hard: float | None = None  # dynamic-cap hard backstop (never exceeded)
    tickers: list[LayerTicker] = Field(default_factory=list)
    # symbols ranked ALONGSIDE this layer in the cross-section but whose risk-layer
    # membership lives elsewhere (e.g. MRVL: risk=L4, but an L3-optical peer).
    cohort_extra: list[str] = Field(default_factory=list)
    # subgroup -> curated KB note path (repo-relative or absolute) for the structure
    # analyst (v2 qualitative overlay). e.g. {光互联: config/knowledge/光互联.md}
    structure_notes: dict[str, str] = Field(default_factory=dict)
    private: list[str] = Field(default_factory=list)   # non-listed players, LLM reference
    # Who may bear witness on this layer's theme, by role (peer / upstream / downstream /
    # reference / private). Human-maintained: this is a governance choice, not something
    # the engine may widen on its own. Symbols are canonical entity ids — aliases fold in
    # via config/entities.yaml, so a company's several listings never count as several
    # witnesses. Roles the engine does not consume (private) are documentation.
    witness_roster: dict[str, list[str]] = Field(default_factory=dict)
    # Falsifiable propositions this layer is being tested on (docs/CHAIN_EVIDENCE.md).
    # Human-maintained: agents may PROPOSE one, only a person adds it here.
    claims: list[ClaimDef] = Field(default_factory=list)

    def roster(self, *roles: str) -> list[str]:
        """Canonical entities in the named roles (all witness roles if none given)."""
        wanted = roles or ("peer", "upstream", "downstream", "reference")
        out: list[str] = []
        for role in wanted:
            for sym in self.witness_roster.get(role, []):
                if sym not in out:
                    out.append(sym)
        return out


class LayerGroup(BaseModel):
    """A ceiling over several layers at once — what keeps a SPLIT from loosening a guard.

    Splitting `L5_fab` (<=30%) into memory and foundry gives two independent caps that
    together allow more than the single cap did. The fix is an added guard, not a
    relaxed one: the children keep their own caps, and their SUM stays bounded by the
    pre-split value declared here. Member caps summing above the group cap is intended —
    it lets the mix tilt between them while the total stays put.
    """
    key: str
    label: str = ""
    layers: list[str] = Field(default_factory=list)
    weight_cap: float = 1.0
    weight_cap_hard: float | None = None


class SectorConfig(BaseModel):
    name: str
    label: str = ""
    sector_etf: str = "SMH"
    benchmark: str = "QQQ"
    output_dir: str = ""
    layers: list[SectorLayer] = Field(default_factory=list)
    layer_groups: list[LayerGroup] = Field(default_factory=list)
    snapshot: dict = Field(default_factory=dict)
    review: dict = Field(default_factory=dict)

    def all_symbols(self) -> list[str]:
        """Deduped universe, layer order preserved (GOOGL in L1+L2 -> once)."""
        seen: set[str] = set()
        out: list[str] = []
        for layer in self.layers:
            for t in layer.tickers:
                if t.symbol not in seen:
                    seen.add(t.symbol)
                    out.append(t.symbol)
        return out

    def layer_of(self, symbol: str) -> str | None:
        for layer in self.layers:
            if any(t.symbol == symbol for t in layer.tickers):
                return layer.key
        return None

    def layer_by_key(self, key: str) -> SectorLayer | None:
        """Resolve a layer key, current or historical.

        Current keys win outright; only then do `legacy_keys` get consulted, so a key
        that is live today never resolves to whoever inherited an older namesake. A
        historical key that split into two layers matches BOTH — callers that need the
        set (history queries) use `layers_by_key`; this one returns the first, which is
        config order, i.e. chain order.
        """
        return next(iter(self.layers_by_key(key)), None)

    def layers_by_key(self, key: str) -> list[SectorLayer]:
        """Every layer a key resolves to — several when a split layer's old key is asked
        for. Empty when the key is unknown; callers skip such rows with a warning rather
        than failing the whole read."""
        exact = [ly for ly in self.layers if ly.key == key]
        if exact:
            return exact
        return [ly for ly in self.layers if key in ly.legacy_keys]

    def layer_for_key_and_symbols(self, key: str, symbols) -> SectorLayer | None:
        """Resolve a key, disambiguating a SPLIT by which half holds those symbols.

        A pre-split key matches both halves, so a bare `layer_by_key` would hand back
        whichever comes first in config order — and audit a memory basket against the
        foundry layer's notes. The rows themselves say which half they belong to, so
        use them; fall back to config order only when nothing overlaps.
        """
        candidates = self.layers_by_key(key)
        if len(candidates) < 2:
            return next(iter(candidates), None)
        wanted = {str(s).upper() for s in symbols}
        best = max(candidates,
                   key=lambda ly: len(wanted & {t.symbol.upper() for t in ly.tickers}))
        overlap = wanted & {t.symbol.upper() for t in best.tickers}
        return best if overlap else candidates[0]

    def is_legacy_key(self, key: str) -> bool:
        """True when `key` is only reachable through `legacy_keys` — such rows describe a
        pre-split lens and must be labelled as such wherever history is displayed."""
        return (not any(ly.key == key for ly in self.layers)
                and any(key in ly.legacy_keys for ly in self.layers))


# --------------------------------------------------------------------------- #
# Persisted weekly review
# --------------------------------------------------------------------------- #
class LayerAssessment(BaseModel):
    key: str
    label: str = ""
    boom_score: float = Field(50.0, ge=0, le=100)   # 景气度
    supply_demand: str = ""                          # 紧张/平衡/过剩 + 依据
    pricing_power: str = ""       # 谁在瓶颈环节；见 SKILL.md 的证据优先纪律
    capital_flow: str = ""
    cycle_position: str = ""
    signal: str = "neutral"                          # bullish | neutral | bearish
    note: str = ""


class CompanyCall(BaseModel):
    symbol: str
    layer: str = ""
    stance: str = "持有"                             # 增持 | 持有 | 减持
    conviction: float = Field(0.0, ge=0, le=1)
    rationale: str = ""


class LayerNameCall(BaseModel):
    """The layer analyst's take on one name: which way, and on what evidence."""
    symbol: str
    subgroup: str = ""
    stance: str = "持有"                              # 增持 | 持有 | 减持
    rationale: str = ""
    # True when the reasoning leans on a reading only that company itself made.
    self_reported_only: bool = False


class BasketRow(BaseModel):
    """One name's cross-sectional standing within a layer cohort."""
    symbol: str
    subgroup: str = ""
    composite: float = 0.0                            # weighted sum of factor z-scores
    rank: int = 0                                     # blended rank (structural, if run)
    quant_rank: int = 0                               # pure-quant rank (pre-blend, for contrast)
    weight: float = 0.0                               # suggested weight as fraction of NAV
    data_ok: bool = True                              # False -> insufficient data, excluded
    tech_tenor: float | None = None                   # -2..+2 技术时间朝向（光进铜退等 secular 位置）
    moat_pricing: float | None = None                 # -2..+2 护城河/份额/定价权/客户集中
    rationale: str = ""                               # structure analyst's per-name note
    factors: dict = Field(default_factory=dict)       # z-score per factor
    metrics: dict = Field(default_factory=dict)       # raw factor values (display)


class LayerBasket(BaseModel):
    """Cross-sectional selection + sizing for one chain layer (WHO / HOW MUCH)."""
    layer_key: str
    as_of: datetime
    layer_cap: float = 0.0                            # fraction of NAV the basket sums to
    structural: bool = False                          # True if the KB structure overlay ran
    # False when the cohort was too small to standardise: `_zscores` returns all-zero
    # below two samples, so every rank is an artefact of config order, not a finding.
    # The budget still lands (a lone name takes the layer's share) — what is suppressed
    # is the CLAIM that the ranking means something.
    cross_section_applicable: bool = True
    subgroup_notes: dict = Field(default_factory=dict)  # subgroup -> tech-curve note (光进铜退…)
    rows: list[BasketRow] = Field(default_factory=list)


class CandidateClaim(BaseModel):
    """A proposition worth tracking that nobody has written down yet.

    Separate from the induction engine's proposals on purpose (see design D19): that one
    fires DETERMINISTICALLY off unmapped observations and lands in `claim_proposals` for
    human adoption. This one is the analyst noticing something while reading, and it
    goes no further than the report — it must never touch a verdict, a rank or a weight.

    `witnesses` and `falsifier` are required in spirit: a candidate that cannot say who
    would testify or what reading would kill it is not a proposition, it is a mood.
    """
    statement: str
    witnesses: list[str] = Field(default_factory=list)   # who could speak to it
    falsifier: str = ""                                  # what reading would refute it
    why_now: str = ""                                    # what in this round raised it

    def is_usable(self) -> bool:
        return bool(self.statement and self.witnesses and self.falsifier)


class LayerVerdict(BaseModel):
    """One layer's allocation call — HOW MUCH of this layer, and WHY.

    The weekly review used to answer only "is this layer hot" (`boom_score` +
    bullish/neutral/bearish), which carries no position meaning: the budget path
    (`weight_cap` x cross-section rank) ran regardless of where in the cycle the layer
    sat. This is the missing verdict, and `allocation` is what drives the layer's budget
    utilisation — downward only, never above `weight_cap` (see risk.yaml).
    """

    layer_key: str
    as_of: datetime
    allocation: str = "标配"                          # 超配 | 标配 | 低配 | 清仓
    confidence: float = Field(0.0, ge=0, le=1)
    cycle_position: str = ""                          # 早/中/晚周期 — from INDUSTRY evidence
    # One line per common claim: its verdict and what it means for this layer's sizing.
    claim_attributions: list[str] = Field(default_factory=list)
    # Falsifiable observations that would flip this call; checked off next round.
    reversal_triggers: list[str] = Field(default_factory=list)
    name_calls: list[LayerNameCall] = Field(default_factory=list)
    # False when the cohort was too small to standardise (see cross_section._zscores).
    cross_section_applicable: bool = True
    # False when the layer declares no claims at all. Distinct from "claims exist but
    # nothing was said this quarter": the first is a CONFIG gap that must stay visible,
    # the second is an EVIDENCE gap. Collapsing them hides the config gap forever.
    has_claims: bool = True
    rationale: str = ""
    # Report-only (design D19): never feeds a verdict, a rank or a weight.
    candidate_claims: list[CandidateClaim] = Field(default_factory=list)

    def is_valid(self) -> bool:
        return self.allocation in ALLOCATIONS


class TopDownComparison(BaseModel):
    """Macro/FactSet interpretation added only after layer verdicts are fixed."""

    macro_background: str = ""
    factset_background: str = ""
    agreements: list[str] = Field(default_factory=list)
    divergences: list[str] = Field(default_factory=list)
    recommendation_impact: str = ""
    availability_notes: list[str] = Field(default_factory=list)
    macro_review_date: str = ""
    factset_report_date: str = ""
    factset_version_id: str = ""


class SectorReview(BaseModel):
    sector: str
    as_of: datetime
    regime: str = ""                  # one self-contained line (injected into PEAD)
    summary: str = ""
    layers: list[LayerAssessment] = Field(default_factory=list)
    company_calls: list[CompanyCall] = Field(default_factory=list)
    rotation_advice: str = ""
    top_risks: list[str] = Field(default_factory=list)
    baskets: list[LayerBasket] = Field(default_factory=list)   # cross-sectional sizing per layer
    layer_verdicts: list[LayerVerdict] = Field(default_factory=list)  # per-layer allocation calls
    top_down_comparison: TopDownComparison | None = None

    def call_for(self, symbol: str) -> CompanyCall | None:
        for c in self.company_calls:
            if c.symbol == symbol:
                return c
        return None

    def layer_assessment(self, key: str | None) -> LayerAssessment | None:
        for a in self.layers:
            if a.key == key:
                return a
        return None

    def verdict_for(self, layer_key: str | None) -> LayerVerdict | None:
        for v in self.layer_verdicts:
            if v.layer_key == layer_key:
                return v
        return None
