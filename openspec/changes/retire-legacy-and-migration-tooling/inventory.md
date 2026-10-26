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
| `agents/risk_manager` | 52 | — | 1 | 无任何引用，只有测试；可删 |
| `agents/risk_validator` | 164 | — | 1 | 无任何引用，只有测试；退役登记里的 `apply_guardrails` 即它；可删 |
| `agents/sector/kb_perturb` | 305 | `ats sector kbperturb` | 1 | 知识库扰动校验工具，R1 里要不要保留需要你决定（见第五节） |
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
| `ats sector review`（**行业周报**） | 同上，写 `行业分析-<行业>-<日期>.md` | **没有调用**：`agents/sector/report.write` 在新路径里没人调（只有旧命令 `ats sector review` 的 `cli.py` 调） | 应为 `行业分析-AI硬件-<日期>.md`；库里最新一份是 `行业分析-AI硬件-2026-09-04.md`，**10-24、10-25 的行业评审都没有写** | **差异：行业周报未恢复**（见第五节 Q1 补充） |
| `ats sector layer` | 单层配置结论 + 层内选股 | `layer-review` 适配器写层级投影，不打印 | 无独立文件；层级结论在上面的层级报告里 | 已承担 |
| `ats sector crosssection` | 单层截面排名，持久化到库 | 行业评审自己在评审里逐层算截面，结果存进评审的 `baskets`；Layer 投影另带截面排名 | 层级报告第三节「截面明细」 | 已核实、已承担：Chief 读的是评审里的 `baskets`，不读独立持久化表，旧调度器每周额外 `persist=True` 的那一遍没有消费者 |
| `ats analyst information` | 一轮信息简报 | `_information_adapter` 同一入口 | 投影（不写 Obsidian 报告）；`g0_latest information_brief` 查看 | 已承担 |
| `ats analyst fundamental` | 例行 / 事件模式 | `_fundamental_*_adapter` 同一入口 | `基本面分析-<代码>-<年份Qn>.md`，如 `基本面分析-LRCX-2027Q1.md`（财报前为 prep 阶段，财报后同一文件更新为 score） | 已承担 |
| `ats pead prep/score` | 预期基线 / 财报后打分 | 例行模式建基线、事件模式评审（`graph/pead` 同一图） | 同上一行 | 已承担，报告落盘已验证（LRCX、TSM、ASML） |
| `ats pead monitor/watch/research` | 新闻追踪、监控 | 信息分析师 + 数据刷新的新闻任务 | 投影 | 已承担 |
| `ats evidence observe/collect/articles` | 产业链证据抽取、文章采集 | 数据刷新 `chain_evidence_daily.*`、`chain_l1_governed_evidence_daily`、`chain_regional_*_weekly`、`semianalysis_newsletter_weekly` | 证据入库，不写报告 | 已承担 |
| `ats evidence report`（每周产业链证据周报） | 旧调度器每周末尾写 `产业链证据-<行业>-<日期>.md` | 行业评审**之后**由 `report_publishers.chain_evidence_report` 写 | `产业链证据-<行业>-<日期>.md`，如 `产业链证据-AI硬件-2026-10-24.md`；每周六 | 已恢复（W10 复核） |
| `ats evidence claims/propose/proposals/review/kbreview` | 命题状态、候选命题与审批 | **没有对应的新路径入口** | 终端输出；`ats evidence claims <行业>` 可看各层命题与证据 | 差异：按 10-21 裁决（Q3）**保留**，R1 不删；10-26 实测可用（`ats evidence --help`、`ats evidence claims ai_hardware`） |
| `ats sector kbperturb` | 知识库有效性校验（不动生产） | 无 | 终端输出 | 按 Q3 保留；`ats sector --help` 中仍在 |

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

- **Q1 可读报告要不要恢复。**（10-21 已裁决：恢复；10-26 实测：技术面报告、层级报告 + HTML、产业链证据周报已恢复，**行业周报 `行业分析-…md` 未恢复**，见 4.1。） 技术面、行业（含层级、HTML）、产业链证据周报现在不再由流水线生成（宏观周报已补上）。如果你仍要看这些报告，需要在 Dispatcher 的适配器里补写报告，并先于旧入口退役完成；如果不需要，就在 G0 清单里记为「接受缺失」。
- **Q2（已核实，无需你决定）持久化截面排名。** Chief 读的是行业评审里的 `baskets`，评审内部已自己计算截面，独立持久化的那一遍没有消费者，不必恢复。
- **Q3 命题治理与知识库校验命令（`ats evidence claims/report/propose/proposals/review/kbreview`、`ats sector kbperturb`）。** 这是人审批新命题的入口，没有新路径对应物。建议保留，不列入 R1。
- **Q4 R4 缩小。** 按第三节，R4 只剩 `risk_manager`、`risk_validator`、`pead/monitor`，你同意吗？

## 六、对 design.md / tasks.md 的修订建议

在你回答上面的问题之后，我一次性修订：

1. design.md D4：R2 去掉 `boundary_drill`（归 R5）、`analysis_verification` 从候选改为确认；R4 缩小为 3 个模块；R1 的 `legacy_entry.evidence_commands` 按 Q3 缩小。
2. design.md 新增「簇外依赖先于删除」一节，列出第二节的 6 处。
3. tasks.md：R2 增加任务——先改 `cutover_wiring` 的接线声明、把 `digest` 从 `shadow_replay` 搬出、确认 `read_windows` 对 `shadow_reports` 的依赖去留；新增「可读报告接续」任务（取决于 Q1）。
4. G0 清单（g0-runbook 会话产出）里「旧入口副作用对照确认」一项引用本文第四节。
