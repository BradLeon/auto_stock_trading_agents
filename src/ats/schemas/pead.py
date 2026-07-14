"""PEAD (Post-Earnings-Announcement Drift) data contracts.

Models the earnings-anchored state machine: per-ticker config + scorecard
dimensions/weights → a pre-earnings expectations baseline + market setup +
signal chain → post-earnings actuals → weighted Surprise Scorecard → decision.
The whole thing is aggregated into a PeadDossier persisted per (symbol, fiscal).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["upstream", "peer", "downstream"]


# --------------------------------------------------------------------------- #
# Config (loaded from config/pead/<SYM>.yaml)
# --------------------------------------------------------------------------- #
class ScorecardDim(BaseModel):
    key: str
    weight: float = Field(ge=0, le=1, description="fraction; weights should sum to ~1")
    label: str = ""


class SignalChainConfig(BaseModel):
    symbol: str
    role: Role = "peer"


class PeadConfig(BaseModel):
    symbol: str
    fiscal_label: str = ""                  # e.g. "Q3 FY2026"
    sector_etf: str = "SMH"                 # run-up benchmark
    benchmark: str = "QQQ"
    run_up_warn_pct: float = 5.0            # excess run-up vs sector that raises the bar
    long_threshold: float = 1.0             # scorecard total needed to go long (COHR: 1.5)
    scorecard_dims: list[ScorecardDim] = Field(default_factory=list)
    signal_chain: list[SignalChainConfig] = Field(default_factory=list)
    narrative_seed: str = ""

    def weight_of(self, key: str) -> float:
        for d in self.scorecard_dims:
            if d.key == key:
                return d.weight
        return 0.0


# --------------------------------------------------------------------------- #
# Pre-earnings: stable company framework (background / peers / valuation band)
# --------------------------------------------------------------------------- #
class FundamentalBackground(BaseModel):
    background: str = ""              # 3-5 numbered bullets: moat / drivers / margin / risk
    peer_comparison: str = ""         # markdown table vs 1-2 key peers
    watch_metrics: str = ""           # grouped quantitative watch-list for the quarter
    catalysts: list[str] = Field(default_factory=list)   # dated upcoming catalysts
    key_risks: list[str] = Field(default_factory=list)   # thesis-invalidating, by severity
    valuation: str = ""               # PE / fwd PE + ceiling-floor with implied prices


# --------------------------------------------------------------------------- #
# Pre-earnings: expectations baseline
# --------------------------------------------------------------------------- #
class Expectation(BaseModel):
    dim_key: str
    metric: str = ""
    conservative: str = ""
    neutral: str = ""        # the base-case expectation
    optimistic: str = ""
    source: str = ""


class ExpectationSet(BaseModel):
    symbol: str
    fiscal_label: str = ""
    as_of: datetime
    narrative: str = ""                       # core thesis / business drivers
    focus_ranking: list[str] = Field(default_factory=list)  # what matters most this quarter
    expectations: list[Expectation] = Field(default_factory=list)
    consensus_eps: float | None = None
    consensus_eps_low: float | None = None
    consensus_eps_high: float | None = None
    consensus_revenue: float | None = None
    consensus_revenue_low: float | None = None
    consensus_revenue_high: float | None = None
    consensus_target_price: float | None = None   # analyst PT mean
    consensus_rating_summary: str = ""            # e.g. "强买5/买12/持有2/卖0"
    consensus_recent_actions: list[str] = Field(default_factory=list)  # last 3 upgrades/downgrades
    valuation: str = ""                       # PE / fwd PE / ceiling-floor note


# --------------------------------------------------------------------------- #
# Pre-earnings: market setup (price + options)
# --------------------------------------------------------------------------- #
class MarketSetup(BaseModel):
    symbol: str
    as_of: datetime
    pre_earnings_close: float | None = None
    run_up_vs_sector_pct: float | None = None   # 20d excess return vs sector_etf
    run_up_vs_bench_pct: float | None = None
    dist_to_ath_pct: float | None = None        # negative = below ATH
    expected_move_pct: float | None = None      # option-implied straddle move
    atm_iv: float | None = None
    iv_skew: float | None = Field(None, description="25Δ put IV - 25Δ call IV")
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Pre-earnings: signal chain
# --------------------------------------------------------------------------- #
class SignalChainItem(BaseModel):
    symbol: str
    role: Role = "peer"
    earnings_date: date | None = None
    reported: bool = False
    price_chg_pct: float | None = None
    signal: str = ""                  # LLM one-liner: what it implies for the target


# --------------------------------------------------------------------------- #
# Post-earnings: actuals
# --------------------------------------------------------------------------- #
class ActualMetric(BaseModel):
    dim_key: str
    metric: str = ""
    actual: str = ""
    vs_expected: str = ""             # e.g. "🔴 远超 / ✅ 中性 / ⚪ / ⚠️"
    note: str = ""


class Actuals(BaseModel):
    symbol: str
    fiscal_label: str = ""
    as_of: datetime
    reported_eps: float | None = None
    reported_revenue: float | None = None
    metrics: list[ActualMetric] = Field(default_factory=list)
    guidance: str = ""                # forward guidance extracted from text
    transcript_signals: list[str] = Field(default_factory=list)
    transcript_source: str = ""


# --------------------------------------------------------------------------- #
# Surprise Scorecard
# --------------------------------------------------------------------------- #
class ScorecardLine(BaseModel):
    dim_key: str
    label: str = ""
    weight: float = 0.0
    score: float = Field(0.0, ge=-2, le=2)
    weighted: float = 0.0
    note: str = ""


class Scorecard(BaseModel):
    symbol: str
    fiscal_label: str = ""
    as_of: datetime
    lines: list[ScorecardLine] = Field(default_factory=list)
    total: float = 0.0
    threshold: float = 1.0
    band: str = ""                    # human band label, e.g. "未达特别门槛"


# --------------------------------------------------------------------------- #
# Dossier — the full per-(symbol, fiscal) record
# --------------------------------------------------------------------------- #
PeadPhase = Literal["prep", "score"]


class PeadDossier(BaseModel):
    symbol: str
    fiscal_label: str = ""
    phase: PeadPhase = "prep"
    updated_at: datetime
    earnings_date: str = ""           # ISO date of the print, when known
    fundamental_background: FundamentalBackground | None = None  # stable company framework
    expectation_set: ExpectationSet | None = None
    market_setup: MarketSetup | None = None
    signal_chain: list[SignalChainItem] = Field(default_factory=list)
    signal_chain_summary: str = ""    # net supportive/cautionary chain read
    fundamentals_context: str = ""    # fd.to_context() snapshot at prep time
    scorecard_dims: list[ScorecardDim] = Field(default_factory=list)  # labels+weights for skeletons
    scorecard_weights: dict[str, float] = Field(default_factory=dict)  # dim_key -> weight
    long_threshold: float = 1.0       # decision-tree params snapshot from config
    run_up_warn_pct: float = 5.0
    actuals: Actuals | None = None
    scorecard: Scorecard | None = None
    decision_summary: str = ""
