"""Sector-analyst contracts — layer/universe config and the persisted weekly review."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

STANCES = ("增持", "持有", "减持")


# --------------------------------------------------------------------------- #
# Config (config/sectors/<name>.yaml)
# --------------------------------------------------------------------------- #
class LayerTicker(BaseModel):
    symbol: str
    note: str = ""
    subgroup: str = ""                # e.g. 光互联/铜连接/衬底/电力冷却 — cross-section label


class SectorLayer(BaseModel):
    key: str                          # e.g. L3_dc_infra — echoed verbatim by the LLM
    label: str
    question: str = ""
    weight_cap: float | None = None   # risk: per-chain-layer portfolio weight ceiling
    weight_cap_hard: float | None = None  # dynamic-cap hard backstop (never exceeded)
    tickers: list[LayerTicker] = Field(default_factory=list)
    # symbols ranked ALONGSIDE this layer in the cross-section but whose risk-layer
    # membership lives elsewhere (e.g. MRVL: risk=L4, but an L3-optical peer).
    cohort_extra: list[str] = Field(default_factory=list)
    private: list[str] = Field(default_factory=list)   # non-listed players, LLM reference


class SectorConfig(BaseModel):
    name: str
    label: str = ""
    sector_etf: str = "SMH"
    benchmark: str = "QQQ"
    output_dir: str = ""
    layers: list[SectorLayer] = Field(default_factory=list)
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


# --------------------------------------------------------------------------- #
# Persisted weekly review
# --------------------------------------------------------------------------- #
class LayerAssessment(BaseModel):
    key: str
    label: str = ""
    boom_score: float = Field(50.0, ge=0, le=100)   # 景气度
    supply_demand: str = ""                          # 紧张/平衡/过剩 + 依据
    pricing_power: str = ""
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


class BasketRow(BaseModel):
    """One name's cross-sectional standing within a layer cohort."""
    symbol: str
    subgroup: str = ""
    composite: float = 0.0                            # weighted sum of factor z-scores
    rank: int = 0
    weight: float = 0.0                               # suggested weight as fraction of NAV
    data_ok: bool = True                              # False -> insufficient data, excluded
    factors: dict = Field(default_factory=dict)       # z-score per factor
    metrics: dict = Field(default_factory=dict)       # raw factor values (display)


class LayerBasket(BaseModel):
    """Cross-sectional selection + sizing for one chain layer (WHO / HOW MUCH)."""
    layer_key: str
    as_of: datetime
    layer_cap: float = 0.0                            # fraction of NAV the basket sums to
    rows: list[BasketRow] = Field(default_factory=list)


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
