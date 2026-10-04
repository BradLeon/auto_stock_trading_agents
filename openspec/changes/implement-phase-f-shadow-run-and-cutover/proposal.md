# Proposal

## Why

Phase A–E 与目标数据流专项（`complete-target-dataflow`，44 项任务）均已归档，十个目标角色的契约、门禁与数据面已就位，但系统仍整体运行在旧入口上：`config/workflow/workflow_owners.yaml` 的 7 个 workflow 全部 `mode: legacy`，`config/workflow/phase_e_schedules.yaml` 的 5 个 schedule 全部 `enabled: false`，`config/data/structured.yaml` 的消费者虽已签发 platform 模式，但按 `docs/DATA_ARCHITECTURE.md:67` 的记录十个 consumer 均未取得生产读取资格。同时存在三个结构性缺口：读/调度/交易三条边界没有统一的切流开关与互斥校验（交易路径完全缺失，`src/ats/broker/ibkr.py:282` 无 paper/live 断言，「任何时刻只有一条可下真单的入口」目前无任何实现）；影子运行只有零散的双读比对（`data/cutover.py` 的非追加式 comparison、`agents/chief/assemble.py:650` 只比 presence 计数），没有覆盖调度遗漏、风控 verdict、审批链与交易归因的差异报告；旧实现退役登记中 `chief.legacy_table_direct_reads`、`sector_reviews`、`trades.client_order_id.legacy_derivation` 三条 pending 明确写着「切流属 Phase F」。

现在必须执行切流，因为继续叠加兼容层会同时稀释审计真相与写入边界，且 `docs/TARGET_WORKFLOW_DATAFLOW.md` §15.5 的上线门禁中第 3、4、5 条（影子运行无未审批下单、影子订单全链可追溯、回滚已演练且单交易入口）当前 0/3 达成。

## What Changes

- **新增切流控制平面**：为 projection read、analyst output、Dispatcher schedule、approval lifecycle、Clerk publication、live Trader 六条边界建立独立功能开关；开关之间按边界互斥，冲突配置在启动时 fail closed；读路径与调度路径的路由判定逐条依据 `ats.data.assurance.qualification()` 的 exact-scope 资格，禁止用单一总开关覆盖未通过路径。
- **新增统一影子运行**：在相同 data vintage 下新旧分析/风控双跑，比较输入快照、分析输出、调度遗漏、风控 verdict/counterproposal、审批链与交易归因六类差异并产出可签核的差异报告；影子期从进程能力层禁止新路径 broker write，影子与真实订单账本隔离。
- **新增交易路径切换与外部授权门**：建立 live Trader 单活跃入口与互斥校验；切流前必须确认无 in-flight authorization；真实 live route 切换写成外部授权门——本 change 只交付门禁代码、runbook 与 paper/隔离演练，实际切换动作需另行取得明确实盘授权后执行。
- **前置接收与实际接入核验（F.0）**：建立版本化前置接收矩阵；按 `domain_id + consumer_id + contract_version + scope` 校验并追加式登记已有资格证据；核验十个角色的实际 CLI/Dispatcher/Workflow 接入与无券商写权限的 Chief—Risk—Trader—Clerk 恢复；对实际缺口形成逐消费者处置与逐批切流 dry-run 清单，缺项保持稳定旧路由。
- **退役清零与回滚演练**：按 11.8 清零旧实现消费者、数据对账、回滚窗口与墓碑登记检查；按 11.9 独立演练 read/schedule/trade 三条边界的回滚，保留影子、审批与账本记录。
- **不重建已验收实现**：复用 `ats.data.assurance` 追加式证据 API、`config/data/target_dataflow_coverage.yaml` 消费者契约、`rollout_modes` 四级解析、`workflow/ownership.py` owner 模式、`execution/authorization.py` 十字段授权与 `legacy_retirement` 三段式退出机制；十角色未取得资格前不得为「让切换可执行」而降低证据标准。
- **新增 pytest 回归保护**：`ats.data.assurance` 当前仅有 `scripts/verify_dataflow_qualification.py` 脚本验证、`tests/` 零覆盖，本 change 补齐其回归测试，并把 Phase F 的门禁不变量纳入架构守卫。

## Capabilities

### New Capabilities
- `workflow/cutover-control`: 切流控制平面——六条边界的独立功能开关、边界间互斥与启动 fail-closed 校验、逐路径 exact-scope 资格门控路由、逐批切流 runbook 的执行与回退状态机。
- `workflow/shadow-reconciliation`: 统一影子运行与差异归因——同 vintage 双跑、六类差异（输入快照/分析输出/调度遗漏/风控 verdict/审批链/交易归因）的差异报告、broker write 进程能力禁令与影子账本隔离。
- `execution/live-route-switch`: 交易路径切换——live Trader 单活跃入口与互斥、in-flight authorization 清空确认、外部实盘授权门、切换与回滚演练。

### Modified Capabilities
- `data/target-dataflow-assurance`: 增加 Phase F 前置接收要求——版本化接收矩阵、已有资格证据的受控追加登记、十个角色实际接入核验、逐消费者处置与逐批 dry-run 资格报告（现有 7 条需求只规定资格如何签发，未规定谁接收、如何复核后使用）。
- `workflow/dispatcher-runtime`: 增加调度路径切流要求——切换后未完成 trigger 的单一所有者确认、禁止双重调度所有权、回滚时不重复消费已执行 trigger。
- `workflow/legacy-retirement`: 增加切流窗口的清零判据——消费者清零证明、数据对账、回滚窗口与墓碑登记检查作为标记已退役的前置条件。
- `execution/authorization-gate`: 增加授权与交易入口绑定——授权有效性须绑定当前活跃交易 route，路由切换后既有授权不得跨 route 复用，须重新取得授权。

## Impact

- **新增代码**：`workflow/` 下的切流控制与影子比对模块（含差异报告生成与门禁校验）、`execution/` 下的 live route 仲裁与授权绑定、broker write 能力校验。
- **改动代码**：`config/workflow/workflow_owners.yaml` 与 `phase_e_schedules.yaml`（owner 模式与 schedule 启用状态随逐批切换变更）、`config/workflow/legacy_retirement.yaml`（三条 Phase F 相关 pending 的状态与判据）、`src/ats/graph/chief.py` 与 `src/ats/trader/execute.py`（交易入口接仲裁）、`src/ats/runtime/cli.py`（切流与差异报告入口）。
- **新增/改动配置**：新增切流开关与 runbook 配置文件；`config/data/structured.yaml` 的消费者模式仅在对应 exact-scope 资格通过后逐条变更，不做整体切换。
- **数据与账本**：影子订单与真实订单账本隔离会影响 `trades` 表写入路径；资格证据继续写入 `dataflow_assurance_events`（追加式，不修改既有行）。
- **测试**：`ats.data.assurance` 补 pytest 回归；新增切流门禁、影子差异、路由互斥、回滚演练的端到端测试；Phase F 相关架构守卫并入 `workflow/architecture-guards`。
- **外部依赖与授权**：真实部署路由变更需明确部署授权，live Trader 真实切换需明确实盘授权；两者均不由本 change 的完成状态自动获得。
- **不在范围内**：不重建数据平台、不重新采集、不改动 Phase A–E 已验收的契约与门禁标准、不在本 change 内执行真实 live route 切换。
