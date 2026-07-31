"""技术面分析师契约 —— 择时/敞口读数。

与其他 analyst 一样：**只产研究，不出单**。这里刻意不含 qty / notional / action —
`target_exposure` 是"建议的风险敞口上限"，把它变成订单是 Chief 的职责，不是这里的
（docs/DESIGN.md §4；本仓库刚修过 PEAD 分析师越界构造 TradeDecision 的缺陷）。

全部字段由确定性代码填写，本 agent 不接 LLM。
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class TechnicalConfig(BaseModel):
    name: str = "technical"
    label: str = "技术面"
    output_dir: str = ""
    strategy: str = "jia"                    # 见 agents/technical/strategy.MODES
    universe: dict = Field(default_factory=dict)   # 标的来源与排除项
    params: dict = Field(default_factory=dict)     # 阈值覆盖，缺省见 strategy.py
    review: dict = Field(default_factory=dict)     # 报告/注入的字符预算


class TechnicalReading(BaseModel):
    """一个标的、一天的读数。

    `score_detail` 保留 7 个分项布尔值：一个光秃秃的 0-7 分事后无法核对，
    必须能看出是哪几条成立。
    """

    symbol: str
    score: int = 0                           # 7 点动量评分 0..7
    score_detail: dict = Field(default_factory=dict)
    raw_ladder: float = 0.0                  # L(score)，未截断（可 >1）
    vol_scalar: float | None = None          # sigma = min(2, 15/VIX)
    target_exposure: float = 0.0             # **建议敞口上限** 0..1，非交易指令
    prev_exposure: float | None = None       # 上一份读数，用于变化检测
    panic_fired: bool = False                # Tier1：VIX/VIX3M 倒挂
    bear_fired: bool = False                 # Tier2：价格 < SMA200
    close: float | None = None
    sma20: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    bars: int = 0                            # 用到的历史根数（不足则读数不可信）
    stale: bool = False                      # 数据不足或过旧
    note: str = ""

    @property
    def changed(self) -> bool:
        if self.prev_exposure is None:
            return False
        return abs(self.target_exposure - self.prev_exposure) > 1e-9

    def one_line(self) -> str:
        arrow = ""
        if self.changed:
            arrow = " ↑" if self.target_exposure > (self.prev_exposure or 0) else " ↓"
        flags = []
        if self.panic_fired:
            flags.append("恐慌")
        if self.bear_fired:
            flags.append("破200日线")
        tail = f" [{'/'.join(flags)}]" if flags else ""
        prev = "" if self.prev_exposure is None else f"（前 {self.prev_exposure:.0%}）"
        return (f"{self.symbol}: 评分 {self.score}/7 · 建议敞口 "
                f"{self.target_exposure:.0%}{arrow}{prev}{tail}")


class TechnicalReview(BaseModel):
    """一次日度运行的全部产出。"""

    name: str = "technical"
    as_of: datetime
    strategy: str = "jia"
    fingerprint: str = ""                    # 参数指纹，供日后追溯读数由哪一版算出
    vix: float | None = None
    vix3m: float | None = None
    market_panic: bool = False               # 期限结构倒挂（全市场级）
    readings: list[TechnicalReading] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)   # 无价格/已过滤的标的
    notes: list[str] = Field(default_factory=list)

    @property
    def as_of_date(self) -> date:
        return self.as_of.date()

    def summary_line(self) -> str:
        live = [r for r in self.readings if not r.stale]
        if not live:
            return "（无有效读数）"
        avg = sum(r.target_exposure for r in live) / len(live)
        reduced = sum(1 for r in live if r.target_exposure < 1.0)
        term = f" · VIX/VIX3M {self.vix / self.vix3m:.2f}" if self.vix and self.vix3m else ""
        return (f"{len(live)} 只 · 平均建议敞口 {avg:.0%} · {reduced} 只低于满仓"
                f"{' · ⚠️期限结构倒挂' if self.market_panic else ''}"
                f" · VIX {self.vix:.1f}{term}" if self.vix else "")

    def chief_block(self, max_chars: int = 1200) -> str:
        """给 Chief 的紧凑注入块。

        只列**需要注意的**标的（未满仓或较上次有变化），满仓且无变化的略去 —— 否则
        20 只标的会把上下文占满，而"一切正常"本来就不需要 Chief 花注意力。
        """
        if not self.readings:
            return ""
        head = [f"[技术面 {self.as_of:%Y-%m-%d} · 策略 {self.strategy}] {self.summary_line()}",
                "（建议的风险敞口上限，非方向判断、非交易指令；Chief 自行决定是否采纳）"]
        notable = [r for r in self.readings
                   if not r.stale and (r.target_exposure < 1.0 or r.changed)]
        notable.sort(key=lambda r: (r.target_exposure, r.symbol))
        if notable:
            head += ["需注意："] + [f"  · {r.one_line()}" for r in notable]
        else:
            head.append("全部标的处于满仓建议且较上次无变化。")
        stale = [r.symbol for r in self.readings if r.stale]
        if stale:
            head.append(f"数据不足未评估：{', '.join(stale)}")
        return "\n".join(head)[:max_chars]
