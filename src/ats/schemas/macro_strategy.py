"""Macro-strategist contracts — theme config + the persisted weekly review.

Equity-strategist paradigm: the primary deliverable is sector_tilts (over/under
weight) + regime + rate_path, NOT per-topic summaries.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

STANCES = ("超配", "中性", "低配")
SIGNALS = ("risk-on", "neutral", "risk-off", "bullish", "bearish")


# --------------------------------------------------------------------------- #
# Config (config/macro.yaml)
# --------------------------------------------------------------------------- #
class MacroTheme(BaseModel):
    key: str
    label: str
    kind: str = "quant"                # quant | qual
    quant: list[str] = Field(default_factory=list)   # MacroData field names
    queries: list[str] = Field(default_factory=list)  # Tavily search queries


class MacroConfig(BaseModel):
    name: str = "macro"
    label: str = "宏观"
    output_dir: str = ""
    themes: list[MacroTheme] = Field(default_factory=list)
    search: dict = Field(default_factory=dict)
    review: dict = Field(default_factory=dict)

    def theme_keys(self) -> set[str]:
        return {t.key for t in self.themes}


# --------------------------------------------------------------------------- #
# Persisted weekly review
# --------------------------------------------------------------------------- #
class ThemeAssess(BaseModel):
    key: str
    label: str = ""
    direction: str = ""                # 该 theme 的方向/变化
    transmission: str = ""             # 对权益市场的传导
    signal: str = "neutral"            # risk-on | neutral | risk-off
    note: str = ""


class SectorTilt(BaseModel):
    sector: str                        # 板块/行业名（自由文本，如 半导体/能源/公用事业）
    stance: str = "中性"               # 超配 | 中性 | 低配
    rationale: str = ""


class MacroReview(BaseModel):
    name: str
    as_of: datetime
    regime: str = ""                   # risk-on/off + 周期位置，一句话自包含（注入用）
    summary: str = ""
    rate_path: str = ""                # 利率路径判断（降/持/加息与时点）
    sector_tilts: list[SectorTilt] = Field(default_factory=list)   # 核心交付物
    asset_implications: str = ""       # 股/债/美元/黄金/原油
    themes: list[ThemeAssess] = Field(default_factory=list)
    top_risks: list[str] = Field(default_factory=list)

    def regime_block(self, max_chars: int = 1200) -> str:
        parts = [f"[宏观评审 {self.as_of:%Y-%m-%d}] {self.regime}"]
        if self.rate_path:
            parts.append(f"利率路径: {self.rate_path}")
        if self.asset_implications:
            parts.append(f"资产含义: {self.asset_implications}")
        if self.sector_tilts:
            tilts = "; ".join(f"{t.sector}={t.stance}" for t in self.sector_tilts)
            parts.append(f"板块倾斜: {tilts}")
        return "\n".join(parts)[:max_chars]
