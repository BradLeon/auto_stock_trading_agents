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
| `acceptance_assertions`（新找出） | acceptance_* | 无 | 5 个（含 22 项追溯） | `audit_*` 四个函数迁长期模块，其余 8.4 删 |
| `shadow_compare` / `shadow_inputs` / `shadow_matrix` / `shadow_replay` | 只在 shadow_* 与 acceptance_* 之间 | `ats shadow` | 共 10 余个 | 8.4 删 |
| `shadow_reports` | `cutover`（激活前查批次报告）、`read_windows`（交易消费者批次报告校验）、batch_manifest、read_deployment、acceptance_reports | `ats shadow` | 6 个；脚本 prepare_phase_f_stage_one | 8.4 删；先去掉 `read_windows` 的交易消费者校验（tasks 原定）。`cutover.py` 的 `preflight` / `record_activation` 两处激活检查留到 R5d 删 `cutover` 时一起处理 |
| `shadow_ledger` | clerk（捕获异常）、shadow_execution、cutover_acceptance | `ats drill`（账本核对） | 3 个 | 8.4 删（先拆 Clerk 引用） |
| `execution/shadow_execution` | trader/execute、graph/chief、memory/store、isolated_entry | 无 | test_phase_f_shadow_business | 见第 3 处 |
| `batch_manifest` / `read_cutover` / `read_deployment` | 互相引用与 boundary_drill | `ats batch`、`ats read` | 10 余个；脚本 prepare_phase_f_batches | 8.5 删 |
| `schedule_cutover` | boundary_drill | `ats dispatch` 迁移期子命令 | test_schedule_cutover | 8.6 删 |
| `cutover_acceptance` | 无 | `ats drill traceability / unapproved-orders / acceptance` | test_cutover_acceptance | 订单可追溯、未批准订单被拒：8.3 核对 T0 与第 2 处迁出的 `audit_*` 是否等价覆盖，覆盖则 8.6 删 |
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
