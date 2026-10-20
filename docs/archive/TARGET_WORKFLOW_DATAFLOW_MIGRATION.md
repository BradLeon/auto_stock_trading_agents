# Workflow / Dataflow 迁移史（从 TARGET_WORKFLOW_DATAFLOW.md 移出）

> 2026-10-20 从 [`docs/TARGET_WORKFLOW_DATAFLOW.md`](../TARGET_WORKFLOW_DATAFLOW.md) 原样移出的四节：切换前的实现映射（§5.4）、
> 当前实现盘点与差距（§6）、分阶段迁移方案及各阶段实施状态（§14）、2026-09-22 的测试基线（§15.1）。
> 这些内容记录的是迁移过程，迁移完成后不再代表现状；正文保留原章节号，只写现状并指回这里。
> 文中 Phase A–F 的 OpenSpec change 均已归档在 `openspec/changes/archive/`。

## §5.4 与当前实现的映射

当前 `src/ats/graph/chief.py` 是单次通过：

```text
chief_decide → risk_gate → persist_decision → boss_review → trader
```

`risk_gate` 会直接过滤或裁剪 `state.decisions`，但不会将流程路由回主理人。因此，实现目标
Loop 时建议做以下演进：

1. 在 `ChiefDecisionState` 增加 `revision_no`、`decision_hash`、`risk_round`、
   `risk_review`、`portfolio_snapshot_id`、`max_risk_rounds` 和 `cycle_status`。
2. 将 `risk_gate` 的输出从 `decisions + risk_notes` 升级为结构化 `RiskReview`，保留原提案，
   不在原对象上静默覆盖。
3. 新增 `chief_revise` 节点，并在 `risk_gate` 后使用 conditional edge：通过去 Boss，驳回去
   `chief_revise`，超过轮次上限去人工复核。
4. 把“保存决策”拆成按轮保存 revision 和保存 risk review；消费 PEAD 信号、生成最终报告等
   一次性副作用只在 cycle 进入终态时执行，不能每轮重复。
5. 继续使用 LangGraph checkpointer 解决暂停和跨进程恢复，但将它视为运行恢复设施；正式审计
   记录仍写入领域数据库，不能把 checkpoint 当成永久交易账本。
6. 


建议的图节点变为：

```text
assemble_context
  → chief_decide
  → persist_revision
  → risk_gate
      ├─ reject  → persist_risk_review → chief_revise ─┐
      │                                                └→ persist_revision → risk_gate
      ├─ approve → persist_risk_review → boss_review → trader
      └─ exhausted → manual_review → END
```


## §6. 当前实现盘点与差距

本节记录目标设计与当前仓库之间的映射。“保留”表示能力和大部分实现可以继续使用，
不表示完全不需要改动接口、依赖边界或存储方式。

| 组件 | 处置 | 当前基础 | 目标变化 |
|---|---|---|---|
| Macro Analyst | 保留 | 已有宏观数据组装、LLM 分析和 `MacroReview` | 取消其作为 Sector、Fundamental 和 Risk 的上游观点来源 |
| Technical Analyst | 保留 | 已有量价和风险信号 | 实时数据统一经 Runtime Data Gateway 读取 |
| PEAD 核心 | 保留并重构 | 已有 prep、score、dossier、expectation、scorecard 和财报窗口 | 拆为例行预期更新和财报事件分析 |
| 确定性风险引擎 | 保留 | `risk/assess.py`、`risk/marginal.py` 和 `risk/checks.py` 已提供多维风险计算 | 只负责硬规则和交易后模拟，不静默修改订单 |
| Trader / IBKR | 保留并收紧 | 已有下单、order reference 和部分幂等能力 | 只执行绑定完整审批链的 revision |
| Journal、绩效、reconcile | 保留并整合 | `journal/`、Trader reconcile 和 performance 已覆盖大部分计算 | 由 Clerk 统一编排、对账和发布 |
| 数据平台 | 保留 | 已有结构化、非结构化、数据产品、准入和 quarantine | 补齐统一 Agent 读取边界和事件日历 |
| Layer Analyst | 拆出并重构 | `agents/sector/layer_review.py` 和横截面分析可复用 | 只输出产业层级、标的对比和证据，不给配置结论 |
| Sector Analyst | 重构 | 已有 cross-section、structure、rotation 和报告 | 独占行业、层次、标的三级配置权 |
| Information Analyst | 新建独立角色 | 能力散落在 PEAD research、triage、monitor 和 digest | 形成独立输入、输出、存储和调度契约 |
| Fundamental Analyst | 重构依赖 | 已有较完整 PEAD 实现 | 只允许读取 Information 投影及共享事实，不执行最终交易风控 |
| Chief | 重构 | `graph/chief.py` 已有一次决策和 Boss interrupt | 成为唯一观点汇总者，产生不可变 revision |
| Risk Officer | 重构 | 已有组合风险 memo 和确定性 pre-trade check | 增加结构化 transaction review、counterproposal 和多轮 Loop |
| Clerk | 新建确定性服务 | 尚无独立角色 | 整合审批、订单、成交、绩效、归因和背景记录 |
| Scheduler | 重构 | `runtime/scheduler.py` 是单 worker 的硬编码串行流程 | 替换为 Dispatcher、Schedule Calendar 和 Trigger Ledger |

### 6.1 已确认的当前冲突

1. Sector 当前会读取 Macro、PEAD 和其他研究观点，与独立性约束冲突。
2. PEAD 当前会读取 Sector 和 Macro 报告，并在 Chief 之前提前执行交易风控。
3. Risk Officer 的组合报告当前会使用 Macro 观点，目标实现中应只依赖决策、组合、市场和规则。
4. Chief—Risk 当前只有单次通过；`risk_gate` 会过滤或裁剪原决策，不会返回 Chief 修订。
5. Boss 当前可以在风控后修改或新增指令，新内容可能绕过风控。
6. Scheduler 当前硬编码串行调用；共享 SQLite 连接也不支持可靠的真并发写入。
7. Information 能力散落且为 PEAD 服务，没有独立 Task Projection、保留策略和调度入口。
8. Clerk 尚不存在，相关能力分散于 Trader、Journal、reconcile 和 performance。
9. `TradingMemory` 采用进程内缓存的共享 SQLite 连接，限制了调度器的并发模型。


## §14. 分阶段迁移方案

### 14.1 阶段 A：稳定基线与契约

- 把项目既有的 `uv` 完整依赖入口固定为唯一的环境与测试入口，并把测量条件随基线数字一并记录。
- 完成数据层写侧 cutover：补齐数据层证据表的写入侧 schema 与写入接口，把证据写入从已被边界
  归类删除的旧表改指数据层。读侧早已完成，缺的正是这一半。
- 修复 PEAD action 枚举大小写漂移和期权到期桶边界问题。
- 建立 Agent 依赖和数据边界的架构测试。
- 定义 Task Projection、WorkflowRun 和 TriggerContext 契约。
- 建立旧实现退役登记机制，并登记首批待退项与各自的退出条件（本阶段只登记，不执行任何清除）。

### 14.2 阶段 B：决策审计与交易安全

- 新增 decision cycle、revision、transaction risk review、Boss approval 和 cycle event 模型。
- 实现 Chief—Risk 多轮 Loop，移除 Risk 静默裁剪。
- 将 Boss 入口限定为批准/拒绝，绑定 revision hash。
- 在 Trader 入口强制校验 ExecutionAuthorization 和新鲜度。
- 所有手动和自动交易指令进入同一审批链。

**实施状态（2026-09-23，change `establish-decision-audit-and-trade-safety` 已实施）**：

- 审计存储五表（`decision_cycles` / `decision_revisions` / `decision_risk_reviews` /
  `boss_approvals` / `cycle_events`）落地；修订不可变、内容哈希幂等、转移 CAS 留痕；
  历史决策诚实迁移（`legacy_unknown` 不伪造哈希）。
- 风控改为以整条修订为评估单元的只读审查（`risk/checks.py::review_revision`，
  含跨单规则与交易前后指标），`pre_trade()` 退化为回滚适配层。
- Chief—Risk 有界 Loop（默认 ≤3 轮）接线至图：approve → 审批、reject → `chief_revise`
  采纳边界、exhausted/修订被驳空 → `manual_review`；No Action 为带理由的正式终态。
- Boss 审批收窄为对精确 revision hash 的批准/拒绝，修改意见只作拒绝备注；
  审批回调幂等键持久化（过程+修订+哈希+渠道+轮次），跨进程/重启去重。
- 执行授权网关（`execution/authorization.py`）：真实下单须持完整授权并逐项复验；
  组合快照过期（`max_snapshot_age_seconds: 60`）即回退重新风控并强制重新审批；
  订单标识改由 cycle + revision + 序号派生；不确定结局的重试不重提。
- 待退登记六项见 `config/workflow/legacy_retirement.yaml`（Phase B 段）；
  验收与保真性对照记录见 change 目录 `verification.md`。

### 14.3 阶段 C：Clerk 和交易账本

- 建立 Clerk 编排层，整合现有 reconcile、journal、marks、performance 和 attribution。
- 实现订单/成交与 cycle/revision/approval 的强关联。
- 增加迟到成交、部分成交、人工订单和漏跑补偿。

**实施状态（2026-09-23，change `establish-clerk-and-trade-ledger` 已实施）**：

- Clerk 确定性编排入口（`execution/clerk.py::clerk_run`）：以「kind+window+as_of」
  派生 run_id（不含请求时刻），同窗口重放幂等复用；串联 reconcile / marks /
  episodes / predictions / performance 五步，单步失败不毒化其余并在 `clerk_runs`
  留痕；每步消费的领域事实投影（`trades` 关键列）在编排前后逐字节不变。
- 强关联落地为「写路径强制 + 列保持可空」（除主键外字段一律可空，用户裁决）：
  系统源真实提交（非 cancelled/rejected）缺 `revision_no`/`decision_hash`/
  `approval_id` 任一即拒写并登记 `broken_link` 异常；匹配成功的 fill 继承订单
  完整决策链；存量断链经一次性迁移登记为 legacy 缺口，不伪造链。
- 归属三类化：匹配→`system` / 人工 orderRef→`manual` / 其余→`unattributed` +
  审计异常（用户裁决：无二义性）；仅显式 `system` 进入系统绩效与归因。
- 补偿语义：累计式成交核算（`filled_qty` 累计 + 成交量加权均价，满量才收口，
  `terminal_basis=broker|inferred`）；迟到成交跨会话日判定并以 `late_backfill`
  标记修正推定终态；漏跑且券商无法返回的会话日登记 `reconciliation_gap`（只
  登记不导入，用户裁决）；重放末尾核对本地 attempt 计数与券商 distinct
  order_id 数（`attempt_count_mismatch`）。
- 绩效/归因重建（`execution/rebuild.py`）：按 (kind, period, method_version)
  写 `ledger_read_models`，带重建时点与 `source_facts_hash`，同指纹命中即跳过，
  失败不回写原始事实。
- Internal State API（`execution/state_api.py`）：portfolio/trades/fills/
  performance/attribution/completeness（缺口统计，任一未清偿异常即 degraded）；
  Chief（assemble 交易历史与成交段）、Risk（assess 绩效段）、channel/context
  已迁移，双读比对后切换。
- LLM 边界机器强制（`execution/llm_boundary.py`）：标注字段白名单 + 伴生
  provenance（`invalidation_source`/`invalidation_checked_at`）强制；架构守卫
  测试扫描全部 LLM 模块，断言零账本写路径与零账本表裸 SQL。
- 存储：新增 `ledger_exceptions` / `clerk_runs` / `ledger_read_models` 三表
  （登记于 `WORKFLOW_MEMORY_TABLES`）；`trades` 增补 `filled_qty`/`terminal_basis`/
  `order_seq` 列，`fills` 增补 `late_backfill` 列，`trade_episodes` 增补
  `invalidation_source`/`invalidation_checked_at` 列（全部 additive、可空）。
- 待退登记六项见 `config/workflow/legacy_retirement.yaml`（Phase C 段：2 retired
  + 4 pending，Phase E 收口直调入口）；验收与保真性对照记录见 change 目录
  `verification.md`。

### 14.4 阶段 D：分析师职责重构

> **实施状态（2026-09-24）**：本阶段已实施完成（OpenSpec change
> `restructure-analyst-roles-and-chief-inputs`，9 组任务全部落地）。下表给出各条目的
> 落地载体；实施细节与验收见
> `openspec/changes/restructure-analyst-roles-and-chief-inputs/verification.md`。

- 从 Sector 拆出 Layer，将配置权收口到 Sector。
  — 落地：`agents/layer/layer_review.py`（层级状态判断 + `LayerAnalysis` 投影）、
  `agents/sector/{review,rotation,cross_section}.py`（三级配置结论、预算与护栏，
  `SectorAllocation` 投影）；`LayerVerdict.allocation` 写路径停写、列保留，
  登记 `layer_verdict.allocation` 待退（`config/workflow/legacy_retirement.yaml`）。
- 建立 Information Analyst，迁移现有抽取、triage 和 digest 能力。
  — 落地：`agents/information/`（entry/documents/extract/triage/digest/briefs，
  `InformationBrief` 投影，携带三类时间与同源聚类）。
- 将 Fundamental 拆成例行和事件模式，删除越界依赖和内部交易风控。
  — 落地：`agents/fundamental/`（routine/event 双模式入口）、
  `graph/pead.py` + `agents/pead/score.py`（内部风控调用与 sizing 树删除，
  `score_decision` → `event_view`，direction 不映射动作）。
- 移除 Sector 对 Macro、PEAD 和研究观点的依赖。
  — 落地：`agents/sector/review.py` 的 `_load_top_down_context` 只装载 FactSet
  行业数据；`agents/sector/assemble.py` 不再读取 PEAD dossier 结论。
- 移除 Risk Officer 对 Macro 观点的依赖。
  — 落地：`agents/risk_officer/review.py` 删除宏观 regime 注入；
  `ReviewBasis`（portfolio_snapshot_id / market_as_of / ruleset_version）绑定
  风控依据三要素，授权门 fail-closed。
- 扩展 Chief 以读取六类分析投影和固定 research snapshot。
  — 落地：`agents/chief/assemble.py`（`build_chief_snapshot`：六类齐备才进周期，
  缺口报告 + 阻断；与旧表直读双读比对）、`decision/snapshot.py`
  （`frozen_snapshot_*`）、`graph/chief.py`（快照随 `create_cycle` 落库，
  修订轮次重验失效即 superseded）。
- 采集侧与 Provider 直连清退（边界收敛）。
  — 落地：`data/collection/evidence.py`（采集取数与原始资产写入迁出 agents/）、
  `data/products/{macro_inputs,sector_inputs,news_inputs,market_inputs,fiscal_tools}`
  （分析师取数统一走数据产品入口）；守卫例外表清零，并新增「例外必须声明
  未来收敛阶段」机器校验（`workflow/architecture_guards.py::validate_exceptions`）。
- 投影发布点（§7.2 契约的落地载体）：七类投影全部经 `agent/task_projection.py`
  的 `build_envelope` + `store.save_task_projection_envelope` 发布——
  `layer_analysis`（`agents/layer/layer_review.py`）、`information_brief`
  （`agents/information/briefs.py`）、`sector_allocation`
  （`agents/sector/review.py`）、`fundamental_expectation_update` /
  `fundamental_event_review`（`agents/fundamental/{routine,event}.py`）、
  `macro_review`（`agents/macro/review.py`，portfolio 作用域）、
  `technical_review`（`agents/technical/review.py`，逐标的 entity 作用域）。

### 14.5 阶段 E：Dispatcher 与事件日历

- 将硬编码 Scheduler 迁移到任务注册、依赖解析和并发执行。
- 完成 SQLite 连接和幂等写入重构。
- 发布 Schedule Calendar 数据产品和 trigger ledger。
- 迁移财报、FOMC、核心宏观事件和人工覆盖。

**实施状态（change `implement-phase-e-dispatcher-and-calendar` 已归档，2026-10-19 核对）**：任务注册表与 Dispatcher、Trigger Ledger（`event_id + event_version + workflow_id` 幂等认领）、task-local SQLite 连接（WAL、busy timeout、短事务）、版本化 `schedule_events` 与只读 Calendar Data Product 均已实现并验收。生产现状：Information、Fundamental 例行与事件、Layer、Sector、Macro、Technical 这 7 个研究工作流的调度负责人是 Dispatcher，常驻进程 `com.ats.phase-e` 只跑研究、不进 Chief、不下单；**主理人决策周期、PEAD 评分窗口以及 journal/clerk 作业仍由旧调度承担**。**生产日历库目前是空的**（`schedule_events` 0 行，`config/data/schedules.yaml` 没有日历刷新任务），事件路径尚未被真实日历事件触发过，现有事件评审均为手动触发。

### 14.6 阶段 F：影子运行与切流

- 新旧分析和调度路径并行运行，新路径默认禁止真实下单。
- 比较输入快照、分析输出、调度遗漏、风控结果和交易归因。
- 通过阶段性验收后分别切换读路径、调度路径和交易路径。
- 迁移窗口内保留旧入口作为可控回滚路径；不同时保留两个可下真单的入口。

**实施状态（change `implement-phase-f-shadow-run-and-cutover` 已归档，121/121，2026-10-19 核对）**：
- **读取**：Information、Fundamental、Layer、Macro、Sector、Technical 六个消费者按消费者整体切到新路径（代次 3），每日健康检查，失败即退回旧路径；Chief 对旧表的直读尚未清零。
- **调度**：7 个研究工作流切到 Dispatcher，并演练过「切回旧路径再切回」。
- **交易 / 审批 / 记账**：未切换。实盘仍锁定，任一时刻只有一条可执行真实交易的入口。
- **旧实现退役**：27 个登记条目 3 个退出、24 个 pending，未改登记。
- **切流口径**：按 2026-10-15 精简裁决，切流以本文档为准，研究读取按消费者整体切换，迁移期退回旧路径；观察 = 一次切换加一次切回演练。
- **限定**：这是有限 Routine 签核加研究侧上线。2026-10-19 起，Dispatcher 发布的 Fundamental 投影已携带旧 PEAD 报告的全部内容（基线、预期表、共识、期权隐含、实际披露、三类差异；交易动作类章节有意不带），TSM、ASML 已做完整财报前后报告；§9.2 的真实窗口验证待 LRCX 10-21 之后。
详见 `docs/archive/PHASE_F_FINAL_ACCEPTANCE_20261018.md`。


## §15.1 当前基线

权威测量：2026-09-22，`uv run pytest` 全量，执行环境不设文件删除配额，环境为 `uv` 托管的
CPython 3.12.12。

- **权威基线**：**135 failed / 1398 passed / 22 warnings（170.50s）**，其中 **error = 0**。
- **环境入口**：`uv sync --all-extras`（57 秒）后全部可选分组可导入——`apscheduler`、
  `pandas_market_calendars`、`langgraph.checkpoint.sqlite`、`ib_async`、`fastapi`、`chromadb`、
  `duckdb`、`pytest_asyncio`。项目本就由 `uv` 管理环境（`.venv/pyvenv.cfg` 内 `uv = 0.9.25`），
  可选 extras 也已由 `pyproject.toml` 声明，缺的不是入口而是入口未被使用。
- **受限执行环境的对照**：同一命令在对文件删除设每轮 50 次配额的环境中为
  121 failed / 1149 passed / **263 errors**（411 秒）——即 14 项失败被 error 掩盖、249 项通过被掩盖。
  该环境 error 的成因是 pytest 运行期清理临时编号目录时触及删除配额，守卫以 `SystemExit`
  打断 fixture setup；`tests/conftest.py` 的 autouse fixture `_isolate_db(tmp_path, ...)` 使故障
  放大为整库失败（换**全新** `--basetemp` 仍复现，实测 `22 passed / 1511 errors`，13 秒）。
  **受限环境的计数不得用作验收判据**；权威基线须在不受该配额约束的环境中取得。

**失败归因（135 项，按簇）**

| 簇 | 数量 | 性质 |
|---|---:|---|
| 旧表缺失 `sqlite3.OperationalError: no such table` | 101 | 数据层写侧 cutover 半途（单一根因） |
| 接口签名漂移（`consumer` 9、`legacy_repository` 3） | 12 | 同一迁移的读侧衍生物 |
| 证据与边界语义断言 | 3 | 同一迁移的读侧衍生物 |
| 调度接线与阶段序列漂移 | 5 | 待逐项归因 |
| 采集返回空（news / yahoo / regional） | 6 | 待逐项归因 |
| 配置与 CLI 文档漂移 | 3 | 待逐项归因 |
| 缺陷 ②（action 词表）与 ③（到期桶） | 2 | 已定位到代码行 |
| 其余单项 | 3 | 待逐项归因 |

101 项旧表缺失按表计：`source_documents` 47、`evidence_observations` 33、`data_sources` 10、
`newsletter_cursors` 4、`document_candidates` 3、`measurement_series` 2、`document_versions` 1、
`evidence_failures` 1。**8 张表全部位于 `TradingMemory._retire_data_tables()` 的 DROP 名单内**
（`src/ats/memory/store.py:381-394`）；受影响测试文件 33 个。

- **缺陷 ①（系统性，116 项 = 86%）**：数据层搬迁只完成读侧，写入仍指向已被边界归类删除的旧表，
  收口于 `TradingMemory.save_observation()`，波及 chain、scheduler、data-products、
  document-assets 等多条链路。修复方向是**补齐数据层写侧并改指写入路径**，而不是恢复旧表。
- **缺陷 ②（1 项）**：PEAD action 枚举大小写与取值漂移（`schemas/decision.py:11` 5 值 /
  `schemas/pead.py:203` 4 值缺 `add` / `schemas/journal.py:121` 裸 `str`；
  `tests/test_pead_graph.py:160` 使用大写 `BUY`）。
- **缺陷 ③（1 项）**：期权到期桶边界为闭区间，0 天与 30 天到期被并入同一桶
  （`src/ats/risk/assess.py:227-240`；`tests/test_risk.py::test_sell_put_survival_separates_expiry_dates`）。

三项必须在开始架构切换前消除。**其余 17 项独立失败**（调度接线、采集返回空、配置与 CLI 文档等）
尚未归因，按 Phase A 的验收要求逐项归因并登记；Phase A 不承诺将其清零。

原文记录的「111 passed、11 failed、8 errors」是针对性子集运行的结果。其对失败的归因
（缺少 APScheduler、pandas-market-calendars 与 LangGraph SQLite checkpointer）方向正确，
但修复方式是使用项目既有的 `uv` 完整依赖入口，而不是新增一套安装方式。

**入口与测量纪律**：唯一入口为 `./scripts/run_tests.sh`（`uv sync --all-extras` +
`uv run pytest`）。测量条件（命令、依赖范围、执行环境限制与影响范围）与「环境性失败 vs 业务回归」
的判据记在 [`docs/TEST_BASELINE.md`](../TEST_BASELINE.md)——引用本节的任何基线数字时，必须连同
那里的测量条件一起读。

