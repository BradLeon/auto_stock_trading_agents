# 父 change 第 12 节最终验收：全量测试、校验与并发、订单追溯、文档同步（2026-10-19）

> 这份记录回答 `refactor-workflow-dataflow-architecture` 第 12 节四项（12.1、12.2、12.3、12.5）做到了什么、还剩什么。每个数字都能在文末列出的文件里复核。

## 一、结论

| 任务 | 结论 | 一句话 |
|---|---|---|
| **12.1** 全量测试并登记失败 | **完成（带限定）** | 全量红测试从首轮的 86 个失败 + 21 个 error 降到 57 个失败、0 个 error（全量最终复跑的结果见第二节表）；剩下的每一个都登记了根因和处置，没有一个是产品缺陷；本轮顺手查出并修复了 3 个我自己引入的问题 |
| **12.2** 校验、守卫、迁移重开、SQLite 并发压力 | **完成** | OpenSpec 全量 strict 通过；架构/切流/数据层守卫通过；新增真实多线程并发压力测试 |
| **12.3** 每笔新系统订单可追溯 | **完成** | 生产库里没有新系统订单；隔离环境的完整链路 22 项机器核验在当前代码上通过 |
| **12.5** 文档同步 | **完成** | TARGET 文档补了 Phase E/F 实施状态；Phase F 操作手册加了过期说明；新鲜度 60 秒、最多 3 轮、单一实盘入口三条规则与配置一致 |

## 二、12.1 全量测试

### 测量条件与结果

| 测量 | 命令 / 条件 | 结果 |
|---|---|---|
| 2026-10-07 权威基线（13.6） | `./scripts/run_tests.sh -p no:randomly --deselect <1 个已登记的挂起测试>` | 45 failed / 3072 passed / 0 errors |
| **2026-10-19 首轮** | 同一命令，4441 个测试，用时 80 分钟 | **86 failed / 4334 passed / 21 errors** |
| 2026-10-19 第二次全量（修复一半时） | 同一命令，77 分钟 | 79 failed / 4355 passed / 10 errors。其中 18 个失败（`macro_cutover` 6、`scoped_intake`/`stage_one` 12）和 10 个 error 是我上一步修复引出的新问题（见下），另有 5 个失败是在这次运行开始之后才修好的 |
| **2026-10-19 第三次全量（最终）** | 同一命令 | **57 failed / 4387 passed / 0 errors**（4444 个测试，81 分钟）。与首轮相比：失败 86 → 57，error 21 → 0，通过 4334 → 4387 |

被排除的那 1 个测试（`test_factset_index_semantic_local` 的语义 PDF 管线）是 10-07 就登记的既有挂起，本轮沿用。

### 首轮多出来的 41 个失败和 21 个 error 是怎么来的

我把没登记过的 72 个失败拿到 8 个更早的提交上逐个对照（`git worktree`，同一命令），确定每个第一次失败的提交：

| 第一次失败 | 个数 | 说明 |
|---|---:|---|
| 10-07 之后、10-15 15:45 之前（Phase F 的早期实现） | 64 | 10-07 基线时它们是绿的；10-09 的一次大提交先让 24 个变红，到 10-15 11:45 变成 58 个。当时没有重跑全量，所以没人发现 |
| 10-15 晚的精简切流（把读取改成按消费者整体切换） | 4 | 调度器多了一个 `consumer_health_daily` 任务，测试的期望清单没跟着改 |
| 10-17 晚我这轮的数据流和 Fundamental 改动 | 3 | 见下面「本轮查出的 3 个问题」（`assurance_surface`、`trader_a`、`independent_runner`） |

21 个 error 全部来自同一个原因：Routine 在隔离验收环境里读了实时财报日历（见下）。

### 本轮查出并修复的 3 个问题（都是我引入的，不是测试问题）

1. **运行时模块被登记成「仅冻结」。** `layer_sector_inputs`、`macro_news`、`macro_series`（产品与来源）4 个模块被 `consumer_health`、`dispatcher` 等运行时代码导入，却登记在「仅冻结」清单里，守卫据此判失败。已移出「仅冻结」清单，登记进共享的生产资格清单（`required_fingerprint_paths`），受指纹面总数仍为 578 个路径。第一次只移出、没登记，复跑时 `scoped_intake` 和 `stage_one` 的 12 个测试因「实现依赖未登记」报错，才补登记。
2. **健康检查直接导入了来源适配器。** `consumer_health` 为了拿到 Macro 指标清单，直接导入 `ats.data.sources.macro_series`，违反「工作流不得导入来源适配器」的数据—Agent 边界，`validate_target_contract()` 因此判无效。已改为由产品层 `MacroSeriesSet.defined` 提供指标清单；生产 Macro 健康检查复测仍为 `ok`（25 个指标定义、14 个日频有最新读数）。复跑时 `test_macro_cutover` 的 6 个测试因手工构造 `MacroSeriesSet` 时没带新字段而失败，已在测试辅助里补上。
3. **Routine 的财报前基线步骤在隔离验收里读了实时日历和时钟。** 合并 PEAD 准备后，Routine 每轮都会问财报日历（联网）并读评估时钟；固定输入验收只允许事先固定的输入，重放子进程因此拒绝（22 项测试变红：1 个 failed + 21 个 error）。已改为在已验证隔离环境内跳过基线步骤并如实标注 `skipped_in_isolation`；生产路径不受影响。修复后独立验收 6 项、订单追溯 22 项全部通过。

### 测试与代码对不上、已同步的（不是产品缺陷）

| 测试 | 个数 | 原因 | 处置 |
|---|---:|---|---|
| `test_scheduler_jobs` | 4 | 调度器新增 `consumer_health_daily`（每天运行，不限交易日） | 更新期望清单 |
| `test_triage`、`test_monitor` | 5 | Information 现在要求模型输出带独立的 `confidence`；一个旧测试断言「期望变化写进简报」，而 Phase D 起 Information 不得输出期望变化 | 补 `confidence`；旧测试改写为断言「带期望变化的输出被拒绝、不发简报、不写档案」 |
| `test_chief_loop::test_single_real_order_submission_path` | 1 | Phase F 新增了两个隔离验收调用方（`shadow_execution`、`acceptance_runner`），它们调用真实券商门面是为了证明被拒绝 | 放行这两个文件，**同时要求它们仍引用各自的禁写守卫**，守卫被去掉就会失败 |
| `test_intake_cli` | 1 | 本地运行手册里引用的证据 ID 哈希过期 | 更新哈希 |
| `test_chief_snapshot` | 11 | 决策快照测试没有配置读取路由，被 Phase F 的「决策依赖必须走合格 target 路由」门拦下 | 新增 opt-in 夹具 `open_decision_read_gate`，只让快照逻辑可测；门本身的 fail-closed 行为不变 |
| `test_sector_viz` | 4 | 用了虚构的行业名，读取门解析不到业务范围 | 夹具直接提供范围 |

### 仍然红的测试：全部登记

| 组 | 个数 | 第一次失败 | 根因 | 能否从新路径/生产到达 | 处置 |
|---|---:|---|---|---|---|
| **A. 旧批次/读取切换机制**（`test_batch_manifest` 17、`test_read_cutover` 16、`test_batch_cli` 5、`test_cutover_cli` 3） | 41 | 10-09 `de1ef6b` 起（当天 24 个，10-15 上午 58 个） | 这套「按批次、逐 scope、需要可审计部署授权」的切换机制在 10-09 被改成要求授权（报错 `layer: auditable deployment authorisation required`），测试没跟上；10-15 的精简裁决（G2）又把生产切换换成了按消费者整体切换，这套机制就此被取代 | **不能**：只能从运维命令 `ats batch`、`ats read` 和边界演练命令到达；生产切换用的 `consumer_cutover` 不导入它们（已用导入关系核对） | 排除出阻塞；该与旧机制一起走退役登记后删除或改写，本轮未做 |
| **B. 决策路径夹具债**（`test_phase_d_e2e` 3、`test_chief` 1） | 4 | 10-09～10-12 起 | `phase_d_e2e` 的 `broker` 夹具要求隔离环境拥有写入目的地，夹具没按 Phase F 隔离重写（13.6 就归在「停用下单所致」）；`test_chief` 的 CLI 测试种子数据没按冻结血缘重造 | 决策路径实际会走到，但门本身的 fail-closed 行为有独立覆盖：订单追溯 22 项、快照测试 12 项、独立验收 6 项都通过 | 排除出阻塞；等 Chief/交易路径切换时一并重写夹具 |
| **C. 已登记的既有失败**（`test_research` 4、`test_factset_earnings_insight` 4，以及 `test_workflow_data_cutover`、`test_sector_allocation`、`test_evidence_work_adoption`、`test_chain_regional_consumer` 各 1） | 12 | 10-07 之前或 13.6 当时就已登记 | `research` 是已迁入 Information 的旧 PEAD 研究抽取；`factset` 的期望是「按报告年龄降级」，而 F.0.5 已裁定不新增这类降级；`workflow_data_cutover` 检查旧调度器里一行导入；`evidence_work_adoption` 期望已退役的 Evidence Observer 角色；`chain_regional_consumer` 的夹具没拿到受管队列租约 | 旧专属，不可从新路径到达 | 沿用 13.6 的登记，排除出阻塞 |

合计 **57 个失败、0 个 error**（A 41 + B 4 + C 12），对比首轮的 86 + 21。`phase_d_e2e` 里有 2 个在中间几轮表现为 error，最终全量里记为失败，仍是同一个夹具问题。

### 没有做的

- 41 个旧批次/读取切换测试没有改写或删除。它们属于被 10-15 裁决取代的机制，该一起退役，但退役要走退役登记（见 12.8），本轮没有新增登记。
- 4 个决策路径测试夹具（`phase_d_e2e` 3、`test_chief` 1）没有补全：它们需要按冻结血缘重新造数据，工作量大；等价覆盖在订单追溯 22 项和快照测试里。

## 三、12.2 校验、守卫、迁移重开、SQLite 并发

| 检查 | 结果 |
|---|---|
| `openspec validate --all --strict` | **45 项全部通过**（含父 change 与 44 个主规格） |
| 架构守卫、切流守卫、数据层守卫、数据迁移测试 | 47 项通过；assurance surface 与记录守卫 50 项通过 |
| schema 迁移与重开 | `test_data_migrations`、`test_structured_foundation` 等迁移/重开用例在全量中通过；记录文档的指纹面数量已同步 |
| **SQLite 并发压力**（新增 `tests/test_sqlite_concurrency_stress.py`） | 3 项，连续 5 次运行全部通过：12 线程 × 25 次发布投影加 12 线程重复发布同一内容，行数恰为 301，没有丢失、没有重复、没有 `database is locked`；8 线程 × 10 次追加决策修订，修订号恰为 1…80 无空洞；16 线程争抢同一个触发，恰好 1 个赢家 |

之前所谓的「并发」测试很轻（24 次插入、或两个对象交替写 4 次），这三项才是真正的多线程争用。

## 四、12.3 订单追溯

要验证的是：每笔新系统的影子或实际订单，都能追到研究快照、决策修订、风控审查、Boss 批准。

| 层 | 结果 |
|---|---|
| **生产库** | `trades` 表 52 笔，全部在 2026-07-23 之前，即 Phase B 之前，没有一笔新系统订单；`decision_cycles`、`boss_approvals`、`decision_risk_reviews` 为 0 行。52 笔里能恢复决策链的有 33 条 legacy 修订，**9 笔无法恢复的登记为 `broken_link` 异常（状态 open）**，没有伪造关联 |
| **写路径** | 新系统订单与成交缺任一链接字段即拒绝写入（Phase C 2.1）；`test_clerk_linkage`、`test_e2e_approval_chain`、`test_clerk_e2e`、`test_shadow_ledger` 在当前代码通过 |
| **隔离环境完整链路** | `test_phase_f_integration_evidence` 22 项在当前代码通过（研究快照→revision→风控→人工批准→意图/回报，含断链判失败、无关旧成交与迟到成交被排除、伪造回执被拒）。本轮中途它曾因 Routine 的实时日历读取而全部报错，修复后重新通过 |

## 五、12.5 文档同步

| 文档 | 动作 |
|---|---|
| `docs/TARGET_WORKFLOW_DATAFLOW.md` | §14.5 补 Phase E 实施状态（含**生产日历库目前是空的**，事件路径从未被真实日历事件触发）；§14.6 补 Phase F 实施状态（读取 6 个消费者、调度 7 个工作流、交易/审批/记账未切、实盘锁定、有限 Routine 签核不是 §9.2 完整达标） |
| `docs/archive/PHASE_F_OPERATIONS.md` | 文首加说明：逐 scope 资格、入口证据包、精确授权等做法已被 10-15 裁决取代，当前口径以最终验收记录和退役 runbook 为准 |
| 一致性核对 | 执行前快照默认 60 秒失效（`config/risk.yaml`、`config.py`、TARGET §10/§15 一致）；Chief—Risk 默认最多 3 轮（TARGET 多处一致）；单一实盘入口（TARGET §14.6/§15.5 与实盘锁定状态一致） |

## 六、需要你知道的

1. **12.1 的「完成」带一个判断：** 我把 41 个旧批次/读取切换测试排除在阻塞之外，依据是它们属于被 10-15 裁决取代的机制，只能从 `ats batch`、`ats read` 和边界演练命令到达，生产的消费者切换（`ats consumer-cutover`）不导入它们。这个判断你可以推翻。
2. **事件日历库是空的**，TARGET 里已写明。它不影响本节验收，但意味着事件路径在生产上没有被真实日历事件走过。
3. **父 7.5（完整 Routine 能力）和 8.9（决策周期与 PEAD 评分窗口仍由旧调度承担）** 本轮没有动，按原裁决保持未完成。

## 附录：证据文件

- 首轮全量：`tmp/12.1/junit.xml`、`tmp/12.1/run.log`；失败归因：`tmp/12.1/attribution.txt`
- 提交对照：`tmp/12.1/bisect-<提交>.xml`（8 个提交）
- 修复后复跑：`tmp/12.1b/junit.xml`
- 并发压力测试：`tests/test_sqlite_concurrency_stress.py`
