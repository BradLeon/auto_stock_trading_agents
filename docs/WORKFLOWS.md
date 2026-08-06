# Workflows 与触发条件

> 状态：v0.4 · 2026-08-06（新增产业链证据 I7-I9） · 配套实现：`graph/chief.py`（决策图）+ `runtime/scheduler.py`（触发路由）

系统只有两类 workflow：**信息型**（更新知识库，永不触碰 broker）与**交易型**（产生订单，
全部汇入同一张 chief 决策图）。触发条件分**周期型**（每日/每周 cron）与**事件型**
（财报日历自动 + `config/events.yaml` 手工日历）。

## 1. 信息型 workflow（不交易）

| # | Workflow | 周期触发 | 事件触发 | 角色链 | 形态 |
|---|---|---|---|---|---|
| I1 | 每日情报流 | 每交易日盘后 | 高置信 research insight → 飞书即时 push | research_extract → news_triage → context_monitor（逐 PEAD target） | 普通函数（线性、无审批、廉价） |
| I2 | 宏观周报 | 每周一 | FOMC / CPI / NFP 当日（events.yaml → `macro`） | macro_strategist | 单 agent 函数 |
| I3 | 行业周报 | 每周一（I2 之后，读新鲜 regime） | 产业链重磅事件 / 龙头财报 read-through（events.yaml → `sector:NAME`） | sector_analyst | 单 agent 函数 |
| I4 | PEAD prep | — | 财报 T-3（earnings calendar 自动） | pead_analyst ×3 + industry_analyst | LangGraph（`graph/pead.py` prep 分支） |
| I5 | 技术面读数 | 每交易日（PEAD 之后、Chief 之前） | — | 确定性代码（**无 LLM**）：7 点动量评分 + VIX 调节 + 期限结构/破位两层 | 普通函数 |
| I6 | 绩效/风控快照 | 每交易日盘后 | risk_state≠normal → 飞书告警 | 确定性代码（无 LLM） | 普通函数 |
| I7 | 产业链证据观察 | — | observe 名单公司财报发布（与打分同一套双重确认） | evidence_observer | 普通函数（只写证据表） |
| I8 | 命题印证 + 截面重排 | 每周（跟在 I3 行业周报之后） | — | 确定性三道闸 → structure_analyst → 截面 | 普通函数 |
| I9 | 涌现命题归纳 | **无周期** | 未归属观测积累到门槛 | claim_proposer | 普通函数（只产待确认卡） |

（原 I5 绩效/风控快照顺延为 I6。）

### 求证工作全流程（I7 → I8 → I9）

```
观察名单公司发财报（8-K 双重确认）
  → I7 抽取观测：实体/维度/方向/**原文片段**，落 evidence_observations
       · 语义归属到命题维度（不是字符串匹配）
       · 用 signal_chain 解析"我们最大的内存合作伙伴"这类指代
       · 归不上任何维度 → 留空，进未映射池（I9 的原料）
  ↓
每周行业评审之后
  → I8 三道闸聚合（确定性，无 LLM）：去重 → 立场 → common/relative 隔离
       → relative 结论 → moat_pricing 证据包 → structure_analyst
       → 截面重排 → basket
       → Chief 上下文（只给"量化第N→复合第M + 依据"，**不给权重数字**）
  ↓
未映射池攒够（≥6 条观测 × ≥3 个来源）
  → I9 归纳一条候选命题 → 待确认卡 → **你审阅**
       → 采纳：你手工写进 config；拒绝：signature 进冷却期
```

**三个节点都不产生订单**，全程不碰 broker。

**I7 的边界**：observe 名单（`config/pead.yaml: observe`）是**覆盖**，不是**可交易**。
这些公司我们大多不持有，读它们是因为利润是流动的——HBM 的供需由海力士/美光/三星
三家共同决定。它们只留下带原文片段的观测，**不打分、不建档案、不触发 Chief、
绝不进下单路径**（`tests/test_scheduler_jobs.py` 直接断言这条边界）。
设计见 [`docs/CHAIN_EVIDENCE.md`](CHAIN_EVIDENCE.md)。

图化取舍：LangGraph 的价值 = interrupt/checkpoint 跨进程恢复 + 多节点编排。只有决策图
（有审批 interrupt）和 PEAD 图（多节点 LLM 链）图化；I1/I2/I3/I5/I6 保持函数，不过度工程。

## 2. 交易型 workflow（产生订单）

**所有下单动作汇入同一张 chief 决策图**——全系统只有一个审批闸口。

| # | Workflow | 触发 | 入口 | source | thread 前缀 |
|---|---|---|---|---|---|
| T1 | 财报事件交易（主 α 环） | 财报 T+0/T+1，transcript 就绪 | `ats pead score SYM --chief` / scheduler | `pead-chief` | `chief-` |
| T2 | 每日收口 | 每交易日盘后（**无条件**，调度末位） | scheduler `_chief_daily` | `scheduled` | `chief-` |
| T3 | 手动 chief | 人工 `ats chief run` | 决策图（decide=True） | `chief` | `chief-` |
| T4 | 手动/存量指令 | 人工 `ats trader execute / buy / sell` | 决策图（decide=False，seed_decisions） | `stored-decisions` / `manual` | `trader-` |

## 3. chief 决策图（`graph/chief.py`）

```
START → assemble_context → chief_decide → risk_gate → persist_decision
persist_decision → (route) → boss_review | END      # 零决策 / --no-execute → 提前结束
boss_review(interrupt) → trader → persist → END
```

设计不变式：
- **风控在审批之前**：Boss 卡片上看到的是 6 层风控过滤/裁剪后的决策 + 风控备注。
- **决策先落库再审批**：`persist_decision` 在 interrupt 之前——Boss 不点卡片，
  决策与完整上下文也已在 `cycles`/`decisions` 表留档。
- **审批 interrupt 是唯一人工闸口**：`boss_review` 之外无第二个审批点。
- source ∈ (`chief`, `scheduled`, `pead-chief`) 才写 decisions 表（chief 自己的决策）；
  `manual`/`stored-decisions` 跳过（避免重复行），但 trades/fills 照常落库。
- thread_id == cycle_id（`chief-YYYYMMDD-HHMMSS` / `trader-YYYYMMDDHHMMSS`），
  checkpoint 恢复据此路由。

## 4. 审批流（同步 / 异步）

- **同步（CLI）**：`run_decision_graph` 进程内循环 interrupt → 终端问答 → resume，
  一次命令跑完整链。
- **异步（feishu / feishu_bot）**：图在 interrupt 处 checkpoint（SqliteSaver，
  `var/checkpoints.sqlite`，`ATS_CHECKPOINT_DB` 可覆写）并退出，卡片发到手机；
  `ats serve` webhook 收到 Approve/Reject 回调后 `resume_cycle(thread_id, approval)`
  重建决策图恢复执行，回填成交推送。

## 5. 触发路由（`runtime/scheduler.py`）

共三个 cron job（mon-fri + NYSE session 过滤），全部串行（单 worker executor —
它们共用同一个 sqlite 连接）：

| job | 时点（ET） | 内容 |
|---|---|---|
| `daily_cycle` | `settings.yaml` `schedule.run_at`（10:30） | 下方级联 |
| `pead_score_amc` | `pead.yaml` `score_windows.amc`（20:00） | 当晚盘后财报的打分 |
| `pead_score_bmo` | `pead.yaml` `score_windows.bmo`（11:00） | 当日盘前财报的打分 |

`daily_cycle` 级联顺序：

```
事件触发(events.yaml) → PEAD(research → 逐 target monitor/prep) →
技术面读数(逐标的评分/建议敞口) → 每日情报 digest →
绩效+风控快照 → 交易日志 marks → Chief 收口(末位)

注：宏观周报/行业周报已移出交易日级联，改为独立的周六 job（`weekly_review`）。
```

Chief 排末位：读当日全部新鲜产出后决策。安静日零决策 → 图在 boss_review 前结束，
不发审批卡（零打扰）。

### PEAD 打分窗口（`pead_score_window`）

打分**不在**日级联里，也不靠预测日期 —— 它由**观测到的财报**触发。原因见
`data/earnings_calendar.py` 模块 docstring：数据商的财报日期会被修订，且
Finnhub 的 `hour`（盘前/盘后）在 13 个标的里有 5 个是空的。

判定链：
1. `last_print()` 找出 lookback 窗口内已发生的财报（Finnhub 向后窗口 ∪ yfinance）
2. `_confirm_reported()` 确认真的发布了：实际 EPS，**或** 申报日 ≥ 财报日的 8-K
   （8-K 在发布后几分钟就有，这才让"当晚打分"可行；早于财报日的一律拒绝）
3. `_score_plan()` 决定动作 —— `score` / `promote` / 不动。session 判不出来的标的
   **两个窗口都试**，靠 `pead_score_runs` 台账保证只打一次
4. 打分后：**只有终版**才交给 Chief（一个窗口一张审批卡）

v1/v2：拿不到本季纪要时先按财报稿/8-K 打 v1 —— 入库但**不惊动 Chief**，权重剔除
只有电话会才能提供的维度并重新归一（记 0 分会把 v1 往"中性"拖），仓位减半。纪要
到位打 v2 → 交给 Chief；`transcript_upgrade_days` 到期仍无纪要则把 v1 提升为终版。
这样一个季度只被 Chief 消费一次，`score_consumption` 语义不变。

盘后窗口的单是隔夜审批的，提交前会按最新价 ±`overnight_limit_slippage_pct`
改成**限价单**（在生成审批卡之前改，所以卡上的价格就是会提交的价格）。

`score_windows_live: false` 时窗口强制 dry-run（即使 daemon 带 `--live`）：打分、
Chief、审批卡照常，但没有单会到券商。

同一个窗口的末尾还跑一遍 **observe 名单**（`_observe_window`）——**独立循环，刻意
不与 targets 合并**：它复用第 2 步同一套 8-K 双重确认（否则会把上一季的数字当成新
证据读进来，而陈旧数字正是伪印证的来源），但只把财报抽成产业链证据，不进 `scored`、
不触发 Chief、不产生任何订单。

### 涌现命题的触发（`chain/induction.py`）

**没有 cron，纯时间流逝不触发。** 门槛是确定性的，不过门连模型都不调用：

| 条件 | 默认 | 为什么 |
|---|---|---|
| 未归属观测数 | ≥ 6 | 少于此只是零星噪音，不成模式 |
| 不同来源数 | ≥ 3 | 一家公司说得再多也只是一家在说 |
| 冷却期 | 30 天 | 被拒的候选不得换个说法下周再来；signature 取自**证据指纹**（实体+指标）而非措辞 |

触发后立刻把那批观测**冻结为 discovery evidence**——发现某命题的材料永远不能再充当
它成立的证据。这道闸没有的话，agent 可以从一个模式归纳出命题、再拿同一批材料自证。

事件日历 `config/events.yaml`（date/kind/label/triggers）：
- `macro` → 宏观策略师额外跑一次（FOMC/CPI/NFP/政府报告）
- `sector` / `sector:NAME` → 行业分析师（行业会议/产品发布会/龙头财报 read-through）
- `pead:SYM` → 该标的额外 monitor
每季度人工补下季日期（`ats events upcoming` 提示过期）；财报日历已自动
（`data/earnings_calendar.py` 驱动 prep/score 时点）。

## 6. CLI 入口 → 决策图

| 命令 | 说明 |
|---|---|
| `ats chief run` | T3 手动收口；`--no-llm --offline` 走 stub 全链（测试接线） |
| `ats pead score SYM --chief` | T1：score 建议落库后立即 chief 收口 |
| `ats pead scorewindow --window amc\|bmo` | 手工跑一个打分窗口；`--plan-only` 只看路由决策、`--as-of ISO` 回拨日历重放历史财报、`--no-chief` 不推审批 |
| `ats pead transcriptprobe [--quarters N]` | 审计 transcript 检索：对各标的最近 N 季核对取到的是否本季（验收标准：错季 = 0） |
| `ats pead show SYM` | 含打分台账（v1/v2、是否有纪要、是否终版、距财报几小时） |
| `ats schedule --window amc\|bmo` | 跑单个窗口后退出（daemon 之外的手工触发） |
| `ats technical review\|show\|probe` | 技术面读数（确定性无 LLM）；`probe` 不落库不写报告 |
| `ats trader buy/sell SYM QTY [--limit PX]` | T4 手动单（经同一风控+审批） |
| `ats trader execute [SYM]` | T4 存量建议（decisions 表）重放 |
| `ats schedule` / `ats schedule --now` | T2 每日 cron / 立即跑一轮级联 |
| `ats serve` | webhook：飞书回调恢复 checkpoint 线程 |
| `ats evidence observe SYM [--file F]` | I7：手动读一次某公司财报抽成证据（`--file` 用本地文档） |
| `ats evidence show [--entity X]` | 看已入库观测（说话人/关于谁/归属维度/原文）与最近抽取失败 |
| `ats evidence claims` | I8：看各命题的印证结论、覆盖率、异议方、**未发声证人** |
| `ats sector crosssection NAME --layer K --structure` | I8：手动跑截面（周报后自动跑，带结构层） |
| `ats evidence propose` | I9：跑一次归纳（门槛不过则不调用模型，只打印判定） |
| `ats evidence proposals` | 列出待确认/已采纳/已拒绝的候选命题 |
| `ats evidence review ID --accept\|--note "..."` | 审批候选命题（默认拒绝；采纳后仍需你手工写进配置） |

## 7. dry_run / --live / --yes 语义

- **dry_run 是默认**（`--live` 显式开启实单）。dry-run 走完整链（风控、审批、落库
  cancelled 记录），但绝不构造 IBKRBroker。
- **`--yes`（auto_approve）永不默认**：跳过 interrupt 直接 approved（reviewer="auto"），
  仅限无人值守 dry-run 冒烟测试；**实盘环境禁用**——Boss 审批闸口是唯一安全机制。
- `--no-execute`：chief 决策+落库后停（persist_decision → END），供只看决策不下单。
- `--offline`：跳过 IBKR 读取（风控层降级为 "risk checks skipped" 备注）。
