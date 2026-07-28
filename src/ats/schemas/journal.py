"""交易日志契约 —— 意图 / 回合 / 可证伪的预测。

与 memory.py 的分工：memory.py 记**券商事实**（订单、成交、绩效快照），
本模块记**决策事实**（当时打算做什么、为什么、后来对不对）。

三个组织单位互相**交叉而非嵌套**，各自回答不同的问题：

    周期 cycle_id        一次 Chief 决策会话     → 审计：当时看到了什么
    意图 JournalEntry    一个标的、一个动作      → 决策质量：打算做什么、为什么
    回合 TradeEpisode    一个标的建仓到清仓      → 结果质量：这笔投资值不值

一个回合横跨多个周期，一个周期横跨多个标的，谁也不包含谁 —— 所以三者不能合并。
另有第四个东西 horizon（T+1/5/20/60），它不是容器而是**尺子**：回合长度是我们自己
选的（内生），固定窗口是外生的。只在平仓时衡量，会把"信号好不好"和"退出退得好不好"
搅在一起。

层次（按是否落库）：
    持久层  JournalEntry · TradeEpisode · Prediction · PredictionOutcome
    投影层  EpisodeCard          —— 不落库，由上面三者现拼
    报告层  CriticBrief · CriticFinding —— 不落库，季度现算
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# 枚举
# --------------------------------------------------------------------------- #
Setup = Literal[
    "pead_event",       # 财报事件
    "risk_repair",      # 修风控破限
    "stop_loss",        # 止损
    "sector_rotation",  # 行业轮动
    "macro_tilt",       # 宏观倾斜
    "manual",           # 手工单
    "boss_override",    # 人工覆盖
    "unknown",
]

RiskUnit = Literal[
    "declared_stop",    # 声明了止损 → 到止损的距离
    "expected_move",    # 事件单 → 期权隐含 1σ（事件的天然风险单位）
    "portfolio_stop",   # 其余 → 组合止损 25%
    "",                 # 拿不到 notional：不猜分母，R 留空
]

ExitReason = Literal[
    "target_hit",           # 按计划止盈 —— 计划对、执行对
    "stop_hit",             # 按计划止损 —— 计划对、判断错
    "thesis_invalidated",   # 预登记的失效条件真的触发了
    "horizon_reached",      # 持有到期，既没到止盈也没到止损
    "risk_forced",          # 风控强制减仓，不是主动判断
    "boss_override",        # 人工干预
    "drift",                # ⚠️ 以上都不是 —— 没有按任何说过的理由退出
]
# drift 是 agent 版的"没遵守计划"。人类日志里"是否遵守计划"对机器恒真，
# 但系统确实会在计划之外平仓（风控级联、Chief 换了套说法），这一类专门抓它。

EpisodeOrigin = Literal[
    "system",        # 系统下的单
    "manual",        # 你在 TWS 手工下的
    "mixed",         # 两者都有
    "pre_tracking",  # 日志上线前的存量持仓
]

BasisSource = Literal[
    "observed_fills",  # 成本来自我们观测到的成交
    "ibkr_avg_cost",   # 成本来自券商均价（存量持仓，无入场记录）
]

PredictionSource = Literal[
    "pead_score",     # 打分卡总分 → 预测漂移方向/强度
    "expected_move",  # 期权隐含波动 → 预测跳空幅度
    "consensus_pt",   # 卖方目标价
]


# --------------------------------------------------------------------------- #
# 持久层
# --------------------------------------------------------------------------- #
class ApprovalDivergence(BaseModel):
    """人在哪里推翻了 agent。

    这是全日志最有价值的一组字段。人类交易日志的核心问句"我是否遵守了计划"，
    对 agent 恒为真 —— 机器不会偏离。真正对应的是反过来：**人在哪里推翻了机器**。
    """

    status: str = ""                                   # approved / rejected / modified
    reviewer: str = ""
    comment: str = ""                                  # 你写的批注
    proposed_symbols: list[str] = Field(default_factory=list)
    effective_symbols: list[str] = Field(default_factory=list)
    dropped_symbols: list[str] = Field(default_factory=list)   # 你砍掉的
    added_symbols: list[str] = Field(default_factory=list)     # 你加上的
    diverged: bool = False


class JournalEntry(BaseModel):
    """一个意图。一行 = 一次提议，**不是**一笔成交。

    52 行历史里 error 24 + cancelled 16 —— "订单蒸发"才是这个系统最常见的真实结局，
    只记成交的日志会把它完全藏起来。

    计划部分在**审批之前**写入且此后不可改：结果出来之后写的计划已经不可能错，
    也就什么都证明不了。
    """

    entry_id: str                       # ＝ trades.client_order_id，两侧天然可 join
    cycle_id: str
    as_of: datetime
    symbol: str
    action: str
    source: str = ""                    # chief / pead-chief / manual
    setup: Setup = "unknown"

    # ── 计划（预登记，不可变）──────────────────────────────
    intended_notional: float | None = None
    intended_qty: float | None = None
    conviction: float = 0.0
    order_type: str = "limit"
    limit_price: float | None = None
    stop_price: float | None = None     # 只作声明，**不发挂单**：隔夜挂单在跳空股上
    target_price: float | None = None   #   保证最差成交
    planned_horizon_days: int | None = None
    invalidation: str = ""              # 什么**观察**会推翻论点（文字，不是价格）
    planned_risk_usd: float | None = None   # R 的分母；拿不到就 None，绝不猜
    risk_unit_source: RiskUnit = ""     # 不同口径的 R 不可混比
    rationale: str = ""                 # 入场 / 加仓 / 减仓 的理由

    # ── 决策时的环境（快照，不是外键）───────────────────────
    # 反规范化是有意的：日后重跑风控评审，不得回头改写我们当时相信的东西。
    regime_risk_state: str | None = None

    # ── 证据质量（agent 版的"是否凭感觉"）──────────────────
    ev_score_total: float | None = None
    ev_score_band: str | None = None
    ev_has_transcript: bool | None = None   # False ＝ 系统版的"凭感觉交易"
    ev_score_latency_h: float | None = None # 财报 → 打分的滞后
    ev_expected_move_pct: float | None = None

    # ── 关卡 ───────────────────────────────────────────
    risk_notes: list[str] = Field(default_factory=list)   # 仓位被削减/拦截的理由
    approval: ApprovalDivergence | None = None

    # ── 结果（执行后回填，**不得触碰上面的计划**）───────────
    terminal_status: str | None = None
    filled_qty: float | None = None
    avg_fill_price: float | None = None
    slippage_bps: float | None = None       # 相对我们要的限价，正数＝成本
    submit_attempts: int = 0


class TradeEpisode(BaseModel):
    """一个回合：净持仓 0 → 非 0 → 回到 0。加减仓是 leg，不是新回合。

    这正好修掉旧 analytics 把"分批减仓算 N 笔交易"的问题。
    """

    episode_id: str
    symbol: str
    direction: Literal["long", "short"] = "long"
    origin: EpisodeOrigin = "system"
    status: Literal["open", "closed"] = "open"

    # ── 事实（不自己做 FIFO 成本核算）───────────────────────
    # 实现盈亏一律取 IBKR 每笔 execution 的 realizedPNL 求和。与券商对账单不一致的
    # 日志，在最需要它的时候不可信。
    opened_at: datetime
    closed_at: datetime | None = None
    avg_entry: float | None = None
    avg_exit: float | None = None
    realized_pnl: float | None = None       # 仅已平仓
    unrealized_pnl: float | None = None     # 仅未平仓（按当前市价）
    commission: float | None = None
    basis_source: BasisSource = "observed_fills"

    # ── 绩效 ───────────────────────────────────────────
    holding_days: int | None = None
    r_multiple: float | None = None         # 已平仓的终局 R；无风险单位则 None
    r_multiple_mtm: float | None = None     # 未平仓：**若此刻平仓**的 R
    risk_unit_source: RiskUnit = ""
    mae_pct: float | None = None            # 最大逆向偏移
    mfe_pct: float | None = None            # 最大有利偏移
    mae_source: str = "daily_bars"          # 只有日线，不假装日内精度
    excess_vs_sector_pct: float | None = None

    # ── 与计划的对照（复盘的落点）──────────────────────────
    setup: Setup = "unknown"
    primary_entry_id: str = ""              # 开仓那笔意图
    exit_reason: ExitReason | None = None
    exit_as_planned: bool | None = None     # exit_reason ∈ {止盈,止损,论点失效,到期}

    # ── 未平仓专属：这两位产出的是**可行动**的结论，不只是教训 ──
    invalidation_triggered: bool | None = None   # 论点已失效但仓位还在 → 该退未退
    horizon_overdue_days: int | None = None      # 超过计划持有期多少天（drift 的实时形态）

    @property
    def decision_gradeable(self) -> bool:
        """能否用于决策质量统计。

        存量持仓没有预登记计划、没有 invalidation、没有意图记录 —— 混进决策质量
        统计会污染它。结果质量则不受影响（券商均价能算浮盈亏）。
        """
        return self.origin != "pre_tracking"


class Prediction(BaseModel):
    """一个可证伪的声明，记录于做出之时。

    可证伪性**不依赖于卖出**：打分说"后续漂移为正"，T+20 到了就能量超额收益，
    不管有没有持仓、有没有平仓。预测按**时钟**结算，不按成交结算。

    entry_id 为 None ＝ 有预测但没交易。这不是缺失数据 —— 被风控拦下、被人否决的
    那些事后走成什么样，正是判断"门槛设对没有""我的干预对不对"的唯一依据。
    """

    prediction_id: str
    made_at: datetime
    symbol: str
    source: PredictionSource
    ref_key: str                        # "GOOG:Q2 2026"
    kind: str
    predicted_value: float | None = None
    predicted_band: str = ""

    # 参考点是**可行动日**（打分日），不是财报日：财报跳空不可捕获 —— 看到结果之后
    # 没法按公告前收盘价成交，而 PEAD 本就定义为公告**之后**的漂移。
    ref_price: float | None = None
    ref_date: date | None = None
    print_date: date | None = None      # 财报日，仅作参考

    sector_etf: str = ""
    benchmark: str = ""
    entry_id: str | None = None


class PredictionOutcome(BaseModel):
    """一个周期的实现。四个周期**全部保留**。

    T+1 对而 T+20 错 ＝ 入场对、持有期错。压成一个结论就把这个发现丢了。
    """

    prediction_id: str
    horizon_days: int                   # 1 / 5 / 20 / 60
    as_of: date | None = None
    realized_pct: float | None = None
    excess_vs_sector_pct: float | None = None
    excess_vs_bench_pct: float | None = None


# --------------------------------------------------------------------------- #
# 投影层（不落库，现拼）
# --------------------------------------------------------------------------- #
class EpisodeCard(BaseModel):
    """一个回合的可读全貌 ＝ 回合 ⋈ 开仓意图 ⋈ 各 leg ⋈ 该季预测。

    不落库：每个字段都能从三张表拼出来，建表反而会引入不一致。

    两个消费方共用：
      · 交易复盘-<SYM>-<日期>.md   人读，含盈亏
      · critic 的 cases            LLM 读；判定论点是否失效时用 blind()
    """

    episode: TradeEpisode
    plan: JournalEntry | None = None                # 开仓时的计划，原文照录
    legs: list[JournalEntry] = Field(default_factory=list)   # 加/减/平各自的理由
    predictions: list[tuple[Prediction, list[PredictionOutcome]]] = Field(
        default_factory=list)

    def blind(self) -> "EpisodeCard":
        """抹掉一切结果信息的副本。

        判定"预登记的失效条件是否触发"时**必须**用这个。那是事实分类，不是叙事：
        给 LLM 看到盈亏，它就会从结果倒推出一个自洽的因果故事，对纯噪音也一样。
        把这条约定写成方法，是为了让它在类型上可强制，而不只是口头纪律。
        """
        ep = self.episode.model_copy(update={
            "realized_pnl": None, "unrealized_pnl": None,
            "r_multiple": None, "r_multiple_mtm": None,
            "mae_pct": None, "mfe_pct": None, "excess_vs_sector_pct": None,
            "avg_exit": None, "exit_reason": None, "exit_as_planned": None,
        })
        blind_preds = [(p, []) for p, _ in self.predictions]   # 只留声明，不留实现
        return EpisodeCard(episode=ep, plan=self.plan, legs=self.legs,
                           predictions=blind_preds)


# --------------------------------------------------------------------------- #
# 报告层（不落库，季度现算）—— critic agent 的契约
# --------------------------------------------------------------------------- #
FindingCategory = Literal[
    "calibration",     # 打分校准：模型预测有系统性偏差
    "holding",         # 持有期：入场对、退出错（或反之）
    "execution",       # 执行：滑点、时延、重试
    "human_gate",      # 人审闸：人的干预帮了还是害了
    "risk_gate",       # 风控闸：削减/拦截帮了还是害了
    "evidence",        # 证据质量：缺纪要的单表现如何
    "open_position",   # 当下就该处理的（区别于历史教训）
]


class EvidenceBlock(BaseModel):
    """一个**已经算完**的问题。critic 只解释，不计算。

    LLM 做算术既不可靠也不可复现；给它原始行，它会在噪音里找模式。
    """

    question: str                       # "打分 band 能否预测后续超额收益？"
    table: list[dict] = Field(default_factory=list)     # 已聚合好的行
    n_closed: int = 0                   # 已平仓样本
    n_open: int = 0                     # 未平仓样本（MTM）——两者永不合并成一个数
    n_min: int = 10
    counterexamples: list[str] = Field(default_factory=list)
    # 只给均值，LLM 会把 n=14 的均值当规律。强制附最强反例，它才会说"但有两次相反"。

    @property
    def sufficient(self) -> bool:
        return (self.n_closed + self.n_open) >= self.n_min


class CriticBrief(BaseModel):
    """critic 的输入：已算好的证据 + 代表性个案。"""

    period: str                         # "2026Q3"
    blocks: list[EvidenceBlock] = Field(default_factory=list)
    cases: list[EpisodeCard] = Field(default_factory=list)


class ProposedChange(BaseModel):
    """建议的改动。critic 只提，人来改 —— 系统绝不自动应用。

    n<30 的样本上自动调参一定过拟合，而且没有样本外集能发现它。
    """

    target: Literal["config", "skill", "playbook"]
    locator: str                        # "config/pead/COHR.yaml: long_threshold"
    current: str
    proposed: str
    expected_effect: str                # "已观测的 14 次中会多捕获 3 次，少踩 1 次"


class CriticFinding(BaseModel):
    """一条可被逐条采纳/驳回的发现。

    三段的**作者不同**，这是防后见之明的关键：observation 由确定性代码填、
    hypothesis 由 LLM 填。审阅时能一眼分清"这是事实"还是"这是它编的故事"。
    """

    finding_id: str
    category: FindingCategory

    # ── 确定性代码填写，LLM 不得改写 ──────────────────────
    observation: str                    # 事实陈述，必须含数字
    n: int = 0
    n_sufficient: bool = False
    evidence_ref: list[str] = Field(default_factory=list)   # episode_id / prediction_id

    # ── LLM 填写（唯一允许它生成的部分）────────────────────
    # n_sufficient=False 时报告**不渲染**这两项：样本不足时连让它猜的机会都不给。
    hypothesis: str = ""
    falsifier: str = ""                 # 什么观察会推翻这条 finding 本身

    proposed_change: ProposedChange | None = None
    confidence: Literal["强", "弱", "仅供观察"] = "仅供观察"
