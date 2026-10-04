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
5. **授权无生命周期**。`ExecutionAuthorization`（`execution/authorization.py:45-57`）只有十个 §10.4 因果字段，无授权 ID、无有效期、无完成状态，由审查与审批记录即时构造；`IBKRBroker.place_orders`（`ibkr.py:299`）仅 `ib.sleep(wait)` 短暂轮询即返回，后续成交与不确定提交交由对账处理。因此「枚举有效且未完成的授权」在现模型下无判定依据。
6. **旧调度完全不接入统一触发身份**（比「未使用 owner_mode」更严重）。`src/ats/runtime/scheduler.py` 中 `owner_mode` **完全不存在**，`trigger_key` 只出现在 `_start_phase_e`（L1027+）内部；旧常驻调度自建 APScheduler jobs 并直接调用角色入口。仅改 YAML 的 owner/enabled 既不能证明已运行的旧进程停止触发，也无法让旧路径在回滚后按新账本去重。
7. **多进程真实存在**。常驻 scheduler 可经 `_chief_daily` 到达 trader 节点，叠加一次性 CLI 进程，进程内 grant 无法跨进程撤销。券商账户身份可从 `config.secrets.ibkr_account` 取（`ibkr.py:146`），可作为环境匹配判据的输入。
8. **受指纹约束的代码与配置面**（决策 9 的依据）。`assurance` 有两条独立失效路径：文件内容指纹（`_dependencies()` 对文件字节做 sha256）与 `manifest_hash` 比对（`_invalid_event` 判 `manifest_drift`，基准是 `config/data/target_dataflow_coverage.yaml` 自身摘要）。受影响面：`required_fingerprint_paths` 为全部十消费者共享（`assurance.py`、`consumer_api.py`、`structured.yaml`、`unstructured.yaml`）；`consumer_fingerprint_paths` 追加 trader+clerk（`authorization.py`、`decision/repository.py`、`clerk.py`）与 chief+risk（`state_api.py`、`risk.yaml`）。

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

**选择**：Phase F 的读路径解析入口在解析出「将走新路由」时**强制**调用 `qualification()`，未通过即进入「安全回退判定」（见决策 2 补充）。

**回退必须先判定是否安全**：本 change 同时允许旧实现退役，因此「资格不过就回旧路由」并非无条件安全。当回退目标为墓碑已退役标识、回退证据自身失效、或目标实际不可用时，系统 SHALL 停止受影响范围、记录 `blocked` / `unavailable` 并阻断依赖该输入的交易，SHALL NOT 强行退回。资格漂移 SHALL NOT 仅记录日志后继续用旧分析结果提交；已组装快照的下游审批 SHALL 按既有依赖失效规则重验。

**备选 1**：把资格写进 `config/data/structured.yaml` 的 `feature_flags`（人工预先把未通过项改成 legacy）。否决——资格是带 TTL 与撤销的时变事实，写进签入配置会立刻腐化，且无法表达「同一数据域不同消费者结论不同」；且该文件本身在指纹约束内（决策 9）。

### 决策 3：交易单活仲裁放在下单能力层，且能力与授权绑定持久化的 route 代次

最直接的实现是在 `ats.graph.chief.trader` 节点加判断，但那只能覆盖经该节点的路径；`ats.trader.execute.place_orders` 与 `ats.broker.ibkr` 也可能被直接调用（CLI `ats trader execute`、`ats trader buy/sell`）。把判断放在图节点等于把门禁建在调用者之上。

**进程内 grant 不足以定义跨进程单活**：本部署同时存在常驻 scheduler 进程（经 `_chief_daily` 可达 trader 节点）与一次性 CLI 进程。若切换时只签发新进程 grant，旧进程仍持有有效能力并可提交。配置层互斥也无法约束尚未退出的旧进程。

**选择**：引入**持久化的活跃 route 状态**，含 `route_id` 与**单调递增的 `generation`**，由单一权威存储持有（本机 SQLite，与项目既有部署一致，不引入分布式组件）。下单能力 grant 与执行授权均绑定 `(route_id, generation, environment, account)` 四元组；`place_orders` 在**每次实际提交**时重新读取权威状态并校验，SHALL NOT 依赖启动时的快照。切换与提交建立互斥边界：切换在权威存储上以事务提升 generation，旧代能力在提交校验时因代次落后而被拒。能力签发须校验**正向环境与账户匹配**（broker 实际连接账户须与 grant 声明的账户一致，`secrets.ibkr_account` 可作账户来源），SHALL NOT 仅凭端口或调用方声明判定。新增下单出口必须经同一仲裁，架构守卫扫描绕过路径。

**回滚可区分性**：只比较 `route_id` 无法区分 A→B→A 的第一次 A 与第二次 A。绑定 generation 后，此前签发的授权在回滚后仍因代次落后而失效，SHALL 被接受。

**备选 1**：只按 `ibkr_port ∈ {7496, 4001}` 判定 paper/live。否决——端口是部署事实而非授权事实，无法表达「本进程被禁止真实下单」，也无法阻止旧入口在切换后仍用同一 paper 端口下真单。

**备选 2**：grant 存于进程内存。否决——无法跨进程撤销，无法定义单活，A→B→A 不可区分。

### 决策 4：授权生命周期与原子切换协议（冻结 → 排空 → 换代次 → 开放）

现模型无生命周期支撑：`ExecutionAuthorization`（`src/ats/execution/authorization.py:45-57`）只有十个 §10.4 因果字段，**无授权 ID、无有效期、无完成状态**，且由审查与审批记录即时构造；`IBKRBroker.place_orders`（`ibkr.py:299`）仅 `ib.sleep(wait)` 短暂轮询即返回，后续成交与不确定提交交由对账。因此「枚举有效且未完成的授权并返回剩余有效期」在现模型下不可实现，必须先补生命周期。

**权威状态复用**：不另建重复账本。授权的未完成状态 SHALL 由既有的 decision / cycle 状态与订单意图记录派生（提交未终结、部分成交、状态未知、迟到成交各自可判）。

**原子切换协议**（顺序不可交换）：

1. **冻结**：对当前 route 停止签发新授权、停止接受新提交（能力层拒绝，已在途的不受影响）。
2. **排空核验**：查询并确认无「有效且未终结」的授权；对 `submitted` / `partial` / `unknown` / 迟到成交四类分别规定阻断或显式承接策略。授权过期 SHALL NOT 使状态未知的提交从检查范围消失。
3. **换代次**：在权威存储上提升 generation 并写入新 route。
4. **开放**：新 route 取得能力后开始接受提交。

旧订单可由只读对账承接，但 SHALL NOT 使旧 route 恢复新增提交能力。

**备选**：以「等待超时」代替冻结与排空。否决——超时无法证明没有并发签发，且会把已提交但状态未知的订单误判为已排空。

### 决策 5：影子差异报告为追加式，且通过判据须闭合

现有 `record_consumer_comparison` 是 `INSERT OR REPLACE`（同键覆盖），无法表达「同一批运行先出报告、后补齐调度面」，也不可审计谁在何时改过结论。

**选择**：影子报告为追加记录，含逐面结论与 `not-compared` 原因；签核/驳回/撤销作为新记录追加。切流执行器引用报告时 SHALL 校验四项：签核有效、必需面无 `not-compared`、无未接受差异、报告仍适用于当前 scope 与当前代码/配置。`not-compared` 的必需面使该报告**不可**被引为该范围的通过证据。

**备选**：一次性生成完整报告，缺面即整体失败。否决——调度遗漏往往需要跨重启窗口才能观测（misfire/restart 场景），一次性生成会把「需要时间观测」误作「无法验证」。

### 决策 6：影子输入必须可重放，且按批次适用矩阵分级固定

「同一已发布数据快照」不足以固定全链输入：`consumer_api.read_input` 对 `MARKET_DATA` 读运行时行情（`consumer_api.py:173-199`）、`BROKER_STATE` 读实时券商状态；Chief 旧上下文用 `datetime.now()` 与 `live_broker` 组合快照（`agents/chief/assemble.py:45-59`）。新旧路径顺序运行期间行情、持仓、成交与时间都可能改变——持久化 vintage 相同仍不能把风控差异归因于新旧路径。

**选择**：定义**可重放的影子输入包**，固定已发布数据 refs、运行时行情与账户与历史状态、逻辑评估时间、规则版本、模型与提示配置，并保存各项 hash。按**逐批适用矩阵**分级要求：

- **纯研究读取批次**（Layer / Information / Sector / Fundamental / Macro，输入全为持久化快照）：只需固定已发布数据 refs 与投影 hash，runtime 面记为不适用并显式标注。
- **完整交易批次**（含 Technical / Chief / Risk / Trader / Clerk）：必须固定运行时行情、账户与历史状态、逻辑评估时间、规则版本与模型提示配置。

**差异接受规则**：矩阵逐类批次声明必需比较面、允许的语义或数值差异范围、谁可接受某类 `diverged`。LLM 输出与角色重构引入的合法语义变化 SHALL 有显式允许区间，SHALL NOT 以逐字相等为默认判据。

**备选**：所有批次一律要求全量输入固定。否决——纯研究读取批次不消费 runtime 输入，强制全量固定会引入无意义的观测成本并拖慢首批切换。

### 决策 7：影子账本物理隔离，真实 `trades` 表不参与影子写入

影子订单意图若写入真实 `trades`，会污染持仓、绩效与资金对账，并让「影子期禁止真实下单」这一断言无法自证。

**选择**：影子意图写入独立影子账本（与 Phase E 的 `var/shadow/phase-e.sqlite` 同族但独立），保留可重建所需的全部归因字段；`trades` 写入路径在影子模式下经仲裁拒绝而非静默改道（避免影子被误认为真实成交）。

**下单证明的观测对象**：`trades` 无新增**不足以**证明无真实下单——券商可能已接收订单而本地未落库；反之旧活跃 route 的合法成交或切换前订单的迟到成交会使表新增，却不是新路径越权。因此**主要证据**为：新路径的 broker submit 调用与拒绝审计记录、能力校验结果、隔离演练中的券商模拟接收记录。`trades` 未污染作为**另一条独立**验收，二者不互相替代。真实只读核验按 route / 账户 / 订单归因排除既存合法成交。意料之中的模拟提交被能力层阻断计为成功；**意外真实提交尝试必须成为独立差异或停止条件**。

**备选**：影子意图只写内存/临时文件。否决——无法在进程重启后重建归因，也不满足 Clerk 的对账语义。

### 决策 8：旧调度必须先接入统一逻辑触发身份，再谈 owner 切换

`src/ats/runtime/scheduler.py` 中 `owner_mode` **完全不存在**，`trigger_key` 只出现在 `_start_phase_e`（L1027+）内部。旧常驻调度自建 APScheduler jobs 并直接调用角色入口；Phase E 是另一条显式 `--phase-e` 启动路径。因此仅改 `workflow_owners.yaml` 的 mode 与 `phase_e_schedules.yaml` 的 enabled，**既不能证明已运行的旧进程停止触发，也无法让旧路径在回滚后按新账本去重**。

**选择**：新增旧调度适配层——把旧 job 与 event 映射到共同的逻辑触发身份（workflow + scope + 计划时点，或 event + version）；旧路径在执行前读取共享 owner 状态与代次并登记认领；切换流程为「冻结新认领 → 清点未完成 → 移交」；运行任务承接时 SHALL 证明旧执行方不能继续发布同一结果。配置变更的重载或受控重启方式 SHALL 明确。调度遗漏比较 SHALL 包含**独立预期触发集合**，否则新旧同时遗漏不会被双方差异捕获。

**决策 9 的顺序约束**：冻结新认领必须在清点之前，否则清点后又会产生新触发——单靠「逐个声明处置」无法解决该竞态。

**备选**：切换时强制等待旧路径排空。否决——`TriggerService` 的 lease 心跳允许长任务，强制排空会让切流窗口不可预测；且排空并不解决「新旧两条路径都认为该触发归自己」的语义歧义。

### 决策 9：切流不得修改受取证约束的文件，证据登记后置到代码冻结之后

`assurance` 的失效判定有两条独立路径：`required_fingerprint_paths` + `consumer_fingerprint_paths` 的**文件内容**比对（`_dependencies()` 对文件字节做 sha256），以及 `manifest_hash` 与当前 `config/data/target_dataflow_coverage.yaml` 摘要的比对（`_invalid_event` 判 `manifest_drift`）。

受指纹约束的完整面：

| 范围 | 文件 |
|---|---|
| 全部十消费者共享 | `src/ats/data/assurance.py`、`src/ats/data/consumer_api.py`、`config/data/structured.yaml`、`config/data/unstructured.yaml` |
| trader + clerk 追加 | `src/ats/execution/authorization.py`、`src/ats/decision/repository.py`、`src/ats/execution/clerk.py` |
| chief + risk 追加 | `src/ats/execution/state_api.py`、`config/risk.yaml` |
| 全部已登记证据 | `config/data/target_dataflow_coverage.yaml` 自身摘要 |

因此：**先取证再改配置会使资格立即自失效**（登记证据 → eligible → 改消费者开关 → 指纹变化 → 后续 ineligible），且该文件是多消费者共同依赖，改一个消费者可能使其他消费者失效。原 proposal 计划「资格通过后修改 `structured.yaml` 消费者模式」与此直接冲突。

**选择**：

1. **运行时切流只使用既有 release overlay（`var/structured_data/releases.yaml`）与本 change 独立的控制状态**，SHALL NOT 修改已取证的 catalog 基线（`config/data/structured.yaml`）与 `config/data/target_dataflow_coverage.yaml`。
2. 若确需修改受约束文件，则 SHALL 在**最终代码/配置**上重新验证、追加证据并重算受影响消费者资格；SHALL NOT 修改旧证据或放宽指纹标准。
3. **证据登记整体后置到代码冻结之后**。本 change 的代码改动必然触碰上表文件（尤其授权绑定 route+generation 会改 `authorization.py`，它是 trader+clerk 的指纹路径），故不得按「每批切流前登记一次」推进。
4. **影子输入包 SHALL 以新模块包裹 `consumer_api.py`**，SHALL NOT 直接修改 `src/ats/data/consumer_api.py`（它是全部十消费者共享的指纹路径）。同理，切流控制平面与影子模块 SHALL 为新增文件。

**备选**：接受自失效并逐批重验。否决——这使「逐范围切换」退化为「每次改动都让上一批失效」，无法分批推进，且会诱发放宽指纹标准的压力。

### 决策 10：先补 `assurance.py` 回归与进程禁写，再建依赖它们的门禁与核验

Phase F 的路由判定直接读 `qualification()`，而该模块只有脚本验证（`tests/` 零覆盖）。若先建门禁后补测试，门禁的正确性将建立在一个无回归保护的模块上。

同理，F.0.3 的接入核验要求在无券商写权限的环境进行，而该保护若到影子阶段才建立，接入核验开工时保护尚不存在——形成依赖倒置。故**进程级 broker write 禁令与隔离运行环境必须前移至最早阶段**，先于任何接入核验。

**备选**：把隔离环境视为部署前提而非本 change 交付物。否决——前置核验无法在缺少该前提时开始，等于把 Phase F 的关键路径依赖外部条件。

**实现形态（任务 1.6/1.7 落地记录）**：切流不变量扫描独立成 `ats.workflow.cutover_guards`，不并入 `ats.workflow.architecture_guards`。三个理由：一是扫描域不同——架构守卫只扫 `agents/`，而切流要约束的路由解析与下单路径分布在 `data/`、`execution/`、`workflow/`、`runtime/`；二是豁免生命周期不同——Phase F 期间声明的绕过只能指向**更晚**的阶段，故词汇表延展至 `Phase G`，与架构守卫的 `PHASES` 分离；三是规则精度不同——切流守卫匹配的是**接收者+方法**（`manager.apply`）而非裸方法名，因为 `rollback` 在四个模块里是 SQLite 事务回滚，按方法名匹配会立刻产生 4 项误报并训练所有人忽略守卫。

**规则设计上的关键取舍**：读取模式（`read_mode`/`source_mode`/`owner_mode`）**不**判违规。当前有 23 个模块合法地询问「这个 consumer 是什么模式」，那是既有且有效的 rollout 机制；若一并标记，就会产出 23 个虚假豁免并让守卫失去意义。被禁止的是**自己改路由**（应用 release overlay、写 owner mode、设全局默认），因为那能绕过资格门提升无证据覆盖的 consumer。

### 决策 11：影子与资格之间不得形成循环依赖

总规划允许在未签发生产资格时通过受限候选 / 隔离路径补验。若读路径强制 `qualification()`，而接入核验又要求先取得资格，会形成「先资格才能接入、先接入才能资格」的循环。

**选择**：明确**隔离接入验收入口**——该入口在无生产资格时可用，但 SHALL 运行在进程禁写与隔离账本之下，SHALL NOT 产生任何生产路由变更、审批或账本记录；其产出的运行证据用于资格登记。提交真实交易前仍 SHALL 按本次决策的 scope 重新验证资格。

### 决策 12：交付状态分列，回写总 change

`design.md` 允许全部 ineligible 时交付门禁与演练，而 tasks 的实际切换任务需要部署 / 实盘授权。两者并不冲突，但 SHALL 明确区分三种收尾状态并各自回写总 change：

1. **门禁实现验收完成**——切流控制平面、影子能力、交易仲裁、守卫与回归已交付并通过验证。
2. **生产读路径 / 调度路径切流完成**——对应范围已取得资格且完成分批切换与观察。
3. **实盘切流完成**——已取得明确实盘授权并完成 live route 切换。

本子 change 的完成**不代表整个 Phase F 完成**；未获授权或未就绪的实际切换任务 SHALL 保持未勾选并向 `refactor-workflow-dataflow-architecture` 回写状态与原因。

### 决策 13：实盘授权为显式外部输入，且与部署授权分离

旧规划 F.0.7 与 11.7 都要求「明确实盘授权」，且明确「规划批准与前置通过均不构成该授权」。

**选择**：实盘授权作为**独立的、可审计的外部输入**（来源、操作者、生效范围、有效期），由仲裁在切换时校验；缺授权即拒绝并报告。部署路由变更授权与实盘授权是**两个不同授权**，前者不蕴含后者——`specs/execution/live-route-switch` 据此分别要求两者。纸面 / 隔离演练记录与真实切换记录分开保存，且演练不计为已获授权。

**备选**：用环境变量开关充当授权。否决——环境变量无来源与操作者，不可审计，且易随部署继承而长期存在。

**新路由激活门禁的范围语义**：`specs/workflow/cutover-control` 中「读路径未取得资格而交易开关开启 → 启动失败」约束的是**新 route 的激活**，SHALL NOT 被解释为对现存旧 route 的追溯否决。交易开关关闭时，研究服务 SHALL 允许在无生产资格的情况下启动（研究读取与交易激活是解耦的两件事）。未知 scope 的资格 SHALL NOT 在启动时被假定为已全面验证，提交前仍 SHALL 按本次决策的 scope 重新验证。

## Risks / Trade-offs

- **[十消费者均未取得生产资格 → 可切范围可能为零]** → F.0 逐消费者处置允许部分范围进入；spec 明确「缺项只阻断受影响范围」。若最终全部 ineligible，按决策 12 只达成「门禁实现验收完成」，生产切流状态回写总 change——这比降低证据标准更可接受。
- **[受指纹约束的代码改动会使既有资格失效]** → 决策 9 要求证据登记整体后置到代码冻结之后，且切流只走 release overlay、不改取证基线。代价是「改代码 → 重新取证」的循环必须一次做完，不能边改边取证；这是把分批切流从「随时可做」变成「冻结后有序推进」的代价。
- **[影子运行成本翻倍]**（同 vintage 双跑分析/风控/调度，且完整交易批需固定 runtime 输入）→ 影子按批次与范围启用而非全量常驻；纯研究读取批按决策 6 只固定持久化 refs，避免无谓的 runtime 观测成本。
- **[资格 TTL 到期导致切流中途失效]**（technical/trader/clerk 的 TTL 为 1 天）→ 切流批次按短 TTL 消费者单独排期；`qualification()` 的时点判定保证过期即 ineligible，无需额外机制。
- **[新增下单仲裁可能被绕过]**（未来新入口）→ 仲裁置于 broker 提交层而非调用层，每次提交重验权威状态，并入架构守卫扫描；`specs/execution/live-route-switch` 要求绕过即判违规且豁免须声明收敛阶段。
- **[跨进程单活依赖权威存储的可用性]** → 仲裁状态不可读时 SHALL fail closed（拒绝提交）而非降级放行；代价是权威存储故障会阻断交易，但这是「宁可不下单也不双活」的必然取舍。
- **[读路径降级回旧路由造成「看起来没切」]** → 每次降级记录缺口并进入逐批报告；ineligible 消费者在报告中显式列为未切换，不静默沿用新路由。
- **[「无安全旧路由」分支可能使部分范围直接停摆]** → 决策 2 的 blocked/unavailable 分支优先于「强行回旧路由」；预期影响限于退役已完成且回退证据失效的组合，属本 change 明确登记的缺口。
- **[in-flight 冻结使交易切换难以自动化]** → 这是有意的保守设计：交易路由切换预期为低频人工操作，自动化收益低于误切换代价。
- **[旧调度适配涉及常驻进程行为]** → 配置重载或受控重启方式须显式登记；常驻进程未退出时 SHALL 由共享代次与认领登记拒绝其发布，而非依赖运维手动确认。
- **[`assurance` 回归从脚本移植时可能与脚本行为漂移]** → 断言以 specs 的 fail-closed reason code 为准，脚本与 pytest 并存比对；不一致时以 specs 为准并修脚本。
- **[六面比较中「调度遗漏」与「风控 verdict」需要跨进程观测]** → 差异报告为追加式、可分批补齐；`not-compared` 面使该报告不可用作通过证据，避免「未比较」被当作「一致」。遗漏比较另需独立预期触发集合，否则新旧同时遗漏不可见。

## Migration Plan

**阶段 0（无路由变更，先建保护）**：补 `assurance` pytest 回归与切流不变量守卫；**建立进程级 broker write 禁令与隔离运行环境**（前移项——F.0.3 接入核验依赖它）；建立切流控制平面与六条边界开关，默认全部保持当前路由（读=旧、调度=legacy、交易=旧）。此阶段不改变任何生产行为。

**阶段 1（旧调度接入统一触发身份）**：旧 job / event 映射到共同逻辑触发身份，执行前读共享 owner 代次并登记认领；此阶段仍全部 owner=legacy，不切换。

**阶段 2（代码冻结点）**：完成交易仲裁与代次、授权生命周期与原子切换、影子输入包与比较矩阵、旧调度适配、边界接线。**完成后方可进入取证**——此后不再修改受指纹约束的文件（决策 9）。

**阶段 3（证据接收，后置）**：执行 F.0.1–F.0.2，在**最终代码/配置**上产出版本化接收矩阵与逐消费者 eligible/ineligible 报告。若此阶段后仍需改取证基线文件，须重算受影响消费者资格。此阶段不切流。

**阶段 4（接入核验）**：执行 F.0.3–F.0.5，经隔离接入验收入口（决策 11）产出十角色实际接入核验与逐消费者处置。此阶段不切流。

**阶段 5（影子运行）**：执行 F.0.6 与 11.1–11.4，影子模式与差异报告投入运行，逐批产出影子报告并按批次适用矩阵签核。仍不切流、不下单。

**阶段 6（读路径分批切换）**：执行 11.5，**在取得明确部署授权后**按批次对已取得资格、已验证回退且回退目标未退役的消费者范围切换，每批记录切换前后路由、观察窗口、结果与回退演练。

**阶段 7（调度路径切换）**：执行 11.6，冻结新认领后清点与移交未完成触发，再切换 workflow owner 与 schedule 启用状态，验证无双重所有权。

**阶段 8（交易路径）**：执行 11.7，**默认在纸面/隔离环境演练**。真实 live route 切换保持锁定，直至收到可审计的实盘授权且全部交易门禁通过。

**阶段 9（清零与回滚演练）**：执行 11.8–11.9，消费者清零证明、数据对账、回滚窗口核验、墓碑状态一致性检查，并独立演练 read/schedule/trade 三条边界回滚。

**回滚策略**：三条边界各自独立回滚。读路径回滚 = 该消费者范围切回旧路由（`ReleaseManager.rollback` 或 owner 模式回退），前提是回退目标未退役且可用，否则停止该范围并阻断依赖交易；调度回滚 = owner 模式回 `legacy` 并按已执行触发记录去重；交易回滚 = 按原子协议冻结 → 排空 → 提升 generation 切回，失败则保持现状而不静默转入故障路由。影子、审批与账本记录在回滚中一律保留。

**收尾状态**（决策 12）：分别列示「门禁实现验收完成」「生产读/调度切流完成」「实盘切流完成」，未获授权者保持未勾选并向总 change 回写。

## Open Questions

- 十消费者中实际可首批切流的范围，取决于代码冻结后 F.0.2 的登记结果与各消费者 TTL；本阶段按 spec 的逐范围判定处理，无需预先确定清单。
- 首批批次的观察窗口长度与停止条件阈值，需由 F.0.6 的 dry-run 与首批影子报告的实际噪声水平确定；在首批报告产出前以保守值登记，不在本阶段固化。
- 权威 route 状态的存储位置（复用现有平台数据 SQLite 或独立文件）需在实现前确认，约束是 SHALL NOT 落在受指纹约束的路径集合内。
