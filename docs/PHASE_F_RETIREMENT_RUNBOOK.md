# 旧实现退役 Runbook（Phase F，2026-10-17）

> 写给要决定「什么时候可以删旧代码」的人。先说结论（**2026-10-17 的快照，最新数字见下面第〇节**）：**27 个登记条目里，4 个已退出（2026-10-20 经你批准退役 `scheduler.hardcoded_serial_run`），23 个还不能退。** 新路径已在生产上跑，但旧的读写还在，退役要等清零、对账、回滚窗口三件事都有证据，而且由人批准。本文把「判据工具做完了」和「当前退出结果」分开写，不混在一起。

## 〇、2026-10-27 更新：分组、依赖与门（退役变更 retire-legacy-and-migration-tooling）

G0（新路径人工核验）已于 **2026-10-27 通过并签字**（`openspec/changes/retire-legacy-and-migration-tooling/verification.md`，78 项全部通过）。从这一天起，登记处不再只是一张平铺的清单，而是带批次、依赖与门的退役计划。**下面第一至第九节保留 2026-10-17 的原文与快照，数字以本节为准。**

**登记处现在是 41 条**（10-27 起 `task_projections.legacy_columns`、`evidence_fact_projections.legacy_observation_id` 移出待退项：前者是信息分析师的抽取缓存，后者是事实投影的血缘键，都在用）（`ats retirement decide` 会逐条列出）：

| 批次 | 条数 | 内容 | 门 |
|---|---|---|---|
| R1 命名与入口残留 | 5 | 旧手动命令、负责人 legacy 模式、证据命令（保留命题治理命令）、`consumer-cutover` 命名、文档里的迁移措辞（10-27 已退役 4 条；`legacy_entry.research_owner_legacy_mode` 仍 pending：对外用法已换成 `pause` / `resume`，内部取值 `legacy` 保留，取消它另案处理） | G0 |
| R2 研究侧切换期工具 | 0（原 5 条已于 10-27 **整组转入 R5**：与交易侧的演练、验收和接入核验相连，研究侧已于 4.1 解耦） | — | — |
| R3 登记修正与研究侧已登记条目 | 1 | `layer_verdict.allocation`（10-27 已退役：所有读取改按层状态推出，字段保留）；另两条经核查是在用记录，移出待退项 | G0 |
| R4 只被旧入口用到的旧分析代码 | 4 | `risk_manager`、`risk_validator.apply_guardrails`、`pead/monitor`、`pead/research`（10-27 已全部退役并删除；交易链路其余代码保留；顺带删除两个因此变成孤儿的模块 `pead/triage`、`data/products/news_inputs`） | G0 |
| R5 交易路径相关 | 20（含从 R2 转入的 5 条） | 读路由与资格机制、交易侧切换工具、旧读模型表（含新补登记的 `macro_reviews`、`technical_reviews`）等；本 change 只登记 | **trade_path_cutover**（外部门） |
| 未分组（早期阶段已退役） | 11 | 例如 `workflow_memory.*`、`scheduler.hardcoded_serial_run` | — |

**两道门：**
- **G0**：由 `ats retirement gate --show` 只读汇总 verification.md——每一项须是「通过 / 接受缺口 / 不适用」，空和「未通过」都挡住，G0 结论须签字。现在显示已开。
- **trade_path_cutover**：交易路径（Chief、Clerk、Risk、Trader、Boss）切换的外部门，由另一个 change 满足，**这里永远不会放行**。所以 R5 的 20 条在本 change 里只登记、不退。

**依赖：** 条目的 `depends_on` 列出必须先退役的前序条目，加载时校验不指向未登记条目、不成环。例如 `naming.migration_terms_in_docs` 要等四个入口类条目都退役；`agents.pead.monitor`、`agents.pead.research` 要等 `legacy_entry.research_manual_commands`；`migration.analysis_verification` 与 `migration.intake` 一起走。`ats retirement decide` 现在会把「前序未退役」和「门未开」写进缺项，缺项任何一条存在都不得判可退出。

**这次修正的登记错误：**
- `pead_dossier` 不再是待退项：它是基本面分析师自有的在用记录（财报前基线存在这里，事件评审读它，同一财季只留一份档案），留在在用清册里，不登记墓碑。
- `macro_reviews`、`technical_reviews` 补登记（消费方是 Chief，归 R5）。
- 8 条「登记为 retired 却无法证明」的条目，`ats retirement zero` 现在显示「**待补证**」，不再看起来像通过；补 `MODULE_TO_ROLE` 映射后重扫属于退役变更的 6.1。

**每批怎么退（V1–V6，设计见 design.md D5）：** 退役前 V1 静态无引用、V2 运行无调用、V3 `ats retirement decide` 的依赖与门检查；退役后 V4 测试与基线对照、V5 重启服务后观察下一次定时触发与数据刷新、V6 历史可读与已退役提示。**研究与数据侧验证通过即退役，无需等满一周；交易链路暂不退役**（2026-10-27 用户裁决）。

**有意保留、不迁移：** `ats evidence claims/report/propose/proposals/review/kbreview` 与 `ats sector kbperturb`——人在回路的治理与开发工具，不是分析师入口（见 inventory.md 4.1）。

### 〇·二、R5 的接续方式（任务 9.2）

R5 的 20 条由 `openspec/changes/switch-trade-path-and-retire-r5` 接手。门 `trade_path_cutover` 在那个 change 的 5.4（观察窗口结论由你签字）时才记为已满足，之后才按下表逐条退役；本 change 不替它开门。

| R5 条目 | 接手任务（switch-trade-path-and-retire-r5） |
|---|---|
| `chief.legacy_table_direct_reads` | 2.1 改读快照 → 6.1 退役 |
| `store.direct_trade_reads` | 2.2 改读 Internal State → 6.2 退役 |
| `sector_reviews`、`legacy_read_models` | 6.3 |
| `macro_reviews`、`technical_reviews` | 6.5 |
| `decision_roles.unwired_to_governed_read_api` | 6.4 |
| `memory.store.legacy_decision_write_path` | 2.3 改读 → 7.1 移除镜像写 |
| `trades.client_order_id.legacy_derivation` | 7.2 |
| `scheduler.adhoc_journal_jobs`、`performance.legacy_snapshot` | 7.3 |
| `journal.report.render_ledger` | 7.4 |
| `risk.checks.pre_trade_adapter` | 7.5 |
| `migration.batch_read_cutover` | 8.5 |
| `migration.schedule_cutover` | 8.6 |
| `migration.intake`、`migration.analysis_verification` | 8.7（`isolated_entry` 只删可删部分，见其 8.3） |
| `migration.dispatch_dedup` | 8.8（必须在 8.6 之后） |
| `migration.trade_cutover_tooling` | 8.4（`joint_cutover`、`boundary_drill`、`acceptance_*`、`paired_business`、`shadow_*`、`ats cutover`） |
| `migration.read_route_and_qualification` | 9.2–9.3（读路由与资格机制收窄：`consumer_api.read_input` 收窄为「查声明 → 查数据产品 → 读」，再删路由表、资格账本、指纹绑定与边界接线证据） |

每条退役沿用本 change 的做法：V1–V3 → 删除 → V4–V6，并把登记改成 retired、附清零证明；`ats retirement decide` 显示「外部门未满足」的 20 条，在那个 change 的 5.4 之后变为可判定。

**核对时发现、与那个 change 对不上的地方（2026-10-27，退役 change 的 9.2）：**

1. **三个模块没有任务认领。** `exit_requirements`（被 `data/assurance.py` 引用）、`business_replay_inputs`（被 `consumer_api`、`route_switch` 引用）、`cutover_guards`（登记里已补进 `migration.read_route_and_qualification` 的退出条件）在那个 change 的 tasks.md 与 design.md 里都没出现。`shadow_ledger` 被 Clerk 引用，只被 8.4 的 `shadow_*` 通配涵盖，没有单独写明引用要先拆开。建议在 8.2 的调用图清单和 9.3 的删除清单里补上。
2. **6.3、6.5 的前提没有达成。** 6.3 写「在退役 change R1 清掉 `runtime.cli` 消费方之后」，但 R1 只退役了旧手动入口，`ats macro/sector/technical show` 等只读命令仍读 `macro_reviews`、`sector_reviews`、`technical_reviews`（登记里 `runtime.cli` 仍是这四条的消费方）。6.3、6.5 需要自己决定这些只读命令改读投影还是随表一起处置。
3. **「停用时记录跳过原因」已于 10-27 实现。** 那个 change 的 2.7 验证要求停用的 `chief-daily` / `clerk-daily` 记录跳过原因；退役 change 核对时发现定时与日历路径只写日志、覆盖核验也不认识「因停用而跳过」，已修复（手动、定时、日历三条路径写 `skipped_owner_legacy`；`compare_triggers` 区分 `skipped_by_pause` / `voided_by_pause`，见 inventory.md §十）。2.7 直接沿用，不必重做；`ats dispatch runtime-expected` / `runtime-compare` 属于 8.8 要删的 `runtime-*`，到时需要把覆盖核验搬到保留的命令下。
4. **「负责人默认 `paused`」的写法对不上。** 现行实现里停用就是负责人取值 `legacy`（命令是 `ats dispatch pause` / `resume`，内部取值保留），没有 `paused` 这个取值。代码里没有 `paused` 这个取值，2.7 要新建的两个工作流不能照此注册；要么沿用 `legacy`，要么先做长期方案（取消停用开关，改由 `research_schedules.yaml` 的 `enabled` 控制，需要规格变更，另案）。
5. **proposal.md 里「11 条已登记条目」是旧数字。** 登记处现在 R5 是 20 条（含原 R2 转入的 5 条与新补登记的 `macro_reviews`、`technical_reviews`）。

## 一、当前退出结果（与判据工具是否完成无关）

登记状态与独立核验的对照（`ats retirement decide`，带演练引用 `phase-f-dispatcher-2026-10-17`；生成时间 2026-10-17）：

| 条目 | 登记状态 | 责任域 | 消费方 | zero / not_zero / partial | 判定 |
|---|---|---|---|---|---|
| `agent.evidence_observer` | retired | target-dataflow-assurance | 0 | 0 / 0 / 0 | 可退出 |
| `reconcile.origin_manual_fallback` | retired | clerk-ledger | 0 | 0 / 0 / 0 | 可退出 |
| `reconcile.partial_fill_as_filled` | retired | clerk-ledger | 0 | 0 / 0 / 0 | 可退出 |
| `agents.risk_validator.apply_guardrails` | retired | deterministic-review | 1 | 0 / 0 / 1 | pending |
| `chief.legacy_table_direct_reads` | pending | research-snapshot | 1 | 0 / 1 / 0 | pending |
| `decision_roles.unwired_to_governed_read_api` | pending | target-dataflow-assurance | 4 | 2 / 2 / 0 | pending |
| `evidence_fact_projections.legacy_observation_id` | pending | data-layer-architecture | 2 | 0 / 0 / 2 | pending |
| `graph.pead.internal_risk_and_sizing` | retired | fundamental-pead | 2 | 0 / 0 / 2 | pending |
| `journal.report.render_ledger` | pending | internal-state-api | 1 | 0 / 0 / 1 | pending |
| `layer_verdict.allocation` | pending | layer-analyst | 4 | 1 / 0 / 3 | pending |
| `legacy_read_models` | pending | task-projection | 2 | 0 / 0 / 2 | pending |
| `memory.store.legacy_decision_write_path` | pending | decision-audit-store | 3 | 0 / 0 / 3 | pending |
| `pead_dossier` | pending | fundamental-pead | 2 | 0 / 0 / 2 | pending |
| `performance.legacy_snapshot` | pending | clerk-ledger | 2 | 0 / 0 / 2 | pending |
| `risk.checks.in_place_clipping` | retired | deterministic-review | 2 | 0 / 0 / 2 | pending |
| `risk.checks.pre_trade_adapter` | pending | deterministic-review | 2 | 0 / 0 / 2 | pending |
| `runtime.server.in_process_resume_dedup` | retired | approval-lifecycle | 2 | 0 / 0 / 2 | pending |
| `scheduler.adhoc_journal_jobs` | pending | clerk-ledger | 1 | 0 / 0 / 1 | pending |
| `scheduler.hardcoded_serial_run` | pending | run-contracts | 2 | 0 / 0 / 2 | pending |
| `sector_reviews` | pending | sector-allocation | 2 | 0 / 1 / 1 | pending |
| `store.direct_trade_reads` | pending | internal-state-api | 3 | 0 / 1 / 2 | pending |
| `task_projections.legacy_columns` | pending | task-projection | 2 | 0 / 0 / 2 | pending |
| `trades.client_order_id.legacy_derivation` | pending | authorization-gate | 3 | 0 / 0 / 3 | pending |
| `workflow_memory.evidence_fact_projections` | retired | data-layer-architecture | 2 | 0 / 0 / 2 | pending |
| `workflow_memory.evidence_facts` | retired | data-layer-architecture | 2 | 0 / 0 / 2 | pending |
| `workflow_memory.evidence_failures` | retired | data-layer-architecture | 2 | 0 / 0 / 2 | pending |
| `workflow_memory.evidence_observations` | retired | data-layer-architecture | 4 | 0 / 0 / 4 | pending |

**怎么读这张表**
- **可退出（3）**：没有任何消费方，且回滚已演练、墓碑一致。
- **登记状态是 retired、但独立核验判 pending（8）**：登记处写着已退役，工具却无法证明消费方已清零——它们的消费方登记为模块路径或函数（如 `graph.pead`、`runtime.cli`），没有角色映射，工具拒绝猜测，所以判「状态未知」。这不是说它们还在被读取，而是**没有证据说它们没有**。在补齐映射、拿到 zero 之前，不能把「登记为 retired」当成「已确认可以删」。
- **登记状态是 pending（16）**：其中真正仍绕过受治理读取面的是 Chief（3 处）、Risk Officer、Clerk，见 `docs/archive/PHASE_F_12_1_CLEARANCE_20261017.md`。

## 二、判据工具（已完成；完成不等于条目已退出）

| 工具 | 回答什么 | 任务 |
|---|---|---|
| `ats retirement zero` | 登记的消费方还有没有经旧路径读取（zero / not_zero / partial） | 12.1 |
| `ats retirement reconcile` | 旧实现服务过的**全部用途**是否都已对账（缺一项就不算完成） | 12.2、12.6 |
| `ats retirement tombstone` | 登记为 retired 的是否真的读不到（登记说谎比还在 pending 更糟） | 12.4 |
| `ats retirement fallback-precheck` | 回退目标是否已退役或不可用，是则拒绝执行注定失败的回退 | 12.5 |
| `ats retirement decide` | 把清零、对账、回滚窗口、墓碑合成一条结论；缺一项就保持 pending 并写明缺什么 | 12.6 |
| `ats dispatch pause / resume / owner-log` | 研究工作流的停用、恢复与追加式日志（原 `owner-switch / owner-rollback`，已改名，旧名只给提示） | 10.4、10.5 |

这些工具都是**只读**的：它们给出结论和「若满足条件则应写成什么」，不修改登记表。把某个条目改成 retired 是单独的、由人批准的一步。

## 三、生产清零怎么做

1. `ats retirement zero`：看每个消费方的结论。判读：**zero**＝已走受治理读取面；**not_zero**＝仍绕过；**partial**＝无法静态判定（缺角色映射）。
2. 静态扫描只是辅证：历史材料、停用的离线诊断工具保留，不等于生产消费者没清零；以「真实调用链」为准——7 个研究工作流在 Dispatcher 下的完成认领（`var/phase_f_dispatch.sqlite`）与 `ats dispatch owner-log`。
3. 扫描的入口点登记在 `intake_verification.CONSUMER_ENTRY_POINTS`。**入口点漏了真正的读取模块，会把已切换的消费者说成「没接入」**（Fundamental 就出过这个偏差，已修）。新增或改名读取模块时同步登记。
4. 不为让条目可退出而降低判据：缺项只阻断受影响的范围。

## 四、新需求的数据覆盖（12.2）

| 方面 | 现状 |
|---|---|
| 读取路由 | Information、Fundamental、Layer、Sector、Macro、Technical 均为 target，每日健康检查通过 |
| 采集 | 刷新控制器已启用：证据抽取、多标的财务、Macro 新闻与指标（试点名单，上线后补缺口） |
| 财务包 | 30 只截面标的里 9 只有受治理财务；ASML 现金流缺口已按「只缺 CFO/CapEx 且同源上一季齐全」放行并写明 gaps |
| 官方披露 | TSM/ASML Q3 的 SEC 披露进不来（本机经 Clash 访问 sec.gov 被重置），相应的事件评审按设计不触发 |
| 历史引用 | 8 条缺陷旧 Layer 投影标为 failed、不删除；发布台账只增不改 |

详见 `docs/archive/PHASE_F_DATAFLOW_REMEDIATION_PLAN.md`、`docs/PHASE_F_10_5_*_RESULT.md`。

## 五、历史保留

- 调度切换日志（`schedule_owner_log`）与发布台账（`schedule_result_publications`）有禁止更新、删除的触发器。
- 作废的认领保留并带处置记录；已完成的认领切换后仍在。
- 投影按内容哈希编号，**不能被覆盖**；不再可用的投影只改状态为 failed，不删除。
- 墓碑保留原 `target_phase`；清除分两段（登记墓碑 → fail-closed 读取门 → 两段式清除），第二段需要单独批准。

## 六、安全恢复与观察窗口（12.3）

窗口 `phase-f-dispatcher-2026-10-17`：7 个研究工作流，起点 10-17，**终点 10-18（用户裁决，取代原建议的 10-28）**，六条停止条件；10-18 核对结果见 `docs/archive/PHASE_F_12_8_RETIREMENT_20261018.md`（五条未触发，第③条有一次 LRCX 信息简报缺失，属数据缺口，待用户裁决是否算触发）。回退：`ats dispatch owner-rollback --workflow <id> --actor <谁> --reason <为什么>`；全部停止：`launchctl bootout gui/$(id -u)/com.ats.dispatcher`。详见 `docs/archive/PHASE_F_12_3_ROLLBACK_WINDOW_20261017.md`。

## 七、退出责任（谁、什么条件下、做什么）

| 待退出的旧东西 | 条目举例 | 责任 | 触发条件与下一步 |
|---|---|---|---|
| 登记里只有模块路径、无法静态判定的消费方（45 行） | `runtime.cli`、`graph.pead`、`memory.store.*` | 退役登记的维护者 | 补角色映射（`MODULE_TO_ROLE`）或把消费方改为角色/入口点；之后复跑 `zero`，不需要改业务代码 |
| Chief、Risk Officer、Clerk 仍绕过受治理读取面 | `chief.legacy_table_direct_reads`、`decision_roles.unwired_to_governed_read_api`、`store.direct_trade_reads` | 决策与执行域（Phase F 之后） | 先接入受治理读取面并拿到 zero；**实盘保持锁定**，不在本 change 范围 |
| PEAD 图里路由门控的旧供应商回退 | `graph.pead.internal_risk_and_sizing`（4 处回退在 `graph/pead.py`） | 基本面域 | 观察窗口结束且不再需要「退回旧路径」之后，由你批准删除；删除前它们是回滚目标 |
| 旧调度器的临时日记作业 | `scheduler.adhoc_journal_jobs` | 调度域 | 随 Clerk 排程（交易路径切换）收口。`scheduler.hardcoded_serial_run` 已于 2026-10-20 退役：旧调度器代码与 `com.ats.schedule` 一并删除 |
| 旧决策写路径、`client_order_id` 旧派生 | `memory.store.legacy_decision_write_path`、`trades.client_order_id.legacy_derivation` | 原 owner（决策/记账域） | 随 Chief/Clerk 迁移，历史清理由原 owner 负责 |
| 旧读模型与旧表 | `legacy_read_models`、`task_projections.legacy_columns`、`pead_dossier`、`sector_reviews` | 各读取方 | 对应消费方切到投影后复核 `zero` |

## 八、不再要求「旧全用途业务一致」

对账（`reconcile`）只要求旧实现服务过的、**新设计仍需要**的用途全部对账；不再要求旧的全部用途业务结果与新路径逐项一致。旧路径与新路径在不属于新设计的用途上有差异，不阻塞退出。

## 九、下一步

1. 观察窗口已于 10-18 结束（用户裁决）。12.8 已复跑 `ats retirement zero` 与 `decide` 并逐项核验：3 个可退出（本来就是 retired）、24 个继续 pending，没有改登记；要退役任何新条目须用户明确同意。24 个 pending 条目还缺的清零与对账，见 `docs/archive/PHASE_F_12_8_RETIREMENT_20261018.md`。
2. 任务 13.8、13.9 已回写最终验收与门禁状态：`docs/archive/PHASE_F_FINAL_ACCEPTANCE_20261018.md`。
