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
    "unknown",          # 未分类（不要用正则从中文 rationale 里猜）
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
    "pre_tracking",  # 全部来自日志上线前的存量持仓（无入场记录，只能用券商均价）
    "mixed",         # 混合：系统/手工/存量里至少两种都有
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

    status: str = ""                    # 裁决结果：approved / rejected / modified
    reviewer: str = ""                   # 审批人（一般就是你）
    comment: str = ""                    # 你写的批注（自由文本，最原始的干预记录）
    proposed_symbols: list[str] = Field(default_factory=list)
    # agent 最初提议动手的标的全集（未经你筛选）
    effective_symbols: list[str] = Field(default_factory=list)
    # 裁决之后实际会执行的标的全集（经你筛选/覆盖/追加后的结果）
    dropped_symbols: list[str] = Field(default_factory=list)
    # 你砍掉的：在 proposed 里、但不在 effective 里
    added_symbols: list[str] = Field(default_factory=list)
    # 你加上的：不在 proposed 里、却出现在 effective 里（direct_instructions）
    diverged: bool = False
    # 是否与 agent 的原始提议不同：dropped/added 非空，或有 override，或非 approved


class JournalEntry(BaseModel):
    """一个意图。一行 = 一次提议，**不是**一笔成交。

    52 行历史里 error 24 + cancelled 16 —— "订单蒸发"才是这个系统最常见的真实结局，
    只记成交的日志会把它完全藏起来。

    计划部分在**审批之前**写入且此后不可改：结果出来之后写的计划已经不可能错，
    也就什么都证明不了。
    """

    entry_id: str                       # ＝ trades.client_order_id，两侧天然可 join
    cycle_id: str                       # 属于哪一次 Chief 决策会话（审计单位）
    as_of: datetime                     # 提议产生的时刻
    symbol: str                         # 标的代码
    action: str                         # buy / add / trim / sell / hold
    source: str = ""                    # 提议来源：chief / pead-chief / manual
    setup: Setup = "unknown"            # 策略归类，按类别统计期望值要靠它

    # ── 计划（预登记，不可变）──────────────────────────────
    intended_notional: float | None = None   # 打算下多大金额（美元）
    intended_qty: float | None = None        # 打算下多少股（金额与股数二选一或都填）
    conviction: float = 0.0                  # 信心 0..1，越高表示越确定
    order_type: str = "limit"                # market / limit
    limit_price: float | None = None         # 限价单的价格
    stop_price: float | None = None     # 只作声明，**不发挂单**：隔夜挂单在跳空股上
    target_price: float | None = None   #   保证最差成交
    planned_horizon_days: int | None = None  # 打算持有多少个交易日
    invalidation: str = ""              # 什么**观察**会推翻论点（文字，不是价格）
    planned_risk_usd: float | None = None   # R 的分母（美元）；拿不到就 None，绝不猜
    risk_unit_source: RiskUnit = ""     # 上面那个分母是怎么算出来的；不同口径的 R 不可混比
    rationale: str = ""                 # 入场 / 加仓 / 减仓 的理由（自由文本）

    # ── 决策时的环境（快照，不是外键）───────────────────────
    # 反规范化是有意的：日后重跑风控评审，不得回头改写我们当时相信的东西。
    regime_risk_state: str | None = None     # 决策那一刻的组合风控状态（如 normal/derisk）

    # ── 证据质量（agent 版的"是否凭感觉"）──────────────────
    ev_score_total: float | None = None      # 当时的 PEAD 打分卡总分
    ev_score_band: str | None = None         # 打分卡给出的定性档位（如"达到做多门槛"）
    ev_has_transcript: bool | None = None    # False ＝ 系统版的"凭感觉交易"（无电话会纪要）
    ev_score_latency_h: float | None = None  # 财报 → 打分的滞后小时数
    ev_expected_move_pct: float | None = None  # 期权隐含的预期波动幅度（%）

    # ── 关卡 ───────────────────────────────────────────
    risk_notes: list[str] = Field(default_factory=list)   # 仓位被削减/拦截的理由（逐条）
    approval: ApprovalDivergence | None = None             # 人审的完整裁决与分歧记录

    # ── 结果（执行后回填，**不得触碰上面的计划**）───────────
    terminal_status: str | None = None       # 订单最终状态：filled/cancelled/error/expired…
    filled_qty: float | None = None          # 实际成交的股数
    avg_fill_price: float | None = None      # 实际成交均价
    slippage_bps: float | None = None       # 相对我们要的限价，正数＝成本（基点）
    submit_attempts: int = 0                 # 提交重试了几次（IBKR 掉线等）


class TradeEpisode(BaseModel):
    """一个回合：净持仓 0 → 非 0 → 回到 0。加减仓是 leg，不是新回合。

    这正好修掉旧 analytics 把"分批减仓算 N 笔交易"的问题。
    """

    episode_id: str                     # 回合唯一标识
    symbol: str                         # 标的代码
    direction: Literal["long", "short"] = "long"   # 多头 / 空头
    origin: EpisodeOrigin = "system"    # 这个回合的单是系统下的、手工下的，还是存量持仓
    status: Literal["open", "closed"] = "open"     # 是否已平仓

    # ── 事实（不自己做 FIFO 成本核算）───────────────────────
    # 实现盈亏一律取 IBKR 每笔 execution 的 realizedPNL 求和。与券商对账单不一致的
    # 日志，在最需要它的时候不可信。
    opened_at: datetime                 # 开仓时刻（净持仓由 0 变为非 0）
    closed_at: datetime | None = None   # 平仓时刻（净持仓回到 0）；未平仓则为 None
    avg_entry: float | None = None      # 平均建仓成本
    avg_exit: float | None = None       # 已减仓部分的平均卖出/买回价（尚无减仓则为 None）
    realized_pnl: float | None = None       # 累计已实现盈亏（美元）——哪怕仍未平仓，
                                             # 之前减仓部分也计入；来自 IBKR 逐笔求和
    unrealized_pnl: float | None = None     # 仅剩余未平部分：按当前市价算的浮动盈亏
                                             # （已完全平仓则为 None）
    commission: float | None = None         # 该回合累计佣金
    basis_source: BasisSource = "observed_fills"   # 成本价来自观测成交还是券商均价

    # ── 绩效 ───────────────────────────────────────────
    holding_days: int | None = None         # 实际持有了多少个交易日
    r_multiple: float | None = None         # 已平仓的终局 R（盈亏/计划风险）；无风险单位则 None
    r_multiple_mtm: float | None = None     # 未平仓：**若此刻平仓**会是多少 R
    risk_unit_source: RiskUnit = ""         # R 的分母是怎么定的（与开仓时一致）
    mae_pct: float | None = None            # 最大逆向偏移（持有期内最不利的浮亏百分比）
    mfe_pct: float | None = None            # 最大有利偏移（持有期内最有利的浮盈百分比）
    mae_source: str = "daily_bars"          # 只有日线，不假装日内精度
    excess_vs_sector_pct: float | None = None   # 相对板块 ETF 的超额收益（%）

    # ── 与计划的对照（复盘的落点）──────────────────────────
    setup: Setup = "unknown"                # 这个回合属于哪一类策略
    primary_entry_id: str = ""              # 开仓那笔意图的 entry_id
    exit_reason: ExitReason | None = None   # 退出原因分类
    exit_as_planned: bool | None = None     # exit_reason ∈ {止盈,止损,论点失效,到期} 时为 True

    # ── 未平仓专属：这两位产出的是**可行动**的结论，不只是教训 ──
    invalidation_triggered: bool | None = None   # 论点已失效但仓位还在 → 该退未退（每周判定）
    horizon_overdue_days: int | None = None      # 超过计划持有期多少天（drift 的实时形态）

    @property
    def decision_gradeable(self) -> bool:
        """能否用于决策质量统计。

        真正的判据是"有没有预登记计划可对照"，即 primary_entry_id 是否指向一条
        真实的 JournalEntry —— 而不是 origin 是否为 pre_tracking。存量持仓固然没有
        计划，但**手工单同样没有**（manual 走的下单路径绕过 persist_decision，
        参见 Stage 1c），origin='manual' 或 'mixed' 不代表就有计划可评。
        混进决策质量统计会污染它；结果质量则不受影响（券商均价能算浮盈亏）。
        """
        return bool(self.primary_entry_id)


class Prediction(BaseModel):
    """一个可证伪的声明，记录于做出之时。

    可证伪性**不依赖于卖出**：打分说"后续漂移为正"，T+20 到了就能量超额收益，
    不管有没有持仓、有没有平仓。预测按**时钟**结算，不按成交结算。

    entry_id 为 None ＝ 有预测但没交易。这不是缺失数据 —— 被风控拦下、被人否决的
    那些事后走成什么样，正是判断"门槛设对没有""我的干预对不对"的唯一依据。
    """

    prediction_id: str                  # 预测唯一标识
    made_at: datetime                   # 做出这个预测的实际时刻（系统时间）
    symbol: str                         # 标的代码
    source: PredictionSource            # 预测来自哪种信号：打分卡/期权隐含波动/卖方目标价
    ref_key: str                        # 人可读的季度标识，如 "GOOG:Q2 2026"
    kind: str                           # 预测的具体内容，如 drift_direction / abs_move_pct
    predicted_value: float | None = None   # 预测的数值
    predicted_band: str = ""               # 预测的定性档位（如打分卡给出的 band）

    # 参考点是**可行动日**（打分日），不是财报日：财报跳空不可捕获 —— 看到结果之后
    # 没法按公告前收盘价成交，而 PEAD 本就定义为公告**之后**的漂移。
    ref_price: float | None = None      # 可行动日的收盘价（后续超额收益的基准价）
    ref_date: date | None = None        # 可行动日（我们知道答案且能动手的第一个交易日）
    print_date: date | None = None      # 财报日，仅作参考，不参与收益计算

    sector_etf: str = ""                # 用哪个 ETF 算板块超额收益
    benchmark: str = ""                 # 用哪个指数算基准超额收益
    entry_id: str | None = None         # 若据此下了单，指向对应的 JournalEntry；否则为 None


class PredictionOutcome(BaseModel):
    """一个周期的实现。四个周期**全部保留**。

    T+1 对而 T+20 错 ＝ 入场对、持有期错。压成一个结论就把这个发现丢了。
    """

    prediction_id: str                  # 对应哪个预测
    horizon_days: int                   # 周期：1 / 5 / 20 / 60（交易日）
    as_of: date | None = None           # 该周期实际落到的交易日
    realized_pct: float | None = None   # 标的自身的实现涨跌幅（%）
    excess_vs_sector_pct: float | None = None   # 相对板块 ETF 的超额收益（%）
    excess_vs_bench_pct: float | None = None    # 相对基准指数的超额收益（%）


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

    episode: TradeEpisode               # 这个回合的全部事实与绩效
    plan: JournalEntry | None = None    # 开仓时的计划，原文照录
    legs: list[JournalEntry] = Field(default_factory=list)
    # 该回合内每一次加仓/减仓/平仓各自的意图（各自的理由都在里面）
    predictions: list[tuple[Prediction, list[PredictionOutcome]]] = Field(
        default_factory=list)
    # 与该标的/该季度相关的预测，及其在各个周期上的实现结果

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

    question: str                       # 这个证据块回答什么问题，如"打分 band 能否预测后续超额收益？"
    table: list[dict] = Field(default_factory=list)     # 已聚合好的行（现算的统计表）
    n_closed: int = 0                   # 已平仓样本数
    n_open: int = 0                     # 未平仓样本数（MTM）——两者永不合并成一个数
    n_min: int = 10                     # 达到多少样本才认为结论可信
    counterexamples: list[str] = Field(default_factory=list)
    # 最强的 2-3 个反例（episode_id/prediction_id）。
    # 只给均值，LLM 会把 n=14 的均值当规律。强制附最强反例，它才会说"但有两次相反"。

    @property
    def sufficient(self) -> bool:
        return (self.n_closed + self.n_open) >= self.n_min


class CriticBrief(BaseModel):
    """critic 的输入：已算好的证据 + 代表性个案。"""

    period: str                         # 复盘周期标识，如 "2026Q3"
    blocks: list[EvidenceBlock] = Field(default_factory=list)   # 这次复盘涉及的全部证据块
    cases: list[EpisodeCard] = Field(default_factory=list)      # 供 LLM 参考的代表性个案


class ProposedChange(BaseModel):
    """建议的改动。critic 只提，人来改 —— 系统绝不自动应用。

    n<30 的样本上自动调参一定过拟合，而且没有样本外集能发现它。
    """

    target: Literal["config", "skill", "playbook"]   # 改动对象类型：配置文件/skill 提示词/操作手册
    locator: str                        # 具体改哪里，如 "config/pead/COHR.yaml: long_threshold"
    current: str                        # 现在的值
    proposed: str                       # 建议改成的值
    expected_effect: str                # 预期效果（带 n），如"已观测的 14 次中会多捕获 3 次，少踩 1 次"


class CriticFinding(BaseModel):
    """一条可被逐条采纳/驳回的发现。

    三段的**作者不同**，这是防后见之明的关键：observation 由确定性代码填、
    hypothesis 由 LLM 填。审阅时能一眼分清"这是事实"还是"这是它编的故事"。
    """

    finding_id: str                     # 发现唯一标识
    category: FindingCategory           # 属于哪一类问题

    # ── 确定性代码填写，LLM 不得改写 ──────────────────────
    observation: str                    # 事实陈述，必须含数字
    n: int = 0                          # 支撑这条发现的样本量
    n_sufficient: bool = False          # 样本量是否达到该类别的最小要求
    evidence_ref: list[str] = Field(default_factory=list)
    # 可回溯的原始证据 id（episode_id / prediction_id），供人核实

    # ── LLM 填写（唯一允许它生成的部分）────────────────────
    # n_sufficient=False 时报告**不渲染**这两项：样本不足时连让它猜的机会都不给。
    hypothesis: str = ""                # 为什么会这样（可能错，明确标注是假设）
    falsifier: str = ""                 # 什么观察会推翻这条 finding 本身

    proposed_change: ProposedChange | None = None   # 建议的具体改动（可为空，仅供观察）
    confidence: Literal["强", "弱", "仅供观察"] = "仅供观察"   # 这条发现本身有多可信
