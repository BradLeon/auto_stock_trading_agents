# Design

## Context

动机见 `proposal.md`；本阶段的行为契约见 `specs/`。这里只记录塑造实现方式的现状与约束。

**已验收、本阶段必须复用的机制**（均由 `complete-target-dataflow` 与 Phase A–E 建立）：

- 读取资格：`ats.data.assurance` 提供追加式证据表 `dataflow_assurance_events`（含 `UPDATE`/`DELETE` 拒绝触发器）、`record_evidence` / `revoke_evidence` / `qualification` / `evidence_history` 四个 API，以及 `config/data/target_dataflow_coverage.yaml` 的十消费者契约（`qualification_policy` 的 `required_fingerprint_paths`、`optional_inputs`、`rollback_routes`）。`qualification()` 已按 `domain_id + consumer_id + contract_version + scope` 做 fail-closed 判定，并支持 SEC 原文 optional 非阻塞例外。
- 读路径开关：`ats.data.rollout_modes.read_mode()` 的四级解析（env → release overlay → catalog `feature_flags.consumer_sources` → `consumers`）与 `ReleaseManager` 的 `publish` / `rollback`（仅映射变更，不删数据）。
- 调度开关：`ats.workflow.ownership` 的 `{legacy, shadow, dispatcher}` owner 模式、每模式独立 SQLite（`var/shadow/phase-e.sqlite`）与 `run_owned_workflow`。
- 执行门禁：`ats.execution.authorization` 的十字段授权与 `validate_authorization`；`ats.graph.chief.trader` 节点的 `stale` / `refused` / `placed` 三出口；`ats.trader.execute.place_orders` 的授权 fail-closed 与 `client_order_id` 幂等。
- 退出机制：`ats.workflow.legacy_retirement` 的墓碑 → fail-closed 读门 → 两段式清除，`config/workflow/legacy_retirement.yaml` 现有 26 条（12 retired / 14 pending）。
- 调度运行时：`ats.workflow.dispatcher.Dispatcher.dispatch`、`TriggerService` 的 `claim_trigger` / `renew_trigger` / `finish_trigger` / `compensate_trigger`、`WorkflowStore` 的 `workflow_runs` / `trigger_runs` 表。

**必须新建的缺口**（现状经逐项核验）：

1. **交易路径开关与单活仲裁完全缺失**。`ats.broker.ibkr.IBKRBroker.place_orders`（`ibkr.py:282`）无 paper/live 断言；`ats.trader.execute.is_live`（`execute.py:108`）只用于在审批卡片加横幅，不影响是否下单；「任何时刻只有一条可下真单的入口」无任何实现。`legacy_retirement.write_gate()` 只覆盖 legacy 标识符，不覆盖交易入口。
2. **统一影子比对缺失**。现有：`ats.data.cutover.record_consumer_comparison`（非追加式 `INSERT OR REPLACE`，只记录 matched 布尔）、`ats.agents.chief.assemble.dual_read_diffs`（`assemble.py:650`，只统计「类别是否存在」的 presence 计数，且自述从不用作门禁）。缺失：调度遗漏比对、风控 verdict 双跑、审批链比对、交易归因比对。
3. **资格机制无 pytest 保护**。`tests/` 对 `ats.data.assurance` 零覆盖；现有验证只在 `scripts/verify_dataflow_qualification.py`（827 行，含 30 项机制探针与 10 消费者回退钻取）。Phase F 的路由判定将直接依赖该模块，必须先补回归。
4. **影子与真实共用 `trades` 表**，无隔离。

**部署现状约束**（决定哪些范围实际可切）：`workflow_owners.yaml` 7 个 workflow 全部 `legacy`；`phase_e_schedules.yaml` 5 个 schedule 全部 `enabled: false`；`docs/DATA_ARCHITECTURE.md:67` 记录十个 consumer 均未签发生产读取资格；`docs/validation/DATAFLOW_ASSURANCE_RUNBOOK.md:93` 记录五个研究角色样本回退可读，而 Technical、Chief、Risk、Trader、Clerk 保持 ineligible。

**环境约束**：SQLite 且 `TradingMemory` 连接按进程缓存，scheduler 历史上因此刻意串行；`assurance.py` 写连接已启用 WAL + `busy_timeout=30000`，读连接为 `?mode=ro` + `query_only`。

## Goals / Non-Goals

**Goals:**

- 切流成为**受控的显式操作**：每次切换都有批次记录、资格依据、观察窗口、停止条件与已验证回退。
- **逐消费者判定**，不让一个数据域的整体通过掩盖个别消费者缺证据。
- 交易路径在**能力层**单活，缺能力即拒绝启动，而非依赖调用方自觉传参。
- 影子结果**可签核、不可事后改写**，缺任一必需比较面即不能作为通过证据。
- 缺项**只阻断受影响范围**，不阻断无关的已验收路径。
- 真实实盘切换**默认锁定**，只由可审计的外部授权解锁。

**Non-Goals:**

- 不重建数据平台、不重复全量采集、不改动 A–E 已验收的契约与门禁标准。
- 不为「让切换可执行」而放宽证据标准或新增按报告年龄的 stale 降级。
- 不在本阶段执行真实 live route 切换（只交付门禁、runbook 与演练）。
- 不修复旧路径已登记的问题，只登记。
- 不把 `assurance.py` 重构为通用证据框架——只补它缺失的 pytest 回归与 Phase F 所需的读取封装。

## Decisions

### 决策 1：切流控制平面作为独立模块，不并入 `release.py`

`ReleaseManager` 的 `kind` 只接受 `source` / `consumer`，语义是「结构化数据发布覆盖层」，其回滚语义是改映射不删数据。Phase F 需要的是**跨边界的边界仲裁**：读路径按消费者+资格判定、调度路径按 workflow 判定、交易路径按单活仲裁，三者的判定输入、互斥规则与回滚语义都不同。

**选择**：新增 `ats.workflow.cutover` 承载边界开关、互斥校验、资格门控与批次状态机；对结构化读路径**调用** `rollout_modes` / `ReleaseManager`，不复制其解析逻辑。

**备选**：给 `ReleaseManager` 扩展 `kind="workflow" | "trade"`。否决——会让「数据发布回滚」与「路由切换回滚」共用一个状态机，而两者的回滚语义不同（前者不删数据、后者必须先清空在途授权），混在一起后无法对交易边界做 fail-closed 校验。

### 决策 2：资格门控在路由解析处强制，不作为可选前置检查

现有读路径路由由 `read_mode()` 决定，它只看 env/overlay/catalog，**不知道资格**。若把资格做成调用方可选的预检，遗漏即等于放行——这与项目既有的 fail-closed 惯例（`place_orders` 无授权即拒单、`qualification()` 缺项即 ineligible）冲突。

**选择**：Phase F 的读路径解析入口在解析出「将走新路由」时**强制**调用 `qualification()`，未通过即降回该消费者的稳定旧路由并记录缺口；旧路由不受影响，因此未迁移路径的可用性不依赖本阶段。

**备选**：把资格写进 `config/data/structured.yaml` 的 `feature_flags`（人工预先把未通过项改成 legacy）。否决——资格是带 TTL 与撤销的时变事实，写进签入配置会立刻腐化，且无法表达「同一数据域不同消费者结论不同」。

### 决策 3：交易单活仲裁放在下单能力层，而非图节点或 CLI

最直接的实现是在 `ats.graph.chief.trader` 节点加判断，但那只能覆盖经该节点的路径；`ats.trader.execute.place_orders` 与 `ats.broker.ibkr` 也可能被直接调用（CLI `ats trader execute`、`ats trader buy/sell`）。把判断放在图节点等于把门禁建在调用者之上。

**选择**：新增**下单能力仲裁**（broker write capability grant），由切流控制平面在部署/启动时签发到进程内；`IBKRBroker.place_orders` 在提交前校验该能力，无能力即拒绝。新增下单出口必须经同一仲裁，架构守卫扫描绕过路径。

**备选**：只按 `ibkr_port ∈ {7496, 4001}` 判定 paper/live。否决——端口是部署事实而非授权事实，无法表达「本进程被禁止真实下单」，也无法阻止旧入口在切换后仍用同一 paper 端口下真单。

### 决策 4：影子差异报告为追加式，且缺面即不可用作证据

现有 `record_consumer_comparison` 是 `INSERT OR REPLACE`（同键覆盖），无法表达「同一批运行先出报告、后补齐调度面」，也不可审计谁在何时改过结论。

**选择**：影子报告为追加记录，含六面结论与 `not-compared` 原因；签核/驳回/撤销作为新记录追加。`not-compared` 的必需面使该报告**不可**被引为该范围的通过证据（由 cutover-control 在引用时校验）。

**备选**：一次性生成完整报告，缺面即整体失败。否决——调度遗漏往往需要跨重启窗口才能观测（misfire/restart 场景），一次性生成会把「需要时间观测」误作「无法验证」。

### 决策 5：影子账本物理隔离，真实 `trades` 表不参与影子写入

影子订单意图若写入真实 `trades`，会污染持仓、绩效与资金对账，并让「影子期禁止真实下单」这一断言无法自证。

**选择**：影子意图写入独立影子账本（与 Phase E 的 `var/shadow/phase-e.sqlite` 同族但独立），保留可重建所需的全部归因字段；`trades` 写入路径在影子模式下经仲裁拒绝而非静默改道（避免影子被误认为真实成交）。

**备选**：影子意图只写内存/临时文件。否决——无法在进程重启后重建归因，也不满足 Clerk 的对账语义。

### 决策 6：调度切流的未完成触发采用「声明式处置」，不自动迁移

切换瞬间旧路径上可能存在已认领未完成的触发。自动迁移会与旧路径的 in-flight 执行竞态，自动作废会丢工作。

**选择**：切换前列举未完成触发并要求逐个声明处置（承接 / 执行完毕 / 作废并记录原因）；有未声明者即拒绝切换。回滚时按已执行触发记录去重，不以时间窗口近似。

**备选**：切换时强制等待旧路径排空。否决——`TriggerService` 的 lease 心跳允许长任务，强制排空会让切流窗口不可预测；且排空并不解决「新旧两条路径都认为该触发归自己」的语义歧义。

### 决策 7：先补 `assurance.py` 回归，再建依赖它的门禁

Phase F 的路由判定直接读 `qualification()`，而该模块只有脚本验证。若先建门禁后补测试，门禁的正确性将建立在一个无回归保护的模块上。

**选择**：把 `scripts/verify_dataflow_qualification.py` 的机制探针转为 pytest（追加式、不修改既有行、只读连接、TTL、manifest/dependency 漂移、scope 隔离、跨消费者撤销隔离、SEC 例外），并把切流不变量并入 `ats.workflow.architecture_guards`。脚本保留作为端到端取证。

**备选**：直接复用脚本作为门禁前置。否决——脚本以 `main()` 返回码表达结论，无法被切流代码逐项查询，也不产出结构化缺失清单。

### 决策 8：实盘授权为显式外部输入，且与部署授权分离

旧规划 F.0.7 与 11.7 都要求「明确实盘授权」，且明确「规划批准与前置通过均不构成该授权」。

**选择**：实盘授权作为**独立的、可审计的外部输入**（来源、操作者、生效范围、有效期），由仲裁在切换时校验；缺授权即拒绝并报告。部署路由变更授权与实盘授权是**两个不同授权**，前者不蕴含后者。纸面/隔离演练记录与真实切换记录分开保存，且演练不计为已获授权。

**备选**：用环境变量开关充当授权。否决——环境变量无来源与操作者，不可审计，且易随部署继承而长期存在。

## Risks / Trade-offs

- **[十消费者均未取得生产资格 → 可切范围可能为零]** → F.0 逐消费者处置允许部分范围进入；spec 明确「缺项只阻断受影响范围」。若最终全部 ineligible，本阶段交付的是**门禁、影子能力与演练**，切流执行留待资格取得后——这比降低证据标准更可接受。
- **[影子运行成本翻倍]**（同 vintage 双跑分析/风控/调度）→ 影子按批次与范围启用而非全量常驻；影子账本与 Workflow 影子库复用 Phase E 的独立 SQLite 约定，避免污染生产数据面。
- **[资格 TTL 到期导致切流中途失效]**（technical/trader/clerk 的 TTL 为 1 天）→ 切流批次按短 TTL 消费者单独排期；`qualification()` 的时点判定保证过期即 ineligible，无需额外机制。
- **[新增下单仲裁可能被绕过]**（未来新入口）→ 仲裁置于 broker 提交层而非调用层，并入架构守卫扫描；`specs/execution/live-route-switch` 要求绕过即判违规且豁免须声明收敛阶段。
- **[读路径降级回旧路由造成「看起来没切」]** → 每次降级记录缺口并进入逐批报告；ineligible 消费者在报告中显式列为未切换，不静默沿用新路由。
- **[in-flight 清空检查使交易切换难以自动化]** → 这是有意的保守设计：交易路由切换预期为低频人工操作，自动化收益低于误切换代价。
- **[`assurance` 回归从脚本移植时可能与脚本行为漂移]** → 断言以 specs 的 fail-closed reason code 为准，脚本与 pytest 并存比对；不一致时以 specs 为准并修脚本。
- **[六面比较中「调度遗漏」与「风控 verdict」需要跨进程观测]** → 差异报告为追加式、可分批补齐；`not-compared` 面使该报告不可用作通过证据，避免「未比较」被当作「一致」。

## Migration Plan

**阶段 0（无路由变更）**：补 `assurance` pytest 回归与切流不变量守卫；建立切流控制平面与边界开关，默认全部保持当前路由（读=旧、调度=legacy、交易=旧）。此阶段不改变任何生产行为。

**阶段 1（证据接收）**：执行 F.0.1–F.0.2，产出版本化接收矩阵与逐消费者 eligible/ineligible 报告。此阶段不切流。

**阶段 2（接入核验）**：执行 F.0.3–F.0.5，产出十角色实际接入核验与逐消费者处置。此阶段不切流。

**阶段 3（影子运行）**：执行 F.0.6 与 11.1–11.4，六个边界开关上线（默认关闭新路由）、影子模式与六面差异报告投入运行，逐批产出影子报告。仍不切流、不下单。

**阶段 4（读路径分批切换）**：执行 11.5，**在取得明确部署授权后**按批次对已取得资格且已验证回退的消费者范围切换，每批记录切换前后路由、观察窗口、结果与回退演练。

**阶段 5（调度路径切换）**：执行 11.6，处置未完成触发后切换 workflow owner 与 schedule 启用状态，验证无双重所有权。

**阶段 6（交易路径）**：执行 11.7，**默认在纸面/隔离环境演练**。真实 live route 切换保持锁定，直至收到可审计的实盘授权且全部交易门禁通过。

**阶段 7（清零与回滚演练）**：执行 11.8–11.9，消费者清零证明、数据对账、回滚窗口核验、墓碑状态一致性检查，并独立演练 read/schedule/trade 三条边界回滚。

**回滚策略**：三条边界各自独立回滚。读路径回滚 = 该消费者范围切回旧路由（`ReleaseManager.rollback` 或 owner 模式回退）；调度回滚 = owner 模式回 `legacy` 并按已执行触发记录去重；交易回滚 = 撤销下单能力并确认目标路由可用后回切，失败则保持现状而不静默转入故障路由。影子、审批与账本记录在回滚中一律保留。

## Open Questions

- 十消费者中实际可首批切流的范围，取决于 F.0.2 登记结果与各消费者 TTL；本阶段按 spec 的逐范围判定处理，无需预先确定清单。
- 首批批次的观察窗口长度与停止条件阈值，需由 F.0.6 的 dry-run 与首批影子报告的实际噪声水平确定；在首批报告产出前以保守值登记，不在本阶段固化。
