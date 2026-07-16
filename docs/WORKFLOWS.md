# Workflows 与触发条件

> 状态：v0.3 · 2026-07-16 · 配套实现：`graph/chief.py`（决策图）+ `runtime/scheduler.py`（触发路由）

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
| I5 | 绩效/风控快照 | 每交易日盘后 | risk_state≠normal → 飞书告警 | 确定性代码（无 LLM） | 普通函数 |

图化取舍：LangGraph 的价值 = interrupt/checkpoint 跨进程恢复 + 多节点编排。只有决策图
（有审批 interrupt）和 PEAD 图（多节点 LLM 链）图化；I1/I2/I3/I5 保持函数，不过度工程。

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

每交易日 cron（mon-fri + NYSE session 过滤），`_daily` 级联顺序：

```
宏观周报(周一) → 行业周报(周一) → 事件触发(events.yaml) →
PEAD(research → 逐 target monitor/prep/score) → 绩效+风控快照 → Chief 收口(末位)
```

Chief 排末位：读当日全部新鲜产出后决策。安静日零决策 → 图在 boss_review 前结束，
不发审批卡（零打扰）。

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
| `ats trader buy/sell SYM QTY [--limit PX]` | T4 手动单（经同一风控+审批） |
| `ats trader execute [SYM]` | T4 存量建议（decisions 表）重放 |
| `ats schedule` / `ats schedule --now` | T2 每日 cron / 立即跑一轮级联 |
| `ats serve` | webhook：飞书回调恢复 checkpoint 线程 |

## 7. dry_run / --live / --yes 语义

- **dry_run 是默认**（`--live` 显式开启实单）。dry-run 走完整链（风控、审批、落库
  cancelled 记录），但绝不构造 IBKRBroker。
- **`--yes`（auto_approve）永不默认**：跳过 interrupt 直接 approved（reviewer="auto"），
  仅限无人值守 dry-run 冒烟测试；**实盘环境禁用**——Boss 审批闸口是唯一安全机制。
- `--no-execute`：chief 决策+落库后停（persist_decision → END），供只看决策不下单。
- `--offline`：跳过 IBKR 读取（风控层降级为 "risk checks skipped" 备注）。
