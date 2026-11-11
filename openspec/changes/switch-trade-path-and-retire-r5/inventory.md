# R5c 调用图核对（tasks 8.2，2026-11-08）

## 结论先行：与 design.md D10 不符、需要你定的四处

> **2026-11-08 用户决定：** 四处都按下面的建议办；`isolated_entry` 没有长期调用方，8.7 整个删除。

1. **`joint_cutover` 不是叶子。** 生产读取链有六处导入它的运行时门禁：
   - `runtime_reads`、`read_windows`、`scoped_routes`、`cutover_routing`、`cutover_wiring` 用 `assert_scope_open` / `epoch`；
   - Dispatcher 的 `schedule_runtime` 用 `assert_schedule_open` / `authorize_schedule`。

   生产库 `var/phase_f_cutover.sqlite` 里没有 `joint_cutover_*` 表，这些门禁在生产上都是空操作。
   **建议：** `joint_cutover` 从 8.4 移到 R5d（第 9 组），和 `cutover`、`scoped_routes`、`read_windows` 一起删，省得 8.4 去动 Dispatcher 和读路由代码。它没有自己的命令。
2. **22 项订单追溯测试依赖 8.4 要删的模块。**
   - 测试本身：`tests/test_phase_f_integration_evidence.py` 导入 `acceptance_reports`（读已签报告）和 `acceptance_assertions.audit_attribution`（重审订单联接）。夹具由 `scripts/verify_phase_f_integration.py` 生成。
   - 这个 `acceptance_assertions` 不在 D10 清单上，是这次核对新找出的。
   - 检查逻辑（`audit_attribution` / `audit_approval` / `audit_risk` / `_revision`）只依赖 `isolation`、`broker.ibkr`、`schemas`，可以搬走。

   **建议：** 8.3 把这四个函数迁到长期模块（如 `src/ats/execution/trade_attribution.py`）；22 项测试的夹具改为用隔离模拟（FakeBroker）现跑一笔，不再读 Phase F 报告库。之后 `acceptance_*` 可以整组删除。

   **2026-11-10 更正（用户批准，8.3）：** 不迁移。22 项追溯测试是 T0.6 的验收手段，任务已完成；它们守的事在长期测试里已有不依赖迁移期工具的等价用例（见文末「8.3 覆盖对照」），8.4 与 `acceptance_*`、`scripts/verify_phase_f_integration.py` 一起删除。删后少的只是“破坏账本看审计器是否拒绝”这类测试手段，审计器本身生产从不调用。
3. **`shadow_execution` 在实盘锁定期没有用途。**
   - `ShadowBroker` 是“券商拒绝 + 意图写影子账本”的传输层，只有 `isolated_entry` 的 `shadow=True` 会选中它，`isolated_entry` 只被两个测试使用。
   - 隔离模拟已由 `simulation`（FakeBroker）承担，属长期保留。
   - 生产上 `selected_shadow()` 恒为 `None`，`publish_shadow_results` 恒返回 `False`。

   **建议：**
   - 删除 `ShadowBroker`、`shadow_execution()`、`selected_shadow`、`publish_shadow_results`；`trader/execute.py` 两处与 `graph/chief.py` 两处随之简化（行为不变）。
   - `assert_trade_publication`（`memory/store.py` 写 `trades` / `fills` 前的断言）的三部分分开处理：
     - “隔离结果不得写出其隔离根”是长期安全检查，迁到长期模块；
     - 影子模式拒写随影子删除；
     - 写入目的地检查（`cutover_wiring`）留到 R5d。
   - Clerk 不再捕获 `ShadowLedgerWriteRefused`。
4. **回放冻结不是长期能力。** `business_replay_inputs` 的磁带只由 `paired_business`、`acceptance_runner` 激活（`bind_tape`）。删掉这两个之后，`consumer_api.read_input` 里的 `governed_read` 恒返回 `None`、`capture_governed` 原样返回。
   **建议：**
   - `route_switch` 用的 `implementation_hashes` 迁到长期模块（`assurance_surface` 旁）；
   - 8.4 删掉 `consumer_api` 对磁带的两处调用；
   - 剩余部分随 R5d 删除。

## 逐模块结论

“生产调用方”指 `src/` 内除 `runtime/cli.py` 之外的导入方。测试与脚本是导入该模块的文件。

| 模块 | 生产调用方 | 命令 | 测试 / 脚本 | 结论 |
|---|---|---|---|---|
| `boundary_drill` | 无 | `ats drill` | test_boundary_drill | 8.4 删 |
| `joint_cutover` | runtime_reads、read_windows、scoped_routes、cutover_routing、cutover_wiring、schedule_runtime（均为空操作门禁） | 无 | 7 个 phase_f 测试 | **改到 R5d 删**（见第 1 处） |
| `paired_business` | acceptance_runner | `ats shadow`（配对业务） | 3 个 | 8.4 删 |
| `acceptance_runner` / `acceptance_reports` / `acceptance_matrix` / `acceptance_disposition` | 互相引用；acceptance_reports 被 exit_requirements、shadow_reports 引用 | `ats shadow`（验收） | 6 个；脚本 capture_phase_f_research/technical、verify_phase_f_integration | 8.3 迁出追溯检查后，8.4 删（见第 2 处） |
| `acceptance_assertions`（新找出） | acceptance_* | 无 | 5 个（含 22 项追溯） | 8.4 整删（11-10 更正：不迁移，见「8.3 覆盖对照」） |
| `shadow_compare` / `shadow_inputs` / `shadow_matrix` / `shadow_replay` | 只在 shadow_* 与 acceptance_* 之间 | `ats shadow` | 共 10 余个 | 8.4 删 |
| `shadow_reports` | `cutover`（激活前查批次报告）、`read_windows`（交易消费者批次报告校验）、batch_manifest、read_deployment、acceptance_reports | `ats shadow` | 6 个；脚本 prepare_phase_f_stage_one | 8.4 删；先去掉 `read_windows` 的交易消费者校验（tasks 原定）。`cutover.py` 的 `preflight` / `record_activation` 两处激活检查留到 R5d 删 `cutover` 时一起处理 |
| `shadow_ledger` | clerk（捕获异常）、shadow_execution、cutover_acceptance | `ats drill`（账本核对） | 3 个 | 8.4 删（先拆 Clerk 引用） |
| `execution/shadow_execution` | trader/execute、graph/chief、memory/store、isolated_entry | 无 | test_phase_f_shadow_business | 见第 3 处 |
| `batch_manifest` / `read_cutover` / `read_deployment` | 互相引用与 boundary_drill | `ats batch`、`ats read` | 10 余个；脚本 prepare_phase_f_batches | 8.5 删 |
| `schedule_cutover` | boundary_drill | `ats dispatch` 迁移期子命令 | test_schedule_cutover | 8.6 删 |
| `cutover_acceptance` | 无 | `ats drill traceability / unapproved-orders / acceptance` | test_cutover_acceptance | 订单可追溯、未批准订单被拒已有长期等价覆盖（见「8.3 覆盖对照」），8.6 直接删 |
| `intake` | 无 | `ats intake` | test_intake | 8.7 删 |
| `intake_verification` | analysis_verification、consumer_disposition、isolated_entry、paired_business | `ats intake` | 5 个 | 扫描器已迁出（8.1）；余下 8.7 删 |
| `scoped_intake` | 无（**在生产资格指纹清单里**） | 无 | 2 个；4 个 phase_f 脚本 | 8.7 删，同时从 `required_fingerprint_paths` 去掉（R5d 收窄资格时一起核） |
| `analysis_verification` | 无 | 无 | test_phase_f_opinion_execution | 8.7 删 |
| `consumer_disposition` | 无 | `ats intake disposition` | 3 个 | 8.7 删 |
| `isolated_entry` | 无 | 无 | test_phase_f_safe_reads、test_phase_f_shadow_business | 隔离模拟长期能力由 `simulation` 承担，`isolated_entry` 自身无长期调用方；8.7 整删（用户 11-08 确认，取代 D10“部分保留”） |
| `dispatch_claims` | schedule_cutover | `ats dispatch` 迁移期子命令 | test_dispatch_claims | 8.8（先迁覆盖核验） |
| `business_replay_inputs` | consumer_api（磁带读取）、route_switch（`implementation_hashes`）、acceptance_*、paired_business、read_deployment、shadow_replay、exit_requirements | 无 | 8 个 | 见第 4 处 |
| `exit_requirements` | data/assurance（`_artifact_verified`） | 无 | 1 个；2 个脚本 | R5d（原定） |
| `cutover_guards` | 无 | 无 | test_cutover_guards | R5d（原定） |

## 对执行时机的影响

- 8.3、8.4 要改 `trader/execute.py`、`graph/chief.py`、`memory/store.py`、`execution/clerk.py`、`data/consumer_api.py`，都在定时运行的路径上。Dispatcher 在触发时加载工作目录代码，如果在 11-09 22:00 的 chief-daily、11-10 07:00 的 clerk-daily 之前合入，6.6 和第 7 组的观察结论就会混进第 8 组的改动。
- 8.1（已提交 a5cbc7e）与本文件只动退役工具和文档，不在定时路径上。

## 数据来源

- 调用图：对 `src/`、`tests/`、`scripts/` 做 AST 导入扫描，含函数内的延迟导入。脚本与结果存 `var/test_runs/switch-8.2/`（`g8_callgraph.py`、`callgraph.json`）。
- 生产库只读核对：`var/phase_f_cutover.sqlite` 无 `joint_cutover_operations` 表。

## 8.3 覆盖对照（2026-11-10，用户批准）

结论：迁移期验收模块里没有需要迁进长期测试支撑的检查。下表左列是 22 项追溯与 `cutover_acceptance` 守的事，右列是只用长期模块（`isolation.isolated_run`、`simulation`、`route_registry`、`broker_write_guard`）的现有测试。

| 要守的事 | 长期测试 |
|---|---|
| 下单必须与已批准修订一致 | `test_phase_f_trader_a::test_submitted_order_must_equal_approved_revision`、`test_actual_graph_through_human_approval_to_simulation` |
| 未经批准的订单不能下 | `test_chief_graph::test_rejected_places_nothing_but_logs`；`test_phase_f_trader_a::test_no_grant_simulation_refuses`、`test_c3_still_blocks_without_explicit_simulation` |
| 隔离环境中真实券商必须拒单 | `test_phase_f_trader_a::test_real_ibkr_transport_stays_prohibited_in_simulation` |
| 模拟不能逃出隔离 | `test_phase_f_trader_a::test_simulation_requires_complete_isolation`、`test_saved_fake_broker_cannot_escape_context`；`test_phase_f_isolated_authority` 两项 |
| 成交落账、部分与迟到成交 | `test_chief_graph::test_approved_places_and_persists`；`test_phase_f_trade_switch_drill::test_actual_partial_late_fill_blocks_drain_then_switch_and_rollback`；`test_clerk_linkage` |

各模块处置：
- 22 项追溯（`test_phase_f_integration_evidence`）、`acceptance_assertions`、`scripts/verify_phase_f_integration.py`：8.4 删除。
- `cutover_acceptance`（`ats drill traceability / unapproved-orders / acceptance`）：8.6 删除，不迁。
- `analysis_verification`、`scoped_intake`、`consumer_disposition`：只服务迁移期资格认定，8.7 删除。
- `isolated_entry`：8.7 整删（11-08 已定）。

## 8.10 本批复验（2026-11-11，用户批准按现有证据勾选）

| 项 | 结果 |
|---|---|
| 全量测试与基线对照 | 35 failed / 4138 passed，新增变红 13 个已归因（4 个本组已修 edaa936，9 个 worktree 环境、主仓库复验通过）；见 TEST_BASELINE「交易路径切换第 8 组全量」与 `var/test_runs/switch-8-vs-7.txt` |
| 重启 | 11-10 14:15 台北合入 de854d8 后重启 dispatcher（PID 42111）、serve（PID 42129），`/health` ok，7 个工作流注册 |
| 下一次定时触发 | 11-10 台北 21:00 information-brief complete、21:31 fundamental-routine complete、21:33 technical-review incomplete、22:45 chief-daily failed、23:58 fills-capture 与 11-11 07:00 clerk-daily failed，均按时触发。失败原因与本批无关：technical-review 16/18 标的 Yahoo 取价超时（经 Clash fake-IP 连接时断时续），chief 因此缺技术面输入被 `decision_requirements` 拒；fills-capture、clerk 因 TWS 未登录（用户断网） |
| 日历消费、数据刷新 | `phase_e:calendar-consume` 每 30 分钟正常结束；data-refresh 进程持续运行（既有 misfire 遗留与本批无关） |
| clerk-daily | 11-11 09:39 台北手动补跑 11-10 窗口 `run_44108d5511b0fc9144bbdae2` completed（无新成交、持仓 18、Flex 补漏一致），Clerk 日报与风控日报已发布；与定时路径调用同一 `clerk_run` + `clerk_report.publish` + 风控日报 |
| chief-daily | 技术面逐标的手动补齐 18/18 后，11-11 10:04 台北手动 `ats chief run --live --channel pending`（`chief-20261111-020407`）：全部输入读取通过（本批去掉了 `consumer_api` 磁带调用），模型出决策（卖 DRAM 214、NVDX 400）；订单规范化时 IBKR 隔夜逐笔报价 10 秒未到（`execution_quote_source_timestamp_missing`），周期停在 manual_review。约 40 秒后只读复测 DRAM、NVDX、NVDA 隔夜报价均取到实时买卖价，属偶发行情问题，与退役无关（用户判定） |
| 旧表与台账可读 | `var/phase_f_cutover.sqlite`（11 表）、`var/phase_f_batches.sqlite`（5 表）只读打开正常；`ats retirement decide --drill-reference "verification.md 3.9 回退演练（2026-10-29）"` 38 条全部可退出，第 8 组六条为 retired |
| 未覆盖 | `execution/trade_publication.py`（trades/fills 写入前检查）本次无新成交未被调用，待首笔真实成交时观察 |

# R5d 调用图核对（tasks 9.1 起，2026-11-11）

## 结论与用户决定（2026-11-11 用户批准 D1–D5 全按建议）

第 9 组删 14 个模块（约 4,850 行），涉及 39 个测试文件、3 个脚本（`verify_dataflow_qualification`、`verify_dataflow_tasks5`、`verify_information_closeout`）。中枢 `runtime_reads` 被 30 个生产模块使用，既有长期的 scope 绑定与隔离检查，也有迁移期的路由代次、联合门禁、读停止、读窗口、调用绑定与资格判定。

生产库只读核对（`var/phase_f_cutover.sqlite`）：10 个消费者路由均为 target；六个边界 5 个 legacy、`live_trader` disabled（从未接线）；`cutover_activations` 0 行；无 `joint_cutover_*` 表；`read_recovery_events` 240 行，涉及 8 个 identity，最后一条 2026-10-15（资格演练），之后生产读取全部通过，没有现役 scope 处于停止；`production_consumer_receipts` 4105 行，只被 `exit_requirements` 读；`data.sqlite` 的 `dataflow_assurance_events` 384 行。

不能直接删、需要保留或搬移的五处：
1. `consumer_reads.read_projection`（主理人与分析师读投影）、`record_read`：搬到新模块。
2. `scoped_routes.RouteIdentity`、`cutover_routing.RouteUnavailable`：搬到新模块并改名（D1）。
3. `cutover_wiring.assert_write_destination` 的隔离写入检查：长期安全检查，搬到 `isolation`。
4. `cutover.assert_runtime_startup`（CLI、dispatcher、serve 启动时调用）：按 cutover-control 增量改为基于 `route_registry` 的交易路由启动检查。
5. `schedule_runtime` 的 resume 前提读 `consumer_cutover` 路由、数据健康历史存在 `consumer_cutover` 表：去掉路由前提，健康历史归 `consumer_health`（D3）。

用户决定：
- D1：新模块 `workflow/read_scope.py`，`RouteIdentity`→`ReadScope`、`RouteUnavailable`→`ReadUnavailable`，不留旧名别名。
- D2：保留每次读取的 `consumer_read` 日志行（去掉路由、代次、联合 epoch 字段）；停写 `production_consumer_receipts`；测试用 `trace_reads` 保留。
- D3：`ats dispatch resume` 只查数据健康，不再查路由；“legacy 负责人”技术债不动。
- D4：整删 `read_recovery`；长期停止手段是交易路由冻结与 `dispatch pause`。
- D5：`ats data assurance` 改已退役提示，删除资格判定，`dataflow_assurance_events` 保留只读；删除 `required_fingerprint_paths`；冻结面中架构守卫所需部分与 `route_switch` 的 `implementation_hashes` 保留；`ats consumer-cutover restore` 改已退役提示。

证据：`var/test_runs/switch-9.1/`（`g9_callgraph.py`、`callgraph.json`、`surface.txt`）。
