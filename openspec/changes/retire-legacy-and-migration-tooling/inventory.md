# 退役清点报告（R0：任务 1.1、1.2）

> 生成日期 2026-10-21，基线提交 99b82b4 之后的工作区。**只读清点，没有改任何代码、配置或登记处。**
> 本文是后续 R1–R4 的依据：每一批退役前，用 V1–V3（design.md D5）按本文的结论复核一次，因为代码会继续变化。

## 一、方法与局限

- **调用图：** 对 `src/ats` 与 `tests/`、`scripts/` 的 Python 源码做文本级引用扫描——完整点分路径、`from … import …`（相对与绝对）、同包 `from . import x`。
- **局限：** 函数体内的延迟导入能识别，但用字符串拼出来的模块名、`importlib` 动态导入识别不了。V2（14 天无运行调用）用来兜底这类情况。
- **「工具簇」：** 29 个只服务于切换与接入核验的 `workflow/` 模块（见第二节）。判断能不能删，看的是**簇外**还有没有人用它。
- 原始数据在会话暂存区（`wf_users.json`、`agent_users.json`），需要时可重新生成。

## 二、切换期工具簇：簇外依赖是删除的真正阻碍

结论先说：**簇内的模块没有一个被研究侧的生产路径直接使用**，可以整批退役。但有 6 处簇外代码依赖簇内模块，必须先处理。

| 簇内模块 | 簇外使用方 | 用法 | 处置 |
|---|---|---|---|
| `dispatch_claims` | `workflow/cutover_wiring.py:351` | 只在「边界接线声明」里写了一个字符串路径（`ats.workflow.dispatch_claims.claim`）当作登记的调用点，并非运行时调用 | R2 删除 `dispatch_claims` 前，先把这条声明从 `cutover_wiring` 的 `DECLARED_BOUNDARY_WIRING` 里去掉，同步 `boundary_evidence` 的调用点清单与它的测试 |
| `shadow_replay` | `workflow/dispatcher.py:689` | `from .shadow_replay import digest`——Dispatcher 用它算内容摘要 | **不能删**。把 `digest` 搬到 Dispatcher 自己或一个通用模块后，`shadow_replay` 才可退役；其余部分（回放）属于验收工具 |
| `shadow_reports` | `workflow/cutover.py:574,639`（激活批次）、`workflow/read_windows.py:84`（`check_batch_report`/`require_batch_report`） | 路由激活要求影子报告；Dispatcher 计划绑定读窗口时校验批次报告 | `read_windows` 那条在研究侧调用，且被 Dispatcher 与 `runtime_reads` 引用——**R2 不能直接删 `shadow_reports`**。需要先确认研究侧的读窗口校验还要不要（已固定新路径，应当可以去掉），再拆；`cutover.py` 的用法属于 R5 |
| `shadow_ledger` | `execution/clerk.py`、`execution/shadow_execution.py` | 影子订单账本 | 属于交易路径验证，归 R5 |
| `exit_requirements` | `data/assurance.py:246` | `_artifact_verified`，资格登记时核验证据文件 | 数据层资格机制的一部分，归 R5；`ats retirement` 还会用到，**不能在 R2 删** |
| `business_replay_inputs` | `data/consumer_api.py:104`、`execution/route_switch.py:303` | `governed_read`/`capture_governed`、`implementation_hashes` | 读取闸门与交易路由切换在用，归 R5 |

### 簇内整批可删（R2）的模块与命令

> **2026-10-27 复核：R2 整组转入 R5。** 清点时只按 `src/` 看使用方，补查测试和命令组后发现：这 10 个模块没有一个能单独删——`boundary_drill`（`ats drill` 的读边界回退演练）依赖 `batch_manifest`/`read_cutover`/`schedule_cutover`；`paired_business` 与 `ats retirement` 依赖 `intake_verification`；`analysis_verification`、`isolated_entry`、`scoped_intake`、`consumer_disposition` 被 Phase F 验收测试（研究、决策、执行、恢复，隔离环境跑交易侧角色等）使用；`cutover_acceptance` 验证交易订单可追溯与未经批准的订单；`dispatch_claims` 被 `schedule_cutover` 直接使用。交易链路（Chief、Clerk、Risk、Trader、Boss）暂不退役，所以它们与交易侧一起留到 R5（门 `trade_path_cutover`）。研究侧已于 4.1 解耦：Dispatcher 与研究侧读窗口不再依赖这些工具。

下面各项在簇内之外没有使用方，只被命令行与测试引用（「测试」列是引用该模块的测试文件数）。

| 模块 | 命令行 | 测试 | 备注 |
|---|---|---|---|
| `batch_manifest` | `ats batch` | 11 | 被 `boundary_drill`、`read_cutover` 引用 |
| `read_cutover` | `ats read` | 8 | 被 `batch_manifest`、`boundary_drill`、`read_deployment` 引用 |
| `read_deployment` | `ats read` | 3 | |
| `schedule_cutover` | — | 1 | 被 `boundary_drill` 引用 |
| `cutover_acceptance` | — | 1 | 引用 `shadow_ledger` |
| `intake` | `ats intake` | 2 | |
| `intake_verification` | `ats intake` | 6 | 被 `analysis_verification`、`consumer_disposition`、`isolated_entry`、`paired_business` 引用 |
| `scoped_intake` | — | 6 | |
| `analysis_verification` | — | 1 | 簇内、无簇外使用，可随 `intake_verification` 一起走 |
| `consumer_disposition` | `ats intake` | 3 | |
| `isolated_entry` | — | 2 | |
| `boundary_drill` | `ats drill` | 1 | 引用 `batch_manifest`、`read_cutover`、`schedule_cutover`、`retirement_clearance`；**`ats drill` 是交易侧三边界演练，见下** |
| `dispatch_claims` | `ats dispatch runtime-*` 等 | 1 | 先解决上表第一条的接线声明 |
| `retirement_clearance` | `ats retirement` | 1 | **不能删**：剩余退役条目还要靠它判清零 |

### 簇内但应留给交易路径（R5，不在 R2 里动）

| 模块 | 命令行 | 理由 |
|---|---|---|
| `shadow_compare`、`shadow_inputs`、`shadow_matrix`、`shadow_replay`、`shadow_reports`、`shadow_ledger` | `ats shadow` | 交易路径切换前的影子比较、固定输入验收；`shadow_ledger` 还被 Clerk 引用 |
| `acceptance_runner`、`acceptance_reports`、`acceptance_matrix`、`acceptance_assertions`、`acceptance_disposition`、`paired_business` | `ats shadow acceptance-*`、`run-pair` | 新入口验收报告；交易路径切换需要同样的验收，且 `acceptance_*` 之间互相引用 |
| `boundary_drill` | `ats drill` | 其用途是「三边界独立回滚演练」，含交易与审批边界；研究边界部分已无用。与 `batch_manifest`/`read_cutover` 的引用关系要拆开，整体归 R5 |
| `joint_cutover`、`cutover`、`cutover_routing`、`scoped_routes`、`consumer_cutover`（路由表）、`consumer_reads`、`read_windows`、`read_recovery`、`invocation_bindings`、`boundary_evidence`、`cutover_wiring`、`assurance_surface`、`cutover_guards` | `ats cutover`、`ats live` | 读闸门与路由机制，Chief 与研究分析师共用；`cutover_routing` 等被 `agents.chief.assemble`、`trader.execute`、`risk.assess` 等 20 多个生产模块引用 |

**修正 design.md D4：** R2 里的 `migration.analysis_verification` 不是「候选」，而是确认可删，且和 `intake_verification` 绑在一起。`boundary_drill` 不属于 R2，改归 R5。`migration.dispatch_dedup`（`dispatch_claims`）可以删，但前置是修改 `cutover_wiring` 的接线声明。

## 三、研究侧旧分析代码（R4）的清点结果

只列「**没有其他源码模块引用**」的（也就是只被命令行或测试引用）：

| 模块 | 行数 | 命令行 | 测试 | 结论 |
|---|---|---|---|---|
| `agents/pead/monitor` | 79 | `ats pead monitor/watch` | 3 | 只被命令行用；R1 删命令后进入 R4 |
| `agents/pead/research` | 约 30 | `ats pead research` | 4（test_research、test_fact_projections、test_unstructured_consumer_routing、test_data_products 有引用） | **10-26 增补进 R4**：迁移期薄代理，能力已迁到 `agents/information/extract`；只被命令行 `run_pead_research` 用（`data/*/unstructured/__init__` 里的 `research` 是另一个同名模块，与它无关）；R1 删命令后进入 R4 |
| `agents/risk_manager` | 52 | — | 1 | 无任何引用，只有测试；可删 |
| `agents/risk_validator` | 164 | — | 1 | 无任何引用，只有测试；退役登记里的 `apply_guardrails` 即它；可删 |
| `agents/sector/kb_perturb` | 305 | `ats sector kbperturb` | 1 | 知识库扰动校验工具；10-21 裁决并于 10-26 再确认：**保留，不迁移，不进 R4**（见第五节 Q3） |
| `runtime/evidence_observe` | 95 | `ats data evidence-observe` | 1 | **保留**：数据刷新任务 `chain_evidence_daily.*` 用命令 `ats data evidence-observe` 调用它（`config/data/schedules.yaml`），是新路径的一部分，不是旧入口 |
| `graph/checkpoint`、`runtime/server` | — | `ats serve` | — | **保留**（审批回调与恢复在用） |

被其他源码引用的旧分析代码（**不能当旧代码删**，因为新路径在用）：

| 模块 | 使用方 | 结论 |
|---|---|---|
| `graph/pead`（658 行） | `agents/fundamental/routine`、`agents/fundamental/event`、`agents/pead/prep`、`score` | **保留**：例行模式建基线、事件模式评审的图都在这里 |
| `agents/pead/prep`、`score`、`report`、`units` | `graph/pead`、`agents/fundamental/event`、sector/technical/risk 的报告 | **保留**：被新路径直接调用 |
| `agents/pead/research` | `agents/information/*`、`data/articles/semianalysis`、`data/research` 等 | **保留**：信息分析师在用 |
| `agents/pead/triage` | `agents/information/target_research`、`pead/monitor` | **保留**：信息分析师在用；`monitor` 删了之后它仍有使用方 |
| `runtime/digest`（297 行） | `journal/calibration`、`critic`、`episode_report`、`report` | **保留**：交易日志与复盘在用，归交易路径 |
| `agents/evidence/observer`（383 行） | `agents/sector/viz`、`chain/articles`、`runtime/evidence_observe` | **保留**：产业链证据抽取仍被引用；退役登记里的 `agent.evidence_observer` 说的是作为独立角色调度，不是这个模块 |
| `agents/sector/context` | `channel/cli_channel`、`feishu_bot`、`feishu_channel` | **保留**：审批卡片渲染在用 |
| `agents/sector/structure`、`rotation` | `cross_section`、`sector/review`、`business_replay_inputs` | **保留**：行业分析师在用 |
| `chain/*`（`induction`、`corroborate`、`report`） | `agents/sector/*`、`risk_officer/report`、`technical/review` | **保留**：被多个报告引用 |

**修正 design.md D4：** R4 的范围比预想小得多。确认可删的只有 `risk_manager`、`risk_validator`，以及 R1 删掉命令后的 `pead/monitor`。原先列在 R4 候选里的 `pead/prep`、`score`、`report`、`triage`、`runtime/digest`、`runtime/evidence_observe`（待确认）、`sector/structure`、`rotation` 都有生产使用方，应当移出。

## 四、旧手动入口的副作用对照（任务 1.2）

对象：命令行里用于研究分析师的旧手动入口，以及已被删除的旧调度流程（faeac82 之前）。表中「新路径」指 Dispatcher 的任务适配器。

### 4.1 研究分析师手动入口

> **2026-10-26 更新**：本节最初写于 R0（a1c89e3），当时新路径不生成技术面、行业、层级与 HTML、产业链证据周报。这些已由退役方案第 2 组 RP 恢复，下表的「新路径」「结论」两列按 10-26 的实际产出重写，并新增「新路径的产出」一列（文件名与位置）。所有报告都写到Obsidian「量化投资/个股分析系统」目录（`/Users/liuchao/Documents/Obsidian Vault/量化投资/个股分析系统/`，各配置的 `output_dir`）。

| 旧入口 | 旧入口做的事 | 新路径（Dispatcher） | 新路径的产出（文件名 / 位置） | 结论 |
|---|---|---|---|---|
| `ats macro review` | 评审 + 打印 + 写 Obsidian 周报 | `_macro_adapter` 评审 + `write_new_version` 写周报 | `宏观分析-<标签>-<日期>.md`，如 `宏观分析-宏观-2026-10-24.md`；每周六 09:00（台北） | 已承担 |
| `ats technical review` | 评审 + 打印 + 写 Obsidian 技术面报告 | `_technical_adapter` 只写投影；报告由 `report_publishers.technical_report` 在**整次运行结束后**用已发布的投影汇总写一份（不显示 VIX 数值，只显示是否触发恐慌层） | `技术面读数-<标签>-<日期>.md`，如 `技术面读数-技术面-2026-10-24.md`；工作日 22:00（台北） | 已恢复（W10 复核） |
| `ats sector review`（层级报告 + HTML） | 评审 + 写行业周报 + 层级报告 + 可视化 HTML | `sector-review` 任务内 `agents/sector/review.py` 写层级报告与 HTML | 层级：`层分析-<行业>-<层名>-<日期>.md`（8 层各一份），如 `层分析-AI硬件-L1AI应用层（Token经济）-2026-10-25.md`；页面：`层分析-<行业>-<日期>.html`；每周六 09:30（台北） | 已恢复 |
| `ats sector review`（**行业周报**） | 同上，写 `行业分析-<行业>-<日期>.md` | `sector-review` 任务内 `agents/sector/review.py` 在层级报告之后调用 `report.write`（10-26 补上；此前新路径没有调用，10-24、10-25 都没写） | `行业分析-<行业>-<日期>.md`，如 `行业分析-AI硬件-2026-10-26.md`；每周六 09:30（台北） | 已恢复（10-26 补，W10 复核） |
| `ats sector layer` | 单层配置结论 + 层内选股 | `layer-review` 适配器写层级投影，不打印 | 无独立文件；层级结论在上面的层级报告里 | 已承担 |
| `ats sector crosssection` | 单层截面排名，持久化到库 | 行业评审自己在评审里逐层算截面，结果存进评审的 `baskets`；Layer 投影另带截面排名 | 层级报告第三节「截面明细」 | 已核实、已承担：Chief 读的是评审里的 `baskets`，不读独立持久化表，旧调度器每周额外 `persist=True` 的那一遍没有消费者 |
| `ats analyst information` | 一轮信息简报 | `_information_adapter` 同一入口 | 投影（不写 Obsidian 报告）；`g0_latest information_brief` 查看 | 已承担 |
| `ats analyst fundamental` | 例行 / 事件模式 | `_fundamental_*_adapter` 同一入口 | `基本面分析-<代码>-<年份Qn>.md`，如 `基本面分析-LRCX-2027Q1.md`（财报前为 prep 阶段，财报后同一文件更新为 score） | 已承担 |
| `ats pead prep/score` | 预期基线 / 财报后打分 | 例行模式建基线、事件模式评审（`graph/pead` 同一图） | 同上一行 | 已承担，报告落盘已验证（LRCX、TSM、ASML） |
| `ats pead monitor/watch/research` | 新闻追踪、监控 | 信息分析师 + 数据刷新的新闻任务 | 投影 | 已承担 |
| `ats evidence observe/collect/articles` | 产业链证据抽取、文章采集 | 数据刷新 `chain_evidence_daily.*`、`chain_l1_governed_evidence_daily`、`chain_regional_*_weekly`、`semianalysis_newsletter_weekly` | 证据入库，不写报告 | 已承担 |
| `ats evidence report`（每周产业链证据周报） | 旧调度器每周末尾写 `产业链证据-<行业>-<日期>.md` | 行业评审**之后**由 `report_publishers.chain_evidence_report` 写 | `产业链证据-<行业>-<日期>.md`，如 `产业链证据-AI硬件-2026-10-24.md`；每周六 | 已恢复（W10 复核） |
| `ats evidence claims/propose/proposals/review/kbreview` | 命题状态、候选命题与审批 | **没有对应的新路径入口，且有意不迁移**：它们是人在终端使用的治理工具，不是分析师任务（无投影、无定时/事件触发）；命题归纳按设计“证据驱动、不是日历驱动”，不能放进定时器；`review` 是审批动作，必须由人发起。读取侧已自动进入每周产业链证据周报（待确认命题、知识库待办两节） | 终端输出；`ats evidence claims ai_hardware` 可看各层命题与证据 | 按 10-21 裁决（Q3）**保留**；10-26 复核后**确认保留、不迁移**，R1 不删；10-26 实测可用 |
| `ats sector kbperturb` | 知识库有效性校验（不动生产） | 无，有意不迁移：开发期验证工具，不写库，生产无任何依赖 | 终端输出 | 按 Q3 保留；10-26 复核后**确认保留、不迁移**；`ats sector --help` 中仍在 |

### 4.2 已删除的旧调度流程，被删后没人接手的工作

faeac82 删除了旧调度器。它的工作多数有新负责人（参见 `docs/OPERATIONS.md`、`config/settings.yaml` 的迁移注释），下面是没有接手方的：

| 旧工作 | 旧调度器里的做法 | 现状 |
|---|---|---|
| 每周产业链证据 Obsidian 周报 | `_cross_section_weekly` 末尾调 `chain.report.write` | **已接手**（RP）：行业评审之后由 `report_publishers.chain_evidence_report` 写 `产业链证据-<行业>-<日期>.md` |
| 每周行业截面排名的持久化 | `_cross_section_weekly` 逐层 `cross_section.run_layer(persist=True)` | 无接手方，**也无消费者**（见 4.1）：Chief 读评审内的 `baskets`，评审内部已经自己算了截面。无需恢复 |
| 每日情报摘要 | `_intel_digest` → `digest.intel_digest` | 无接手方；设置里本就关闭（`intel_digest: false`） |
| 每日绩效 / 风控摘要 | `_perf_risk_digest` | 无接手方；设置里本就关闭 |
| 每日绩效快照与风控快照、风险告警 | `_perf_snapshot`（含 `risk_assess` + `_push_risk_alert`） | 无接手方；库里 `performance`、`risk_reviews` 最近记录是 2026-08-31，**早在迁移前就已停写**。属于交易路径，归 R5 |
| 日志标记 | `_journal_marks` | 无接手方；库里最近 2026-09-02。归交易路径 |
| 主理人每日决策、书记员对账 | `_chief_daily`、`_journal_reconcile` | 已知：交易路径切换时在 Dispatcher 重建 |
| Yahoo 新闻回填 | `_news_backfill_daily` | 无接手方；设置里本就关闭，数据平台里也没有 Yahoo 来源的文档，没有实际影响 |

## 五、需要你决定的问题（写进 G0 清单前先定）

- **Q1 可读报告要不要恢复。**（10-21 已裁决：恢复；10-26 实测：技术面报告、层级报告 + HTML、产业链证据周报已恢复，行业周报 `行业分析-…md` 原先漏了，10-26 已补，见 4.1。） 技术面、行业（含层级、HTML）、产业链证据周报现在不再由流水线生成（宏观周报已补上）。如果你仍要看这些报告，需要在 Dispatcher 的适配器里补写报告，并先于旧入口退役完成；如果不需要，就在 G0 清单里记为「接受缺失」。
- **Q2（已核实，无需你决定）持久化截面排名。** Chief 读的是行业评审里的 `baskets`，评审内部已自己计算截面，独立持久化的那一遍没有消费者，不必恢复。
- **Q3（10-26 再次确认：保留，不迁移；下阶段只清退其余旧入口）命题治理与知识库校验命令（`ats evidence claims/report/propose/proposals/review/kbreview`、`ats sector kbperturb`）。** 这是人审批新命题的入口，没有新路径对应物。建议保留，不列入 R1。
- **Q4 R4 缩小。**（10-26 已确认）R4 只剩：`risk_manager`、`risk_validator` 里已退役的 `apply_guardrails`（同文件的 `review_guardrails` 是声明的替代品，等 R5 决定去留，不随本批删除）、`pead/monitor`、`pead/research`（10-26 增补）。`agents/pead/{prep,score,report,units,triage,outputs}` 是新路径 Fundamental Analyst 的实现本体，保留。**风控部分的原则（用户 10-26）**：Risk / Chief / Clerk 的新路径还没有试用过（交易流程未上线），所以 R4 里只清退确实用不着的 Risk 代码（`risk_manager`、`apply_guardrails`），其余 Risk 代码一律保留，后续再清退旧路径剩余部分。

## 六、对 design.md / tasks.md 的修订建议

在你回答上面的问题之后，我一次性修订：

1. design.md D4：R2 去掉 `boundary_drill`（归 R5）、`analysis_verification` 从候选改为确认；R4 缩小为 3 个模块；R1 的 `legacy_entry.evidence_commands` 按 Q3 缩小。
2. design.md 新增「簇外依赖先于删除」一节，列出第二节的 6 处。
3. tasks.md：R2 增加任务——先改 `cutover_wiring` 的接线声明、把 `digest` 从 `shadow_replay` 搬出、确认 `read_windows` 对 `shadow_reports` 的依赖去留；新增「可读报告接续」任务（取决于 Q1）。
4. G0 清单（g0-runbook 会话产出）里「旧入口副作用对照确认」一项引用本文第四节。

## 七、各批复验记录（V4–V6）

### 第 5 组（R1：旧手动入口与命名）· 2026-10-27

提交：0ae8e9f、5f6fd90、2cd1fb5、8d2b79d（5.1–5.3、5.5、5.6）、86e4c19（5.4）、0a6ae9c（5.7）。

| 项 | 结果 |
|---|---|
| V4 测试 | 受影响文件逐个对照干净基线（`3b2a745` 的 worktree，同一批 id）：`test_phase_f_opinion_execution`、`research_gate`、`schedule_runtime`、`scoped_intake`、`stage_one` 失败集合与基线**完全相同**；`test_phase_f_acceptance_reports` 25 例全过；`test_schedule_owner_switch`、`workflow_ownership`、`macro_cutover`、`retired_research_commands`、`legacy_retirement`、`retirement_clearance`、`workflow_dispatcher`、`phase_f_schedule_stop` 全过。`test_cutover_cli` 3 例与 `test_sector_allocation` 1 例在更早提交同样失败，属既有。`test_phase_f_independent_runner`（2 失败）、`test_phase_f_integration_evidence`（21 错误）当前树结果已有，基线对照仍在后台运行，出结果前不记「既有」。本批未重跑全量（单次全量约 80 分钟）。TEST_BASELINE 已记本批 |
| V5 运行 | 10-27 11:27（台北）`launchctl kickstart -k` 重启 `com.ats.dispatcher`（PID 31647）与 `com.ats.serve`（PID 31651），serve 在 8000 端口应答、启动日志无错误；`ats data health --consumer information` 为 ok。**尚未观察到**：重启后的下一次定时触发与日历消费（日历消费 06:10 / 18:10 UTC）、下一次数据刷新——这三项等真实触发后补记 |
| V6 历史 | 4 条已登记 retired 的条目 `ats retirement tombstone` 均显示 `registered_status: retired`、`readable_by: []`；旧命令返回提示（`macro review`、`consumer-cutover health`、`dispatch owner-switch/owner-rollback`、`evidence collect`、`sector crosssection` 已逐个实跑）；旧表与台账可读（库里仍可查认领与历史） |

遗留与注意：
- `legacy_entry.research_owner_legacy_mode` 有意保持 pending（5.4 取「丙」，库内部值仍为 `legacy`），长期方案乙见 verification.md「上线后待办」。
- 库里有 11 条 `running` 认领，最后更新在 10-19 至 10-23（information-brief 7 条、technical-review 4 条），无对应活进程，是早先中断留下的；`pause` 遇到 `running` 认领会拒绝，若以后要停用这两个工作流，需先单独处置它们。本任务未动。
- `RESEARCH_MANUAL_MOVED` 里 `evidence collect/observe/articles` 与 `sector crosssection` 的提示前缀仍写「研究分析师由 Dispatcher 运行，请用」，读起来略别扭，不影响功能。

### 第 6 组（R3：登记修正与研究侧已登记条目）· 2026-10-27

提交：1f2324b（6.1）、a336ffe（6.2–6.5）。

| 项 | 结果 |
|---|---|
| V1–V3 | 6.1 的 8 条以 grep 复核（四张旧表只剩建表 / 退役 / 迁移与边界登记表；`_apply_order_caps`、`_clip_event_notional`、`_RESUMED` 已无引用；`apply_guardrails` 只剩定义）。6.2、6.3 核查发现是在用记录，经用户同意改登记而非退役。6.4 的 11 处回退读取已清零，src 内除字段定义外无代码读 `LayerVerdict.allocation` |
| V4 测试 | 受影响的 10 个测试文件通过；`test_sector_allocation` 1 例失败在更早提交上同样失败（既有）。新增 `test_sector_viz` 历史行读回用例。未重跑全量 |
| V5 运行 | 10-27 12:22（台北）再次重启 dispatcher（PID 43954）与 serve（PID 43967），serve 应答、启动日志无错误。**尚未观察到**重启后的下一次真实触发（日历消费 06:10 UTC 即 14:10 台北），待补记 |
| V6 历史 | `ats retirement tombstone --identifier layer_verdict.allocation` 显示 retired、一致、无读取方；历史行读回由新增测试覆盖 |

遗留：`task_projections` 与 `legacy_observation_id` 名字里的 legacy 是历史遗留，未改名（改名要动两个库的表结构，收益不值）。

### 第 7 组（R4：只被旧入口用到的旧分析代码）· 2026-10-27

提交见 git log（"refactor(agents): R4 …"）。

| 项 | 结果 |
|---|---|
| V1–V3 | `ats retirement decide` 对四项都只剩「回退未演练」。静态：`risk_manager` 零引用；`pead/monitor`、`pead/research` 只被 cli 里已不可达的 `run_pead_*` 引用；`apply_guardrails` 只剩定义 |
| 删除内容 | `agents/risk_manager.py`、`agents/pead/monitor.py`、`agents/pead/research.py`；`risk_validator.py` 里 `apply_guardrails` 与独有的 `_enforce_sector_cap`、`_enforce_budget`（保留只读的 `review_guardrails`）；cli 里 `run_pead_monitor`、`run_pead_watch`、`run_pead_research` 与 main 里 3 处分支 |
| 7.3 调用图重跑 | 用 AST 扫描「没有任何 src 模块导入」的模块，对比 3b2a745 基线：删除后新增 2 个只被已删入口引用的模块，一并删除——`agents/pead/triage.py`（只是对 `agents/information/triage` 的转发壳，inventory 第三节原先写「保留」是因为当时以为 `target_research` 用它，实际用的是 information 下的那份）、`data/products/news_inputs.py`（只有 `monitor_news` 一个函数）。另外 `agents/risk_validator`（保留 `review_guardrails`）现在 src 内无调用方，是已记录的「等交易路径决定」。第三节「明确保留」的 `graph/pead.py`、`pead/{prep,score,report,units,outputs}`、`runtime/digest.py`、`runtime/evidence_observe.py`、`agents/evidence/observer.py` 仍有使用方，保留 |
| 顺带发现 | `store.append_events`、`store.set_triage` 在删除 monitor 后 src 内无调用方（只剩测试使用），属于下一轮可清的存量，本批未动 |
| 回归 | 复验时发现第 5 组的 2cd1fb5 在删 `run_cross_section` 时误删了 `run_layer_review` 上方的 `@scoped_read("layer")`，导致 `test_phase_f_business_wiring` 的 `run_layer_review` 用例失败（路由禁用时本应拒绝，却跑起了 8 层评审，测试环境的模型守卫拦住了真实调用，没有产生费用）。已补回并单独提交；其余装饰器逐个与基线比对，只有这一处不同 |
| V4 测试 | 见 TEST_BASELINE 同日记录 |
| V5 运行 | 10-27 12:56（台北）重启 dispatcher（PID 51975）与 serve（PID 51998）：dispatcher 正常启动（含读取前的数据产品健康检查），serve 应答、启动日志无错误；`ats data health --consumer information` 为 ok。**尚未观察到**重启后的下一次真实定时触发（日历消费 14:10 台北），待补记 |
| V6 历史 | 四项 `ats retirement tombstone` 显示 retired、一致、无读取方；`ats pead monitor/watch/research` 仍返回已退役提示 |

清单影响：从 `config/data/target_dataflow_coverage.yaml` 的 `required_fingerprint_paths` 移除 6 个路径（上述 4 项 + `pead/triage.py` + `news_inputs.py`）。清单哈希因此变化，已记录的资格证据会显示 `manifest_drift`。研究侧读路由已固定，不受影响；交易侧的资格证据在 trade_path_cutover 前本来就要补验，补验属生产操作，需另行授权。

## 八、规格增量与实际行为对照（任务 8.1）· 2026-10-27

对 `specs/` 下四份增量里的每条要求、每个场景，列出能证明它的测试或运行记录。判定三档：**有测试**（有直接断言）、**有记录**（只有运行或人工核验记录）、**缺口**（找不到证据，或证据只覆盖了一半）。缺口只在这里如实登记，本任务不改行为；处置见本节末尾。测试名均在 `tests/` 下，写作 `文件::用例`。

### 8.1 `workflow/dispatcher-runtime`（ADDED）

| 要求 / 场景 | 判定 | 证据 |
|---|---|---|
| 研究工作流必须只经 Dispatcher 执行 / 手动与定时同窗口 | 有测试 | `test_dispatch_claims::test_a_manual_and_an_automatic_trigger_merge_into_one_execution`、`::test_the_legacy_daily_cascade_and_the_new_schedule_resolve_to_one_identity` |
| 同上 / 调用已退役的旧分析入口 | 有测试 | `test_retired_research_commands::test_a_retired_command_names_its_replacement_and_runs_nothing`（15 条旧命令，断言退出码 2、含「已退役」与替代写法、`run_*` 全被替换成「调用即失败」） |
| 同上 / 已停用的工作流 | 有测试（缺口 a 已于 10-27 修复） | 「不执行」有测试：`test_schedule_owner_switch::test_pause_stops_the_workflow_without_approval_and_resume_needs_it`、`::test_the_old_owner_commands_answer_with_pause_and_resume_and_change_nothing`、`test_workflow_ownership::test_legacy_owner_does_not_invoke_dispatcher_or_open_phase_e_run`。「记录被跳过的原因」修复前只有手动路径做到；修复后手动、定时、日历三条路径都写一条 `skipped_owner_legacy` 记录，同一触发只记一次：`test_schedule_owner_switch::test_a_paused_workflow_leaves_one_skip_record_per_trigger_for_scheduled_and_manual_runs`、`::test_a_dispatcher_owned_workflow_leaves_no_skip_record`、`test_workflow_ownership::test_calendar_events_for_a_workflow_the_dispatcher_does_not_own_leave_skip_records` |
| 停用与恢复必须先冻结再清点 / 清点后又产生新触发 | 有测试（间接） | 先冻结后清点的顺序与冻结失败即停：`test_schedule_cutover::test_the_step_order_is_freeze_inventory_dispose_handover`、`::test_a_failed_freeze_stops_before_inventory`；`test_dispatch_claims::test_a_second_freeze_while_frozen_is_refused`、`::test_only_the_attempt_that_froze_may_open` |
| 同上 / 存在处置方式未声明的未完成触发 | 有测试 | `test_dispatch_claims::test_an_undeclared_disposition_refuses_the_handover`、`test_schedule_cutover::test_an_undeclared_disposition_stops_the_cutover`、`test_schedule_owner_switch::test_unfinished_claims_block_the_switch_until_voided_on_purpose` |
| 同上 / 有正在运行的认领 | 有测试 | `test_schedule_owner_switch::test_a_running_claim_is_never_voided`、`::test_pause_is_refused_while_a_claim_is_running` |
| 同上 / 停用时作废未完成触发 | 有测试（缺口 c 已于 10-27 核实） | 「记录原因与操作者」有测试：`test_dispatch_claims::test_a_void_disposition_records_its_reason_and_does_not_execute`、`test_schedule_cutover::test_voiding_without_a_reason_stops_the_cutover`。「恢复后不被自动执行」新增 `test_schedule_owner_switch::test_a_trigger_voided_by_a_pause_is_not_executed_again_after_the_resume`：作废的认领保持 void，恢复后同一触发再到达被 `schedule_skipped` 跳过。原先引用的 `test_dispatch_claims::test_a_void_trigger_can_be_claimed_again…` 属于旧的迁移期认领表（R5 的 `migration.dispatch_dedup`），不是现行的 `schedule_runtime`，不能据此判断 |
| 调度覆盖必须依据独立预期触发集合 / 调度遗漏必需触发 | 有测试 | `test_phase_f_schedule_runtime::test_independent_oracle_detects_the_dispatcher_omitting_a_tick`、`test_dispatch_claims::test_both_paths_missing_the_same_trigger_is_detected` |
| 同上 / 停用期间的触发 | 有测试（缺口 b 已于 10-27 修复） | `compare_triggers` 按追加式 owner log 还原停用区间（只认 `pause` 开出的区间），区间内到点且没有认领的触发记为 `skipped_by_pause`，因停用作废的记为 `voided_by_pause`，均不算遗漏：`test_schedule_owner_switch::test_expected_triggers_during_a_pause_are_skipped_not_missing`、`::test_a_pause_that_is_still_open_covers_every_later_trigger`、`::test_a_workflow_that_was_never_moved_to_the_dispatcher_is_not_treated_as_paused`、`::test_a_trigger_that_has_a_claim_is_never_reclassified_as_skipped`、`::test_a_trigger_voided_by_the_pause_is_reported_as_such`、`::test_an_event_trigger_dropped_during_a_pause_is_skipped_not_missing` |
| 同上 / 预期来源不一致 | 有测试 | `test_dispatch_claims::test_an_expected_set_that_disagrees_with_registration_is_not_trusted`、`::test_no_declared_set_means_no_verdict_at_all` |
| 可读报告必须由 Dispatcher 路径生成 / 技术面定时运行后 | 有测试 | `test_report_publishers::test_one_summary_report_covers_every_symbol_of_the_run` |
| 同上 / 周度行业评审后 | 有测试（部分有记录） | 周报跟随新的行业评审：`test_report_publishers::test_chain_evidence_report_follows_a_new_sector_review_only`；行业与层级报告内容：`test_layer_report`、`test_sector_viz`（`test_run_sector_html_*`）。首次真实定时的行业周报在 10-31 09:30（台北），尚无运行记录 |
| 同上 / 报告生成失败 | 有测试 | `test_report_publishers::test_an_unset_output_directory_is_a_skip_not_an_error`、`::test_a_publisher_failure_is_reported_and_never_raised`、`::test_failed_tasks_do_not_count_as_published` |
| 同上 / 复用既有投影 | 有测试 | `test_report_publishers::test_a_run_that_only_reused_projections_writes_no_report`、`::test_a_second_report_on_the_same_day_is_a_new_version_file`（后者是同日再发时的版本化，不是重复生成） |

### 8.2 `workflow/cutover-control`（ADDED）

| 要求 / 场景 | 判定 | 证据 |
|---|---|---|
| 读路由固定为新路径并在发布时与读取前检查 / 数据刷新后产品不合格 | 有测试 | `test_consumer_health::test_observe_logs_and_alerts_only_when_the_status_changes`、`::test_after_refresh_checks_only_when_a_job_ran`；路由不变：`test_consumer_cutover::test_switch_refuses_a_failing_health_check_and_changes_nothing` |
| 同上 / 任务执行前产品不合格 | 有测试 | `test_workflow_dispatcher::test_unhealthy_data_products_block_the_task_before_it_runs`、`test_consumer_health::test_gate_maps_tasks_to_consumers_and_leaves_upstream_projections_to_the_dispatcher` |
| 同上 / 状态未变化时不重复告警 | 有测试 | `test_consumer_health::test_observe_logs_and_alerts_only_when_the_status_changes` |
| 同上 / 同一数据域其他消费者已通过 | 有测试（间接） | 路由与检查按消费者逐个进行：`test_consumer_cutover::test_switch_moves_every_scope_of_the_consumer_and_no_other_consumer`、`test_consumer_health::test_a_consumer_without_a_defined_check_cannot_pass`。没有「同域其他消费者全过而本消费者不过」的单独用例 |
| 同上 / 读取资格不得开启交易权限 | 有测试 | `test_consumer_cutover::test_only_research_read_consumers_can_switch`、`::test_the_switch_does_not_open_any_other_boundary`、`::test_consumer_wide_row_is_only_ever_written_for_projection_read` |
| 同上 / 原生产品查询不能替代业务 scope | 有测试 | `test_phase_f_scoped_routes::test_products_list_is_not_business_scope`、`::test_incomplete_or_ambiguous_business_scopes_refuse`、`test_phase_f_safe_reads::test_isolated_entry_persists_actual_read_refs` |
| 检查失败时安全停止而不退回旧路径 / 窗口关闭后的检查失败 | 有测试 | `test_consumer_cutover::test_there_is_no_way_back_to_the_old_route_any_more`、`test_workflow_dispatcher::test_unhealthy_data_products_block_the_task_before_it_runs` |
| 同上 / 旧入口退役后的安全停止 | 有测试 | `test_phase_f_schedule_stop`、`test_phase_f_state_stop`、`test_phase_f_trade_stop` 三组 stop/restore 用例；`test_phase_f_safe_reads::test_actual_read_refuses_each_unsafe_fallback` |
| 同上 / 恢复已验收的新版本 | 有测试 | `test_phase_f_scoped_routes::test_target_scope_rollback_requires_proof_and_keeps_history`、`test_phase_f_trade_stop::test_proof_expires_after_bump_keeps_stop`、`::test_recovery_cannot_use_an_isolated_label_to_touch_production` |
| 同上 / 已组装快照继续执行 | 有测试 | `test_phase_f_safe_reads::test_actual_risk_node_blocks_before_account_or_dependent_execution`、`::test_actual_clerk_stops_next_step_after_read_revocation`、`::test_failed_scope_does_not_stop_unrelated_legacy_reader` |
| 同上 / 尚未切换的边界 | 有测试 | `test_phase_f_safe_reads::test_actual_chief_read_falls_back_using_once_checked_state`、`test_consumer_cutover::test_unswitched_consumer_defaults_to_the_old_route` |
| 被意外切回旧路径的消费者必须能经批准恢复 / 恢复被切回旧路径的消费者 | 有测试 | `test_consumer_cutover::test_restore_puts_a_consumer_that_was_moved_to_the_old_route_back_on_the_new_one`（代次加一，日志含操作者、批准原文、原因） |
| 同上 / 缺少批准或检查未通过 | 有测试 | `test_consumer_cutover::test_restore_refuses_without_approval_or_reason_or_a_passing_check_or_when_already_new` |
| 同上 / 已在新路径或非研究消费者 | 有测试 | 同一用例（已在新路径）与 `::test_restore_only_moves_research_consumers`（Chief 被拒；风控、交易员、书记员走同一判断，用例只取了 Chief） |

### 8.3 `workflow/legacy-retirement`（MODIFIED + ADDED）

| 要求 / 场景 | 判定 | 证据 |
|---|---|---|
| 每个阶段登记待退旧实现 / 替代已就绪但消费方未清零 | 有测试 | `test_legacy_retirement::test_a_pending_tombstone_must_state_what_it_is_waiting_for`、`::test_a_tombstone_carries_the_exit_criterion_not_just_the_target`、`::test_a_pending_item_may_still_be_in_use` |
| 同上 / 阶段遗漏待退项 | 有记录 | 规划产物层面的检查，没有自动化。本 change 以 inventory.md 第二、三节清点 + G0 L01 人工核验承担；`test_the_registered_batch_covers_every_documented_item` 只覆盖已文档化的批次 |
| 同上 / 迁移期工具未登记 | 有测试 + 记录 | `test_legacy_retirement::test_the_registered_batch_covers_every_documented_item`；inventory.md 第二节逐模块清点 |
| 同上 / 条目未声明依赖或所属门 | 有测试 | `test_legacy_retirement::test_every_entry_of_the_retirement_change_has_a_group_a_gate_and_known_dependencies`、`::test_an_unknown_gate_group_or_kind_is_rejected` |
| 退役必须经过已演练的切回与观察窗口 / 未演练即退役 | 有测试 | `test_retirement_clearance::test_an_undrilled_boundary_is_refused`；`ats retirement decide` 对在册条目普遍给出「回退未演练」 |
| 同上 / 已演练且窗口结束 | 有测试 | `test_retirement_clearance::test_a_drilled_clean_window_passes`、`::test_an_entry_meeting_every_criterion_may_exit` |
| 同上 / 窗口触发停止条件 | 有测试 | `test_retirement_clearance::test_a_triggered_stop_condition_keeps_the_window_pending` |
| 同上 / 迁移窗口已关闭的研究边界 | 有记录 | 第 5–7 组用既有演练与窗口记录退役，未重开旧路径（inventory §七；`ats retirement decide` 的「回退未演练」按已接受先例处理，见 design.md） |
| 同上 / 只被命令行和测试使用的工具 | 有记录 | 第 5–7 组各条目的 `missing_condition` 内写入了消费方清零证明（先例 `scheduler.hardcoded_serial_run`），未要求演练切回 |
| 退役执行必须以新路径人工核验通过为前置 / 核验未完成就开始删除 | 有测试 | `test_retirement_clearance::test_g0_is_open_only_when_every_item_has_an_open_verdict_and_the_conclusion_is_signed`、`::test_an_entry_with_an_unmet_dependency_or_a_closed_gate_cannot_be_decided_retired` |
| 同上 / 核验通过后进入退役 | 有记录 | G0 78/78 已于 10-27 签署（verification.md「G0 结论」），随后第 5–7 组的 `decide` 以 G0 开启为前提 |
| 同上 / 核验发现新路径缺陷 | 有记录 | G0 过程中发现并修复的缺陷（verification.md S01–S25 小结列了 9 项）；`test_..._every_item_has_an_open_verdict` 保证有「未通过」项时门不开 |
| 退役条目必须按依赖顺序执行 / 前序条目未完成 | 有测试 | `test_legacy_retirement::test_a_valid_dependency_graph_loads_and_reports_unmet_dependencies`、`test_retirement_clearance::test_an_entry_with_an_unmet_dependency_or_a_closed_gate_cannot_be_decided_retired` |
| 同上 / 依赖交易路径切换的条目 | 有测试 + 记录 | `test_retirement_clearance::test_an_external_gate_is_never_opened_from_here`；9.1 的 `decide` 输出（见第九节） |
| 同上 / 依赖存在环或悬空引用 | 有测试 | `test_legacy_retirement::test_a_cycle_in_the_dependencies_fails_the_registry`、`::test_a_dependency_on_an_unregistered_entry_fails_the_registry` |
| 每批退役后必须复验 / 退役后定时触发失败 | 有记录 | 各组 V5 记录（inventory §七）；第 5–7 组重启后的首次真实定时触发仍在观察中（任务 10.2） |
| 同上 / 全量测试出现未登记的新失败 | 有记录 | TEST_BASELINE 各组记录；每组新失败均已归因（第 7 组为被误删的 `@scoped_read("layer")`，当场修复） |
| 同上 / 历史记录不可读 | 有记录 | 各组 V6 记录（`ats retirement tombstone`、历史表读取）；`test_legacy_retirement::test_no_physical_purge_has_been_performed` |
| 登记为已退役的条目必须可证明消费方清零 / 已退役但消费方状态未知 | 有测试 | `test_legacy_retirement::test_unproven_retired_entries_read_as_awaiting_proof_not_as_cleared`、`test_retirement_clearance::test_no_reader_at_all_is_partial_not_zero`、`::test_an_unmapped_module_path_is_rejected_rather_than_guessed` |
| 同上 / 重新核验发现仍有使用 | 有测试 | `test_retirement_clearance::test_a_retired_tombstone_that_is_still_readable_fails`、`::test_a_consumer_still_on_the_legacy_path_blocks_exit` |

### 8.4 `data/target-dataflow-assurance`（REMOVED）及其他 REMOVED 条款

四份增量里的 REMOVED 条款（前置接收矩阵、逐批 eligible / ineligible 报告，以及 `dispatcher-runtime`、`cutover-control` 里被新要求取代的旧条款）没有可测的行为，随归档从主规格里删除。核对点是每条的 Migration 所指向的新要求都已在上面有证据：前置接收 → 8.3 的人工核验前置；逐批报告 → 8.3 的依赖顺序与 R5 外部门；旧切流 / 回退条款 → 8.2。

### 8.5 缺口小结与处置

缺口 a、b、c 已在同日修复或核实（见 §十），不再是缺口；d 随 a 一并解决。

| # | 缺口 | 结果 |
|---|---|---|
| a | 已停用工作流的定时 / 日历触发没有「被跳过的原因」记录 | 已修复：手动、定时、日历三条路径各写一条 `skipped_owner_legacy`，同一触发只记一次 |
| b | 预期触发集合不区分停用，停用期间会被报成遗漏 | 已修复：`compare_triggers` 新增 `skipped_by_pause` / `voided_by_pause` |
| c | 作废触发「恢复后不被自动执行」无断言 | 核实：现行代码本来就不会重跑（作废认领保留为 void，同键再到达被跳过）；补了测试 |
| d | 5.4 原验证口径只被部分覆盖 | 随 a 解决 |

## 九、文档同步与 R5 核对记录（任务 8.2、9.1、9.2）· 2026-10-27

**8.2 文档修正。** 改了 9 处过期说法：`README.md`（旧入口不再「报 caller is not current owner」，改为已退役并提示替代写法）、`docs/agents/MACRO_ANALYST.md` 两处、`SECTOR_ANALYST.md` 两处（其中 `ats sector crosssection` 一条改为已退役）、`FUNDAMENTAL_ANALYST.md`、`INFORMATION_ANALYST.md`；`docs/ARCHITECTURE.md` §6「旧实现」一行改为当前状态（41 条在册、20 条已退役、21 条未退，其中 20 条绑交易路径）；`docs/OPERATIONS.md` 第 3.4 节补了停用期间覆盖核验的已知局限（见 §8.5 缺口 a、b）。

**命令有效性核对。** 新增 `scripts/check_doc_commands.py`：从 README、OPERATIONS、DEVELOPMENT、ARCHITECTURE、`docs/agents/*` 的代码块与行内代码里取出全部 `ats …` 命令，用真实的参数解析器核对子命令路径与每个 `--flag`，不运行任何命令。结果：检查 110 条，未知命令 0 条；另有 14 处提到已退役命令（只回提示桩），逐处核对过，文字都已写明「已退役」。首版脚本没有核对 `action` 这类位置参数，漏掉了这 14 处，补上后才查出；之后每次改文档可重跑。

**9.1 R5 登记核对。** `ats retirement decide` 对 R5 的 20 条全部显示「external gate not met: trade_path_cutover」；20 条都有退出条件、缺失条件、清零判据、位置，门均为 `trade_path_cutover`（只有 `migration.analysis_verification` 另有一条前序依赖）。inventory §二 归 R5 的模块逐个对照登记位置：`shadow_*`、`acceptance_*` 以通配符登记，其余均点名；只有 `cutover_guards` 没有出现在任何退出条件里，已补进 `migration.read_route_and_qualification`（只改登记文字，登记与清零测试 58 项通过）。

**9.2 runbook 接续。** `docs/PHASE_F_RETIREMENT_RUNBOOK.md` 新增「〇·二」节：20 条与 `switch-trade-path-and-retire-r5` 各任务一一对应，并列出 5 处对不上的地方（三个模块无人认领、6.3/6.5 的前提未达成、2.7 的跳过记录与 `paused` 取值、proposal 里的旧数字）。这些只记录，没有改那个 change。同节顺手纠正了「R5 的 15 条」为 20 条。


## 十、缺口 a/b/c 的修复记录 · 2026-10-27

**c（先查）。** 读 `schedule_runtime.execute`：同一触发键已有认领行且不是 pending / failed 时直接返回 `schedule_skipped`，作废的认领（`status='void'`）永久保留，所以追赶式触发和 `workflow retry` 都不会把它重跑；之前引用的「可再次认领」用例属于旧的 `dispatch_claims`，不是现行路径。结论：行为本来就对，补了一条测试固定它。

**a。** `schedule_runtime.observe_skipped_not_owned(key, payload)`：把「因为 Dispatcher 不拥有这个工作流而没运行」写进运行历史（动作 `skipped_owner_legacy`），按触发键去重（日历路径是轮询，同一触发会反复到达）。`ownership.py` 三处调用：`run_owned_workflow`（手动与定时都经过这里）、日历事件分发、计划中事件的到期释放。不改任何所有权判断。

**b。** `compare_triggers` 新增分类。停用区间来自追加式 `schedule_owner_log`：`rollback`（即 `ats dispatch pause`）开区间，下一个 `switch` 关区间；从未迁移过的工作流没有区间，所以它的触发仍按遗漏处理。定时触发用计划时刻比对区间；事件触发没有计划时刻，用 a 写下的跳过记录的时刻。已有认领的触发不重新分类。`expected_triggers` 保持只依据配置导出，仅给每行加了 `workflow` 字段。

**验证。** 相关 13 个测试文件共 267 个用例，266 通过；唯一失败是 `test_committed_result_survives_control_record_crash_without_republication`，在 3b2a745 基线上同样失败（既有问题）。新增用例 10 个。生产库里没有被测试写入跳过记录（已查）。

**还没做的。** dispatcher 常驻进程尚未重启，生产上跑的仍是旧代码，需要你同意后 `launchctl kickstart -k gui/$(id -u)/com.ats.dispatcher` 并观察下一次定时触发。`ownership.py`、`schedule_runtime.py` 在资格指纹清单内，会使已记录的资格证据显示 `manifest_drift`（性质与之前相同，重新记录需要你授权）。

## 十一、第 10 组记录（2026-10-27）

### 10.1 全量测试

见 `docs/TEST_BASELINE.md` 新增的「全量权威测量」行。要点：73 failed / 4507 passed / 10 errors（4590 例）；
把涉及的 17 个测试文件在退役起点 3b2a745 的干净 worktree 上重跑，失败集合与现在逐项一致（83 对 83，新增 0、消失 0）。
与 10-19 基线（57 / 0 errors）的差值来自 10-19 之后、退役之前的其他提交（issuer_ir_files 指纹登记缺口等），不属于本 change。

### 10.2 生产观察（部分，未收齐）

| 项 | 观察 | 状态 |
|---|---|---|
| dispatcher 常驻进程 | 12:56 重启后持续运行（PID 51975），重启后日志无 ERROR | 通过 |
| 日历消费 | 10-27 14:10 一次完成，无错误 | 通过（1 次） |
| 数据刷新 | launchd `com.ats.data-refresh` 当前 last exit 0；此前一次退出 1，输出为数据侧任务在 10-22/10-24 窗口的 misfire 列表，窗口早于第 5 组 | 未发现与退役相关；该次退出 1 的成因未追到根 |
| 工作日定时 | 本次观察期内只覆盖 10-27（周二） | 未满一周 |
| 周六宏观 + 行业 | 尚未到；首个定时行业报告 10-31 09:30（台北） | **待观察** |
| 缺口 a/b/c 的代码 | 已提交但 dispatcher 未重启，生产仍是旧代码 | **待用户同意重启** |

**结论**：目前没有发现因退役导致的失败，但 10.2 要求的完整周期要到 10-31 周六之后才能收齐，所以 10.2 **不勾选**。
