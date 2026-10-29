# 测试基线：入口、测量条件与归因判据

> 为什么有这份文档：基线数字只有在**命令、依赖范围、执行环境条件**三者一起给出时才可复核。
> 曾经出现的失败是「121 failed / 263 errors」被当成回归排查，而其中 263 项 error 其实是执行环境
> 的删除配额打断了 fixture setup——环境限制伪装成了业务失败。本文件固定这三要素，并给出
> 「环境性失败 vs 业务回归」的判据。

---

## 1. 唯一入口

```bash
./scripts/run_tests.sh                       # uv sync --all-extras + uv run pytest（参数透传）
./scripts/run_tests.sh --check                # 只装依赖并校验各可选分组均可导入
./scripts/run_tests.sh --batched 8            # 受限环境下的分批取证（不能替代全量基线）
./scripts/run_tests.sh tests/test_risk.py -k survival
```

脚本内部等价于 `uv sync --all-extras` + `uv run pytest -q --tb=no -rA --basetemp=<仓库内 tmp/>`。
**不要**用 `PYTHONPATH=src .venv/bin/python -m pytest` 替代：后者不保证可选分组已装入，缺分组时
失败会伪装成业务断言失败（`ats.runtime.optional_deps` 现在会把这种缺失显式报成
「模块 + 所属 extras 分组」）。

- **依赖范围**：`pyproject.toml` 声明的全部 extras——`data`、`broker`、`memory`（chromadb）、
  `schedule`、`memory_persist`、`channel`、`dev`。测试面需要**全部**装入；部署机仍按角色挑选分组
  （见 `docs/DEVELOPMENT.md` 第 1 节），`memory` 的 chromadb 只是未接入运行时，装它不代表部署依赖。
- **环境探针**：`--check` 会导入 `apscheduler`、`pandas_market_calendars`、
  `langgraph.checkpoint.sqlite`、`ib_async`、`fastapi`、`chromadb`，任一缺失即报出模块名与分组。

---

## 2. 测量记录

### 2.1 权威测量（验收判据）

#### 2026-10-07（Phase F task 13.6，本机 uv 环境）

| 项 | 值 |
|---|---|
| 命令 | `./scripts/run_tests.sh -p no:randomly --junitxml=… --deselect tests/test_factset_index_semantic_local.py::test_semantic_core_passes_with_exact_index_and_technology_reviews` |
| 依赖范围 | `uv sync --all-extras`（探针 6 个可选模块全部就绪） |
| 执行环境条件 | 本机执行、**删除配额阈值提至 100000**、`--basetemp` 在仓库内 `tmp/13.6/`；`uv` 0.12.15 托管 CPython 3.12.12（`.venv/pyvenv.cfg` 内 `uv = 0.9.25`） |
| 结果 | **45 failed / 3072 passed / 0 errors / 2 deselected（2533.42s ≈ 42 分钟）** |
| 归因后 | **0 例本 change 引入的回归**（详见下表） |

**为什么带 `--deselect`**：首次全量在 36% 处停在
`test_factset_index_semantic_local::test_semantic_core_passes_with_exact_index_and_technology_reviews`
（已登记的预存在 hang，`e80671a` 引入，语义 PDF 管线）。该 hang 使全量无法跑完，
故重跑时**只排除这一条**——被排除的是 1 个测试项（参数化 2 例），不是一批测试。
两次运行的读数分别存档，不把「跑到 36% 的部分」与「跑完全量的部分」混为一谈。

**45 例的四类归属**（`scripts/attribute_test_failures.py` + `git worktree` HEAD 对照）：

| 类 | 例数 | 判据 |
|---|---:|---|
| **A 既有失败** | 27 | 在 `git worktree add /tmp/ats-base HEAD` 的干净基线上**同样失败**（逐条 id 对照，非文件级） |
| **B 停用下单所致** | 16 | 用户 2026-10-06 裁决的 C3 决定（`AUTO_EXECUTION_ENABLED = False`，见任务 7.9）。**有意为之，不是回归**；恢复条件已记录在 `PHASE_F_GROUP_PROGRESS.md` 的 `PF-7-03` |
| **C 退役表守卫命中** | 1 | `test_data_layer_write_cutover` 命中本 change 新增的 `intake_verification.py`。**已在本轮修复**（见下） |
| **D 顺序依赖** | 1 | `test_write_lease`，隔离运行通过；全量中受前序测试影响 |

**C 类的处置**：`intake_verification.py` 的 `NEUTRAL_FACT_TABLES` / `PROJECTION_TABLES`
只是**分类标签**（让 7.3 能判定「分析角色把观点写进中立事实表」），不执行 SQL。
守卫按字面量匹配退役表名，因此命中。已把该模块加入守卫的 `SANCTIONED`——
与既有的 `ownership.py` 同属「**为管制而列出表名**」这一类，注释里写明了理由。

**加白名单后必须证明守卫没被削弱**（否则「加白名单」就是消音器）：
向该模块的**函数体内**注入一条真实的 `INSERT INTO evidence_facts`，守卫仍判失败
（`_probe_write` 被点名）——因为白名单只放了 `<module>` 作用域，函数体不在其中。
读侧的三处 `execute` 亦已逐一核对：两处参数化，一处 f-string 但只从硬编码的
6 元组 `_PRODUCTION_WATCH_TABLES` 取值、以 `mode=ro` 连接，且列的是**新表**
`data_evidence_facts` 而非退役表。

#### 2026-10-19（父 change 12.1，本机 uv 环境）

| 项 | 值 |
|---|---|
| 命令 | `./scripts/run_tests.sh -p no:randomly --junitxml=… --deselect tests/test_factset_index_semantic_local.py::test_semantic_core_passes_with_exact_index_and_technology_reviews` |
| 结果 | 首轮 86 failed / 4334 passed / 21 errors；修复后最终全量 **57 failed / 4387 passed / 0 errors**（4444 个测试，81 分钟） |
| 归因 | 57 个失败：41 个旧批次/读取切换机制（被 10-15 裁决取代，只能从运维命令到达）、4 个决策路径夹具债、12 个 13.6 之前已登记的既有失败；没有产品缺陷。逐组根因、第一次失败的提交和处置见 `docs/archive/PHASE_F_PARENT_12_ACCEPTANCE_20261019.md` |
| 方法 | `scripts/attribute_test_failures.py` 按文件与签名归因；对没登记过的 72 个失败用 `git worktree` 在 8 个更早的提交上逐个对照，确定第一次失败的提交 |
| 之后的退役（2026-10-20） | Phase F 两个 change 归档后，读 git 忽略的 `docs/validation/` 记录、守卫已归档 change 的测试随之退役：删除 `tests/test_phase_f_record_guardian.py`、`tests/test_phase_f_progress_doc.py`，以及 `test_intake_cli.py` 中 3 个、`test_cutover_cli.py` 中 6 个校验 `PHASE_F_CUTOVER_RUNBOOK.md` 内容的测试。它们在 10-19 全量里都是通过的，所以失败数不变，通过数相应减少 |
| 旧调度器删除（2026-10-20） | 随 `src/ats/runtime/scheduler.py` 删除：`test_scheduler.py`、`test_scheduler_jobs.py`、`test_factset_semantic_schedule_local.py` 整份删除；`test_score_state`、`test_pead_async`、`test_events`、`test_chain_{articles,evidence,sources}`、`test_macro_strategy`、`test_sector`、`test_factset_earnings_insight`、`test_managed_cache_miss_local`、`test_workflow_data_cutover` 中只测旧级联/周报/打分窗口的用例删除；Phase F 调度与启动测试改为驱动 `dispatcher_daemon`。10-19 登记的 C 组里 `test_workflow_data_cutover` 那 1 个失败（检查旧调度器里一行导入）随之消失 |
| 旧手动入口与命名退役（2026-10-27，退役变更第 5 组） | 未重跑全量，按受影响文件对照。**新增**：`test_data_health_cli.py`（`ats data health --consumer` / `health-log`）、`test_retired_research_commands.py`（15 个旧命令返回替代写法且不运行分析师）、`test_schedule_owner_switch.py` 3 例（`pause`/`resume`、旧名只提示、有 running 认领时拒绝停用）。**改写**：`test_phase_f_startup_admission.py`、`test_phase_f_business_wiring.py` 里 3 处原本通过旧命令进入的用例改为直接调入口函数。**删除**：无测试文件删除。对照（同一批 id，干净基线 `3b2a745` 的 worktree 与当前树）：`test_phase_f_opinion_execution`（1 失败）、`test_phase_f_research_gate`（2）、`test_phase_f_schedule_runtime`（1）、`test_phase_f_scoped_intake`（8 失败 10 错误）、`test_phase_f_stage_one`（4）失败集合**完全相同**；`test_cutover_cli` 的 3 个失败与 `test_sector_allocation::test_sector_allocation_projection_published_with_input_refs` 在更早提交上同样失败；`test_phase_f_acceptance_reports` 25 例全过。`test_phase_f_independent_runner`（2 失败）、`test_phase_f_integration_evidence`（21 错误）两份当前树结果已有，干净基线上的对照仍在后台运行，出结果前不下「既有」结论。本批没有新增失败 |
| 登记修正与 allocation 退役（2026-10-27，退役变更第 6 组） | 未重跑全量，按受影响文件对照。**改写**：`test_legacy_retirement.py`（登记集合去掉 `task_projections.legacy_columns`、`evidence_fact_projections.legacy_observation_id`，改为断言二者留在 active 清册且无墓碑；`layer_verdict.allocation` 改断言 retired 且无消费方）。**新增**：`test_sector_viz.py` 1 例（历史行的 allocation 能读回，但展示档位只由 `layer_status` 推出）。**删除**：无。受影响文件：`test_sector_viz`、`test_layer_review`、`test_layer_report`、`test_layer_budget`、`test_sector`、`test_sector_cutover`、`test_layer_cutover`、`test_l1_analyst_context`、`test_legacy_retirement`、`test_retirement_clearance` 全过；`test_sector_allocation::test_sector_allocation_projection_published_with_input_refs` 1 例失败，在更早提交上同样失败（既有）。本批没有新增失败 |
| R4 旧分析代码删除（2026-10-27，退役变更第 7 组） | 未重跑全量，按受影响文件对照。**删除**：`test_monitor.py` 整份（5 例）；`test_broker_risk.py` 的 2 个 `assess` 用例；`test_triage.py` 里 4 个经 `pead.monitor` 驱动的用例；`test_information_analyst.py` 的 `test_monitor_chain…` 1 例；`test_collection_boundary.py` 里 `news_inputs` 的断言。**改写**：monitor 的两条保证（信息分析师不得发出预期变更、失败不发简报且不写 dossier）改为直接驱动 `documents.run_document_pass`；`test_triage` 剩余用例改指 `agents/information/triage`。对照：`test_triage`、`test_information_analyst`、`test_broker_risk`、`test_risk_validator`、`test_decision_risk_review`、`test_retired_research_commands`、`test_legacy_retirement`、`test_assurance_surface`、`test_information_closeout`、`test_information_output_quality`（196 例）、`test_phase_f_trader_a`、`test_dataflow_assurance`、`test_architecture_guards`、`test_fundamental_modes`、`test_intake`、`test_anthropic_consumer_boundaries`、`test_target_consumer_identity`、`test_dataflow_assurance_parity`、`test_phase_f_entry_evidence`、`test_phase_f_startup_admission`、`test_workflow_test_baseline` 全过；`test_collection_boundary::test_technical_review_publishes_per_entity_projections` 1 例失败，在 3b2a745 基线同样失败（既有）。**回归并修复**：`test_phase_f_business_wiring` 的 `run_layer_review` 用例失败，根因是第 5 组 2cd1fb5 误删 `@scoped_read("layer")`，已补回。本批没有遗留的新增失败 |
| 停用 / 恢复缺口修复（2026-10-27，退役变更 8.1 缺口 a/b/c） | 未重跑全量，按受影响文件对照（13 个文件 267 例，266 通过）。**新增 10 例**：`test_schedule_owner_switch.py` 9 例（作废触发恢复后不重跑、定时/手动跳过记录且去重、Dispatcher 拥有时无记录、停用区间内触发记为 `skipped_by_pause`、未结束的停用、从未迁移不算停用、已有认领不重分类、`voided_by_pause`、事件触发）、`test_workflow_ownership.py` 1 例（日历路径跳过记录且去重）。**删除 / 改写**：无。唯一失败 `test_phase_f_schedule_runtime::test_committed_result_survives_control_record_crash_without_republication` 在 3b2a745 基线同样失败（既有）。本批没有新增失败 |
| **全量权威测量（2026-10-27，退役变更 10.1）** | 命令 `./scripts/run_tests.sh -p no:randomly -v --junitxml=var/test_runs/retire-final.xml`（HEAD f74d21a，含缺口 a/b/c 修复）。结果 **73 failed / 4507 passed / 10 errors**（4590 个测试，2 小时 15 分）。**与 10-19 的 57 failed / 0 errors 不同，且差异不是退役造成的**：把这 83 个失败/错误涉及的 17 个文件，在退役变更起点 3b2a745（第 1 组之前）的干净 worktree 上用同一命令逐文件重跑，失败集合与现在**逐项一致（83 对 83，新增 0、消失 0）**。即退役 8 组改动没有引入任何失败，也没有修好任何失败。构成：test_batch_manifest 17、test_read_cutover 16、test_batch_cli 5、test_cutover_cli 3 共 41 个，沿用 10-19 裁决的「旧批次/读取切换机制」；test_phase_d_e2e 3、test_phase_f_research_gate 2、test_phase_f_schedule_runtime 1 等沿用既有登记；**10-19 之后、退役之前新出现的**有 test_phase_f_scoped_intake 18（8 failed + 10 errors）与 test_phase_f_stage_one 4（`unregistered implementation dependencies: …issuer_ir…`，由 issuer_ir_files 入库那批提交引入的指纹登记缺口）、test_research 4、test_factset_earnings_insight 4 等，它们在 3b2a745 就已红，不属于本 change，**未处置，只登记**。本 change 对测试集的净变化：4444 → 4590 个测试（含退役前其他变更），本 change 自己的删除/新增见上面第 5–8 组各行。 |
| 交易路径切换第 2 组（2026-10-28，`switch-trade-path-and-retire-r5` 2.1–2.9、2.11） | 未重跑全量，按受影响文件对照（调度/切换/审批/退役相关 21 个文件 417 通过，决策与上下文相关 234 通过，审批与排程新增文件全过）；**最终全量放在第 3 组之后，且必须先在干净 worktree 重测证据指纹基线（`src/` 已变）**。**新增测试文件**：`test_approval_cli.py`（18 例：待审批列表、风控未过不出现、hash 前缀、批准时刷新快照并对同一修订确定性复审、复审结论不同则拒绝、修改意见与 2 次上限、提醒不带链接、supersede、重复批准返回首个结果）、`test_trade_schedules.py`（14 例：停用时不注册且不在期望触发集、交易日历、启用后认领去重、补跑只补 resume 之后的 tick）、`test_decision_read_audit.py`（7 例：旧决策表读取白名单守卫）、`test_approved_not_executed.py`（6）、`test_chief_context.py`（7：风控状态块、陈旧与缺失标记）、`test_live_authorisation_provenance.py`（8）、`test_ibkr_client_id.py`（5）。**恢复**：`test_chain_to_factor.py` 3 例（Chief 看到量化与复合排名的分歧但没有权重、纯量化不增加内容、`feed_chief_basket` 关闭即撤）。**改写**：`test_chief.py`、`test_chief_snapshot.py`、`test_chief_temporal.py`、`test_phase_d_e2e.py`、`test_decision_snapshot.py`、`test_consumer_cutover.py`、`test_consumer_health.py`、`test_live_route_drill.py`、`test_retirement_clearance.py` 等改为驱动新的读取入口与审计表。**删除（共 18 个函数，随旧读取路径一起退役）**：旧上下文装配与双读对账（`test_assemble_*` 4 例、`test_sector_block_renders_layer_boom_scores`、`test_pead_block_renders_signal_chain_summary`、`test_dual_read_paths_agree_and_diffs_are_zero`、`test_dual_read_counts_disagreement`）、旧评分与交易历史读取（`test_score_actionable_once_then_consumed`、`test_recent_actions_shows_execution_status`、`test_unexecuted_proposal_does_not_hard_suppress`、`test_v1_is_background_to_the_chief`、`test_v2_is_actionable_to_the_chief`、`test_promoted_v1_becomes_actionable`、`test_consumed_score_stays_background`、`test_score_age_comes_from_the_run_not_the_monitor_bump`）、仅研究消费者可切换的两条旧约束（`test_only_research_read_consumers_can_switch`、`test_restore_only_moves_research_consumers`，已被 2.11 给 risk/trader 补读路由取代）。**登记、不处置的失败（本组之前就红，干净基线同样失败）**：`test_cutover_cli` 3、`test_sector_allocation::test_sector_allocation_projection_published_with_input_refs` 1、`test_collection_boundary::test_technical_review_publishes_per_entity_projections` 1、`test_phase_f_schedule_runtime::test_committed_result_survives_control_record_crash_without_republication` 1、`test_phase_f_research_gate` 2。本组没有新增失败；中途出现的 1 个（`owner_switch` 拒绝信息改词后 `test_schedule_owner_switch` 的正则不匹配）已修，复跑 25 通过。 |

#### 历史权威测量

| 项 | 值 |
|---|---|
| 命令 | `uv run pytest`（全量） |
| 依赖范围 | `uv sync --all-extras` |
| 执行环境条件 | 执行环境**不设**文件删除配额；`uv` 托管 CPython 3.12.12（`.venv/pyvenv.cfg` 内 `uv = 0.9.25`） |
| 结果 | **135 failed / 1398 passed / 22 warnings（170.50s），error = 0** |
| 日期 | 2026-09-22 |

该数字已回填 `docs/ARCHITECTURE.md` §15.1，并附按簇归因（116 项同源 = 86%）。

### 2.2 受限环境测量（仅作对照，不得用作验收判据）

| 测量 | 命令与条件 | 结果 |
|---|---|---|
| 全量，仓库内 `--basetemp` | 沙箱执行环境，删除配额 50/轮 | 120 failed / 1147 passed / **266 errors**（536s） |
| 全量，不受删除配额 | `--basetemp` 同上，关闭沙箱守卫 | 81 failed / 825 passed / **648 errors**（146s） |
| 分批取证 | `scripts/run_tests_batched.py --batch-size 6`，23 批 | 133 failed / 1161 passed / **264 errors**（877s），其中环境性 12 |
| 对照：默认临时根 | 沙箱执行环境，无 `--basetemp` | 全部测试 error（`PermissionError: EEXIST`，pytest 默认临时根无法创建） |

三次受限测量的 error 都是**同一现象**：从某个测试序号起，其后所有测试在 setup 阶段失败
（不受限那次从第 906 个测试起、共 648 项，全部为 setup 中断）。**这是环境限制，不是回归**——
被中断的测试从未执行到断言。

---

## 3. 执行环境的两条限制与影响范围

1. **默认临时根不可用**。沙箱代理了文件系统操作，pytest 创建 `/…/T/pytest-of-*` 时得到
   `PermissionError: EEXIST`，整库跑会全数 error。**对策**：`--basetemp` 指向仓库内目录
   （`scripts/run_tests.sh` 已默认如此，目录为 gitignore 的 `tmp/pytest-basetemp/`）。
2. **删除配额**。pytest 运行期清理临时编号目录会累计删除量，触及配额后守卫以 `SystemExit` 打断
   fixture setup；`tests/conftest.py` 的 autouse fixture `_isolate_db(tmp_path, …)` 使每个测试都依赖
   临时目录，故障因此被**放大**为整库级失败。
   **影响范围**：配额触发点之后的全部测试，表现为 setup 阶段 error，与被测代码无关。
   **对策**：①在不受该配额约束的环境测量；②或按文件分批运行（`--batched`），每批一个独立
   pytest 进程与独立临时目录根。

> 分批取证**不能**替代全量基线：批次之间不共享进程内状态、临时目录生命周期与全局缓存，
> 跨批次的顺序依赖与资源竞争不会显现。它只用于**枚举失败**，判定回归仍以全量基线为准。

---

## 4. 归因判据（环境性 vs 业务性）

判定顺序（`ats.workflow.test_baseline` 实现了同样的规则，可直接跑
`render_summary(summarize(parse_junit_xml(...)))`）：

1. **setup/teardown 失败**（JUnit `<error>`）优先判为环境性候选；call 阶段失败（`<failure>`）
   是业务断言失败。
2. 命中已知环境签名即判环境性：`SAFE_DELETE_BULK_CONFIRM_REQUIRED`、删除配额、
   `pytest-of-` / 临时目录 `PermissionError`、`SystemExit`、只读文件系统、设备无空间。
3. 命中 `No module named X` 且 X 属于某个 extras 分组 → 环境性（依赖缺失），并报出模块与分组。
4. 其余按根因签名分簇：`no such table: <table>`（同一缺失表）、
   `unexpected keyword argument '<arg>'`（接口签名漂移）。
5. 单一环境性簇占比 ≥ 25% 时，判定为**整库级环境性中断**：给出放大机制说明与受影响范围，
   **不以逐项业务失败的形式呈现**。
6. 未匹配任何签名的失败进入 `unattributed`，必须标注数量与归因归属，不得以「其余失败」带过。

### 4.1 分批取证结果（2026-09-22，23 批）

| 簇 | 类型 | 规模 | 判据 |
|---|---|---:|---|
| `missing table: source_documents` | 业务 | 45 | 同一张缺失表；位于 `_retire_data_tables()` 的 DROP 名单 |
| `missing table: evidence_observations` | 业务 | 33 | 同上 |
| `interface signature drift: consumer` | 业务 | 9 | 调用方传入被调方不接受的关键字参数 |
| `missing table: data_sources` | 业务 | 7 | 同一张缺失表 |
| `missing table: newsletter_cursors` | 业务 | 4 | 同一张缺失表 |
| `missing table: document_candidates` | 业务 | 3 | 同一张缺失表 |
| `interface signature drift: legacy_repository` | 业务 | 3 | 接口签名漂移 |
| `missing table: document_versions` / `evidence_failures` | 业务 | 各 1 | 同一张缺失表 |
| `environmental: 守卫以 SystemExit 打断 fixture setup` | 环境 | 12 | setup 阶段 `SystemExit: 1` |
| `unattributed` | 业务 | 279 | 未匹配已知签名，逐项归因后登记（本阶段不要求清零） |

前 8 簇共 106 项 + 签名漂移 12 项即缺陷 ① 的同源失败面，与 §2.1 权威归因的
「101 项缺表 + 12 项签名漂移」方向一致（分批与全量的计数口径不同，故不完全相等）。

---

## 5. 复现方式

```bash
# 1. 装依赖并校验
./scripts/run_tests.sh --check

# 2. 全量（不受删除配额约束的环境）
uv run pytest -q --tb=no -rA --basetemp=tmp/pytest-basetemp/run --junitxml=tmp/baseline.xml

# 3. 归因摘要
uv run python -c "
from pathlib import Path
from ats.workflow.test_baseline import parse_junit_xml, summarize, render_summary
print(render_summary(summarize(parse_junit_xml(Path('tmp/baseline.xml').read_text()))))"

# 4. 受限环境：分批取证
uv run python scripts/run_tests_batched.py --batch-size 6
```

## 6. 逐例归因（13.6 用法）

`test_baseline` 给的是**簇**的摘要；13.6 要求对**每一例**给出根因与处置，因此需要逐例
对照。判定必须以**测试 id** 为单位，不能以文件为单位——同一个文件里可能既有既有失败
也有本轮新增。

```bash
# 4a. 逐例归因：按文件分组、按消息签名归并，并与已登记清单对照
uv run python scripts/attribute_test_failures.py tmp/baseline.xml \
  --registered tmp/13.6/registered-preexisting.txt

# 4b. 对「未登记」的失败，在干净基线上复跑同一批 id —— 这是判定「既有」的唯一硬证据
git worktree add /tmp/ats-base HEAD
PYTHONPATH=/tmp/ats-base/src uv run python -m pytest <同一批 id> -q --tb=no \
  --basetemp=/tmp/ats-base-tmp
git worktree remove /tmp/ats-base --force
```

**为什么必须在干净 worktree 上对照，而不是凭印象改判**：「看起来眼熟」不是判据。
本次 45 例里，若只按「印象」判，会把 16 例**用户明确裁决的停用下单**当成新回归去「修」——
而修它的正确动作是补授权（方案 A），不是改测试。

**顺序依赖与真回归的区分**：全量失败但在**隔离**运行下通过的，是顺序依赖
（`test_write_lease` 即此类），不是回归。反之隔离运行仍失败的才是真问题。

## 与 Phase A 验收的关系

Phase A（`openspec/changes/establish-workflow-contracts-and-green-baseline`）的实测基线、
失败归因与「需求场景 → 验证方式」对照表记录在 `docs/archive/PHASE_A_ACCEPTANCE.md`。本文件定义
「怎么测、怎么归因」，那份文件记录「测出来是什么」。
