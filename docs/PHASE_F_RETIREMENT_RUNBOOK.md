# 旧实现退役 Runbook（Phase F，2026-10-17）

> 写给要决定「什么时候可以删旧代码」的人。先说结论：**27 个登记条目里，3 个已验证可退出，24 个还不能退。** 新路径已在生产上跑，但旧的读写还在，退役要等清零、对账、回滚窗口三件事都有证据，而且由人批准。本文把「判据工具做完了」和「当前退出结果」分开写，不混在一起。

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
- **登记状态是 pending（16）**：其中真正仍绕过受治理读取面的是 Chief（3 处）、Risk Officer、Clerk，见 `docs/PHASE_F_12_1_CLEARANCE_20261017.md`。

## 二、判据工具（已完成；完成不等于条目已退出）

| 工具 | 回答什么 | 任务 |
|---|---|---|
| `ats retirement zero` | 登记的消费方还有没有经旧路径读取（zero / not_zero / partial） | 12.1 |
| `ats retirement reconcile` | 旧实现服务过的**全部用途**是否都已对账（缺一项就不算完成） | 12.2、12.6 |
| `ats retirement tombstone` | 登记为 retired 的是否真的读不到（登记说谎比还在 pending 更糟） | 12.4 |
| `ats retirement fallback-precheck` | 回退目标是否已退役或不可用，是则拒绝执行注定失败的回退 | 12.5 |
| `ats retirement decide` | 把清零、对账、回滚窗口、墓碑合成一条结论；缺一项就保持 pending 并写明缺什么 | 12.6 |
| `ats dispatch owner-switch / owner-rollback / owner-log` | 调度负责人的切换、切回与追加式日志 | 10.4、10.5 |

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

详见 `docs/PHASE_F_DATAFLOW_REMEDIATION_PLAN.md`、`docs/PHASE_F_10_5_*_RESULT.md`。

## 五、历史保留

- 调度切换日志（`schedule_owner_log`）与发布台账（`schedule_result_publications`）有禁止更新、删除的触发器。
- 作废的认领保留并带处置记录；已完成的认领切换后仍在。
- 投影按内容哈希编号，**不能被覆盖**；不再可用的投影只改状态为 failed，不删除。
- 墓碑保留原 `target_phase`；清除分两段（登记墓碑 → fail-closed 读取门 → 两段式清除），第二段需要单独批准。

## 六、安全恢复与观察窗口（12.3）

窗口 `phase-f-dispatcher-2026-10-17`：7 个研究工作流，起点 10-17，建议终点 10-28，六条停止条件，现状均未触发。回退：`ats dispatch owner-rollback --workflow <id> --actor <谁> --reason <为什么>`；全部停止：`launchctl bootout gui/$(id -u)/com.ats.phase-e`。详见 `docs/PHASE_F_12_3_ROLLBACK_WINDOW_20261017.md`。

## 七、退出责任（谁、什么条件下、做什么）

| 待退出的旧东西 | 条目举例 | 责任 | 触发条件与下一步 |
|---|---|---|---|
| 登记里只有模块路径、无法静态判定的消费方（45 行） | `runtime.cli`、`graph.pead`、`memory.store.*` | 退役登记的维护者 | 补角色映射（`MODULE_TO_ROLE`）或把消费方改为角色/入口点；之后复跑 `zero`，不需要改业务代码 |
| Chief、Risk Officer、Clerk 仍绕过受治理读取面 | `chief.legacy_table_direct_reads`、`decision_roles.unwired_to_governed_read_api`、`store.direct_trade_reads` | 决策与执行域（Phase F 之后） | 先接入受治理读取面并拿到 zero；**实盘保持锁定**，不在本 change 范围 |
| PEAD 图里路由门控的旧供应商回退 | `graph.pead.internal_risk_and_sizing`（4 处回退在 `graph/pead.py`） | 基本面域 | 观察窗口结束且不再需要「退回旧路径」之后，由你批准删除；删除前它们是回滚目标 |
| 旧调度器的硬编码串行运行与临时日记作业 | `scheduler.hardcoded_serial_run`、`scheduler.adhoc_journal_jobs` | 调度域 | 窗口结束、`com.ats.schedule` 确认不再需要后，由你批准删除；**在此之前不要加载它（它带 --live）** |
| 旧决策写路径、`client_order_id` 旧派生 | `memory.store.legacy_decision_write_path`、`trades.client_order_id.legacy_derivation` | 原 owner（决策/记账域） | 随 Chief/Clerk 迁移，历史清理由原 owner 负责 |
| 旧读模型与旧表 | `legacy_read_models`、`task_projections.legacy_columns`、`pead_dossier`、`sector_reviews` | 各读取方 | 对应消费方切到投影后复核 `zero` |

## 八、不再要求「旧全用途业务一致」

对账（`reconcile`）只要求旧实现服务过的、**新设计仍需要**的用途全部对账；不再要求旧的全部用途业务结果与新路径逐项一致。旧路径与新路径在不属于新设计的用途上有差异，不阻塞退出。

## 九、下一步

1. 观察窗口结束（建议 10-28）后复跑 `ats retirement zero` 与 `decide`，任务 12.8 据此逐项核验清零、对账、回滚窗口和墓碑，条件全满足才受控退役，缺条件继续 pending。
2. 任务 13.8、13.9 回写最终验收与门禁状态。
