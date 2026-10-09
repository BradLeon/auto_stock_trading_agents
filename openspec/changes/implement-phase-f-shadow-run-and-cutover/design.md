# Design

## 2026-10-09 当前验收裁决：新入口按需求独立达标

用户已确认：新设计是正确性基准，旧业务未完备、遗留错误、旧侧缺失及新旧结果差异不阻塞新入口验收。对比工具与历史报告保留为辅助诊断，不继续补旧业务；旧入口仅核验退出安全，防重复触发/发布、越权提交和状态污染。

必需验收按固定需求/所选 Event 或 Routine 与依赖闭包、实际治理数据/投影、独立预期调度、风控审批/修订、Trader/FakeBroker/Clerk、幂等恢复与故障拒绝建立断言。新入口、共享依赖、必需历史状态或权限边界失败仍必须处理。停止与版本恢复先登记并从真实入口实测；不要求旧路可用，不豁免新资格或生产部署授权，C3 保留。

验收调整曾重开 **3.2–3.6、5.7、12.2/12.3** 八项；本轮 **3.2–3.6、5.7** 已实施，12.2/12.3 仍待办；旧实现/测试仍可复用，历史勾选和报告不自动代表新契约通过。当前 **76/116 完成，40 项待办**。3.9 已交付工具保留历史完成；11.2/3.10 已完成选定隔离范围的独立新入口 capture/run/replay、新报告与实际授权出口，报告保持 unsigned。父整图范围及生产/实盘状态不因本次规划改变。

## Context

动机见 `proposal.md`；本阶段的行为契约见 `specs/`。这里只记录塑造实现方式的现状与约束。

**已验收、本阶段必须复用的机制**（均由 `complete-target-dataflow` 与 Phase A–E 建立）：

- 读取资格：`ats.data.assurance` 提供追加式证据表 `dataflow_assurance_events`（含 `UPDATE`/`DELETE` 拒绝触发器）、`record_evidence` / `revoke_evidence` / `qualification` / `evidence_history` 四个 API，以及 `config/data/target_dataflow_coverage.yaml` 的十消费者契约（`qualification_policy` 的 `required_fingerprint_paths`、`optional_inputs`、`rollback_routes`）。`qualification()` 已按 `domain_id + consumer_id + contract_version + scope` 做 fail-closed 判定，并支持 SEC 原文 optional 非阻塞例外。
- 读路径开关：`ats.data.rollout_modes.read_mode()` 的四级解析（env → release overlay → catalog `feature_flags.consumer_sources` → `consumers`）与 `ReleaseManager` 的 `publish` / `rollback`（仅映射变更，不删数据）。
- 调度开关：`ats.workflow.ownership` 的 `{legacy, shadow, dispatcher}` owner 模式、每模式独立 SQLite（`var/shadow/phase-e.sqlite`）与 `run_owned_workflow`。
- 执行门禁：`ats.execution.authorization` 的十字段授权与 `validate_authorization`；`ats.graph.chief.trader` 节点的 `stale` / `refused` / `placed` 三出口；`ats.trader.execute.place_orders` 的授权 fail-closed 与 `client_order_id` 幂等。
- 退出机制：`ats.workflow.legacy_retirement` 的墓碑 → fail-closed 读门 → 两段式清除，`config/workflow/legacy_retirement.yaml` 现有 26 条（12 retired / 14 pending）。
- 调度运行时：`ats.workflow.dispatcher.Dispatcher.dispatch`、`TriggerService` 的 `claim_trigger` / `renew_trigger` / `finish_trigger` / `compensate_trigger`、`WorkflowStore` 的 `workflow_runs` / `trigger_runs` 表。

**2026-10-07 审计确认的当前状态**（原立项基线与原模块测试保留在历史验证材料）：

- 已交付：assurance 回归、隔离环境、控制状态、route/generation、授权生命周期、六面比较与报告工具。不能按原“零实现/零测试”现状重建。
- 控制声明未约束实际审批/Clerk/角色写点；实际读入口没有接入新增资格路由。关闭审批仍可经真实 repository 写入隔离审批。
- broker 缺 grant/UNSET 可进入 session；配置账户一致不等于真实会话账户核验。
- 切换执行器在缺报告/投影检查时放行；部分消费者合格却改全局 route；配对边界逐次提交可留下不兼容半迁移状态。
- 旧调度仍缺新增 owner/claim 接线。十角色静态扫描为 5/10 compliant，但 refs/hash 全空，尚非动态接入验收。
- 输入包主要保存 hash，缺恢复内容与同输入双 runner。影子订单/提交尝试为零，默认报告库不存在；实际影子期未验收。
- 本机十消费者均因 assurance_ledger_missing 而 ineligible；五边界 legacy、一交易 disabled；owner YAML 全 legacy、Phase E schedules 全 disabled；退役三项仍 pending。
- 既有六分析部署授权及调度授权需按动作范围核对；LIVE-X 来源未核实，不推断用户实盘授权。审计时 B 尚未实现；2026-10-07 用户改选 A，B 留作历史，C3 自动下单停用保留。

审计源码和反例见 `docs/validation/PHASE_F_ACTUAL_COMPLETION_AUDIT_2026-10-07.md`。本设计和 tasks 取代历史验收中“门禁完成、仅剩授权取证”的口径。

**环境约束**：SQLite 且 `TradingMemory` 连接按进程缓存，scheduler 历史上因此刻意串行；`assurance.py` 写连接已启用 WAL + `busy_timeout=30000`，读连接为 `?mode=ro` + `query_only`。

## Goals / Non-Goals

**Goals:**

- 切流成为**受控的显式操作**：每次切换都有批次记录、资格依据、观察窗口、停止条件与已验证回退。
- **逐消费者判定**，不让一个数据域的整体通过掩盖个别消费者缺证据。
- 交易路径在**能力层**单活，缺能力即拒绝真实提交；影子启动缺禁令能力即拒绝，不影响正常只读服务。
- 影子结果**可签核、不可事后改写**，缺任一必需需求断言即不能作为通过证据。
- 缺项**只阻断受影响范围**，不阻断无关的已验收路径。
- 真实实盘切换**默认锁定**，只由可审计的外部授权解锁。

**Non-Goals:**

- 不重建数据平台、不重复全量采集、不整体重做 A–E 或放宽其门禁标准；允许用户已选 A 所需的取证前 Trader 行情契约升级及共享接口适配。
- 不为「让切换可执行」而放宽证据标准或新增按报告年龄的 stale 降级。
- 不在本阶段执行真实 live route 切换（只交付门禁、runbook 与演练）。
- 不修复或补完旧业务、不要求新旧结果一致；仅旧入口仍能影响新状态/触发/发布/提交时核验失权或关停，已交付仲裁适配复用。
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

**选择**：引入**持久化的活跃 route 状态**，含 `route_id` 与**单调递增的 `generation`**，由单一权威存储持有（本机 SQLite，与项目既有部署一致，不引入分布式组件）。下单能力 grant 与执行授权均绑定 `(route_id, generation, environment, account)` 四元组；`place_orders` 在**每次实际提交**时重新读取权威状态并校验，SHALL NOT 依赖启动时的快照。切换与提交建立互斥边界：切换在权威存储上以事务提升 generation，旧代能力在提交校验时因代次落后而被拒。能力签发须校验**正向环境与账户匹配**（broker 实际连接账户须与 grant 声明的账户一致，`secrets.ibkr_account` 只作预期声明，实际会话/订单账户必须另行确认），SHALL NOT 仅凭端口或调用方声明判定。新增下单出口必须经同一仲裁，架构守卫扫描绕过路径。

**回滚可区分性**：只比较 `route_id` 无法区分 A→B→A 的第一次 A 与第二次 A。绑定 generation 后，此前签发的授权在回滚后仍因代次落后而失效，SHALL NOT 被接受。

**2026-10-08 实际出口实施（1.1/2.2/2.3/2.8）**：CLI/scheduler 启动、shadow 命令、shadow workflow owner 和直接 shadow Dispatcher 均先安装并断言进程禁写；隔离 context 向子进程传递禁令并正确恢复嵌套环境。broker 无 grant/UNSET 即拒绝，实际 `placeOrder` 和 `cancelOrder` 受同一仲裁。账户来自当次 `managedAccounts()`，配置仅选择预期账户；每笔订单显式指定并核验 account。环境只识别实际会话中已支持的个人账户格式 DU+数字（paper）或 U+数字（live），其他格式拒绝，不能从端口推断；真实网关验证仍由 7.13 承接。

本机跨进程互斥使用 SQLite 权威文件旁的稳定 `.lock` 文件及 OS 文件锁；冻结、换代、开放和实际 broker 写入共用锁，进程内 grant 撤销/禁令也与写点互斥。锁等待上限 5 秒，无法仲裁即拒绝；锁仅覆盖检查/发送/接收提交回执，不覆盖后续等待成交。进程退出由 OS 释放锁；本部署不声称提供跨主机仲裁。直接读取 route 使用 SQLite 只读连接，缺文件不创建空权威状态。

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

**写点崩溃窗口**：实际 broker 调用前，在同一互斥区内提交 `trade_submit_receipts` 仲裁回执，记录 cycle/revision/sequence、route/generation、账户/环境、PID、payload hash 与 orderRef。它只保护不可逆写点，不另行派生 decision 授权生命周期。意图身份使用完整 cycle/revision/sequence，不使用截断 orderRef，也不因换代、账户或 symbol 变化获得重试资格。超时/进程退出保留 unknown；unknown/submitted/partial 回执补入排空阻塞，不能用调用者的“已完成”生命周期掩盖券商已接收而业务账本未写的订单。只有券商观察到的终态才释放阻塞；后续实际只读对账/恢复由 7.5/11.3/13.3 继续集成，不能自动清理回执或盲重试。冻结事务内还核对 expected generation，竞争失败者不能再次换代或解除胜者的冻结。

**备选**：以「等待超时」代替冻结与排空。否决——超时无法证明没有并发签发，且会把已提交但状态未知的订单误判为已排空。

### 决策 5：新入口验收报告按需求判定并追加保存

旧业务不是 oracle。报告采用版本化 requirement/场景→预期→实际结果→输入/输出 refs→结论映射，六面分别核验新入口的输入、调度、分析、风控、审批与交易归因。必需项必须 passed，failed/untested 不可签成通过，not-applicable 仅按运行前固定的范围矩阵使用。新旧 matched/diverged/not-compared 和差异接受仅为诊断元数据，不参与放行。

新报告显式区别于既有 comparison 类型。签核/驳回/撤销追加保留；修复后生成新 run 与报告，不改旧结论。正式 checker 核验需求版本/scope、有效签核、必需断言和真实新运行/重放/读取/输出、当前指纹。不能只改 legacy report 的标签继承资格。3.2–3.6 重开实施，旧工具与历史证据保留。

备选：保留新旧差异接受门槛。否决——旧业务错误会成为新设计负担，也可能把共同错误当作一致。

### 决策 6：固定输入支持新入口独立运行与重放

保留 3.1 的可恢复内容、分阶段治理报价、逻辑时钟、规则/模型/提示和源码指纹。实际新入口首次运行与独立新进程重放分别留 run IDs/refs；无需 legacy run ID 或 distinct legacy/new entry pair。未知输入、阶段/scope/版本变化拒绝，不回 Provider 刷新。3.9 的新侧和输入工具复用，但现有 capture-business 仍会调用旧侧，须由 3.10 补独立入口后才能满足新标准；不把现有命令改名就认定已实现。

矩阵按真实消费固定输入：纯持久化研究可不要求 runtime/交易面，实际用行情的研究必须固定行情/时间，决策与交易须固定账户、行情、历史、审批与归因。所选 Fundamental 模式及全部必需类别/任务 scope 独立满足；未选模式不强求。

模型以事先声明的 schema、事实/观点血缘、必需内容、风险约束与允许的语义/数值范围验收，不以逐字相同或旧输出正确为假设。录制响应说明受控 transport 条件；fixture 证明机制与真实数据/网关证明分列，不能冒充生产资格或模型质量全覆盖。

### 决策 7：影子账本物理隔离，真实 `trades` 表不参与影子写入

影子订单意图若写入真实 `trades`，会污染持仓、绩效与资金对账，并让「影子期禁止真实下单」这一断言无法自证。

**选择**：影子意图写入独立影子账本（与 Phase E 的 `var/shadow/phase-e.sqlite` 同族但独立），保留可重建所需的全部归因字段；`trades` 写入路径在影子模式下经仲裁拒绝而非静默改道（避免影子被误认为真实成交）。

**下单证明的观测对象**：`trades` 无新增**不足以**证明无真实下单——券商可能已接收订单而本地未落库；反之旧活跃 route 的合法成交或切换前订单的迟到成交会使表新增，却不是新路径越权。因此**主要证据**为：新路径的 broker submit 调用与拒绝审计记录、能力校验结果、隔离演练中的券商模拟接收记录。`trades` 未污染作为**另一条独立**验收，二者不互相替代。真实只读核验按 route / 账户 / 订单归因排除既存合法成交。意料之中的模拟提交被能力层阻断计为成功；**意外真实提交尝试必须成为独立差异或停止条件**。

**备选**：影子意图只写内存/临时文件。否决——无法在进程重启后重建归因，也不满足 Clerk 的对账语义。

### 决策 8：旧调度必须先接入统一逻辑触发身份，再谈 owner 切换

`src/ats/runtime/scheduler.py` 中 `owner_mode` **完全不存在**，`trigger_key` 只出现在 `_start_phase_e`（L1027+）内部。旧常驻调度自建 APScheduler jobs 并直接调用角色入口；Phase E 是另一条显式 `--phase-e` 启动路径。因此仅改 `workflow_owners.yaml` 的 mode 与 `phase_e_schedules.yaml` 的 enabled，**既不能证明已运行的旧进程停止触发，也无法让旧路径在回滚后按新账本去重**。

**选择**：新增旧调度适配层——把旧 job 与 event 映射到共同的逻辑触发身份（workflow + scope + 计划时点，或 event + version）；仍可执行的旧路径在执行前读取共享 owner 状态与代次并登记认领，或经可核验关停/失权退出；切换流程为「冻结新认领 → 清点未完成 → 移交」；运行任务承接时 SHALL 证明旧执行方不能继续发布同一结果。配置变更的重载或受控重启方式 SHALL 明确。新调度 SHALL 依据需求/配置/发布事件独立导出预期触发集合，不以旧运行或两侧并集定义成功；旧业务不要求成功。

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
3. **证据登记后置到受影响实现依赖闭包的最终冻结之后**。不能只完成第 2–5 组模块便取证，再改第 7 组实际业务接线。涉及共同依赖的范围一起冻结；短 TTL 的临切流刷新只追加证据，不能夹带受保护代码/配置修改。
4. **影子输入包 SHALL 以新模块包裹 `consumer_api.py`**，影子/切流动作 SHALL NOT 改写该共享基线。A 的必要 consumer API 适配允许在最终取证前由 7.14/7.15 完成，须按 0.1/7.17 评估并重验全部受影响消费者；不能在已登记证据上边切流边改契约。切流控制平面与影子模块仍独立实现。

**备选**：接受自失效并逐批重验。否决——这使「逐范围切换」退化为「每次改动都让上一批失效」，无法分批推进，且会诱发放宽指纹标准的压力。

### 决策 10：先补 `assurance.py` 回归与进程禁写，再建依赖它们的门禁与核验

Phase F 的路由判定直接读 `qualification()`；立项时该模块只有脚本验证，现已交付专项 pytest。若先建门禁后补测试，门禁的正确性将建立在一个无回归保护的模块上。

同理，F.0.3 的接入核验要求在无券商写权限的环境进行，而该保护若到影子阶段才建立，接入核验开工时保护尚不存在——形成依赖倒置。故**进程级 broker write 禁令与隔离运行环境必须前移至最早阶段**，先于任何接入核验。

**备选**：把隔离环境视为部署前提而非本 change 交付物。否决——前置核验无法在缺少该前提时开始，等于把 Phase F 的关键路径依赖外部条件。

**实现形态（任务 1.6/1.7 落地记录）**：切流不变量扫描独立成 `ats.workflow.cutover_guards`，不并入 `ats.workflow.architecture_guards`。三个理由：一是扫描域不同——架构守卫只扫 `agents/`，而切流要约束的路由解析与下单路径分布在 `data/`、`execution/`、`workflow/`、`runtime/`；二是豁免生命周期不同——Phase F 期间声明的绕过只能指向**更晚**的阶段，故词汇表延展至 `Phase G`，与架构守卫的 `PHASES` 分离；三是规则精度不同——切流守卫匹配的是**接收者+方法**（`manager.apply`）而非裸方法名，因为 `rollback` 在四个模块里是 SQLite 事务回滚，按方法名匹配会立刻产生 4 项误报并训练所有人忽略守卫。

**规则设计上的关键取舍**：读取模式（`read_mode`/`source_mode`/`owner_mode`）**不**判违规。当前有 23 个模块合法地询问「这个 consumer 是什么模式」，那是既有且有效的 rollout 机制；若一并标记，就会产出 23 个虚假豁免并让守卫失去意义。被禁止的是**自己改路由**（应用 release overlay、写 owner mode、设全局默认），因为那能绕过资格门提升无证据覆盖的 consumer。

### 决策 11：影子与资格之间不得形成循环依赖

总规划允许在未签发生产资格时通过受限候选 / 隔离路径补验。若读路径强制 `qualification()`，而接入核验又要求先取得资格，会形成「先资格才能接入、先接入才能资格」的循环。

**选择**：明确**隔离接入验收入口**——该入口在无生产资格时可用，但 SHALL 运行在进程禁写与隔离账本之下，SHALL NOT 产生任何生产路由变更、审批或账本记录；其产出的运行证据用于资格登记。提交真实交易前仍 SHALL 按本次决策的 scope 重新验证资格。

### 决策 12：交付状态分列，回写总 change

`design.md` 允许全部 ineligible 时交付门禁与演练，而 tasks 的实际切换任务需要部署 / 实盘授权。两者并不冲突，但 SHALL 明确区分三种收尾状态并各自回写总 change：

1. **门禁实现验收完成**——实际业务入口受控、动态隔离/影子运行、切前安全恢复、scope 隔离及否定路径已验证；模块工具交付或声明 wired 不足以构成此状态。
2. **生产读路径 / 调度路径切流完成**——对应范围已取得资格且完成分批切换与观察。
3. **实盘切流完成**——已取得明确实盘授权并完成 live route 切换。

本子 change 的完成**不代表整个 Phase F 完成**；未获授权或未就绪的实际切换任务 SHALL 保持未勾选并向 `refactor-workflow-dataflow-architecture` 回写状态与原因。

### 决策 13：实盘授权为显式外部输入，且与部署授权分离

旧规划 F.0.7 与 11.7 都要求「明确实盘授权」，且明确「规划批准与前置通过均不构成该授权」。

**选择**：实盘授权作为**独立的、可审计的外部输入**（来源、操作者、生效范围、有效期），由仲裁在切换时校验；缺授权即拒绝并报告。部署路由变更授权与实盘授权是**两个不同授权**，前者不蕴含后者——`specs/execution/live-route-switch` 据此分别要求两者。纸面 / 隔离演练记录与真实切换记录分开保存，且演练不计为已获授权。

**备选**：用环境变量开关充当授权。否决——环境变量无来源与操作者，不可审计，且易随部署继承而长期存在。

**新路由激活门禁的范围语义**：`specs/workflow/cutover-control` 中「读路径未取得资格而交易开关开启 → 启动失败」约束的是**新 route 的激活**，SHALL NOT 被解释为对现存旧 route 的追溯否决。交易开关关闭时，研究服务 SHALL 允许在无生产资格的情况下启动（研究读取与交易激活是解耦的两件事）。未知 scope 的资格 SHALL NOT 在启动时被假定为已全面验证，提交前仍 SHALL 按本次决策的 scope 重新验证。

## Risks / Trade-offs

- **[十消费者均未取得生产资格 → 可切范围可能为零]** → F.0 逐消费者处置允许部分范围进入；spec 明确「缺项只阻断受影响范围」。若最终全部 ineligible，只有实际接线、动态隔离运行和切前集成门禁已通过才可单列「门禁实现验收完成」；否则该状态仍未完成，生产切流状态回写总 change——这比降低证据标准更可接受。
- **[受指纹约束的代码改动会使既有资格失效]** → 先按 0.1 确认证据保护影响方案，在所有受影响实现闭包最终冻结后登记；共同依赖不能遗漏，运行时切流不改基线，短 TTL 刷新不能夹带代码改动。
- **[旧对比继续占用验收成本]** → 新入口独立验证；旧双运行只按明确诊断需要启用，旧失败不阻塞新断言。
- **[资格 TTL 到期导致切流中途失效]**（technical/trader/clerk 的 TTL 为 1 天）→ 切流批次按短 TTL 消费者单独排期；`qualification()` 的时点判定保证过期即 ineligible，无需额外机制。
- **[新增下单仲裁可能被绕过]**（未来新入口）→ 仲裁置于 broker 提交层而非调用层，每次提交重验权威状态，并入架构守卫扫描；`specs/execution/live-route-switch` 要求绕过即判违规且豁免须声明收敛阶段。
- **[跨进程单活依赖权威存储的可用性]** → 仲裁状态不可读时 SHALL fail closed（拒绝提交）而非降级放行；代价是权威存储故障会阻断交易，但这是「宁可不下单也不双活」的必然取舍。
- **[读路径降级回旧路由造成「看起来没切」]** → 每次降级记录缺口并进入逐批报告；ineligible 消费者在报告中显式列为未切换，不静默沿用新路由。
- **[「无安全旧路由」分支可能使部分范围直接停摆]** → 决策 2 的 blocked/unavailable 分支优先于「强行回旧路由」；预期影响限于退役已完成且回退证据失效的组合，属本 change 明确登记的缺口。
- **[in-flight 冻结使交易切换难以自动化]** → 这是有意的保守设计：交易路由切换预期为低频人工操作，自动化收益低于误切换代价。
- **[旧调度适配涉及常驻进程行为]** → 配置重载或受控重启方式须显式登记；常驻进程未退出时 SHALL 由共享代次与认领登记拒绝其发布，而非依赖运维手动确认。
- **[`assurance` 回归从脚本移植时可能与脚本行为漂移]** → 断言以 specs 的 fail-closed reason code 为准，脚本与 pytest 并存比对；不一致时以 specs 为准并修脚本。
- **[把旧差异或未测误算通过]** → 六面改为新需求断言；必需 failed/untested 拒绝，旧诊断分列，调度基准独立于执行记录。

### 决策 14：接线证明、scope 与联合动作必须落实到运行时

**5.4 前置补齐**：兼容性按具体 workflow/完整 RouteIdentity 的依赖边判断，每条边引用当前跨版本证明，并明确 mixed_compatible；不再将旧全局三组配对作为运行时条件矩阵。缺当前矩阵、workflow/scope 不符或不兼容拒绝；明确兼容时允许仅请求边界迁移，其他边界/owner 不动。该矩阵在 prepare、route/owner 提交及开放前重读并留历史；生产单 scope 原语不得绕过协调器。正式生产证据适配由 9.2 实施，隔离 proof fixture 不取得生产资格。

**2026-10-09 实施补记（5.15/11.3）**：`joint_cutover` 以控制库 prepared journal 封住实际 read/claim/publication，全部 scope route/history 单事务提交，实际 SQL owner 另事务 freeze/handover，最终重验后解除持久栅栏；跨库不宣称原子。生产授权绑定整个规范请求且持久不可修改，撤销追加记录；提交和恢复重复验证资格/报告/回退/接线/兼容。恢复核对同请求、原 SQL-owner 路径、generation 与本批次冻结 token。旧 worker 绑定 epoch 失效，独立不兼容不扩动作；live_trader 禁止混入。scope 控制由 ATS_CUTOVER_DB 生效，SQL owner 由 ATS_DISPATCH_STATE_PATH 生效，YAML 为模板，release overlay 保持原映射，不作为 scope 激活权威。旧 read executor 的顺序全局联合 apply 拒绝，9.2/10.2/10.3 正式适配仍待办。

交易真实仲裁演练验证部分/迟到成交与 Clerk 排空、代次 1→2→3、旧代 grant 拒绝及恢复后新批准提交。换代后开放失败或权威不可读保留冻结；终结状态与实际决策状态机对齐。固定源码的 Routine/Event 完整 capture/独立进程重放重新通过，闭包 465，新报告 unsigned；原报告保持历史。见 [联合与交易恢复验证](../../../docs/validation/PHASE_F_JOINT_AND_TRADE_RECOVERY_2026-10-09.md)。生产/实盘权威未修改，13.3/13.10 全范围恢复及最终资格独立推进。

**接线**：声明登记仅为 declared，必须以真实调用点和业务入口否定路径证明 enforced。关闭审批时 repository 写入、关闭 Clerk 时实际发布、未合格时业务读取必须被拒绝或按安全回退处置。静态导入/字符串扫描仅是辅证。

**范围**：consumer/domain/contract/business scope 的规范表达由任务 0.3 从已有契约与实际输入确认；资格、报告、回退、运行路由与记录使用同一范围，不允许把实体/时间换成产品列表。权威路由必须能表示“layer 已迁移而 sector 未迁移”。共同依赖按矩阵纳入，不能用总开关覆盖未通过者。

**边界**：独立批次只变其声明边界；不兼容时拒绝，不能偷偷扩成两边界动作。确需一起迁移时显式登记联合批次，按具体 workflow/scope 列全资格、授权、报告及回退要求；一次协调提交，失败后所有受影响范围不得暴露可执行半状态。控制数据库事务、发布覆盖层和 YAML 的生效权威须明确；涉及多个状态存储时用冻结及可恢复协议，不能将两次独立 COMMIT 称为原子。

**执行门禁**：缺 grant/UNSET、空报告、缺 checker、投影不可确认、权威状态不可读均拒绝。broker 校验与实际提交之间的冻结竞态须受跨进程仲裁约束。报告和投影检查由正式入口强制提供，不是可选 callback。


**2026-10-08 局部实施（5.2/5.5/5.14）**：新增 `boundary_evidence` 实际调用点矩阵和按 exact-scope/mode 的追加式接线证据。声明、历史 wired 位及 bootstrap 不证明 enforced；只有覆盖适用实际入口、匹配 scope/mode、正反业务测试及源码/测试摘要的证明可被重验。26 个调用点包括旧/新发布与旧调度；未接线项显式保留缺口。实际 broker 在 isolated/fake transport 下验证证明协议，不能改标 production。

`scoped_routes.RouteIdentity` 固定 domain/consumer/contract 与 kind/id、排序去重 entities、UTC `time_range.start/end`、可选成对 event_id/event_version；额外业务字段保留。资格、报告、回退、路由历史使用同一规范身份。逐 scope 状态及历史在同一 SQLite 事务提交，generation 做并发仲裁；读取不会继承全局 target，global disabled 仍可停止范围。`read_route` 已支持该持久协议及查询重验，缺业务身份的目标请求拒绝；旧 `cutover`/read executor 在未接入新协议前拒绝仅声明切流。Chief/Sector owner 引用实际 9.3/9.6 并验证职责及源码调用点。

Layer 合格/Sector 不合格、重启一致、竞争提交与事务回滚由隔离控制 API/运行路由测试验证，其中外部资格/报告/回退/接线适配器显式模拟。**不能据此勾选实际业务入口 5.3/5.6/5.7、正式执行器 9.2、Chief/Sector 9.3/9.6 或联合提交 5.15**。见 [本轮报告](../../../docs/validation/PHASE_F_SCOPED_ROUTING_2026-10-08.md)。

### 决策 15：完整性裁决、A 契约升级与受保护面是明确前置

CATEGORY 与 TASK 两种完整性判定冲突须在任务 0.2 提交可审阅方案并取得裁决，再由 7.4 实现同一权威判定；未裁决不能单方面选择或放宽 A–E 必需输入规则。它不是可推迟到上线后的 Open Question。

**当前裁决为 A**：用户于 2026-10-07 明确要求将 A 纳入计划，替换尚未实施的 B。7.9 的历史 B 裁决和旧验证材料保留，不再作为当前实施限制。A 限定为 Trader 的受治理 runtime 行情读取能力；复用 MARKET_DATA，契约清单、代码白名单、API、守卫、文档与测试同步升级版本。其他角色权限与批准授权的十字段完整性标准保持既有要求。

**报价服务与用途**：Risk/Trader 共用治理价格服务，Risk 不再依赖 Trader 私有取价函数。审批前按当前待审修订标的读取，仅用于股数换算、限价规范化及相关风险检查；审批后仅可读取获批修订标的作执行条件校验，不获得全市场分析或 Provider 直连权限。runtime 结果不回写共享事实。0.6 必须明确价格类型（如 close/bid/ask/last）、实际 source_as_of、查询时点、来源、币种、交易时段与新鲜度/偏离政策；已有 close-history 能力不能被命名为实时可执行报价，逻辑决策时间不能替代行情真实时点。必要字段/适配由 7.15 补齐。

**资格证据面**：0.6 明确新增行情与授权输入各自的 domain/scope、required_evidence、vintage/completeness/fallback，7.14 同步治理连线及文档；保留授权证据并补齐行情来源、时效和回退证明。不能只把 MARKET_DATA 加入 products，却继续仅用原授权证据证明行情读取合格。

**第 0 组实施基线**：[恢复实施前置确认](../../../docs/validation/PHASE_F_RECOVERY_PREFLIGHT_2026-10-07.md) 已固定 0.1/0.3/0.4/0.6 的影响面、兼容矩阵、Git/uv/授权来源与价格政策。A 计划升级 Trader/Risk consumer 为 v2，其他角色权限不变；行情资格证明治理能力，不是本次待审订单的批准。隔夜历史 Close 与当前 bid/ask 的时点、精度及用途分开，具体数值/失败策略以报告 0.6 的 `phase-f-execution-price-v1` 为实施基线。0.2 于 2026-10-08 获用户明确裁决：选定 Event/Routine 及其依赖必须满足，另一模式不能替代，其他必需类别仍须齐全；第 0 组已收尾，实际代码接线由 7.4 承接。

**审批冻结**：7.12 先规范化完整订单，再固定 revision/hash 并完成 Risk review/Boss approval。审批依据报价引用和执行阶段报价分别留审计，不以将价格附入授权来替代治理读取。审批后报价只能检查执行条件；超出批准约束或必要报价不可用时拒绝并返回重新审查批准，不能重算股数、重写限价或改变已批准 symbol/type/direction/hash。缺价处理在审批卡可见；股数或风险未知不能靠市价降级掩盖，金额型订单不能静默丢弃。

**权限与影子**：行情 read capability 与 broker write grant、账户/环境、代次及幂等门禁分别校验；7.12/7.16 模拟链路完成不解除 C3，也不取得实盘授权。Risk/Trader 在影子双跑和跨进程重放中消费输入包内对应阶段的同一报价，不能因新增读取权限而重新查询变化的 Provider。

任务 0.1 先确认已登记证据及受保护依赖影响，优先复用既有能力。A 已获方向授权，仍需明确改动/重验清单：manifest 为全文件摘要，consumer_api 为共享指纹路径，不能只重验 Trader 或假定其他九角色资格不受影响。7.17 完成受影响十角色契约/资格回归及最小补验清单，6.1 冻结后由 6.4 追加新基线证据；原账本、数据、历史验证记录保持可追溯，漂移旧证据不得重标成新契约有效。无需重做 44 项数据建设或全量采集；未解决的证据缺口按实际受影响 scope 阻塞。

## Migration Plan

依赖以 tasks 中直接前置和任务 0.3 的范围矩阵为准；保留历史编号不表示按 1→13 线性执行。

1. **前置确认（0 组）**：受保护面和证据影响评估；CATEGORY/TASK 权威裁决；实际 scope/边界兼容/owner 矩阵；前置 Git/uv 基线与授权来源核对；0.6 明确 A 契约与价格政策。同步旧验证材料口径，不执行生产变更。
2. **保护及实际接线**：复用隔离工具，修 broker 默认拒绝与会话账户；接审批/角色/Clerk、scope 读路由、旧/新调度 claim 与发布仲裁；补 Chief/Sector 读模型、联合提交与崩溃恢复。A 按 0.6 → 7.14 契约升级 → 7.15 治理价格 → 7.12 审批前规范化 → 7.16 审批后校验 → 7.5 实际链路 → 7.17 受影响回归推进，再进入 6.1/6.4 冻结取证。每项同时交付实际入口测试与文档，不后置到“统一 fix”。
3. **动态接入与影子**：无生产资格时经隔离入口运行十角色及恢复链，保存 refs/hash/run。持久输入并固定逻辑时钟，实际新入口独立运行/跨进程重放、六面需求断言及提交拒绝留证；旧比较可选。需要真实网关的项目单列只读验证，缺网关不阻塞无关研究范围。
4. **切前安全恢复**：13.1–13.3/13.10 在隔离环境验证实际读、调度、交易及审批/Clerk 回退；为生产切换提供证明。不能依赖先做 9.4/10.4，不能把原 helper 演练当作修复后的业务回滚。
5. **最终冻结与资格登记**：6.1 核对受影响 scope 的全部实现依赖闭包，所有计划内受保护改动完成后，按 6.4 接收原真实材料、最小补验并追加登记，6.6 复算资格，6.7 准备短 TTL 刷新。动态隔离证明只登记其真正证明的能力，不冒充当前生产账本/网关完整性。
6. **切前集成门禁**：13.4/13.5 实际非空影子记录的全链追溯与无真实提交验收；13.6 前置提交回归对照，13.7 strict/守卫/reopen。8.5/8.6 准备报告、观察窗口、停止阈值、回退负责人、当次资格与报告适用性，并核对部署授权每条实际边界的覆盖。
7. **获授权生产读/调度切换**：仅就绪范围进入 9.4/9.5/10.4。独立范围只改声明边界；联合读/调度作为一个动作分别留下两侧验收，不重复执行。记录业务探针、前后路由、触发所有权、观察及回退结果。影子报告必须先于其支持的生产动作。
8. **退役与收尾**：在对应迁移/观察窗口后执行 12.8 全部生产消费者清零、新需求数据覆盖/历史保留、安全恢复/观察窗口和墓碑核验，未满足仍 pending。13.8/13.9 分列三种状态并回写父 change。真实 live 切换依旧锁定、不在本 change 执行；父 change 新需求/共享安全及整图验收另行完成，旧专属失败按可达性证据排除。

**安全恢复策略**：独立范围只回退该范围；联合范围按已登记的全边界协议冻结/恢复，不能暗中扩大动作。可登记安全停止作为完整策略，真实入口必须封住读/发布/依赖交易并保留在途和历史。恢复版本时核验目标证明、scope/资格、未退役及可用性；不能确认则保持停止，不强制旧业务恢复。调度按共享触发记录去重；交易冻结→核验未终结→换代次→开放，故障安全拒绝。所有影子、审批、账本和旧证据保留。切前隔离证明与切后生产回退分开记录。

**收尾判据**：模块完成不是门禁验收；门禁验收需真实入口、动态隔离/影子、回滚与否定路径验证。生产切流需实际路由及观察，退役需实际清零。实盘状态不从库中一条未知来源授权推断。未完成任务继续未勾选。

## Open Questions

- 首批可切范围由任务 0.3 的依赖矩阵与最终资格结果确定；当前不预设任何消费者已合格。
- 观察窗口与停止阈值由实际影子噪声和风险等级形成候选值，在任务 8.6 明确审阅并登记，未确认前不得生产切流。
- 必要 TWS 只读访问时点和环境由 7.13 核实；不可达只阻断依赖该证明的范围，不伪造记录。

### D29. 7.3/7.5 动态观点与完整模拟恢复验收

隔离验收观察实际 Provider/仓库/Connection/Cursor 调用及治理投影返回，保存 producer、scope、ID/hash，与实际发布 input_refs 交叉核对。旁路被角色捕获仍使验收失败；候选须经实际 admission/publication 判断。Information 文档处理 lease 只属生命周期元数据，insights 引用经产品解析、观点仍留 Workflow Memory。动态观察只证明执行分支，不替代静态审阅或成为生产安全沙箱。

完整 Chief graph 由真实研究快照产生非空决策；真实 Risk、审批中断、Trader/FakeBroker、Clerk 运行，外部账户/报价/模型/人工答复为明确 fixture。Trader 独立绑定 scope；价格变更只能拒绝并新 revision 重审重批；审批拒绝后清除旧 stale outcome 并终止。累计成交须更新 VWAP，部分订单保持未终结；恢复和新进程重开不能重复下单/入账，绩效读模型按实际事实重建。

受控单链不是生产全范围或 3.9/3.10 双跑验收。新增共享 Memory、Chief 输出/decide 与 reconcile 的指纹依赖在最终冻结后由 7.17/6.1/6.4 补验，不改历史证据或生产 C3。结果及既有失败/Clerk 处置限制见 docs/validation/PHASE_F_OPINION_AND_EXECUTION_2026-10-08.md。


### D22. 5.3/5.6 实际入口接线（2026-10-08）

发布点先只读查询边界权威状态，disabled 或权威不可读即拒绝。Analyst 新 envelope/旧 projection、Risk review/Boss approval 及 Clerk 的启动/每步/完成发布均接入；Chief 不吞掉审批边界拒绝。隔离/影子发布核对 SQLite 实际连接文件，不能以对象声明的 path 替代；Clerk 子步骤绑定同一连接。隔离初始化只发生在隔离控制库，真实 broker 写能力仍禁止。

CLI 业务入口、owned Workflow、Dispatcher、Chief graph/快照及 native consumer_api 使用不可变 business identity；domain/consumer/contract 来自清单，kind/id/entities/时间窗/事件版本来自本次请求和配置展开，绝不用产品列表替代。旧路由也查询当次资格，但不会因此迁移；只有逐 scope target 且接线证明/资格通过才使用新路由。Dispatcher 在恢复/复用之前及 worker 入口重查，fresh run 的不合格任务记 blocked，独立合格分支可继续。计划配置漂移拒绝，不静默重解释 scope。

实际 CLI 可使用顶层 `--read-scope-json`（按 consumer 键）；Workflow 请求的 task_inputs.read_scopes 按 task instance key 或 consumer:ProjectionScope.key 键。native read_input 的 query scope 与 business_scope 分列：后者必需（或来自已绑定入口），实体/消费者/历史 cutoff 逃逸拒绝，门禁异常不能吞成空 payload。持久化输入省略 as_of 时继承绑定上下文的冻结 cutoff；current_only 输入仍保留真实来源/查询时点，不接受历史 as_of。native API 本身没有 legacy 实现，生产 legacy 不能借它服务 target 数据；完整路径重定向+broker 禁写的隔离候选仍可读本地数据形成证据。

本次 consumer_api/repository/Clerk 属于 0.1 已确认的取证前必要修改。旧全文件指纹证据保留为历史，consumer_api 影响全部十角色；最终补验与脚本的显式 business_scope 接入由 7.17/6.1/6.4 承接。本轮不登记生产 enforced/eligible，不提前完成 5.7/5.8、9.3/9.6 的实际读模型迁移或解除 C3。

### 7.7/7.17 当前处置与补验基线（2026-10-08）

逐消费者处置绑定实际 scope 与产物摘要/定位，空产物为 pending，记录漂移拒绝。`ok` 不是运行证明、资格或切流批准；旧业务缺陷仅登记，claim/publication/boundary 工程缺口仍按 4/5 组实施。Clerk 子步骤失败未必改变 completed 的旧风险保留，成功须核对步骤与事实。

A 的共享保护并集当前为 72 个路径（含全文件 manifest），十角色契约/指纹漂移与旧版本隔离回归已完成，非 Trader 权限及 optional/no_coverage/FactSet/partial 政策不变。实际隔离完整链和最小补验清单见 `docs/validation/PHASE_F_CONSUMER_DISPOSITION_AND_A_REGRESSION_2026-10-08.md`。此前实施段落中的路径数/待办保持原时点，本段为当前状态；6.1 仍须等完整接线/影子/回退前置并冻结选定范围的真实依赖闭包，6.4 再追加资格，不覆盖旧证据，不全量重采。

### D23. 测试执行与生产 C3 分离（2026-10-08 用户确认）

隔离账本、进程真实 broker 禁写和明确的无网络 FakeBroker 同时成立时，允许实际 Chief/Risk/审批/Trader 业务链模拟提交。审批、完整修订、行情政策、账户/代次/grant、幂等检查均保留，禁止用预置订单替代上游执行。7.5 验收非空真实业务产物及部分/迟到成交、Clerk 对账/绩效重建；11.2 验收模拟授权、测试 grant 和 broker 选择不能逃逸到 IBKR 或生产。生产 C3 保留，IBKR Paper 真实网络写入须单独明确开放，真实资金账户不在本 change 执行范围。不得用全局启用开关替代环境与 transport 策略。


### 3.1/3.6 实施契约（2026-10-08）

可恢复输入采用独立 shadow-replay-v1 内容存储，保留完整逐面内容与治理 ConsumerInput，不能把旧 hash-only declaration 当作已冻结内容。捕获报价以 stage/purpose 区分审批前与批准后，保存源时点与查询时点。3.1/3.9 历史实施向新旧路径注入同一输入/时钟，拒绝未知查询；当前验收复用输入与新侧，由 3.10 实现无旧侧依赖的新入口独立运行/重放，不以测试 helper 替代。

历史正式 checker 与工具级 check_citable 分开，既有 checker 要求 durable input_store/left_run_id/right_run_id 并重算比较。当前 3.6 重开：新报告 checker 改为 input_store/new_run_id/replay_run_id、需求版本及断言，校验实际新入口与重放，不再要求旧侧或两种不同业务入口。所有切换入口强制要求非空报告 ID 和 checker；CLI 使用内建 checker，缺失或报错拒绝。report_id 不再是可选的放行提示。新报告的修复补验进入 pending，须重新签核；报告指纹包含相关门禁、重放源文件和配置，资格 manifest 与历史资格记录保持独立。

3.1/3.6 的实现与验证见 docs/validation/PHASE_F_SHADOW_FOUNDATION_2026-10-08.md。它们解除基础依赖，不构成 3.9/3.10 的真实业务报告或任何生产切流批准。


### 7.1/3.7 实施契约（2026-10-08）

run_isolated_entry 调用实际业务 callable，在 isolated_verification 中显式绑定隔离 Workflow store；目标路径须先检查与生产默认/当前路径不重合。首次建库执行 schema bootstrap，既有隔离库只安全重开并核验必要表，避免重启自动重做历史回填而破坏审查/批准绑定。退出审计在异常路径也执行，生产审批、路由、资格及交易表核对 counts/row digest；不可读或变化均使验收失效，不自动清理。上下文恢复原 grant，运行证据与返回结果明确不可交易。

shadow_execution 为独立、显式的拒绝传输，不能与 FakeBroker 同时选择。实际 Trader 仍执行授权/修订/报价门禁，再由真实 IBKR facade 的进程禁令在任何会话前拒绝。订单意图按 run/cycle/revision/sequence 幂等保留，完整订单/授权/route 归因与审批依据存 provenance，当前执行报价及真实拒绝审计存 attempt；二者均追加式。预期拒绝使用 rejected 订单状态及 shadow_refused gate outcome，不表示成交或运行失败。任何意外未阻断调用都留审计并停止。

Chief 对影子结果显式发布，不能把 save_trades 误写静默重定向。真实 trades/fills 发布点拒绝影子来源，FakeBroker 返回结果携带 isolation_root，离开上下文后仍不能进入生产或另一隔离范围；Clerk 传播发布拒绝。新进程只读重建影子归因，不回查 Provider、不改原记录。详见 docs/validation/PHASE_F_SHADOW_LEDGER_2026-10-08.md；本基础样例不代替多轮/完整接入/双 runner 验收。


### D26. 实际读入口的安全回退（5.7）

回退必须消费当前 exact-scope 路由历史绑定的不可变 JSON 证明，校验文件 SHA256、scope、有效期、旧实现标识/源码摘要与当前 manifest 依赖指纹。读取退役登记，执行一次真实旧读并检查非空 refs，只返回该次已核验结果。读取前后重查证明撤销、退役和路由代次；任一项失效即记录 blocked/unavailable 并中止调用。独立 fallback_revocations 追加撤销记录，不改证明字节，不扩大其他 scope。现有无消费适配的入口仍拒绝，不能把 gate 的降级判定当作已读取成功；内部状态使用冻结 InternalFallback 适配，不接受任意布尔回调放行。

### D27. 五角色实际消费边界（7.11）

Fundamental Routine 消费治理 brief 投影；Event/PEAD 经公司财务快照、Consensus、层级证据和已接收文档包消费，不在目标/隔离候选路径补调用 Provider。Chief 通过治理投影和 state API 组装上下文；Risk 独立绑定实际 decision scope，经只读账户 runtime、state API 与规则版本留引用。Trader 除受限治理报价外，经治理接口核对完整批准授权，保持 A 的账户/grant/修订/价格/代次门禁。Clerk 复用只读 broker facade 读取账户、执行回报和真实 decision audit 链，各步骤及最终发布前重查当次资格；失败停止，不自动新建替代 broker。

### D28. 研究读模型与固定决策要求（9.3/9.6 → 7.2 → 7.4）

Chief 必须读取并渲染全部必需 scope 的完整 projection ID/hash 与内容；Sector CLI 在目标/隔离候选路径读实际 SectorAllocation，旧报告兼容路径保留。完整性检查不可由 CLI 选项省略。Information 在候选路径使用治理文档产品，实际 brief 保存 admitted document/version/publication 引用。

实际 Dispatcher 计划本身是固定需求来源，按 `decision-requirements-v1` 保存 profile/version/hash、源配置摘要、所选模式与任务依赖、scope、schema、outcomes 和 manifest。Chief 组装、模型调用、首次风险审查与开周期持久化重新解析并校验同一份要求；配置漂移、必需 scope 缺失、引用或 lineage 不匹配、未完成 attempt、过期均停止。独立 Chief CLI 显式选择 `--fundamental-mode routine|event`（缺省 routine）和 `--decision-profile`，只允许同一固定要求下的合法复用；PEAD 收口显式选择 event。未配置 Chief runner 的 Dispatcher 不宣称已经进入周期。

Snapshot 与 run contract 检查同一已选依赖闭包。Event/Routine 只选一个，另一模式不能替代，六类必需分析及各配置 scope 仍须齐全。历史 snapshot inventory 的任一模式比较只用于兼容库存查看，不作为实际开周期依据。隔离 fixture 范围和 No Action 验收不替代生产配置全覆盖、7.5 的非空交易链或最终资格。

实际调用记录 consumer/API/refs/status 并进入结构化日志；隔离入口将 trace 追加到独立业务运行账本。native 投影读检查 owner、scope、内容 hash 与复用状态；不扩展其他角色产品权限，也不要求所有角色直接导入 read_input。共享面/manifest 修改影响全部十消费者，当前依赖闭包为 44 个路径；旧证据保留，7.17/6.1/6.4 在最终冻结后补验。9.3/9.6 的完整渲染兼容与跨进程可用性、7.4 的完整性判定、7.5 的完整恢复链继续按原依赖推进。验证见 docs/validation/PHASE_F_SAFE_READS_2026-10-08.md。


## 第 4 组实施收口：2026-10-09

实际入口使用独立 SQL schedule runtime 控制表；旧 dispatch_claims/schedule_cutover helper 保留兼容，不作为实际业务认领证据。YAML 首次显式安装时提供 owner，后续仅提供 wake 配置；启动/重载必须读取现存 SQL 权威，缺失或损坏停止，不自动修复或降代。owner/generation 按精确 workflow/scope，模板仅用于首次范围建档。未改动生产 owner YAML 或 enabled 标志；原先未安装栅栏的驻留程序须在部署阶段受控重启，不能宣称旧二进制自动受保护。

旧计划 tick 经 APScheduler worker 传入，旧配置事件与新事件使用同一稳定 event ID/version；PEAD 财报评分窗口只消费已发布财报版本，缺版本停止，不由研究进程重建 calendar。旧 monitor 映射 information-brief，实际评分映射 fundamental-event。相同逻辑身份完成后不因手动重试或输入变化重发；失败恢复要求独立 attempt actor。

冻结先于未完成清点；交接事务再次核对全部未完成认领及显式原因。carry_over 仅允许未发布任务，void 留墓碑；already_run 必须由原 worker 实际完成并留下结果引用，不接受操作者自报完成。真实 Memory/报告发布与控制栅栏配合，结果库保留 claim/owner/generation/actor 绑定以封住结果已提交而控制记录未提交的崩溃窗口；财报 final 元数据修改仍核对原 score 绑定及当前代次。

预期集合从 cron/event 配置及已发布事件独立导出，不使用两侧执行并集；空账本不能通过。双路径 arrival、实际 refs、共同遗漏、wake/misfire 均可查。研究调度只消费 calendar，交接不操作采集 owner/queue lease/job 生命周期。隔离 prepare/freeze/handover/abort 与实际发布已验证；生产激活和多边界事务协调仍按 10.2/5.15/10.4 实施，最终资格冻结仍由 6.1 承接。验证与原始材料见 docs/validation/PHASE_F_SCHEDULE_RUNTIME_2026-10-09.md。


### D29. 3.9 同输入实际业务入口（2026-10-09）

隔离 capture-business 固定请求、配置、已接收数据/历史、治理读、runtime 与模型响应；run-pair 恢复完整内容，分别执行现有 legacy 手动 role/CLI 依赖链与真实 Dispatcher，风控分别调用 pre_trade/review_revision。两侧独立库/独立 run ID、同一逻辑时钟和完整依赖指纹；实际计算及发布保留，不导入左右业务 JSON。模型响应是固定 transport 输入，prompt hash 与查询集合留痕；允许不同查询子集，未知查询/输入漂移停止。当前旧入口是受治理保护的兼容业务链，旧风控适配器委托 revision，引擎独立性与历史二进制等价性不在本轮结论中。

数据库快照包含已有文档处理 lease，仍要求操作有效期，不启动/续租采集。逻辑时间仅作用于隔离业务计算，源报价时点不改写；复制 ContextVar 至 Dispatcher worker。PEAD 报告重定向隔离根并核对发布权。冻结输入、运行与失败/完成 receipt 追加保存，后侧失败保留前侧与真实 refs；不擦除重试。验证 input/clock/source、依赖闭包和实际 SQLite projection hash/payload。共享面新增九路径，保护并集 100，十角色旧证据保留并待最终新指纹资格验收。

3.9 实施证据与受控 fixture 范围见 docs/validation/PHASE_F_PAIRED_BUSINESS_2026-10-09.md；当前 3.10 改为新入口需求验收/报告签核及提交/独立调度覆盖，完整交易范围仍依赖 11.2；不要求旧侧继续成功，生产 C3/切流和最终冻结独立。

### D30. 安全停止、已验收版本恢复与失败归因（2026-10-09）

**13.3 实施**：停止由实际持久 freeze token 保护签发和提交；新增仅隔离 `restore_stopped_simulation`，在跨进程仲裁锁中校验原 token/当前代次、当前目标证明/实际 FakeBroker 账户环境及实现指纹，核验全部意图回执和实际账本全周期。未知/部分订单、遗漏批准周期或不可用目标均保持停止；Clerk 迟到只读/幂等承接不开放写权。恢复换代再开放，开放失败或证明提交中失效不 abort；已换代重试只完成同代开放。旧代 grant 不复活，后续订单仍需新代绑定及全部审批/报价门禁。生产/C3 未改，其他边界和最终资格独立推进。见 [验证记录](../../../docs/validation/PHASE_F_TRADE_SAFE_STOP_2026-10-09.md)。

5.7 扩展逐 scope 恢复策略；13.1–13.3/13.10 实测停止或已验收版本恢复，9.5/10.4 保存真实生产动作与观察结果。停止不是静态 disabled 标签：实际认领/读取/发布/依赖提交应拒绝，在途订单/步骤有明确处置，历史与审计不删除，不触及无关范围；目标缺失可保持停止。恢复版本重新核对当前资格、兼容、批准修订、代次和幂等；不能复活旧授权。旧目标仅在明确选用时验证可用性，不为其补完旧业务。

12.2/12.3 重开，新设计全部必需用途/scope 与历史引用须覆盖，但废弃旧用途和旧输出一致不作退役门槛。生产消费者清零与历史/离线诊断材料保留分列。

13.6 全量运行仍用于发现问题；逐失败记录位置/基线/需求关联、是否新入口或共享依赖可达、隔离/退出安全证明及阻塞范围。仅属废弃旧业务且无新状态影响的失败可登记排除；新链路失败、共享事实/权限/历史破坏、无法证明归属者不能借排除放行。新范围验收无需全仓旧测试全绿；不得据此宣称整个仓库全绿或父 change 全部达标。


### 3.2/5.7 新口径实施（2026-10-09）

`acceptance_matrix.freeze/restore/check_inputs` 编译实际 WorkflowPlan 的所选模式、依赖闭包、task/read identity、schema/时效预期和输入固定程度。矩阵只固定预期，不产生通过结论；即使重算 hash，也不能删掉必需断言或依赖。Technical/Sector 及 live Macro/Event 的 runtime 必须固定；只有明确关闭模型的任务可声明模型输入不适用。旧 shadow_matrix 与比较工具保留历史语义，新报告链由 3.3–3.6/3.10 接入。

`read_recovery.register` 在独立控制库追加绑定不可变证明及 scope，恢复策略为 stop/new_version/legacy。恢复证明绑定实现及依赖摘要、有效期、目标版本/scope 与内容绑定的已验收目标引用；new_version 独立核验目标当前资格，不能继承原版本资格或自行下载/部署旧代码。实际版本 adapter 必须明确供应；缺 adapter/目标未合格/不可读则停止。已有 state/Chief 入口通过 InternalFallback 显式携带受核验的新版本 reader，不把降级路由当作已成功读取。

失败追加 stopped 事件并锁定范围，即使随后资格变为 eligible 也不自动复活；显式重新登记才可重新绑定入口。绑定上下文保存恢复代次，读前后核对权威/撤销/退役/资格，发布点拒绝 stopped 范围及策略变化前的旧 worker。其他 scope、历史数据、路由历史和 broker 权限保持独立。全边界原子恢复、已有快照依赖失效重验、正式报告签核及生产恢复分别仍待 5.15/5.8/3.6/13.x/9.5。验证与指纹影响见 PHASE_F_NEW_REQUIREMENTS_AND_RECOVERY_2026-10-09.md。

### 3.3 六面断言实施（2026-10-09）

矩阵升级为 new-entry-matrix-v2，编译独立任务触发身份，并固定有限 JSON 语义/数值 criteria。acceptance_assertions.evaluate 只读取固定输入、实际工作流/claim、投影与 attempt、完整 revision/review/人工审批/receipt/成交记录，逐项给出 passed/failed/untested/not-applicable、预期/实际/refs 与原因。条件只能增加要求，不能关闭 schema、实体语义、hash、时效、血缘、完整审查/审批与隔离/幂等检查；旧诊断数据不参与 oracle。无预先场景或实际测量的必需边界为 untested。

136 项相关回归通过。真实 Chief/FakeBroker/Clerk 重开反例发现兼容迁移重复导入 native cycle，推进当前 revision，使审批断言 failed；它是新入口共享状态缺口，由 3.4 修复重验，不以旧缺陷排除。签核/报告/checker 和实际独立 capture/run/replay 仍待 3.4–3.6/3.10；本轮当前业务组件输入证据仍标 untested，未授予生产资格。详见 docs/validation/PHASE_F_REQUIREMENT_ASSERTIONS_2026-10-09.md。


### 3.4–3.6 修订恢复、新报告与正式 checker（2026-10-09）

空库 legacy backfill 也记录完成，已有 revision 的 cycle 不重复导入兼容镜像；原生批准修订跨 Clerk 子进程重开保持不变，真实 legacy-only/unknown 历史保留。3.3 历史失败材料不修改，修复结果在 PHASE_F_ACCEPTANCE_REPORTS_2026-10-09.md 追加。

新报告类型 new-entry-acceptance-v1 保存固定矩阵、输入、实际新入口/独立重放/工作流 run、refs、完整代码/配置指纹及逐项结论，失败处置不能接受旧差异豁免；历史比较显式保留 historical-comparison-v1。报告和签核/驳回/撤销为追加记录，绑定报告 hash 和前一事件；补验必须新运行、新报告、重新签核。

中央 check_batch_report 只核验新报告，重解析固定输入、实际 SQL 记录与六面断言，核对 exact scope/消费者覆盖/版本/指纹及未撤销签核。只读检查不迁移或初始化证据库，缺实际执行/重放/refs/必需项和旧 JSON 均拒绝；内部审批/Clerk 批次须完整风控/审批/归因，不能借纯研究或 risk-only 矩阵降级。真实 Dispatcher 新入口可用固定输入执行隔离独立重放，不再要求旧侧或左右相同。完整 capture、全选定 workflow 窗口和交易输入报告仍由 3.10（交易依赖 11.2）交付；共享影响十角色，最终冻结/资格独立。


### 11.2 → 3.10：独立新入口与隔离授权（2026-10-09）

真实 capture 固定运行前矩阵/config/seed 与完整源码指纹，只调用新 Dispatcher。通过声明数据/模型/实际人工答复的传输记录及按业务调用位置/顺序记录的评估时刻，另一个进程在无网络情况下使用相同输入独立执行。行情/账户源时间不重写；每轮 revision/review/批准/授权经当前实际审计链重建，不注入捕获侧授权对象。每轮新 workflow/replay ID 与 ledger，不比较或依赖旧侧。

完整交易显式选择无网络 FakeBroker，测试 grant 绑定 simulation/paper/account/generation；真实 IBKR facade（含 Paper）仍在网络前拒绝，实际拒绝持久化由 checker 回查。Trader 重试 receipt 不增加；实际模拟回报交 Clerk 落库和归因。跨进程发现的 Chief 报告生产目录泄漏通过完整隔离根内 docs/chief 写入修复，生产行为不变；遗漏的 Sector runtime 传輸也纳入固定接口。

选定 Routine/Event 各两轮的 11 个必需断言全部通过，6 个固定研究触发全覆盖、每轮 19 refs；Technical 子范围独立通过，缺行情负例保留失败且拒绝签核。三个通过报告保持 unsigned，旧差异接受/旧签核不为前置。当前 464 完整源码/提示/配置依赖及 100 保护路径，后续漂移须新运行/新报告，最终生产冻结与资格仍按 6.1/6.4。40 项核心/启动有效验收、274 项回归通过；原错误、目录配置失败与旧夹具失败保留。详见 docs/validation/PHASE_F_INDEPENDENT_RUNNER_2026-10-09.md。


### 7.8 → 13.4 → 13.5：动态索引与原始账本验收（2026-10-09）

只读脚本 scripts/verify_phase_f_integration.py 从原始报告/输入/两轮运行/数据库复算动态索引、逐笔全链与提交安全；不修改业务代码/配置或原始证据。原 464 完整实现闭包仍可验证，审计器自身摘要独立保存。提交时 receipt 状态可随实际 Clerk 成交推进，全部固定字段必须一致，推进须与当前 trade/fills 一致。生产本轮 before/after 逐行摘要及精确归因核验与拒绝审计/模拟接收独立，不能由无生产新增推断没有下单。四轮模拟全链通过、58 项有效检查通过，报告 unsigned，TWS 未测；详见 [本轮验证](../../../docs/validation/PHASE_F_INTEGRATION_2026-10-09.md)。全边界恢复/最终资格/生产激活保持未完成。
